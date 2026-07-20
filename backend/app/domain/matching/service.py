"""Matching service: three-layer JD-to-resume scoring (Phase 3).

Layer 1 (vector pre-rank): pgvector cosine similarity ranks jobs cheaply.
Layer 2 (deterministic skill recall): free, reproducible evidence of which
    JD-required skills the resume contains (alias-aware whole-word matching).
Layer 3 (LLM explanation/score): selective, grounded by layer 2, cached on the
    ``UserJobMatch`` row so we never pay twice.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.domain.matching.exceptions import (
    MatchNotFoundError,
    NoActiveProfileError,
)
from app.domain.matching.prompts import MATCH_EXPLANATION_PROMPT
from app.domain.matching.readiness import build_match_readiness
from app.domain.matching.readiness_preferences import ReadinessPreferenceRepository
from app.domain.matching.repository import MatchingRepository
from app.domain.matching.schemas import (
    ClassifiedSkillEvidence,
    ExperienceRequirement,
    MatchEvidence,
    MatchExplanation,
    MatchItem,
    MatchListResponse,
    MatchReadiness,
    MatchSkillAnalysis,
    MatchSummary,
    RankResponse,
    SkillEvidence,
    SkillEvidenceGroup,
    SkillGapItem,
    SkillsGapSummary,
)
from app.domain.matching.skill_matcher import (
    classify_job_skills,
    compute_skill_match,
    extract_known_skills,
    parse_experience_requirements,
    report_to_dict,
)
from app.infrastructure.llm_client import LLMUnavailableError, llm_chat_completion
from app.infrastructure.llm_utils import strip_code_fences

logger = get_logger(__name__)

_VALID_CONFIDENCE = {"strong", "moderate", "stretch", "overqualified"}


def confidence_band(similarity: float) -> str:
    """Map cosine similarity (0..1) to a coarse confidence band."""
    if similarity >= 0.80:
        return "strong"
    if similarity >= 0.65:
        return "moderate"
    if similarity >= 0.50:
        return "stretch"
    return "weak"


class MatchingService:
    def __init__(
        self,
        repository: MatchingRepository,
        readiness_repository: ReadinessPreferenceRepository | None = None,
    ) -> None:
        self._repo = repository
        self._readiness_repo = readiness_repository

    async def rank_jobs(self, user_id: int, limit: int) -> RankResponse:
        profile = await self._repo.get_active_profile(user_id)
        if profile is None or profile.embedding is None:
            raise NoActiveProfileError
        embedding = list(profile.embedding)
        ranked = await self._repo.rank_jobs_by_vector(embedding, limit, user_id=user_id)
        for job_id, similarity in ranked:
            await self._repo.upsert_match(
                user_id=user_id,
                job_id=job_id,
                vector_score=round(similarity * 100, 2),
                confidence=confidence_band(similarity),
            )
        logger.info("matches.ranked", user_id=user_id, count=len(ranked))
        return RankResponse(ranked=len(ranked), profile_id=profile.id)

    async def list_matches(
        self,
        *,
        user_id: int,
        limit: int,
        offset: int | None = None,
        cursor: str | None = None,
        q: str | None = None,
        source: str | None = None,
        is_remote: bool | None = None,
        job_type: str | None = None,
        location_contains: str | None = None,
        company_contains: str | None = None,
        shift_type: str | None = None,
        source_type: str | None = None,
    ) -> MatchListResponse:
        # Cursor is the frontend path; offset remains a deprecated compatibility
        # fallback for old clients until a later API cleanup removes it.
        rows, total, next_cursor, has_more = await self._repo.list_matches(
            user_id=user_id,
            limit=limit,
            offset=offset,
            cursor=cursor,
            q=q,
            source=source,
            is_remote=is_remote,
            job_type=job_type,
            location_contains=location_contains,
            company_contains=company_contains,
            shift_type=shift_type,
            source_type=source_type,
        )
        items: list[MatchItem] = []
        readiness_preferences = (
            await self._readiness_repo.get_for_user(user_id)
            if self._readiness_repo is not None
            else None
        )
        for job, match in rows:
            items.append(
                MatchItem(
                    job_id=job.id,
                    title=job.title,
                    company=job.company,
                    location=job.location,
                    is_remote=job.is_remote,
                    job_type=job.job_type,
                    description=job.description,
                    shift_type=job.shift_type,
                    salary_min=job.salary_min,
                    salary_max=job.salary_max,
                    salary_currency=job.salary_currency,
                    source=job.source,
                    source_url=job.source_url,
                    company_logo=job.company_logo,
                    vector_score=match.vector_score,
                    confidence=match.confidence,
                    llm_score=match.llm_score,
                    matched_skills=match.matched_skills or None,
                    readiness=self._build_readiness(match, job, readiness_preferences),
                    posted_at=job.posted_at,
                )
            )
        return MatchListResponse(
            items=items,
            total=total,
            limit=limit,
            offset=0 if offset is None else offset,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def skills_gap_summary(self, *, user_id: int, top_n: int = 5) -> SkillsGapSummary:
        """Aggregate the most common missing skills across the user's matches."""
        pairs = await self._repo.aggregate_missing_keywords(user_id=user_id, top_n=top_n)
        total = await self._repo.count_matches_with_gaps(user_id)
        return SkillsGapSummary(
            items=[
                SkillGapItem(keyword=keyword, frequency=frequency) for keyword, frequency in pairs
            ],
            total_matches_considered=total,
        )

    async def get_match_summary(self, *, user_id: int, job_id: int) -> MatchSummary | None:
        """Return existing match data only; never rank, recompute, or call an LLM."""
        job = await self._repo.get_visible_job(user_id=user_id, job_id=job_id)
        if job is None:
            raise MatchNotFoundError(job_id)

        match = await self._repo.get_match(user_id=user_id, job_id=job_id)
        if match is None:
            return None

        readiness_preferences = (
            await self._readiness_repo.get_for_user(user_id)
            if self._readiness_repo is not None
            else None
        )
        return MatchSummary(
            job_id=job_id,
            vector_score=match.vector_score,
            confidence=match.confidence,
            llm_score=match.llm_score,
            matched_skills=match.matched_skills or [],
            missing_keywords=match.missing_keywords or [],
            readiness=self._build_readiness(match, job, readiness_preferences),
        )

    async def explain_match(self, *, user_id: int, job_id: int) -> MatchExplanation:
        match = await self._repo.get_match(user_id=user_id, job_id=job_id)
        if match is None:
            raise MatchNotFoundError(job_id)

        # Cache hit: return the stored explanation, no LLM cost.
        if getattr(match, "llm_analyzed_at", None) is not None or match.llm_explanation is not None:
            return await self._to_explanation(match, user_id)

        job = await self._repo.get_visible_job(user_id=user_id, job_id=job_id)
        profile = await self._repo.get_active_profile(user_id)
        if job is None or profile is None:
            raise MatchNotFoundError(job_id)

        # Layer 2: deterministic skill recall (free, reproducible).
        jd_skills = extract_known_skills(job.description or "")
        report = compute_skill_match(jd_skills, dict(profile.parsed))
        report_dict = report_to_dict(report)

        # Layer 3: LLM explanation grounded by layer 2.
        (
            llm_score,
            llm_confidence,
            missing_keywords,
            explanation,
            conf_band,
            llm_error,
        ) = await self._explain_with_llm(
            profile_parsed=dict(profile.parsed),
            job_description=job.description or "",
            report_dict=report_dict,
            deterministic_missing=report.missing_skills,
        )

        await self._repo.save_explanation(
            match=match,
            llm_score=llm_score,
            llm_confidence=llm_confidence,
            explanation=explanation,
            llm_analyzed_at=datetime.now(UTC),
            llm_error=llm_error,
            matched_skills=report.matched_skills,
            missing_keywords=missing_keywords,
            deterministic_match=report_dict,
            confidence=conf_band,
        )
        return await self._to_explanation(match, user_id)

    async def analyze_skills(self, *, user_id: int, job_id: int) -> MatchSkillAnalysis:
        """Return cached or freshly computed deterministic skill evidence."""
        match = await self._repo.get_match(user_id=user_id, job_id=job_id)
        if match is None:
            raise MatchNotFoundError(job_id)

        if (
            match.deterministic_match is not None
            and match.matched_skills is not None
            and match.missing_keywords is not None
        ):
            return await self._to_skill_analysis(match, user_id)

        job = await self._repo.get_visible_job(user_id=user_id, job_id=job_id)
        profile = await self._repo.get_active_profile(user_id)
        if job is None or profile is None:
            raise MatchNotFoundError(job_id)

        report = compute_skill_match(
            extract_known_skills(job.description or ""),
            dict(profile.parsed),
        )
        await self._repo.save_deterministic_match(
            match=match,
            matched_skills=report.matched_skills,
            missing_keywords=report.missing_skills,
            deterministic_match=report_to_dict(report),
        )
        return await self._to_skill_analysis(match, user_id)

    async def get_match_evidence(self, *, user_id: int, job_id: int) -> MatchEvidence:
        """Build fresh, user-scoped deterministic evidence without persistence."""
        job = await self._repo.get_visible_job(user_id=user_id, job_id=job_id)
        profile = await self._repo.get_active_profile(user_id)
        if job is None or profile is None:
            raise MatchNotFoundError(job_id)

        profile_parsed = dict(profile.parsed)
        groups: dict[str, SkillEvidenceGroup] = {}
        for classification, observed in classify_job_skills(job.description or "").items():
            report = compute_skill_match([item.skill for item in observed], profile_parsed)
            matched = set(report.matched_skills)
            groups[classification] = SkillEvidenceGroup(
                matched=[
                    SkillEvidence(
                        skill=item.skill,
                        job_excerpt=item.job_excerpt,
                        profile_excerpt=report.evidence[item.skill],
                    )
                    for item in observed
                    if item.skill in matched
                ],
                missing=[
                    SkillEvidence(skill=item.skill, job_excerpt=item.job_excerpt)
                    for item in observed
                    if item.skill not in matched
                ],
            )

        profile_years_raw = profile_parsed.get("years_experience")
        profile_years = (
            float(profile_years_raw)
            if isinstance(profile_years_raw, int | float)
            else None
        )
        experience = []
        for requirement in parse_experience_requirements(job.description or ""):
            if requirement.scope == "skill_specific" or profile_years is None:
                assessment = "needs_review"
            elif profile_years < requirement.years_required:
                assessment = "may_not_meet"
            else:
                assessment = "meets"
            experience.append(
                ExperienceRequirement(
                    years_required=requirement.years_required,
                    scope=requirement.scope,
                    skill=requirement.skill,
                    job_excerpt=requirement.job_excerpt,
                    assessment=assessment,
                    profile_years_experience=(
                        profile_years if requirement.scope == "total_experience" else None
                    ),
                )
            )

        return MatchEvidence(
            job_id=job_id,
            skills=ClassifiedSkillEvidence(
                required=groups.get("required", SkillEvidenceGroup()),
                preferred=groups.get("preferred", SkillEvidenceGroup()),
                unclassified=groups.get("unclassified", SkillEvidenceGroup()),
            ),
            experience_requirements=experience,
        )

    async def _explain_with_llm(
        self,
        *,
        profile_parsed: dict[str, object],
        job_description: str,
        report_dict: dict[str, object],
        deterministic_missing: list[str],
    ) -> tuple[int | None, float | None, list[str], str | None, str | None, str | None]:
        user_payload = {
            "candidate_profile": profile_parsed,
            "job_description": job_description,
            "deterministic_skill_report": report_dict,
        }
        messages = [
            {"role": "system", "content": MATCH_EXPLANATION_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, default=str)},
        ]
        try:
            response, _ = await llm_chat_completion(
                messages=messages, json_mode=True, temperature=0.2
            )
        except LLMUnavailableError:
            # Degrade gracefully: deterministic evidence still gives a result.
            logger.warning("matches.explain.llm_unavailable")
            return None, None, deterministic_missing, None, None, "LLM service unavailable"

        content = response.choices[0].message.content or "{}"
        try:
            data = json.loads(strip_code_fences(content))
        except json.JSONDecodeError:
            logger.warning("matches.explain.bad_json")
            return None, None, deterministic_missing, None, None, "LLM returned invalid JSON"

        return self._parse_llm_payload(data, deterministic_missing)

    @staticmethod
    def _parse_llm_payload(
        data: object, deterministic_missing: list[str]
    ) -> tuple[int | None, float | None, list[str], str | None, str | None, str | None]:
        if not isinstance(data, dict):
            return None, None, deterministic_missing, None, None, "LLM returned an invalid response"

        score = data.get("score")
        llm_score = int(score) if isinstance(score, int | float) else None

        confidence = data.get("confidence")
        conf_band = (
            confidence if isinstance(confidence, str) and confidence in _VALID_CONFIDENCE else None
        )

        raw_missing = data.get("missing_keywords")
        missing = (
            [str(k) for k in raw_missing]
            if isinstance(raw_missing, list)
            else deterministic_missing
        )

        explanation = data.get("explanation")
        explanation_str = explanation if isinstance(explanation, str) else None

        # Derive a 0..1 confidence number from the band for the UI.
        band_to_num = {
            "strong": 0.9,
            "moderate": 0.7,
            "stretch": 0.5,
            "overqualified": 0.8,
        }
        llm_confidence = band_to_num.get(conf_band) if conf_band else None

        return llm_score, llm_confidence, missing, explanation_str, conf_band, None

    async def _to_explanation(self, match: object, user_id: int) -> MatchExplanation:
        # ``match`` is a UserJobMatch; typed loosely to avoid a circular import.
        job = None
        prefs = None
        if self._readiness_repo is not None:
            job = await self._repo.get_visible_job(
                user_id=user_id, job_id=match.job_id  # type: ignore[attr-defined]
            )
            prefs = await self._readiness_repo.get_for_user(user_id)
        readiness = build_match_readiness(
            score=match.llm_score if match.llm_score is not None else match.vector_score,  # type: ignore[attr-defined]
            confidence=match.confidence,  # type: ignore[attr-defined]
            matched_skills=match.matched_skills,  # type: ignore[attr-defined]
            missing_keywords=match.missing_keywords,  # type: ignore[attr-defined]
            job=job,
            preferences=prefs,
        )
        return MatchExplanation(
            job_id=match.job_id,  # type: ignore[attr-defined]
            vector_score=match.vector_score,  # type: ignore[attr-defined]
            confidence=match.confidence,  # type: ignore[attr-defined]
            llm_score=match.llm_score,  # type: ignore[attr-defined]
            llm_confidence=match.llm_confidence,  # type: ignore[attr-defined]
            llm_available=(
                getattr(match, "llm_analyzed_at", None) is not None
                and getattr(match, "llm_error", None) is None
            )
            or match.llm_explanation is not None,  # type: ignore[attr-defined]
            llm_error=getattr(match, "llm_error", None),
            explanation=match.llm_explanation,  # type: ignore[attr-defined]
            matched_skills=match.matched_skills or [],  # type: ignore[attr-defined]
            missing_keywords=match.missing_keywords or [],  # type: ignore[attr-defined]
            readiness=readiness,
        )

    async def _to_skill_analysis(self, match: object, user_id: int) -> MatchSkillAnalysis:
        job = None
        prefs = None
        if self._readiness_repo is not None:
            job = await self._repo.get_visible_job(
                user_id=user_id, job_id=match.job_id  # type: ignore[attr-defined]
            )
            prefs = await self._readiness_repo.get_for_user(user_id)
        score = match.llm_score if match.llm_score is not None else match.vector_score  # type: ignore[attr-defined]
        return MatchSkillAnalysis(
            job_id=match.job_id,  # type: ignore[attr-defined]
            vector_score=match.vector_score,  # type: ignore[attr-defined]
            matched_skills=match.matched_skills or [],  # type: ignore[attr-defined]
            missing_keywords=match.missing_keywords or [],  # type: ignore[attr-defined]
            readiness=build_match_readiness(
                score=score,
                confidence=match.confidence,  # type: ignore[attr-defined]
                matched_skills=match.matched_skills,  # type: ignore[attr-defined]
                missing_keywords=match.missing_keywords,  # type: ignore[attr-defined]
                job=job,
                preferences=prefs,
            ),
        )

    @staticmethod
    def _build_readiness(
        match: object,
        job: object | None = None,
        preferences: object | None = None,
    ) -> MatchReadiness:
        score = match.llm_score if match.llm_score is not None else match.vector_score  # type: ignore[attr-defined]
        return build_match_readiness(
            score=score,
            confidence=match.confidence,  # type: ignore[attr-defined]
            matched_skills=match.matched_skills,  # type: ignore[attr-defined]
            missing_keywords=match.missing_keywords,  # type: ignore[attr-defined]
            job=job,
            preferences=preferences,
        )
