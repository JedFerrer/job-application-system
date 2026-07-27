"""Matching repository: hybrid retrieval + match-row persistence."""

from __future__ import annotations

import base64
import binascii
import json
import math
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import and_, case, delete, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.matching.hybrid_retrieval import RetrievalCandidate
from app.domain.matching.llm_fingerprint import canonicalize_cache_value
from app.models.job import Job
from app.models.job_skill_mention import JobSkillMention
from app.models.resume import CandidateProfile, Resume
from app.models.user_job import UserJob
from app.models.user_job_match import UserJobMatch

RankingMode = Literal["vector", "shadow", "fit"]


def _canonical_json(value: object, *, key: str | None = None) -> str:
    return json.dumps(
        canonicalize_cache_value(value, key=key),
        sort_keys=True,
        separators=(",", ":"),
    )


class MatchingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _matchable_job_predicate(*, user_id: int) -> tuple[Any, ...]:
        """Return the shared eligibility predicate for automatic matching."""
        archived_subquery = (
            select(UserJob.job_id)
            .where(UserJob.user_id == user_id, UserJob.status == "archived")
            .scalar_subquery()
        )
        return (
            Job.status == "active",
            Job.description_embedding.is_not(None),
            or_(Job.visibility == "public", Job.created_by_user_id == user_id),
            Job.id.not_in(archived_subquery),
        )

    async def get_active_profile(self, user_id: int) -> CandidateProfile | None:
        """Return the user's active candidate profile (with embedding)."""
        result = await self._session.execute(
            select(CandidateProfile)
            .join(Resume, Resume.id == CandidateProfile.resume_id)
            .where(
                CandidateProfile.user_id == user_id,
                Resume.is_active.is_(True),
            )
            .order_by(CandidateProfile.updated_at.desc())
        )
        return result.scalars().first()

    async def rank_jobs_by_vector(
        self, embedding: list[float], limit: int, *, user_id: int
    ) -> list[tuple[int, float, str | None]]:
        """Return ``(job_id, similarity, description)`` ordered by similarity.

        Only includes jobs the user is allowed to see: public jobs + their own
        private imports.
        """
        distance = Job.description_embedding.cosine_distance(embedding)
        manual_job_ids = select(UserJobMatch.job_id).where(
            UserJobMatch.user_id == user_id,
            UserJobMatch.origin == "manual",
        )
        stmt = (
            select(Job.id, (1 - distance).label("similarity"), Job.description)
            .where(
                Job.id.not_in(manual_job_ids),
                *self._matchable_job_predicate(user_id=user_id),
            )
            .order_by(distance)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [(int(row.id), float(row.similarity), row.description) for row in result.all()]

    async def search_jobs_by_text(
        self, query: str, limit: int, *, user_id: int
    ) -> list[RetrievalCandidate]:
        """Return eligible jobs ranked by the indexed title/description document."""
        if not query.strip() or limit <= 0:
            return []
        manual_job_ids = select(UserJobMatch.job_id).where(
            UserJobMatch.user_id == user_id,
            UserJobMatch.origin == "manual",
        )
        ts_query = func.plainto_tsquery("simple", query)
        rank = func.ts_rank_cd(Job.search_vector, ts_query)
        stmt = (
            select(Job.id, rank.label("text_rank"))
            .where(
                Job.id.not_in(manual_job_ids),
                Job.search_vector.op("@@")(ts_query),
                *self._matchable_job_predicate(user_id=user_id),
            )
            .order_by(rank.desc(), Job.id.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [
            RetrievalCandidate(job_id=int(row.id), score=float(row.text_rank))
            for row in result.all()
        ]

    async def search_jobs_by_skills(
        self, skill_ids: set[str] | frozenset[str], limit: int, *, user_id: int
    ) -> list[RetrievalCandidate]:
        """Return eligible jobs ranked by distinct canonical skill overlap."""
        if not skill_ids or limit <= 0:
            return []
        manual_job_ids = select(UserJobMatch.job_id).where(
            UserJobMatch.user_id == user_id,
            UserJobMatch.origin == "manual",
        )
        overlap = func.count(func.distinct(JobSkillMention.skill_id))
        stmt = (
            select(Job.id, overlap.label("skill_overlap"))
            .join(JobSkillMention, JobSkillMention.job_id == Job.id)
            .where(
                Job.id.not_in(manual_job_ids),
                JobSkillMention.skill_id.in_(skill_ids),
                *self._matchable_job_predicate(user_id=user_id),
            )
            .group_by(Job.id)
            .order_by(overlap.desc(), Job.id.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [
            RetrievalCandidate(job_id=int(row.id), score=float(row.skill_overlap))
            for row in result.all()
        ]

    async def vector_similarities_for_jobs(
        self, *, user_id: int, embedding: list[float], job_ids: list[int]
    ) -> dict[int, float]:
        """Return vector similarity for eligible fused candidates in one query."""
        if not job_ids:
            return {}
        distance = Job.description_embedding.cosine_distance(embedding)
        result = await self._session.execute(
            select(Job.id, (1 - distance).label("similarity")).where(
                Job.id.in_(job_ids),
                *self._matchable_job_predicate(user_id=user_id),
            )
        )
        return {int(row.id): float(row.similarity) for row in result.all()}

    async def upsert_match(
        self,
        *,
        user_id: int,
        job_id: int,
        vector_score: float,
        confidence: str | None,
        origin: str = "automatic",
        matched_skills: list[str] | None = None,
        missing_keywords: list[str] | None = None,
        deterministic_match: dict[str, Any] | None = None,
        fit_score: float | None = None,
        ranking_score: float | None = None,
        score_components: dict[str, object] | None = None,
        source_profile_id: int | None = None,
        source_profile_matching_hash: str | None = None,
        job_description_hash: str | None = None,
        ontology_version: str | None = None,
        scorer_version: str | None = None,
        readiness_preferences_hash: str | None = None,
        retrieval_metadata: dict[str, object] | None = None,
    ) -> None:
        canonical_matched = cast(
            list[str] | None,
            canonicalize_cache_value(matched_skills, key="matched_skills"),
        )
        canonical_missing = cast(
            list[str] | None,
            canonicalize_cache_value(missing_keywords, key="missing_keywords"),
        )
        canonical_evidence = cast(
            dict[str, Any] | None,
            canonicalize_cache_value(deterministic_match, key="deterministic_match"),
        )
        values: dict[str, Any] = {
            "user_id": user_id,
            "job_id": job_id,
            "vector_score": vector_score,
            "confidence": confidence,
            "origin": origin,
        }
        if deterministic_match is not None:
            values.update(
                matched_skills=canonical_matched,
                missing_keywords=canonical_missing,
                deterministic_match=canonical_evidence,
            )
        values.update(
            fit_score=fit_score,
            ranking_score=ranking_score,
            score_components=score_components,
            source_profile_id=source_profile_id,
            source_profile_matching_hash=source_profile_matching_hash,
            job_description_hash=job_description_hash,
            ontology_version=ontology_version,
            scorer_version=scorer_version,
            readiness_preferences_hash=readiness_preferences_hash,
        )
        if retrieval_metadata is not None:
            values["retrieval_metadata"] = retrieval_metadata
        stmt = pg_insert(UserJobMatch).values(**values)
        deterministic_conditions: list[Any] = [
            UserJobMatch.vector_score.is_distinct_from(stmt.excluded.vector_score),
            UserJobMatch.fit_score.is_distinct_from(stmt.excluded.fit_score),
            UserJobMatch.ranking_score.is_distinct_from(stmt.excluded.ranking_score),
            UserJobMatch.score_components.is_distinct_from(stmt.excluded.score_components),
            UserJobMatch.source_profile_id.is_distinct_from(stmt.excluded.source_profile_id),
            UserJobMatch.source_profile_matching_hash.is_distinct_from(
                stmt.excluded.source_profile_matching_hash
            ),
            UserJobMatch.job_description_hash.is_distinct_from(stmt.excluded.job_description_hash),
            UserJobMatch.ontology_version.is_distinct_from(stmt.excluded.ontology_version),
            UserJobMatch.scorer_version.is_distinct_from(stmt.excluded.scorer_version),
            UserJobMatch.readiness_preferences_hash.is_distinct_from(
                stmt.excluded.readiness_preferences_hash
            ),
        ]
        if deterministic_match is not None:
            deterministic_conditions.extend(
                (
                    UserJobMatch.matched_skills.is_distinct_from(stmt.excluded.matched_skills),
                    UserJobMatch.missing_keywords.is_distinct_from(stmt.excluded.missing_keywords),
                    UserJobMatch.deterministic_match.is_distinct_from(
                        stmt.excluded.deterministic_match
                    ),
                )
            )
        deterministic_changed = or_(*deterministic_conditions)
        update_values: dict[str, Any] = {
            "vector_score": stmt.excluded.vector_score,
            "confidence": stmt.excluded.confidence,
            "origin": case(
                (stmt.excluded.origin == "manual", "manual"),
                (UserJobMatch.origin == "manual", "manual"),
                else_="automatic",
            ),
            "updated_at": datetime.now(UTC),
            "fit_score": stmt.excluded.fit_score,
            "ranking_score": stmt.excluded.ranking_score,
            "score_components": stmt.excluded.score_components,
            "source_profile_id": stmt.excluded.source_profile_id,
            "source_profile_matching_hash": stmt.excluded.source_profile_matching_hash,
            "job_description_hash": stmt.excluded.job_description_hash,
            "ontology_version": stmt.excluded.ontology_version,
            "scorer_version": stmt.excluded.scorer_version,
            "readiness_preferences_hash": stmt.excluded.readiness_preferences_hash,
            "llm_score": case((deterministic_changed, None), else_=UserJobMatch.llm_score),
            "llm_confidence": case(
                (deterministic_changed, None), else_=UserJobMatch.llm_confidence
            ),
            "llm_explanation": case(
                (deterministic_changed, None), else_=UserJobMatch.llm_explanation
            ),
            "llm_analyzed_at": case(
                (deterministic_changed, None), else_=UserJobMatch.llm_analyzed_at
            ),
            "llm_error": case((deterministic_changed, None), else_=UserJobMatch.llm_error),
            "llm_input_hash": case(
                (deterministic_changed, None), else_=UserJobMatch.llm_input_hash
            ),
        }
        if retrieval_metadata is not None:
            update_values["retrieval_metadata"] = stmt.excluded.retrieval_metadata
        stmt = stmt.on_conflict_do_update(
            index_elements=[UserJobMatch.user_id, UserJobMatch.job_id],
            set_={
                **update_values,
                **(
                    {
                        "matched_skills": canonical_matched,
                        "missing_keywords": canonical_missing,
                        "deterministic_match": canonical_evidence,
                    }
                    if deterministic_match is not None
                    else {}
                ),
            },
        )
        await self._session.execute(stmt)

    async def score_visible_job(
        self, *, user_id: int, job_id: int, embedding: list[float]
    ) -> float | None:
        """Return one visible job's vector similarity, if it is embedded."""
        distance = Job.description_embedding.cosine_distance(embedding)
        result = await self._session.execute(
            select((1 - distance).label("similarity")).where(
                Job.id == job_id,
                *self._matchable_job_predicate(user_id=user_id),
            )
        )
        value = result.scalar_one_or_none()
        return float(value) if value is not None else None

    async def get_visible_jobs_by_ids(self, *, user_id: int, job_ids: list[int]) -> dict[int, Job]:
        if not job_ids:
            return {}
        result = await self._session.execute(
            select(Job).where(Job.id.in_(job_ids), *self._matchable_job_predicate(user_id=user_id))
        )
        return {job.id: job for job in result.scalars().all()}

    async def prune_automatic_matches(
        self,
        *,
        user_id: int,
        limit: int,
        ranking_mode: RankingMode = "vector",
    ) -> None:
        """Keep only the highest scoring automatic rows; never delete manual rows."""
        eligible_automatic = (
            select(UserJobMatch.id)
            .join(Job, UserJobMatch.job_id == Job.id)
            .where(UserJobMatch.user_id == user_id, UserJobMatch.origin == "automatic")
            .where(*self._matchable_job_predicate(user_id=user_id))
        )
        score_column = (
            UserJobMatch.ranking_score if ranking_mode == "fit" else UserJobMatch.vector_score
        )
        keep_order = (
            score_column.desc().nulls_last()
            if ranking_mode == "fit"
            else score_column.desc()
        )
        keep = eligible_automatic.order_by(keep_order, UserJobMatch.job_id.desc()).limit(limit)
        await self._session.execute(
            delete(UserJobMatch).where(
                UserJobMatch.user_id == user_id,
                UserJobMatch.origin == "automatic",
                UserJobMatch.id.in_(eligible_automatic),
                UserJobMatch.id.not_in(keep),
            )
        )

    async def list_manual_match_job_ids(self, *, user_id: int) -> list[int]:
        result = await self._session.execute(
            select(UserJobMatch.job_id).where(
                UserJobMatch.user_id == user_id,
                UserJobMatch.origin == "manual",
            )
        )
        return [int(job_id) for job_id in result.scalars().all()]

    async def list_matches(
        self,
        *,
        user_id: int,
        limit: int,
        offset: int | None = None,
        cursor: str | None = None,
        ranking_mode: RankingMode = "vector",
        q: str | None = None,
        source: str | None = None,
        is_remote: bool | None = None,
        job_type: str | None = None,
        location_contains: str | None = None,
        company_contains: str | None = None,
        shift_type: str | None = None,
        source_type: str | None = None,
    ) -> tuple[list[tuple[Job, UserJobMatch]], int, str | None, bool]:
        """Return ranked matches with optional filters on job fields.

        Imported jobs without match scores are excluded; they belong in
        /imported, not /matches, until analysis completes.
        """
        score_column = (
            UserJobMatch.ranking_score if ranking_mode == "fit" else UserJobMatch.vector_score
        )
        filters: list[Any] = [
            UserJobMatch.user_id == user_id,
            score_column.is_not(None),
            *self._matchable_job_predicate(user_id=user_id),
        ]

        if q:
            like = f"%{q}%"
            filters.append(
                or_(
                    Job.title.ilike(like),
                    Job.company.ilike(like),
                    Job.description.ilike(like),
                )
            )
        if source is not None:
            filters.append(Job.source == source)
        if is_remote is not None:
            filters.append(Job.is_remote.is_(is_remote))
        if shift_type is not None:
            filters.append(Job.shift_type == shift_type)
        if source_type == "global":
            filters.append(Job.visibility == "public")
        elif source_type == "manual":
            filters.append(and_(Job.visibility == "private", Job.ingest_source == "manual"))
        elif source_type == "extension":
            filters.append(and_(Job.visibility == "private", Job.ingest_source == "extension"))
        if job_type is not None:
            filters.append(func.lower(Job.job_type) == job_type.lower())
        if location_contains is not None:
            filters.append(Job.location.ilike(f"%{location_contains}%"))
        if company_contains is not None:
            filters.append(Job.company.ilike(f"%{company_contains}%"))

        count_stmt = (
            select(func.count())
            .select_from(UserJobMatch)
            .join(Job, UserJobMatch.job_id == Job.id)
            .where(*filters)
        )
        total = int((await self._session.execute(count_stmt)).scalar_one())

        def _encode_cursor(score: float, job_id: int) -> str:
            raw = f"fit|{score!r}|{job_id}" if ranking_mode == "fit" else f"{score!r}|{job_id}"
            return base64.urlsafe_b64encode(raw.encode()).decode()

        def _decode_cursor(raw_cursor: str) -> tuple[float, int]:
            try:
                decoded = base64.b64decode(raw_cursor.encode(), altchars=b"-_", validate=True)
                raw = decoded.decode()
                parts = raw.split("|")
                if ranking_mode == "fit":
                    if len(parts) != 3 or parts[0] != "fit":
                        raise ValueError
                    _, score_str, id_str = parts
                else:
                    if len(parts) != 2:
                        raise ValueError
                    score_str, id_str = parts
                if not score_str or not id_str:
                    raise ValueError
                score = float(score_str)
                if ranking_mode == "fit" and not math.isfinite(score):
                    raise ValueError
                return score, int(id_str)
            except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
                raise ValueError("Invalid cursor") from exc

        pagination_filters = list(filters)
        # Deprecated compatibility fallback for old clients; frontend uses cursor.
        use_offset = cursor is None and offset is not None and offset > 0
        if cursor is not None:
            cursor_score, cursor_job_id = _decode_cursor(cursor)
            pagination_filters.append(
                tuple_(score_column, Job.id) < (cursor_score, cursor_job_id)
            )

        list_stmt = (
            select(Job, UserJobMatch)
            .join(UserJobMatch, UserJobMatch.job_id == Job.id)
            .where(*pagination_filters)
            .order_by(score_column.desc(), Job.id.desc())
            .limit(limit + 1)
        )
        if use_offset:
            list_stmt = list_stmt.offset(offset)
        rows = (await self._session.execute(list_stmt)).all()

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        next_cursor: str | None = None
        if has_more and rows:
            last_job, last_match = rows[-1]
            last_score = (
                last_match.ranking_score
                if ranking_mode == "fit"
                else last_match.vector_score
            )
            if last_score is not None:
                next_cursor = _encode_cursor(last_score, last_job.id)

        return [(r[0], r[1]) for r in rows], total, next_cursor, has_more

    async def get_match(self, *, user_id: int, job_id: int) -> UserJobMatch | None:
        result = await self._session.execute(
            select(UserJobMatch).where(
                UserJobMatch.user_id == user_id,
                UserJobMatch.job_id == job_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_visible_job(self, *, user_id: int, job_id: int) -> Job | None:
        """Return *job_id* only if visible to *user_id*.

        Same visibility + archive-exclusion predicate as
        ``JobRepository.get_visible_by_id``.
        """
        visibility_filter = or_(
            Job.visibility == "public",
            Job.created_by_user_id == user_id,
        )
        archived_subq = (
            select(UserJob.job_id)
            .where(
                UserJob.user_id == user_id,
                UserJob.status == "archived",
            )
            .scalar_subquery()
        )
        result = await self._session.execute(
            select(Job).where(
                Job.id == job_id,
                Job.status == "active",
                visibility_filter,
                Job.id.not_in(archived_subq),
            )
        )
        return result.scalar_one_or_none()

    async def aggregate_missing_keywords(
        self, *, user_id: int, top_n: int
    ) -> list[tuple[str, int]]:
        """Return the most frequent missing keywords across the user's matches.

        Unnests the ``missing_keywords`` JSONB arrays and counts occurrences,
        normalising case so "React" and "react" collapse to one bucket.
        """
        keyword = func.lower(func.jsonb_array_elements_text(UserJobMatch.missing_keywords)).label(
            "keyword"
        )
        subquery = (
            select(keyword)
            .where(
                UserJobMatch.user_id == user_id,
                UserJobMatch.missing_keywords.isnot(None),
            )
            .subquery()
        )
        stmt = (
            select(subquery.c.keyword, func.count().label("frequency"))
            .group_by(subquery.c.keyword)
            .order_by(func.count().desc())
            .limit(top_n)
        )
        result = await self._session.execute(stmt)
        return [(str(row.keyword), int(row.frequency)) for row in result.all()]

    async def count_matches_with_gaps(self, user_id: int) -> int:
        """Count the user's matches that carry an explained missing-keywords list."""
        stmt = (
            select(func.count())
            .select_from(UserJobMatch)
            .where(
                UserJobMatch.user_id == user_id,
                UserJobMatch.missing_keywords.isnot(None),
            )
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def save_explanation(
        self,
        *,
        match: UserJobMatch,
        llm_score: int | None,
        llm_confidence: float | None,
        explanation: str | None,
        llm_analyzed_at: datetime | None,
        llm_error: str | None,
        matched_skills: list[str],
        missing_keywords: list[str],
        deterministic_match: dict[str, Any],
        confidence: str | None,
        llm_input_hash: str | None = None,
    ) -> UserJobMatch:
        match.llm_score = llm_score
        match.llm_confidence = llm_confidence
        match.llm_explanation = explanation
        match.llm_analyzed_at = llm_analyzed_at
        match.llm_error = llm_error
        match.llm_input_hash = llm_input_hash
        match.matched_skills = matched_skills
        match.missing_keywords = missing_keywords
        match.deterministic_match = deterministic_match
        await self._session.flush()
        return match

    async def save_deterministic_match(
        self,
        *,
        match: UserJobMatch,
        matched_skills: list[str],
        missing_keywords: list[str],
        deterministic_match: dict[str, Any],
    ) -> UserJobMatch:
        """Persist deterministic evidence and clear stale LLM output if it changed."""
        deterministic_changed = any(
            _canonical_json(current, key=key) != _canonical_json(updated, key=key)
            for key, current, updated in (
                ("matched_skills", match.matched_skills, matched_skills),
                ("missing_keywords", match.missing_keywords, missing_keywords),
                ("deterministic_match", match.deterministic_match, deterministic_match),
            )
        )
        match.matched_skills = matched_skills
        match.missing_keywords = missing_keywords
        match.deterministic_match = deterministic_match
        if deterministic_changed:
            match.llm_score = None
            match.llm_confidence = None
            match.llm_explanation = None
            match.llm_analyzed_at = None
            match.llm_error = None
            match.llm_input_hash = None
        await self._session.flush()
        return match

    async def clear_llm_analysis(self, match: UserJobMatch) -> None:
        match.llm_score = None
        match.llm_confidence = None
        match.llm_explanation = None
        match.llm_analyzed_at = None
        match.llm_error = None
        match.llm_input_hash = None
        await self._session.flush()
