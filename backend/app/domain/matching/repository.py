"""Matching repository: vector ranking + match-row persistence (Phase 3)."""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, case, delete, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.resume.repository import ResumeRepository
from app.models.application import Application
from app.models.job import Job
from app.models.resume import CandidateProfile
from app.models.user_job import UserJob
from app.models.user_job_match import UserJobMatch


class MatchingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active_profile(self, user_id: int) -> CandidateProfile | None:
        """Return only a complete, supported active profile."""
        return await ResumeRepository(self._session).get_active_profile(user_id)

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
        archived_subq = select(UserJob.job_id).where(
            UserJob.user_id == user_id,
            UserJob.status == "archived",
        )
        stmt = (
            select(Job.id, (1 - distance).label("similarity"), Job.description)
            .where(
                Job.status == "active",
                Job.description_embedding.is_not(None),
                Job.id.not_in(manual_job_ids),
                Job.id.not_in(archived_subq),
                or_(
                    Job.visibility == "public",
                    Job.created_by_user_id == user_id,
                ),
            )
            .order_by(distance)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [
            (int(row.id), float(row.similarity), row.description)
            for row in result.all()
        ]

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
    ) -> None:
        values: dict[str, Any] = {
            "user_id": user_id,
            "job_id": job_id,
            "vector_score": vector_score,
            "confidence": confidence,
            "origin": origin,
        }
        if deterministic_match is not None:
            values.update(
                matched_skills=matched_skills,
                missing_keywords=missing_keywords,
                deterministic_match=deterministic_match,
            )
        stmt = pg_insert(UserJobMatch).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[UserJobMatch.user_id, UserJobMatch.job_id],
            set_={
                "vector_score": stmt.excluded.vector_score,
                "confidence": stmt.excluded.confidence,
                "origin": case(
                    (stmt.excluded.origin == "manual", "manual"),
                    (UserJobMatch.origin == "manual", "manual"),
                    else_="automatic",
                ),
                "updated_at": datetime.now(UTC),
                **(
                    {
                        "matched_skills": matched_skills,
                        "missing_keywords": missing_keywords,
                        "deterministic_match": deterministic_match,
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
        archived_subq = (
            select(UserJob.job_id)
            .where(UserJob.user_id == user_id, UserJob.status == "archived")
            .scalar_subquery()
        )
        result = await self._session.execute(
            select((1 - distance).label("similarity")).where(
                Job.id == job_id,
                Job.status == "active",
                Job.description_embedding.is_not(None),
                or_(Job.visibility == "public", Job.created_by_user_id == user_id),
                Job.id.not_in(archived_subq),
            )
        )
        value = result.scalar_one_or_none()
        return float(value) if value is not None else None

    async def prune_automatic_matches(
        self,
        *,
        user_id: int,
        limit: int,
        keep_job_ids: list[int] | None = None,
    ) -> None:
        """Keep current full-refresh rows or the highest scoring incremental rows."""
        if keep_job_ids is None:
            keep = (
                select(UserJobMatch.id)
                .where(UserJobMatch.user_id == user_id, UserJobMatch.origin == "automatic")
                .order_by(UserJobMatch.vector_score.desc(), UserJobMatch.job_id.desc())
                .limit(limit)
            )
            delete_filter = UserJobMatch.id.not_in(keep)
        else:
            delete_filter = UserJobMatch.job_id.not_in(keep_job_ids)
        await self._session.execute(
            delete(UserJobMatch).where(
                UserJobMatch.user_id == user_id,
                UserJobMatch.origin == "automatic",
                delete_filter,
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
        filters: list[Any] = [
            UserJobMatch.user_id == user_id,
            UserJobMatch.vector_score.is_not(None),
            Job.status == "active",
            visibility_filter,
            Job.id.not_in(archived_subq),
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

        def _encode_cursor(vector_score: float, job_id: int) -> str:
            raw = f"{vector_score!r}|{job_id}"
            return base64.urlsafe_b64encode(raw.encode()).decode()

        def _decode_cursor(raw_cursor: str) -> tuple[float, int]:
            try:
                decoded = base64.b64decode(raw_cursor.encode(), altchars=b"-_", validate=True)
                raw = decoded.decode()
                score_str, id_str = raw.rsplit("|", 1)
                if not score_str or not id_str:
                    raise ValueError
                return float(score_str), int(id_str)
            except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
                raise ValueError("Invalid cursor") from exc

        pagination_filters = list(filters)
        # Deprecated compatibility fallback for old clients; frontend uses cursor.
        use_offset = cursor is None and offset is not None and offset > 0
        if cursor is not None:
            cursor_score, cursor_job_id = _decode_cursor(cursor)
            pagination_filters.append(
                tuple_(UserJobMatch.vector_score, Job.id) < (cursor_score, cursor_job_id)
            )

        list_stmt = (
            select(Job, UserJobMatch)
            .join(UserJobMatch, UserJobMatch.job_id == Job.id)
            .where(*pagination_filters)
            .order_by(UserJobMatch.vector_score.desc(), Job.id.desc())
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
            next_cursor = _encode_cursor(last_match.vector_score, last_job.id)

        return [(r[0], r[1]) for r in rows], total, next_cursor, has_more

    async def list_recommendation_candidates(
        self, *, user_id: int, limit: int = 100
    ) -> list[tuple[Job, UserJobMatch, str | None]]:
        """Return a bounded ranked candidate scan with application state."""
        visibility_filter = or_(
            Job.visibility == "public",
            Job.created_by_user_id == user_id,
        )
        archived_subq = (
            select(UserJob.job_id)
            .where(UserJob.user_id == user_id, UserJob.status == "archived")
            .scalar_subquery()
        )
        stmt = (
            select(Job, UserJobMatch, Application.status)
            .join(UserJobMatch, UserJobMatch.job_id == Job.id)
            .outerjoin(
                Application,
                and_(
                    Application.job_id == UserJobMatch.job_id,
                    Application.user_id == user_id,
                ),
            )
            .where(
                UserJobMatch.user_id == user_id,
                UserJobMatch.vector_score.is_not(None),
                Job.status == "active",
                visibility_filter,
                Job.id.not_in(archived_subq),
                or_(
                    Application.status.is_(None),
                    Application.status.not_in(("rejected", "closed")),
                ),
            )
            .order_by(UserJobMatch.vector_score.desc(), Job.id.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [(job, match, status) for job, match, status in rows]

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
        llm_analyzed_at: datetime,
        llm_error: str | None,
        matched_skills: list[str],
        missing_keywords: list[str],
        deterministic_match: dict[str, Any],
        confidence: str | None,
    ) -> UserJobMatch:
        match.llm_score = llm_score
        match.llm_confidence = llm_confidence
        match.llm_explanation = explanation
        match.llm_analyzed_at = llm_analyzed_at
        match.llm_error = llm_error
        match.matched_skills = matched_skills
        match.missing_keywords = missing_keywords
        match.deterministic_match = deterministic_match
        if confidence is not None:
            match.confidence = confidence
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
        """Persist free deterministic skill evidence without touching LLM fields."""
        match.matched_skills = matched_skills
        match.missing_keywords = missing_keywords
        match.deterministic_match = deterministic_match
        await self._session.flush()
        return match
