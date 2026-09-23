"""Autofill profile service — builds autofill profiles from candidate profiles.

Also handles LLM-based field mapping for unknown form fields (metadata only).
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.domain.extension.application_event_service import ApplicationEventService
from app.domain.extension.repository import ExtensionRepository
from app.domain.extension.schemas import (
    ApplicationEventRequest,
    FieldMappingItem,
    FieldMappingRequest,
    FieldMappingResponse,
    FormFillLogRequest,
    FormFillLogResponse,
)
from app.domain.resume.repository import ResumeRepository
from app.infrastructure.llm_client import (
    LLMUnavailableError,
    classify_provider_failure,
    llm_chat_completion,
)
from app.infrastructure.llm_utils import strip_code_fences

logger = get_logger("autofill")


class AutofillProfileService:
    def __init__(
        self,
        *,
        resume_repository: ResumeRepository,
        extension_repository: ExtensionRepository,
        application_event_service: ApplicationEventService,
    ) -> None:
        self._resume = resume_repository
        self._ext = extension_repository
        self._events = application_event_service

    async def get_autofill_profile(self, *, user_id: int) -> dict[str, object]:
        """Build an autofill profile from the user's candidate profile.

        Returns a structured dict with personal, links, work, authorization,
        education, experience, and skills sections.
        """
        profile = await self._resume.get_active_profile(user_id)

        if profile is None:
            return _empty_autofill_profile()

        parsed = profile.parsed or {}

        return {
            "personal": {
                "first_name": _extract_name_part(parsed.get("full_name"), "first"),
                "last_name": _extract_name_part(parsed.get("full_name"), "last"),
                "email": parsed.get("email"),
                "phone": parsed.get("phone"),
                "city": parsed.get("city"),
                "country": parsed.get("country"),
            },
            "links": {
                "linkedin": parsed.get("linkedin"),
                "github": parsed.get("github"),
                "portfolio": parsed.get("portfolio"),
                "website": parsed.get("website"),
            },
            "work": {
                "current_title": parsed.get("headline") or parsed.get("current_title"),
                "current_company": parsed.get("current_company"),
                "years_experience": parsed.get("years_experience"),
                "expected_salary": parsed.get("expected_salary"),
            },
            "authorization": {
                "requires_sponsorship": parsed.get("requires_sponsorship"),
                "authorized_to_work": parsed.get("authorized_to_work"),
            },
            "education": parsed.get("education") or [],
            "experience": parsed.get("work_history") or [],
            "skills": parsed.get("skills") or [],
        }

    async def map_fields(
        self,
        *,
        request: FieldMappingRequest,
    ) -> FieldMappingResponse:
        """Use LLM to map unknown form fields to profile paths.

        IMPORTANT: Only field metadata (labels, types, options) is sent to the
        LLM. User personal values are NEVER included.
        """
        if not request.fields:
            return FieldMappingResponse(mappings=[])

        # Deterministic mapping handles most fields client-side.
        # This endpoint is the LLM fallback for unknown fields.

        fields_json = [
            {
                "id": f.element_id,
                "label": f.label,
                "type": f.input_type,
                "placeholder": f.placeholder,
                "options": f.options,
            }
            for f in request.fields
        ]

        try:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a field mapping assistant. Given form field metadata "
                        "(label, type, placeholder, options), map each field to a "
                        "profile path using the schema below. Respond with JSON.\n\n"
                        "Available profile paths:\n"
                        "- personal.first_name\n"
                        "- personal.last_name\n"
                        "- personal.email\n"
                        "- personal.phone\n"
                        "- personal.city\n"
                        "- personal.country\n"
                        "- links.linkedin\n"
                        "- links.github\n"
                        "- links.portfolio\n"
                        "- links.website\n"
                        "- work.current_title\n"
                        "- work.current_company\n"
                        "- work.years_experience\n"
                        "- work.expected_salary\n"
                        "- authorization.requires_sponsorship\n"
                        "- authorization.authorized_to_work\n"
                        "- education (array)\n"
                        "- experience (array)\n"
                        "- skills (array)\n\n"
                        "Confidence rules:\n"
                        "- 0.95+ for exact matches (e.g., 'First Name' → personal.first_name)\n"
                        "- 0.70-0.94 for plausible but ambiguous matches\n"
                        "- Below 0.70 for guesses\n"
                        "- null path for un-mappable fields\n\n"
                        "IMPORTANT: Never suggest mapping for fields related to:\n"
                        "race, ethnicity, gender, sex, disability, veteran status,\n"
                        "health, criminal history, religion, political affiliation,\n"
                        "EEO, password, file upload, citizenship, background check.\n"
                        "For these, return path: null, confidence: 0, action: 'skip'."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Map these fields to profile paths:\n{fields_json}"
                    ),
                },
            ]

            completion, _model = await llm_chat_completion(
                messages=messages,
                model=None,  # Use default
                temperature=0.1,
                json_mode=True,
            )

            content = strip_code_fences(
                (completion.choices[0].message.content if completion.choices else None) or "[]"
            )
            import json

            try:
                raw_mappings = json.loads(content)
            except json.JSONDecodeError:
                logger.warning("autofill.llm_json_parse_failed")
                return FieldMappingResponse(mappings=[])

            mappings = []
            for m in (raw_mappings if isinstance(raw_mappings, list) else []):
                if not isinstance(m, dict):
                    continue
                mappings.append(
                    FieldMappingItem(
                        element_id=m.get("id", m.get("element_id", "")),
                        profile_path=m.get("path") or m.get("profile_path"),
                        confidence=float(m.get("confidence", 0)),
                        action=_determine_action(
                            m.get("path") or m.get("profile_path"),
                            float(m.get("confidence", 0)),
                        ),
                        reason_skipped=m.get("reason_skipped"),
                    )
                )

            return FieldMappingResponse(mappings=mappings)

        except LLMUnavailableError as exc:
            logger.warning(
                "autofill.llm_mapping_providers_exhausted",
                provider_failure_category=exc.provider_failure_category,
                providers_exhausted=exc.providers_exhausted,
                attempted_provider_names=exc.attempted_provider_names,
                attempt_count=exc.attempt_count,
            )
            return FieldMappingResponse(mappings=[])
        except Exception as exc:
            category = classify_provider_failure(exc)
            if category != "unknown_provider_error":
                logger.warning(
                    "autofill.llm_mapping_provider_failed",
                    provider_failure_category=category,
                )
            else:
                logger.exception("autofill.llm_mapping_failed")
            return FieldMappingResponse(mappings=[])

    async def log_fill(
        self,
        *,
        user_id: int,
        request: FormFillLogRequest,
    ) -> FormFillLogResponse:
        """Record a form fill session with field-level details."""
        fields = request.fields
        mappings = request.mappings

        session = await self._ext.create_form_session(
            user_id=user_id,
            domain=request.domain,
            ats_type=request.ats_type,
            field_count=len(fields),
        )

        execution = request.execution
        filled = execution.filled if execution is not None else 0
        skipped = execution.skipped if execution is not None else len(fields)

        for i, field in enumerate(fields):
            field_dict = field if isinstance(field, dict) else {}
            mapping_dict = mappings[i] if i < len(mappings) else {}
            if isinstance(mapping_dict, dict):
                # A mapping is a proposal. It is never evidence that a DOM
                # setter actually succeeded.
                was_filled = False

                field_key = field_dict.get("elementId") or field_dict.get("name")
                field_label = field_dict.get("label")
                field_type = field_dict.get("inputType")
                profile_path = mapping_dict.get("profilePath")
                confidence = mapping_dict.get("confidence")
                reason_skipped = mapping_dict.get("reasonSkipped")
                await self._ext.add_form_field(
                    form_session_id=str(session.id),
                    field_key=field_key if isinstance(field_key, str) else None,
                    field_label=field_label if isinstance(field_label, str) else None,
                    field_type=field_type if isinstance(field_type, str) else None,
                    profile_path=profile_path if isinstance(profile_path, str) else None,
                    confidence=confidence if isinstance(confidence, (int, float)) else None,
                    was_filled=was_filled,
                    reason_skipped=(
                        reason_skipped
                        if isinstance(reason_skipped, str) and not was_filled
                        else None
                    ),
                )

        await self._ext.complete_form_session(
            str(session.id),
            filled_count=filled,
            skipped_count=skipped,
        )

        if execution is not None:
            await self._events.record_event(
                user_id=user_id,
                request=ApplicationEventRequest(
                    event_type="autofill_completed",
                    attempt_id=request.attempt_id,
                    event_id=request.event_id,
                    metadata={
                        "domain": request.domain or "",
                        "ats_type": request.ats_type or "",
                        "attempted": execution.attempted,
                        "filled": execution.filled,
                        "skipped": execution.skipped,
                        "failed": execution.failed,
                    },
                ),
            )

        return FormFillLogResponse(
            session_id=str(session.id),
            recorded=True,
        )


# ── Helpers ─────────────────────────────────────────────────────────────────

def _empty_autofill_profile() -> dict[str, object]:
    return {
        "personal": {
            "first_name": None,
            "last_name": None,
            "email": None,
            "phone": None,
            "city": None,
            "country": None,
        },
        "links": {
            "linkedin": None,
            "github": None,
            "portfolio": None,
            "website": None,
        },
        "work": {
            "current_title": None,
            "current_company": None,
            "years_experience": None,
            "expected_salary": None,
        },
        "authorization": {
            "requires_sponsorship": None,
            "authorized_to_work": None,
        },
        "education": [],
        "experience": [],
        "skills": [],
    }


def _extract_name_part(full_name: object | None, part: str) -> str | None:
    if not full_name or not isinstance(full_name, str):
        return None
    parts = full_name.strip().split()
    if not parts:
        return None
    if part == "first":
        return parts[0]
    return parts[-1] if len(parts) > 1 else None


def _determine_action(
    path: str | None,
    confidence: float,
) -> str:
    if path is None or confidence < 0.70:
        return "skip"
    if confidence >= 0.95:
        return "fill"
    return "review"
