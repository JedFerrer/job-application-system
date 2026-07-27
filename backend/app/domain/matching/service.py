"""Matching service: hybrid retrieval and deterministic JD-to-resume scoring.

Layer 1 (hybrid retrieval): vector, full-text, and canonical skill retrieval
    produce a bounded candidate set.
Layer 2 (deterministic skill recall): free, reproducible evidence of which
    JD-required skills the resume contains (alias-aware whole-word matching).
Layer 3 (LLM explanation/score): selective, grounded by layer 2, cached on the
    ``UserJobMatch`` row so we never pay twice.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Literal, cast

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.jobs.embedding_coordinator import JobEmbeddingCoordinator
from app.domain.matching.catalog import CATALOG_VERSION
from app.domain.matching.exceptions import (
    MatchNotFoundError,
    MatchNotReadyError,
    MatchPreparingError,
    MatchUnavailableError,
    NoActiveProfileError,
)
from app.domain.matching.hybrid_retrieval import (
    RetrievalCandidate,
    fuse_ranked_candidates,
)
from app.domain.matching.job_skill_mention_repository import JobSkillMentionRepository
from app.domain.matching.llm_fingerprint import (
    canonical_llm_prompt_payload,
    llm_input_hash_for_payload,
)
from app.domain.matching.llm_fingerprint import (
    llm_input_hash as canonical_llm_input_hash,
)
from app.domain.matching.profile_fingerprint import profile_matching_hash
from app.domain.matching.prompts import MATCH_EXPLANATION_PROMPT
from app.domain.matching.readiness import (
    ReadinessContext,
    build_match_readiness,
    deterministic_match_is_current,
    preference_findings,
    readiness_preference_hash,
    select_readiness_score,
)
from app.domain.matching.readiness_preferences import ReadinessPreferenceRepository
from app.domain.matching.repository import MatchingRepository
from app.domain.matching.schemas import (
    ClassifiedMatchSkills,
    ClassifiedSkillEvidence,
    EvidenceConfidence,
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
    sanitize_score_components,
)
from app.domain.matching.scorer import (
    SCORER_VERSION,
    CanonicalSkill,
    DeterministicScoreInput,
    ReadinessFindings,
    score_match,
)
from app.domain.matching.skill_classification import classify_skill_mentions
from app.domain.matching.skill_extractor import default_skill_extractor
from app.domain.matching.skill_matcher import (
    classify_job_skills,
    compute_skill_match,
    parse_experience_requirements,
    profile_skill_ids,
    report_to_dict,
)
from app.domain.matching.skill_types import SkillMention
from app.infrastructure.llm_client import (
    LLMRequestSpec,
    LLMUnavailableError,
    llm_chat_completion_for_spec,
    normalize_model_identity,
    resolve_llm_request_spec,
)
from app.infrastructure.llm_utils import strip_code_fences
from app.models.job_skill_mention import JobSkillMention

logger = get_logger(__name__)

_VALID_CONFIDENCE = {"strong", "moderate", "stretch", "overqualified"}
_LLM_PROMPT_VERSION = "match-explanation-v1"
_LLM_TEMPERATURE = 0.2
_LLM_JSON_MODE = True

_EvidenceSource = Literal["persisted", "fallback", "unavailable"]


@dataclass(frozen=True)
class _ClassifiedEvidenceResult:
    skills: ClassifiedMatchSkills | None
    source: _EvidenceSource


def job_description_hash(description: str | None) -> str:
    return hashlib.sha256((description or "").encode("utf-8")).hexdigest()


def llm_input_hash(
    *,
    profile_parsed: object,
    job_description: str | None,
    deterministic_report: object,
    model: str | None,
    prompt_version: str | None = _LLM_PROMPT_VERSION,
    prompt_text: str | None = MATCH_EXPLANATION_PROMPT,
    ontology_version: str | None = CATALOG_VERSION,
    scorer_version: str | None = SCORER_VERSION,
    generation_parameters: dict[str, object] | None = None,
) -> str | None:
    return canonical_llm_input_hash(
        profile_parsed=profile_parsed,
        job_description=job_description,
        deterministic_report=deterministic_report,
        model=model,
        prompt_version=prompt_version,
        prompt_text=prompt_text,
        ontology_version=ontology_version,
        scorer_version=scorer_version,
        generation_parameters=generation_parameters
        if generation_parameters is not None
        else {"temperature": _LLM_TEMPERATURE, "json_mode": _LLM_JSON_MODE},
    )


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
        embedding_coordinator: JobEmbeddingCoordinator | None = None,
        mention_repository: JobSkillMentionRepository | None = None,
    ) -> None:
        self._repo = repository
        self._readiness_repo: ReadinessPreferenceRepository | None
        if readiness_repository is not None:
            self._readiness_repo = readiness_repository
        else:
            session = getattr(repository, "_session", None)
            self._readiness_repo = (
                ReadinessPreferenceRepository(session) if session is not None else None
            )
        self._embedding_coordinator = embedding_coordinator
        self._mention_repo: JobSkillMentionRepository | None
        if mention_repository is not None:
            self._mention_repo = mention_repository
        else:
            session = getattr(repository, "_session", None)
            self._mention_repo = JobSkillMentionRepository(session) if session is not None else None

    async def _classified_job_mentions(
        self, *, job_id: int, job_description: str
    ) -> list[JobSkillMention | SkillMention]:
        """Load persisted mentions, falling back only when none exist."""
        mention_repository = cast(
            JobSkillMentionRepository | None, getattr(self, "_mention_repo", None)
        )
        if mention_repository is not None:
            mentions = await mention_repository.list_for_job(job_id)
            if mentions:
                return cast(list[JobSkillMention | SkillMention], mentions)
        extracted = default_skill_extractor().extract(job_description, source="free_text")
        return list(classify_skill_mentions(job_description, extracted).deduplicated().mentions)

    @staticmethod
    def _job_seniority(title: str | None) -> str | None:
        tokens = {token.casefold() for token in (title or "").replace("-", " ").split()}
        known = {
            "intern",
            "trainee",
            "junior",
            "entry",
            "associate",
            "mid",
            "intermediate",
            "senior",
            "lead",
            "principal",
            "staff",
            "director",
            "vp",
            "executive",
        }
        values = tokens & known
        return next(iter(values)) if len(values) == 1 else None

    async def _score_match_inputs(
        self,
        *,
        profile: object,
        job: object,
        vector_similarity: float,
        preferences: object | None,
        job_id: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        profile_parsed = dict(getattr(profile, "parsed", {}))
        mentions = await self._classified_job_mentions(
            job_id=job_id, job_description=getattr(job, "description", None) or ""
        )
        required = [
            item
            for item in mentions
            if item.classification == "required" and getattr(item, "skill_id", None)
        ]
        preferred = [
            item
            for item in mentions
            if item.classification == "preferred" and getattr(item, "skill_id", None)
        ]
        profile_ids = profile_skill_ids(profile_parsed)
        profile_hash = getattr(profile, "profile_matching_hash", None) or profile_matching_hash(
            profile_parsed
        )
        blockers, warnings = preference_findings(job, preferences)
        years = [
            item.years_required
            for item in parse_experience_requirements(getattr(job, "description", None) or "")
            if item.scope == "total_experience"
        ]
        profile_years = profile_parsed.get("years_experience")
        score_input = DeterministicScoreInput(
            required_skills=tuple(
                CanonicalSkill(item.skill_id, item.canonical_label) for item in required
            ),
            matched_required_skills=tuple(
                CanonicalSkill(item.skill_id, item.canonical_label)
                for item in required
                if item.skill_id in profile_ids
            ),
            preferred_skills=tuple(
                CanonicalSkill(item.skill_id, item.canonical_label) for item in preferred
            ),
            matched_preferred_skills=tuple(
                CanonicalSkill(item.skill_id, item.canonical_label)
                for item in preferred
                if item.skill_id in profile_ids
            ),
            semantic_similarity=vector_similarity,
            job_title=getattr(job, "title", None),
            profile_roles=tuple(
                str(value) for value in profile_parsed.get("preferred_roles", []) if value
            ),
            profile_headline=(
                profile_parsed.get("headline")
                if isinstance(profile_parsed.get("headline"), str)
                else None
            ),
            job_seniority=self._job_seniority(getattr(job, "title", None)),
            profile_seniority=(
                profile_parsed.get("seniority")
                if isinstance(profile_parsed.get("seniority"), str)
                else None
            ),
            job_years_required=max(years) if years else None,
            profile_years_experience=(
                float(profile_years) if isinstance(profile_years, int | float) else None
            ),
            readiness=ReadinessFindings(blockers=blockers, warnings=warnings),
        )
        result = score_match(score_input)
        profile_id = cast(Any, profile).id
        provenance = {
            "source_profile_id": int(profile_id),
            "source_profile_matching_hash": profile_hash,
            "job_description_hash": job_description_hash(getattr(job, "description", None)),
            "ontology_version": CATALOG_VERSION,
            "scorer_version": SCORER_VERSION,
            "readiness_preferences_hash": readiness_preference_hash(preferences),
        }
        return {
            "fit_score": result.fit_score,
            "ranking_score": result.ranking_score,
            "score_components": result.score_components,
        }, provenance

    async def rank_jobs(self, user_id: int, limit: int) -> RankResponse:
        profile = await self._repo.get_active_profile(user_id)
        if profile is None or profile.embedding is None:
            raise NoActiveProfileError
        embedding = list(profile.embedding)
        ranked = await self._hybrid_candidates(
            user_id=user_id,
            embedding=embedding,
            profile_parsed=dict(profile.parsed),
            fallback_limit=limit,
        )
        get_jobs = getattr(self._repo, "get_visible_jobs_by_ids", None)
        jobs = (
            await get_jobs(user_id=user_id, job_ids=[job_id for job_id, _, _, _ in ranked])
            if callable(get_jobs)
            else {}
        )
        preferences = (
            await self._readiness_repo.get_for_user(user_id)
            if self._readiness_repo is not None
            else None
        )
        profile_parsed = dict(profile.parsed)
        for job_id, similarity, job_description, retrieval_metadata in ranked:
            job_record = jobs.get(job_id)
            resolved_description = job_description or getattr(job_record, "description", "")
            (
                matched_skills,
                missing_keywords,
                deterministic_match,
            ) = await self._deterministic_match_fields(
                job_id=job_id,
                job_description=resolved_description or "",
                profile_parsed=profile_parsed,
            )
            score_values, provenance = await self._score_match_inputs(
                profile=profile,
                job=job_record
                or SimpleNamespace(id=job_id, title=None, description=resolved_description),
                vector_similarity=similarity,
                preferences=preferences,
                job_id=job_id,
            )
            await self._repo.upsert_match(
                user_id=user_id,
                job_id=job_id,
                vector_score=round(similarity * 100, 2),
                confidence=confidence_band(similarity),
                origin="automatic",
                matched_skills=matched_skills,
                missing_keywords=missing_keywords,
                deterministic_match=deterministic_match,
                **score_values,
                **provenance,
                retrieval_metadata=retrieval_metadata,
            )
        # Manual analyses are retained outside top-K, but profile changes and
        # nightly refreshes must still update their embedding-based score.
        await self.rank_job_ids(
            user_id=user_id,
            job_ids=await self._repo.list_manual_match_job_ids(user_id=user_id),
            limit=limit,
        )
        await self._repo.prune_automatic_matches(user_id=user_id, limit=limit)
        logger.info("matches.ranked", user_id=user_id, count=len(ranked))
        return RankResponse(ranked=len(ranked), profile_id=profile.id)

    @staticmethod
    def _profile_retrieval_inputs(profile_parsed: dict[str, object]) -> tuple[str, set[str]]:
        """Build bounded retrieval inputs from approved profile fields only."""
        text_parts: list[str] = []
        for field_name in ("preferred_roles", "headline"):
            value = profile_parsed.get(field_name)
            if isinstance(value, str):
                text_parts.append(value)
            elif isinstance(value, list):
                text_parts.extend(str(item) for item in value if item)

        structured_values: list[str] = []
        for field_name in ("skills", "certifications"):
            value = profile_parsed.get(field_name)
            if isinstance(value, list):
                structured_values.extend(str(item) for item in value if item)
        structured_report = (
            default_skill_extractor()
            .extract("\n".join(structured_values), source="structured")
            .deduplicated()
        )
        text_parts.extend(mention.canonical_label for mention in structured_report.mentions)
        skill_ids = {mention.skill_id for mention in structured_report.mentions}
        return " ".join(part.strip() for part in text_parts if part.strip()), skill_ids

    async def _hybrid_candidates(
        self,
        *,
        user_id: int,
        embedding: list[float],
        profile_parsed: dict[str, object],
        fallback_limit: int = 200,
    ) -> list[tuple[int, float, str | None, dict[str, object]]]:
        """Retrieve and fuse candidates while keeping vector ordering untouched."""
        candidate_limit = 400
        has_hybrid_retrievers = any(
            callable(getattr(self._repo, method, None))
            for method in ("search_jobs_by_text", "search_jobs_by_skills")
        )
        vector_limit = candidate_limit if has_hybrid_retrievers else fallback_limit
        sources: dict[str, list[RetrievalCandidate]] = {}
        vector_rows: list[tuple[int, float, str | None]] = []
        try:
            vector_rows = await self._repo.rank_jobs_by_vector(
                embedding, vector_limit, user_id=user_id
            )
            sources["vector"] = [
                RetrievalCandidate(job_id=job_id, score=similarity)
                for job_id, similarity, _ in vector_rows
            ]
        except Exception:  # noqa: BLE001 - degraded retrieval is intentional
            logger.warning(
                "matches.hybrid_retrieval_branch_failed",
                branch="vector",
                user_id=user_id,
                exc_info=True,
            )

        text_query, skill_ids = self._profile_retrieval_inputs(profile_parsed)
        for branch, enabled, method, argument in (
            ("text", bool(text_query), "search_jobs_by_text", text_query),
            ("skill", bool(skill_ids), "search_jobs_by_skills", skill_ids),
        ):
            if not enabled:
                continue
            retriever = getattr(self._repo, method, None)
            if not callable(retriever):
                continue
            try:
                sources[branch] = await retriever(argument, candidate_limit, user_id=user_id)
            except Exception:  # noqa: BLE001 - one branch must not abort ranking
                logger.warning(
                    "matches.hybrid_retrieval_branch_failed",
                    branch=branch,
                    user_id=user_id,
                    exc_info=True,
                )

        # Test doubles and legacy repository implementations remain vector-only.
        if not sources:
            return [(job_id, score, description, {}) for job_id, score, description in vector_rows]

        fused = fuse_ranked_candidates(sources, k=60, limit=candidate_limit)
        vector_scores = {job_id: score for job_id, score, _ in vector_rows}
        missing_vector_ids = [item.job_id for item in fused if item.job_id not in vector_scores]
        vector_batch = getattr(self._repo, "vector_similarities_for_jobs", None)
        if missing_vector_ids and callable(vector_batch):
            try:
                vector_scores.update(
                    await vector_batch(
                        user_id=user_id,
                        embedding=embedding,
                        job_ids=missing_vector_ids,
                    )
                )
            except Exception:  # noqa: BLE001 - semantic score can use neutral fallback
                logger.warning(
                    "matches.hybrid_vector_enrichment_failed",
                    user_id=user_id,
                    candidate_count=len(missing_vector_ids),
                    exc_info=True,
                )

        descriptions = {job_id: description for job_id, _, description in vector_rows}
        result: list[tuple[int, float, str | None, dict[str, object]]] = []
        for item in fused:
            result.append(
                (
                    item.job_id,
                    vector_scores.get(item.job_id, 0.5),
                    descriptions.get(item.job_id),
                    item.metadata(),
                )
            )
        return result

    async def analyze_job(self, *, user_id: int, job_id: int) -> MatchSummary:
        """Score and save one visible job without batch ranking or an LLM call."""
        profile = await self._repo.get_active_profile(user_id)
        if profile is None or profile.embedding is None:
            raise NoActiveProfileError
        job = await self._repo.get_visible_job(user_id=user_id, job_id=job_id)
        if job is None:
            raise MatchNotFoundError(job_id)
        if job.description_embedding is None:
            if self._embedding_coordinator is None:
                raise MatchNotReadyError(job_id)
            embedding_result = await self._embedding_coordinator.ensure_embedding(job_id)
            if embedding_result.status == "pending":
                raise MatchPreparingError(job_id)
            if embedding_result.status == "unavailable":
                raise MatchUnavailableError(job_id)
        similarity = await self._repo.score_visible_job(
            user_id=user_id, job_id=job_id, embedding=list(profile.embedding)
        )
        if similarity is None:
            raise MatchNotReadyError(job_id)
        (
            matched_skills,
            missing_keywords,
            deterministic_match,
        ) = await self._deterministic_match_fields(
            job_id=job_id,
            job_description=job.description or "",
            profile_parsed=dict(getattr(profile, "parsed", {})),
        )
        preferences = (
            await self._readiness_repo.get_for_user(user_id)
            if self._readiness_repo is not None
            else None
        )
        score_values, provenance = await self._score_match_inputs(
            profile=profile,
            job=job,
            vector_similarity=similarity,
            preferences=preferences,
            job_id=job_id,
        )
        await self._repo.upsert_match(
            user_id=user_id,
            job_id=job_id,
            vector_score=round(similarity * 100, 2),
            confidence=confidence_band(similarity),
            origin="manual",
            matched_skills=matched_skills,
            missing_keywords=missing_keywords,
            deterministic_match=deterministic_match,
            **score_values,
            **provenance,
        )
        summary = await self.get_match_summary(user_id=user_id, job_id=job_id)
        if summary is None:
            raise MatchNotFoundError(job_id)
        return summary

    async def rank_job_ids(self, *, user_id: int, job_ids: list[int], limit: int) -> int:
        """Incrementally score embedded jobs for one user without a full scan."""
        profile = await self._repo.get_active_profile(user_id)
        if profile is None or profile.embedding is None:
            raise NoActiveProfileError
        ranked = 0
        profile_parsed = dict(profile.parsed)
        preferences = (
            await self._readiness_repo.get_for_user(user_id)
            if self._readiness_repo is not None
            else None
        )
        for job_id in job_ids:
            similarity = await self._repo.score_visible_job(
                user_id=user_id, job_id=job_id, embedding=list(profile.embedding)
            )
            if similarity is None:
                continue
            get_visible_job = getattr(self._repo, "get_visible_job", None)
            job = (
                await get_visible_job(user_id=user_id, job_id=job_id)
                if callable(get_visible_job)
                else None
            )
            if callable(get_visible_job) and job is None:
                continue
            (
                matched_skills,
                missing_keywords,
                deterministic_match,
            ) = await self._deterministic_match_fields(
                job_id=job_id,
                job_description=getattr(job, "description", None) or "",
                profile_parsed=profile_parsed,
            )
            score_values, provenance = await self._score_match_inputs(
                profile=profile,
                job=job or SimpleNamespace(id=job_id, title=None, description=""),
                vector_similarity=similarity,
                preferences=preferences,
                job_id=job_id,
            )
            await self._repo.upsert_match(
                user_id=user_id,
                job_id=job_id,
                vector_score=round(similarity * 100, 2),
                confidence=confidence_band(similarity),
                origin="automatic",
                matched_skills=matched_skills,
                missing_keywords=missing_keywords,
                deterministic_match=deterministic_match,
                **score_values,
                **provenance,
            )
            ranked += 1
        await self._repo.prune_automatic_matches(user_id=user_id, limit=limit)
        return ranked

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
        active_profile = (
            await self._repo.get_active_profile(user_id)
            if self._readiness_repo is not None
            else None
        )
        current_profile_hash = (
            profile_matching_hash(dict(getattr(active_profile, "parsed", {})))
            if active_profile is not None
            else None
        )
        for job, match in rows:
            readiness_context = self._readiness_context(
                job=job,
                active_profile=active_profile,
                preferences=readiness_preferences,
                preferences_loaded=self._readiness_repo is not None,
                current_profile_hash=current_profile_hash,
            )
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
                    **self._match_contract_fields(match, readiness_context),
                    confidence=match.confidence,
                    llm_score=match.llm_score,
                    llm_confidence=match.llm_confidence,
                    matched_skills=match.matched_skills or None,
                    readiness=self._build_readiness(match, readiness_context=readiness_context),
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

        readiness_context = await self._load_readiness_context(user_id=user_id, job=job)
        evidence_result = await self._load_classified_evidence(user_id, job_id)
        return MatchSummary(
            job_id=job_id,
            vector_score=match.vector_score,
            **self._match_contract_fields(match, readiness_context, evidence_result.source),
            confidence=match.confidence,
            llm_score=match.llm_score,
            llm_confidence=match.llm_confidence,
            explanation=match.llm_explanation,
            matched_skills=match.matched_skills or [],
            missing_keywords=match.missing_keywords or [],
            readiness=self._build_readiness(match, readiness_context=readiness_context),
            classified_skills=evidence_result.skills,
        )

    async def explain_match(self, *, user_id: int, job_id: int) -> MatchExplanation:
        match = await self._repo.get_match(user_id=user_id, job_id=job_id)
        if match is None:
            raise MatchNotFoundError(job_id)

        job = await self._repo.get_visible_job(user_id=user_id, job_id=job_id)
        profile = await self._repo.get_active_profile(user_id)
        if job is None or profile is None:
            raise MatchNotFoundError(job_id)
        readiness_context = await self._load_readiness_context(
            user_id=user_id, job=job, active_profile=profile
        )

        profile_parsed = dict(profile.parsed)
        evidence_result = await self._load_classified_evidence(user_id, job_id)
        matched_skills, missing_keywords, report_dict = await self._deterministic_match_fields(
            job_id=job_id,
            job_description=job.description or "",
            profile_parsed=profile_parsed,
            classified_skills=evidence_result.skills,
        )
        if evidence_result.skills is not None:
            report_dict["classified_skills"] = evidence_result.skills.model_dump()
        prompt_payload = canonical_llm_prompt_payload(
            profile_parsed=profile_parsed,
            job_description=job.description or "",
            deterministic_report=report_dict,
        )
        settings = get_settings()
        request_spec = resolve_llm_request_spec(
            model=normalize_model_identity(settings.llm_model),
            temperature=_LLM_TEMPERATURE,
            json_mode=_LLM_JSON_MODE,
        )
        expected_llm_hash = (
            llm_input_hash_for_payload(
                prompt_payload=prompt_payload,
                model=normalize_model_identity(settings.llm_model),
                prompt_version=_LLM_PROMPT_VERSION,
                prompt_text=MATCH_EXPLANATION_PROMPT,
                ontology_version=CATALOG_VERSION,
                scorer_version=SCORER_VERSION,
                generation_parameters=request_spec.cache_identity(),
            )
            if request_spec is not None
            else None
        )
        # Cache hit: return only a successful explanation with matching provenance.
        if self._has_reusable_llm_cache(match, expected_llm_hash):
            return await self._to_explanation(
                match,
                user_id,
                readiness_context=readiness_context,
                evidence_source=evidence_result.source,
                classified_skills=evidence_result.skills,
            )
        if (
            getattr(match, "llm_analyzed_at", None) is not None
            or match.llm_explanation is not None
            or match.llm_input_hash is not None
        ):
            await self._repo.clear_llm_analysis(match)

        # Layer 3: LLM explanation grounded by layer 2.
        (
            llm_score,
            llm_confidence,
            missing_keywords,
            explanation,
            conf_band,
            llm_error,
            cacheable_success,
        ) = await self._explain_with_llm(
            prompt_payload=prompt_payload,
            deterministic_missing=missing_keywords,
            request_spec=request_spec,
        )

        await self._repo.save_explanation(
            match=match,
            llm_score=llm_score,
            llm_confidence=llm_confidence,
            explanation=explanation,
            llm_analyzed_at=datetime.now(UTC) if cacheable_success else None,
            llm_error=llm_error,
            matched_skills=matched_skills,
            missing_keywords=missing_keywords,
            deterministic_match=report_dict,
            confidence=conf_band,
            llm_input_hash=expected_llm_hash if cacheable_success else None,
        )
        return await self._to_explanation(
            match,
            user_id,
            readiness_context=readiness_context,
            evidence_source=evidence_result.source,
            classified_skills=evidence_result.skills,
        )

    async def analyze_skills(self, *, user_id: int, job_id: int) -> MatchSkillAnalysis:
        """Return cached or freshly computed deterministic skill evidence."""
        match = await self._repo.get_match(user_id=user_id, job_id=job_id)
        if match is None:
            raise MatchNotFoundError(job_id)

        job = await self._repo.get_visible_job(user_id=user_id, job_id=job_id)
        profile = await self._repo.get_active_profile(user_id)
        if job is None or profile is None:
            raise MatchNotFoundError(job_id)
        readiness_context = await self._load_readiness_context(
            user_id=user_id, job=job, active_profile=profile
        )
        evidence_result = await self._load_classified_evidence(user_id, job_id)

        if (
            match.deterministic_match is not None
            and match.matched_skills is not None
            and match.missing_keywords is not None
        ):
            return await self._to_skill_analysis(
                match,
                user_id,
                readiness_context=readiness_context,
                evidence_source=evidence_result.source,
                classified_skills=evidence_result.skills,
            )

        (
            matched_skills,
            missing_keywords,
            deterministic_match,
        ) = await self._deterministic_match_fields(
            job_id=job_id,
            job_description=job.description or "",
            profile_parsed=dict(profile.parsed),
            classified_skills=evidence_result.skills,
        )
        await self._repo.save_deterministic_match(
            match=match,
            matched_skills=matched_skills,
            missing_keywords=missing_keywords,
            deterministic_match=deterministic_match,
        )
        return await self._to_skill_analysis(
            match,
            user_id,
            readiness_context=readiness_context,
            evidence_source=evidence_result.source,
            classified_skills=evidence_result.skills,
        )

    async def get_match_evidence(self, *, user_id: int, job_id: int) -> MatchEvidence:
        """Build fresh, user-scoped deterministic evidence without persistence."""
        evidence, _ = await self._get_match_evidence_with_source(user_id=user_id, job_id=job_id)
        return evidence

    async def _get_match_evidence_with_source(
        self, *, user_id: int, job_id: int
    ) -> tuple[MatchEvidence, Literal["persisted", "fallback"]]:
        """Build evidence and retain its internal source for response mapping."""
        job = await self._repo.get_visible_job(user_id=user_id, job_id=job_id)
        profile = await self._repo.get_active_profile(user_id)
        if job is None or profile is None:
            raise MatchNotFoundError(job_id)

        profile_parsed = dict(profile.parsed)
        groups: dict[str, SkillEvidenceGroup] = {}
        mention_repo = getattr(self, "_mention_repo", None)
        mentions = await mention_repo.list_for_job(job_id) if mention_repo is not None else []
        evidence_source: Literal["persisted", "fallback"] = "persisted" if mentions else "fallback"
        if mentions:
            for classification, persisted_observed in self._group_persisted_mentions(
                mentions
            ).items():
                report = compute_skill_match(
                    [item.canonical_label for item in persisted_observed], profile_parsed
                )
                matched = set(report.matched_skills)
                groups[classification] = SkillEvidenceGroup(
                    matched=[
                        SkillEvidence(
                            skill=item.canonical_label,
                            job_excerpt=item.job_excerpt,
                            profile_excerpt=report.evidence[item.canonical_label],
                        )
                        for item in persisted_observed
                        if item.canonical_label in matched
                    ],
                    missing=[
                        SkillEvidence(
                            skill=item.canonical_label,
                            job_excerpt=item.job_excerpt,
                        )
                        for item in persisted_observed
                        if item.canonical_label not in matched
                    ],
                )
        else:
            for classification, fallback_observed in classify_job_skills(
                job.description or ""
            ).items():
                report = compute_skill_match(
                    [item.skill for item in fallback_observed], profile_parsed
                )
                matched = set(report.matched_skills)
                groups[classification] = SkillEvidenceGroup(
                    matched=[
                        SkillEvidence(
                            skill=item.skill,
                            job_excerpt=item.job_excerpt,
                            profile_excerpt=report.evidence[item.skill],
                        )
                        for item in fallback_observed
                        if item.skill in matched
                    ],
                    missing=[
                        SkillEvidence(skill=item.skill, job_excerpt=item.job_excerpt)
                        for item in fallback_observed
                        if item.skill not in matched
                    ],
                )

        profile_years_raw = profile_parsed.get("years_experience")
        profile_years = (
            float(profile_years_raw) if isinstance(profile_years_raw, int | float) else None
        )
        experience = []
        for requirement in parse_experience_requirements(job.description or ""):
            if requirement.scope == "skill_specific" or profile_years is None:
                assessment: Literal["meets", "may_not_meet", "needs_review"] = "needs_review"
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

        result = MatchEvidence(
            job_id=job_id,
            skills=ClassifiedSkillEvidence(
                required=groups.get("required", SkillEvidenceGroup()),
                preferred=groups.get("preferred", SkillEvidenceGroup()),
                unclassified=groups.get("unclassified", SkillEvidenceGroup()),
            ),
            experience_requirements=experience,
        )
        return result, evidence_source

    async def _deterministic_match_fields(
        self,
        *,
        job_id: int,
        job_description: str,
        profile_parsed: dict[str, object],
        classified_skills: ClassifiedMatchSkills | None = None,
    ) -> tuple[list[str], list[str], dict[str, object]]:
        """Return compatible match fields with required-only persisted gaps."""
        if classified_skills is None:
            mentions = await self._classified_job_mentions(
                job_id=job_id, job_description=job_description
            )
            all_skills = [mention.canonical_label for mention in mentions]
            required_skills = [
                mention.canonical_label
                for mention in mentions
                if mention.classification == "required"
            ]
        else:
            all_skills = list(
                dict.fromkeys(
                    [
                        *classified_skills.required_matched_skills,
                        *classified_skills.required_missing_skills,
                        *classified_skills.preferred_matched_skills,
                        *classified_skills.preferred_missing_skills,
                        *classified_skills.unclassified_skills,
                    ]
                )
            )
            required_skills = list(
                dict.fromkeys(
                    [
                        *classified_skills.required_matched_skills,
                        *classified_skills.required_missing_skills,
                    ]
                )
            )

        report = compute_skill_match(all_skills, profile_parsed)
        required_report = compute_skill_match(required_skills, profile_parsed)
        deterministic_match = report_to_dict(report)
        deterministic_match["missing_skills"] = list(required_report.missing_skills)
        return (
            report.matched_skills,
            required_report.missing_skills,
            deterministic_match,
        )

    @staticmethod
    def _group_persisted_mentions(
        mentions: list[JobSkillMention],
    ) -> dict[str, list[JobSkillMention]]:
        groups: dict[str, list[JobSkillMention]] = {
            "required": [],
            "preferred": [],
            "unclassified": [],
        }
        for mention in mentions:
            if mention.classification in groups:
                groups[mention.classification].append(mention)
        return groups

    async def _explain_with_llm(
        self,
        *,
        prompt_payload: dict[str, object],
        deterministic_missing: list[str],
        request_spec: LLMRequestSpec | None,
    ) -> tuple[
        int | None,
        float | None,
        list[str],
        str | None,
        str | None,
        str | None,
        bool,
    ]:
        messages = [
            {"role": "system", "content": MATCH_EXPLANATION_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    prompt_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        ]
        try:
            if request_spec is None:
                raise LLMUnavailableError("No cacheable LLM request policy is configured")
            response, metadata = await llm_chat_completion_for_spec(
                messages=messages, request_spec=request_spec
            )
        except LLMUnavailableError:
            # Degrade gracefully: deterministic evidence still gives a result.
            logger.warning("matches.explain.llm_unavailable")
            return None, None, deterministic_missing, None, None, "LLM service unavailable", False

        content = response.choices[0].message.content or "{}"
        try:
            data = json.loads(strip_code_fences(content))
        except json.JSONDecodeError:
            logger.warning("matches.explain.bad_json")
            return (
                None,
                None,
                deterministic_missing,
                None,
                None,
                "LLM returned invalid JSON",
                False,
            )

        result = self._parse_llm_payload(data, deterministic_missing)
        cacheable_success = (
            result[3] is not None
            and result[5] is None
            and not metadata.fallback_occurred
            and self._models_are_compatible(
                requested_model=metadata.requested_model,
                actual_model=metadata.actual_model,
            )
        )
        return (*result, cacheable_success)

    @staticmethod
    def _has_reusable_llm_cache(match: object, expected_hash: str | None) -> bool:
        return (
            expected_hash is not None
            and getattr(match, "llm_input_hash", None) is not None
            and getattr(match, "llm_input_hash", None) == expected_hash
            and getattr(match, "llm_analyzed_at", None) is not None
            and getattr(match, "llm_error", None) is None
            and getattr(match, "llm_explanation", None) is not None
        )

    @staticmethod
    def _models_are_compatible(*, requested_model: object, actual_model: object) -> bool:
        """Only an explicitly reported exact model match can be cached."""
        requested = normalize_model_identity(requested_model)
        actual = normalize_model_identity(actual_model)
        return requested is not None and actual is not None and actual == requested

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

        # The LLM may explain the deterministic gaps, but may not redefine them.
        missing = deterministic_missing

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

    async def _to_explanation(
        self,
        match: object,
        user_id: int,
        *,
        readiness_context: ReadinessContext | None = None,
        evidence_source: _EvidenceSource = "unavailable",
        classified_skills: ClassifiedMatchSkills | None = None,
    ) -> MatchExplanation:
        if readiness_context is None:
            readiness_context = self._readiness_context(
                job=None,
                active_profile=None,
                preferences=None,
                preferences_loaded=False,
                current_profile_hash=None,
            )
        readiness = self._build_readiness(match, readiness_context=readiness_context)
        return MatchExplanation(
            job_id=match.job_id,  # type: ignore[attr-defined]
            vector_score=match.vector_score,  # type: ignore[attr-defined]
            **self._match_contract_fields(match, readiness_context, evidence_source),
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
            classified_skills=(
                classified_skills
                if classified_skills is not None
                else await self._load_classified_skills(
                    user_id,
                    match.job_id,  # type: ignore[attr-defined]
                )
            ),
        )

    async def _to_skill_analysis(
        self,
        match: object,
        user_id: int,
        *,
        readiness_context: ReadinessContext | None = None,
        evidence_source: _EvidenceSource = "unavailable",
        classified_skills: ClassifiedMatchSkills | None = None,
    ) -> MatchSkillAnalysis:
        if readiness_context is None:
            readiness_context = self._readiness_context(
                job=None,
                active_profile=None,
                preferences=None,
                preferences_loaded=False,
                current_profile_hash=None,
            )
        return MatchSkillAnalysis(
            job_id=match.job_id,  # type: ignore[attr-defined]
            vector_score=match.vector_score,  # type: ignore[attr-defined]
            **self._match_contract_fields(match, readiness_context, evidence_source),
            matched_skills=match.matched_skills or [],  # type: ignore[attr-defined]
            missing_keywords=match.missing_keywords or [],  # type: ignore[attr-defined]
            readiness=self._build_readiness(match, readiness_context=readiness_context),
            classified_skills=(
                classified_skills
                if classified_skills is not None
                else await self._load_classified_skills(
                    user_id,
                    match.job_id,  # type: ignore[attr-defined]
                )
            ),
        )

    async def _load_classified_skills(
        self, user_id: int, job_id: int
    ) -> ClassifiedMatchSkills | None:
        """Return additive classified output when the persisted-evidence path is wired."""
        try:
            evidence = await self.get_match_evidence(user_id=user_id, job_id=job_id)
        except MatchNotFoundError:
            return None
        return self._classified_skills_from_evidence(evidence)

    async def _load_classified_evidence(
        self, user_id: int, job_id: int
    ) -> _ClassifiedEvidenceResult:
        """Load classified skills and retain the evidence source already observed."""
        if getattr(self, "_mention_repo", None) is None:
            return _ClassifiedEvidenceResult(skills=None, source="unavailable")
        if not callable(getattr(self._mention_repo, "list_for_job", None)):
            try:
                evidence = await self.get_match_evidence(user_id=user_id, job_id=job_id)
            except MatchNotFoundError:
                return _ClassifiedEvidenceResult(skills=None, source="unavailable")
            return _ClassifiedEvidenceResult(
                skills=self._classified_skills_from_evidence(evidence),
                source="unavailable",
            )
        try:
            evidence, source = await self._get_match_evidence_with_source(
                user_id=user_id, job_id=job_id
            )
        except MatchNotFoundError:
            # Classified output is additive: do not make legacy cached output
            # unavailable when a profile has been removed since matching.
            return _ClassifiedEvidenceResult(skills=None, source="unavailable")
        return _ClassifiedEvidenceResult(
            skills=self._classified_skills_from_evidence(evidence),
            source=source,
        )

    @staticmethod
    def _match_contract_fields(
        match: object,
        readiness_context: ReadinessContext,
        evidence_source: _EvidenceSource = "unavailable",
    ) -> dict[str, object]:
        current = deterministic_match_is_current(match, readiness_context)
        evidence_confidence: EvidenceConfidence | None = (
            None
            if evidence_source == "unavailable"
            else ("high" if evidence_source == "persisted" else "medium")
        )
        if not current:
            return {
                "fit_score": None,
                "score_components": None,
                "evidence_confidence": ("low" if evidence_confidence is not None else None),
            }
        fit_score = getattr(match, "fit_score", None)
        components = sanitize_score_components(getattr(match, "score_components", None))
        if (
            not isinstance(fit_score, int | float)
            or isinstance(fit_score, bool)
            or not math.isfinite(float(fit_score))
            or components is None
        ):
            return {
                "fit_score": None,
                "score_components": None,
                "evidence_confidence": ("low" if evidence_confidence is not None else None),
            }
        return {
            "fit_score": float(fit_score),
            "score_components": components,
            "evidence_confidence": evidence_confidence,
        }

    @staticmethod
    def _classified_skills_from_evidence(evidence: MatchEvidence) -> ClassifiedMatchSkills:
        def _labels(group: SkillEvidenceGroup) -> list[str]:
            return [item.skill for item in [*group.matched, *group.missing]]

        return ClassifiedMatchSkills(
            required_matched_skills=[item.skill for item in evidence.skills.required.matched],
            required_missing_skills=[item.skill for item in evidence.skills.required.missing],
            preferred_matched_skills=[item.skill for item in evidence.skills.preferred.matched],
            preferred_missing_skills=[item.skill for item in evidence.skills.preferred.missing],
            unclassified_skills=_labels(evidence.skills.unclassified),
        )

    @staticmethod
    def _build_readiness(
        match: object,
        job: object | None = None,
        preferences: object | None = None,
        *,
        readiness_context: ReadinessContext | None = None,
    ) -> MatchReadiness:
        if readiness_context is None:
            readiness_context = MatchingService._readiness_context(
                job=job,
                active_profile=None,
                preferences=preferences,
                preferences_loaded=False,
                current_profile_hash=None,
            )
        score = select_readiness_score(match, readiness_context)
        return build_match_readiness(
            score=score,
            confidence=None,
            matched_skills=match.matched_skills,  # type: ignore[attr-defined]
            missing_keywords=match.missing_keywords,  # type: ignore[attr-defined]
            job=readiness_context.job,
            preferences=readiness_context.preferences,
        )

    @staticmethod
    def _readiness_context(
        *,
        job: object | None,
        active_profile: object | None,
        preferences: object | None,
        preferences_loaded: bool,
        current_profile_hash: str | None,
    ) -> ReadinessContext:
        return ReadinessContext(
            active_profile=active_profile,
            current_profile_matching_hash=current_profile_hash,
            preferences=preferences,
            current_readiness_preferences_hash=(
                readiness_preference_hash(preferences) if preferences_loaded else None
            ),
            preferences_loaded=preferences_loaded,
            job=job,
            current_job_description_hash=(
                job_description_hash(getattr(job, "description", None)) if job is not None else None
            ),
        )

    async def _load_readiness_context(
        self,
        *,
        user_id: int,
        job: object,
        active_profile: object | None = None,
    ) -> ReadinessContext:
        preferences_loaded = self._readiness_repo is not None
        if active_profile is None and self._readiness_repo is not None:
            active_profile = await self._repo.get_active_profile(user_id)
        preferences = (
            await self._readiness_repo.get_for_user(user_id)
            if self._readiness_repo is not None
            else None
        )
        current_profile_hash = (
            profile_matching_hash(dict(getattr(active_profile, "parsed", {})))
            if active_profile is not None
            else None
        )
        return self._readiness_context(
            job=job,
            active_profile=active_profile,
            preferences=preferences,
            preferences_loaded=preferences_loaded,
            current_profile_hash=current_profile_hash,
        )
