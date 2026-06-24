"""Job repository — upsert (dedup) + filtered listing.

Uses ``INSERT ... ON CONFLICT (fingerprint) DO UPDATE`` so the same role from
multiple sources collapses into one row, never ``SELECT``-then-``INSERT``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.user_job import UserJob


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_job(self, values: dict[str, object]) -> tuple[int, bool]:
        """Insert a job or merge into an existing fingerprint.

        Returns ``(job_id, inserted)`` where ``inserted`` is ``True`` for a new
        row and ``False`` when an existing row was refreshed. On conflict we
        refresh ``fetched_at`` and backfill fields only when the incoming value
        is non-null (a second source may supply salary the first lacked).
        """
        stmt = pg_insert(Job).values(**values)
        update_cols = {
            "fetched_at": datetime.now(UTC),
            "title": stmt.excluded.title,
            "source": stmt.excluded.source,
            "source_url": stmt.excluded.source_url,
            "raw_response": stmt.excluded.raw_response,
        }
        # Backfill optional fields only when the new value is present.
        # shift_type is also COALESCE'd: regex may return None for an updated
        # job, so we keep the previously classified value rather than clearing it.
        for col in (
            "company",
            "location",
            "is_remote",
            "job_type",
            "description",
            "salary_min",
            "salary_max",
            "salary_currency",
            "posted_at",
            "shift_type",
        ):
            update_cols[col] = func.coalesce(
                stmt.excluded[col], getattr(Job, col)
            )
        upsert: Any = stmt.on_conflict_do_update(
            index_elements=[Job.fingerprint],
            set_=update_cols,
            # NEVER touch is_archived on conflict — preserve any manual archive.
        ).returning(Job.id, literal_column("(xmax = 0)").label("inserted"))
        result = await self._session.execute(upsert)
        row = result.one()
        return int(row.id), bool(row.inserted)

    async def link_user_jobs(
        self,
        user_id: int,
        job_ids: list[int],
        *,
        preference_id: int | None = None,
    ) -> None:
        """Link globally-deduplicated jobs to a single user's feed.

        Idempotent: re-linking an existing ``(user_id, job_id)`` pair is a
        no-op via ``ON CONFLICT DO NOTHING``, so repeated scrapes never
        duplicate rows.
        """
        if not job_ids:
            return
        rows = [
            {"user_id": user_id, "job_id": job_id, "preference_id": preference_id}
            for job_id in job_ids
        ]
        stmt = pg_insert(UserJob).values(rows).on_conflict_do_nothing(
            index_elements=[UserJob.user_id, UserJob.job_id]
        )
        await self._session.execute(stmt)

    async def set_embedding(self, job_id: int, embedding: list[float]) -> None:
        await self._session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(description_embedding=embedding)
        )

    async def set_shift_type(self, job_id: int, shift_type: str) -> None:
        """Persist a shift_type that was determined by LLM fallback."""
        await self._session.execute(
            update(Job).where(Job.id == job_id).values(shift_type=shift_type)
        )

    async def set_archived(self, job_id: int, *, archived: bool) -> Job | None:
        """Toggle is_archived on a job.  Returns the updated row or None."""
        await self._session.execute(
            update(Job).where(Job.id == job_id).values(is_archived=archived)
        )
        return await self._session.get(Job, job_id)

    async def archive_older_than(self, cutoff: datetime) -> int:
        """Archive non-archived jobs older than ``cutoff``.

        Recency uses ``posted_at`` when present, falling back to ``fetched_at``
        for sources that don't supply a posting date. Returns the row count.
        """
        result = await self._session.execute(
            update(Job)
            .where(
                Job.is_archived.is_(False),
                func.coalesce(Job.posted_at, Job.fetched_at) < cutoff,
            )
            .values(is_archived=True)
        )
        return int(result.rowcount or 0)


    async def get_by_id(self, job_id: int) -> Job | None:
        return await self._session.get(Job, job_id)

    async def list_jobs(
        self,
        *,
        user_id: int,
        source: str | None = None,
        is_remote: bool | None = None,
        min_salary: float | None = None,
        job_type: str | None = None,
        location_contains: str | None = None,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Job], int]:
        # Only jobs linked to this user (scraped for their preferences) are
        # visible. The junction keeps the jobs table globally deduplicated.
        filters = [UserJob.user_id == user_id]
        if source is not None:
            filters.append(Job.source == source)
        if is_remote is not None:
            filters.append(Job.is_remote.is_(is_remote))
        if min_salary is not None:
            filters.append(Job.salary_max >= min_salary)
        if job_type is not None:
            filters.append(func.lower(Job.job_type) == job_type.lower())
        if location_contains is not None:
            filters.append(Job.location.ilike(f"%{location_contains}%"))
        if not include_archived:
            filters.append(Job.is_archived.is_(False))

        count_stmt = (
            select(func.count())
            .select_from(Job)
            .join(UserJob, UserJob.job_id == Job.id)
            .where(*filters)
        )
        total = int((await self._session.execute(count_stmt)).scalar_one())

        list_stmt = (
            select(Job)
            .join(UserJob, UserJob.job_id == Job.id)
            .where(*filters)
            .order_by(Job.fetched_at.desc(), Job.id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = list((await self._session.execute(list_stmt)).scalars().all())
        return rows, total
