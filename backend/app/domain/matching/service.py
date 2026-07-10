"""Matching service: three-layer JD-to-resume scoring (Phase 3).

Layer 1 (vector pre-rank): pgvector cosine similarity ranks jobs cheaply.
Layer 2 (deterministic skill recall): free, reproducible evidence of which
    JD-required skills the resume contains (alias-aware whole-word matching).
Layer 3 (LLM explanation/score): selective, grounded by layer 2, cached on the
    ``UserJobMatch`` row so we never pay twice.
"""

from __future__ import annotations

import json

from app.core.logging import get_logger
from app.domain.matching.exceptions import (
    MatchNotFoundError,
    NoActiveProfileError,
)
from app.domain.matching.prompts import MATCH_EXPLANATION_PROMPT
from app.domain.matching.repository import MatchingRepository
from app.domain.matching.schemas import (
    MatchExplanation,
    MatchItem,
    MatchListResponse,
    RankResponse,
    SkillGapItem,
    SkillsGapSummary,
)
from app.domain.matching.skill_matcher import (
    compute_skill_match,
    extract_known_skills,
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
    def __init__(self, repository: MatchingRepository) -> None:
        self._repo = repository

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
        offset: int,
        q: str | None = None,
        source: str | None = None,
        is_remote: bool | None = None,
        job_type: str | None = None,
        location_contains: str | None = None,
        company_contains: str | None = None,
        shift_type: str | None = None,
        source_type: str | None = None,
    ) -> MatchListResponse:
        rows, total = await self._repo.list_matches(
            user_id=user_id,
            limit=limit,
            offset=offset,
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
                    posted_at=job.posted_at,
                )
            )
        return MatchListResponse(
            items=items, total=total, limit=limit, offset=offset
        )

    async def skills_gap_summary(
        self, *, user_id: int, top_n: int = 5
    ) -> SkillsGapSummary:
        """Aggregate the most common missing skills across the user's matches."""
        pairs = await self._repo.aggregate_missing_keywords(
            user_id=user_id, top_n=top_n
        )
        total = await self._repo.count_matches_with_gaps(user_id)
        return SkillsGapSummary(
            items=[
                SkillGapItem(keyword=keyword, frequency=frequency)
                for keyword, frequency in pairs
            ],
            total_matches_considered=total,
        )

    async def explain_match(self, *, user_id: int, job_id: int) -> MatchExplanation:
        match = await self._repo.get_match(user_id=user_id, job_id=job_id)
        if match is None:
            raise MatchNotFoundError(job_id)

        # Cache hit: return the stored explanation, no LLM cost.
        if match.llm_explanation is not None:
            return self._to_explanation(match)

        job = await self._repo.get_job(job_id)
        profile = await self._repo.get_active_profile(user_id)
        if job is None or profile is None:
            raise MatchNotFoundError(job_id)

        # Layer 2: deterministic skill recall (free, reproducible).
        jd_skills = extract_known_skills(job.description or "")
        report = compute_skill_match(jd_skills, dict(profile.parsed))
        report_dict = report_to_dict(report)

        # Layer 3: LLM explanation grounded by layer 2.
        llm_score, llm_confidence, missing_keywords, explanation, conf_band = (
            await self._explain_with_llm(
                profile_parsed=dict(profile.parsed),
                job_description=job.description or "",
                report_dict=report_dict,
                deterministic_missing=report.missing_skills,
            )
        )

        await self._repo.save_explanation(
            match=match,
            llm_score=llm_score,
            llm_confidence=llm_confidence,
            explanation=explanation,
            matched_skills=report.matched_skills,
            missing_keywords=missing_keywords,
            deterministic_match=report_dict,
            confidence=conf_band,
        )
        return self._to_explanation(match)

    async def _explain_with_llm(
        self,
        *,
        profile_parsed: dict[str, object],
        job_description: str,
        report_dict: dict[str, object],
        deterministic_missing: list[str],
    ) -> tuple[int | None, float | None, list[str], str | None, str | None]:
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
            return None, None, deterministic_missing, None, None

        content = response.choices[0].message.content or "{}"
        try:
            data = json.loads(strip_code_fences(content))
        except json.JSONDecodeError:
            logger.warning("matches.explain.bad_json")
            return None, None, deterministic_missing, None, None

        return self._parse_llm_payload(data, deterministic_missing)

    @staticmethod
    def _parse_llm_payload(
        data: object, deterministic_missing: list[str]
    ) -> tuple[int | None, float | None, list[str], str | None, str | None]:
        if not isinstance(data, dict):
            return None, None, deterministic_missing, None, None

        score = data.get("score")
        llm_score = int(score) if isinstance(score, int | float) else None

        confidence = data.get("confidence")
        conf_band = (
            confidence
            if isinstance(confidence, str) and confidence in _VALID_CONFIDENCE
            else None
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

        return llm_score, llm_confidence, missing, explanation_str, conf_band

    @staticmethod
    def _to_explanation(match: object) -> MatchExplanation:
        # ``match`` is a UserJobMatch; typed loosely to avoid a circular import.
        return MatchExplanation(
            job_id=match.job_id,  # type: ignore[attr-defined]
            vector_score=match.vector_score,  # type: ignore[attr-defined]
            confidence=match.confidence,  # type: ignore[attr-defined]
            llm_score=match.llm_score,  # type: ignore[attr-defined]
            llm_confidence=match.llm_confidence,  # type: ignore[attr-defined]
            explanation=match.llm_explanation,  # type: ignore[attr-defined]
            matched_skills=match.matched_skills or [],  # type: ignore[attr-defined]
            missing_keywords=match.missing_keywords or [],  # type: ignore[attr-defined]
        )
