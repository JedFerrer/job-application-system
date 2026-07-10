"""Job repository — upsert (dedup) + visibility-scoped listing.

Uses ``INSERT ... ON CONFLICT (fingerprint) DO UPDATE`` so the same role from
multiple sources collapses into one row, never ``SELECT``-then-``INSERT``.

Visibility rules:
- ``visibility = 'public'`` → visible to ALL users.
- ``visibility = 'private'`` → visible ONLY to ``created_by_user_id``.
- Per-user archiving via ``user_jobs.status = 'archived'``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, literal_column, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.user_job import UserJob
from app.models.user_job_import import UserJobImport


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
        update_cols: dict[str, object] = {
            "fetched_at": datetime.now(UTC),
            "title": stmt.excluded.title,
            "source": stmt.excluded.source,
            "source_url": stmt.excluded.source_url,
            "raw_response": stmt.excluded.raw_response,
        }
        # Backfill optional fields only when the new value is present.
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
        relationship_source: str = "match",
        import_method: str | None = None,
        status: str = "new",
    ) -> None:
        """Link globally-deduplicated jobs to a single user's feed.

        Idempotent: re-linking an existing ``(user_id, job_id)`` pair is a
        no-op via ``ON CONFLICT DO NOTHING``, so repeated scrapes never
        duplicate rows.
        """
        if not job_ids:
            return
        rows = [
            {
                "user_id": user_id,
                "job_id": job_id,
                "preference_id": preference_id,
                "relationship_source": relationship_source,
                "import_method": import_method,
                "status": status,
            }
            for job_id in job_ids
        ]
        stmt = pg_insert(UserJob).values(rows).on_conflict_do_nothing(
            index_elements=[UserJob.user_id, UserJob.job_id]
        )
        await self._session.execute(stmt)

    async def create_user_job_import(
        self,
        *,
        user_id: int,
        job_id: int,
        user_job_user_id: int,
        user_job_job_id: int,
        import_method: str,
        source_url: str | None = None,
        raw_input: str | None = None,
        raw_payload: dict[str, object] | None = None,
        raw_html_hash: str | None = None,
        parsed_title: str | None = None,
        parsed_company: str | None = None,
        parsed_location: str | None = None,
        parsed_salary: str | None = None,
        parse_confidence: float | None = None,
    ) -> None:
        """Store user-private import metadata. Never touches the public job row."""
        stmt = pg_insert(UserJobImport).values(
            user_id=user_id,
            job_id=job_id,
            user_job_user_id=user_job_user_id,
            user_job_job_id=user_job_job_id,
            import_method=import_method,
            source_url=source_url,
            raw_input=raw_input,
            raw_payload=raw_payload,
            raw_html_hash=raw_html_hash,
            parsed_title=parsed_title,
            parsed_company=parsed_company,
            parsed_location=parsed_location,
            parsed_salary=parsed_salary,
            parse_confidence=parse_confidence,
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

    async def set_user_job_status(
        self, user_id: int, job_id: int, status: str
    ) -> None:
        """Update the per-user relationship status (e.g. mark as archived)."""
        await self._session.execute(
            update(UserJob)
            .where(UserJob.user_id == user_id, UserJob.job_id == job_id)
            .values(status=status)
        )

    async def archive_older_than(self, cutoff: datetime) -> int:
        """Mark jobs older than ``cutoff`` as expired (global lifecycle).

        Recency uses ``posted_at`` when present, falling back to ``fetched_at``
        for sources that don't supply a posting date. Returns the row count.
        """
        result = await self._session.execute(
            update(Job)
            .where(
                Job.status == "active",
                func.coalesce(Job.posted_at, Job.fetched_at) < cutoff,
            )
            .values(status="expired")
        )
        return int(result.rowcount or 0)

    async def get_by_id(self, job_id: int) -> Job | None:
        return await self._session.get(Job, job_id)

    async def find_by_source_url(self, url: str) -> Job | None:
        """Find a job by its source URL (exact match)."""
        result = await self._session.execute(
            select(Job).where(Job.source_url == url).limit(1)
        )
        return result.scalar_one_or_none()

    async def list_jobs(
        self,
        *,
        user_id: int,
        source: str | None = None,
        is_remote: bool | None = None,
        min_salary: float | None = None,
        job_type: str | None = None,
        location_contains: str | None = None,
        source_type: str | None = None,
        imported_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Job], int]:
        """List jobs visible to *user_id*.

        Visibility:
        - Public jobs (``visibility = 'public'``) are visible to everyone.
        - Private jobs (``visibility = 'private'``) are visible ONLY to their
          ``created_by_user_id``.

        Per-user archiving is respected: jobs where ``user_jobs.status =
        'archived'`` for this user are excluded.
        """
        # LEFT JOIN user_jobs so we can filter per-user status and enrich with
        # relationship metadata.
        visibility_filter = or_(
            Job.visibility == "public",
            Job.created_by_user_id == user_id,
        )
        filters: list[Any] = [Job.status == "active", visibility_filter]

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
        if source_type == "global":
            filters.append(Job.visibility == "public")
        elif source_type == "imported":
            filters.append(Job.visibility == "private")
        elif source_type == "manual":
            filters.append(
                and_(Job.visibility == "private", Job.ingest_source == "manual")
            )
        elif source_type == "extension":
            filters.append(
                and_(Job.visibility == "private", Job.ingest_source == "extension")
            )
        if imported_only:
            filters.append(Job.visibility == "private")

        # Exclude jobs the user has explicitly archived
        archived_subq = (
            select(UserJob.job_id)
            .where(
                UserJob.user_id == user_id,
                UserJob.status == "archived",
            )
            .scalar_subquery()
        )
        filters.append(Job.id.not_in(archived_subq))

        count_stmt = (
            select(func.count())
            .select_from(Job)
            .where(*filters)
        )
        total = int((await self._session.execute(count_stmt)).scalar_one())

        list_stmt = (
            select(Job)
            .where(*filters)
            .order_by(
                func.coalesce(
                    Job.last_seen_at, Job.scraped_at, Job.fetched_at
                ).desc(),
                Job.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        rows = list((await self._session.execute(list_stmt)).scalars().all())
        return rows, total

    async def list_imported_jobs(
        self,
        *,
        user_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Job], int]:
        """Return only jobs the user imported (manual or extension)."""
        filters: list[Any] = [
            UserJob.user_id == user_id,
            UserJob.relationship_source.in_(["manual", "extension"]),
            Job.status == "active",
        ]

        count_stmt = (
            select(func.count())
            .select_from(Job)
            .join(UserJob, UserJob.job_id == Job.id)
            .where(*filters)
        )
        total = int((await self._session.execute(count_stmt)).scalar_one())

        list_stmt = (
            select(Job, UserJob.match_score, UserJob.match_reason)
            .join(UserJob, UserJob.job_id == Job.id)
            .where(*filters)
            .order_by(UserJob.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = list((await self._session.execute(list_stmt)).all())
        return rows, total
