"""Job repository — upsert (dedup) + visibility-scoped listing.

Uses ``INSERT ... ON CONFLICT (fingerprint) DO UPDATE`` so the same role from
multiple sources collapses into one row, never ``SELECT``-then-``INSERT``.

Visibility rules:
- ``visibility = 'public'`` → visible to ALL users.
- ``visibility = 'private'`` → visible ONLY to ``created_by_user_id``.
- Per-user archiving via ``user_jobs.status = 'archived'``.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, exists, func, literal_column, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.application import Application
from app.models.application_event import ApplicationEvent
from app.models.application_form_session import ApplicationFormSession
from app.models.cover_letter import CoverLetter
from app.models.interview_prep import InterviewPrep
from app.models.job import Job
from app.models.resume import TailoredResumeDraft
from app.models.user_job import UserJob
from app.models.user_job_import import UserJobImport
from app.models.user_job_match import UserJobMatch


def _normalize_company_name(company: str) -> str:
    """Normalize case and any run of whitespace for exact employer matching."""
    return " ".join(company.split()).casefold()


def _normalized_company_expression(column: Any) -> Any:
    """PostgreSQL equivalent of :func:`_normalize_company_name`."""
    return func.lower(func.regexp_replace(func.btrim(column), r"\s+", " ", "g"))


@dataclass(frozen=True)
class ExpiredJobPurgeBatch:
    """One committed page of expired-job purge work."""

    scanned: int
    protected: int
    purged: int
    next_cursor: tuple[datetime, int] | None

    def totals(self) -> dict[str, int]:
        return {
            "scanned": self.scanned,
            "protected": self.protected,
            "purged": self.purged,
        }


def _encode_cursor(sort_ts: datetime, job_id: int) -> str:
    """Base64-encode a cursor: ``sort_ts.isoformat()|job_id``."""
    raw = f"{sort_ts.isoformat()}|{job_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, int]:
    """Decode a cursor back to ``(sort_ts, job_id)``."""
    raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    ts_str, id_str = raw.rsplit("|", 1)
    return datetime.fromisoformat(ts_str).replace(tzinfo=UTC), int(id_str)


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        """Persist a coordinated embedding before its Redis lock is released."""
        await self._session.commit()

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
            "company_logo",
        ):
            update_cols[col] = func.coalesce(stmt.excluded[col], getattr(Job, col))
        upsert: Any = stmt.on_conflict_do_update(
            index_elements=[Job.fingerprint],
            set_=update_cols,
        ).returning(Job.id, literal_column("(xmax = 0)").label("inserted"))
        result = await self._session.execute(upsert)
        row = result.one()
        return int(row.id), bool(row.inserted)

    async def get_description_by_fingerprint(
        self, fingerprint: str
    ) -> tuple[bool, str | None]:
        """Return whether a deduplicated job exists and its stored description."""
        result = await self._session.execute(
            select(Job.description).where(Job.fingerprint == fingerprint)
        )
        row = result.first()
        return (row is not None, None if row is None else row[0])

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
        stmt = (
            pg_insert(UserJob)
            .values(rows)
            .on_conflict_do_nothing(index_elements=[UserJob.user_id, UserJob.job_id])
        )
        await self._session.execute(stmt)

    async def link_recent_jobs_batch(self, *, user_ids: list[int], since: datetime) -> int:
        """Link every user in *user_ids* to every job fetched since *since*.

        Used after a shared scrape ingest so all subscribers see the results.
        Returns the number of ``user_jobs`` rows inserted.
        """
        if not user_ids:
            return 0

        result = await self._session.execute(select(Job.id).where(Job.fetched_at >= since))
        job_ids = [row for row in result.scalars().all()]
        if not job_ids:
            return 0

        total = 0
        for uid in user_ids:
            rows = [
                {
                    "user_id": uid,
                    "job_id": jid,
                    "relationship_source": "match",
                    "status": "new",
                }
                for jid in job_ids
            ]
            stmt = (
                pg_insert(UserJob)
                .values(rows)
                .on_conflict_do_nothing(index_elements=[UserJob.user_id, UserJob.job_id])
            )
            r = await self._session.execute(stmt)
            total += r.rowcount or 0
        return total

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
            .values(
                description_embedding=embedding,
                status="active",
                raw_response=func.coalesce(Job.raw_response, func.cast("{}", JSONB)).op("-")(
                    "embed_error"
                ),
            )
        )

    async def set_embed_error(self, job_id: int, error: str) -> None:
        """Mark a job whose embedding failed after all retries."""
        await self._session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status="unknown",
                raw_response=func.jsonb_set(
                    func.coalesce(Job.raw_response, func.cast("{}", JSONB)),
                    "{embed_error}",
                    func.cast(error, JSONB),
                ),
            )
        )

    async def set_shift_type(self, job_id: int, shift_type: str) -> None:
        """Persist a shift_type that was determined by LLM fallback."""
        await self._session.execute(
            update(Job).where(Job.id == job_id).values(shift_type=shift_type)
        )

    async def set_user_job_status(self, user_id: int, job_id: int, status: str) -> None:
        """Update the per-user relationship status (e.g. mark as archived)."""
        await self._session.execute(
            update(UserJob)
            .where(UserJob.user_id == user_id, UserJob.job_id == job_id)
            .values(status=status)
        )

    async def archive_user_job(self, *, user_id: int, job_id: int) -> None:
        """Atomically create or archive one user's relationship to a job.

        Existing relationship metadata is intentionally preserved.  A newly
        created relationship uses the generic ``match`` source so a visible
        public job can be archived even when it was never linked to the user.
        """
        stmt = pg_insert(UserJob).values(
            user_id=user_id,
            job_id=job_id,
            relationship_source="match",
            status="archived",
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[UserJob.user_id, UserJob.job_id],
                set_={"status": "archived"},
            )
        )

    async def mark_as_private_import(
        self, *, job_id: int, user_id: int, ingest_source: str = "manual"
    ) -> None:
        """Set visibility=private, created_by_user_id, and ingest_source on a job.

        Used after ``upsert_job`` creates a new row during manual/extension
        import so the job is scoped to the importing user.
        """
        await self._session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                visibility="private",
                created_by_user_id=user_id,
                ingest_source=ingest_source,
                imported_at=datetime.now(UTC),
            )
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
            .values(status="expired", expired_at=datetime.now(UTC))
        )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def purge_expired_unreferenced(
        self,
        *,
        cutoff: datetime,
        batch_size: int,
        after: tuple[datetime, int] | None = None,
    ) -> ExpiredJobPurgeBatch:
        """Permanently delete one bounded batch of expired, unreferenced jobs.

        User-created records intentionally protect a job from purge. Automatic
        match rows do not protect it and are deleted immediately before the job
        because their foreign key does not cascade.
        """
        candidate_filters = [
                Job.status == "expired",
                Job.expired_at.is_not(None),
                Job.expired_at < cutoff,
        ]
        if after is not None:
            expired_at, job_id = after
            candidate_filters.append(
                or_(
                    Job.expired_at > expired_at,
                    and_(Job.expired_at == expired_at, Job.id > job_id),
                )
            )
        candidate_stmt = (
            select(Job.id, Job.expired_at)
            .where(*candidate_filters)
            .order_by(Job.expired_at.asc(), Job.id.asc())
            .limit(batch_size)
            # Lock each candidate parent row. A concurrent insert of durable
            # user content must take a conflicting FK key-share lock, so it
            # either commits before our protection recheck or waits until the
            # purge commits. This prevents ON DELETE SET NULL relationships
            # from being silently detached during a purge.
            .with_for_update(skip_locked=True)
        )
        candidate_rows = list((await self._session.execute(candidate_stmt)).all())
        if not candidate_rows:
            return ExpiredJobPurgeBatch(0, 0, 0, None)

        candidate_ids = [int(row.id) for row in candidate_rows]
        last_expired_at = candidate_rows[-1].expired_at
        assert last_expired_at is not None
        next_cursor = (
            (last_expired_at, int(candidate_rows[-1].id))
            if len(candidate_ids) == batch_size
            else None
        )

        protected = or_(
            exists(select(1).where(Application.job_id == Job.id)),
            exists(select(1).where(UserJob.job_id == Job.id)),
            exists(
                select(1).where(
                    UserJobMatch.job_id == Job.id,
                    UserJobMatch.origin == "manual",
                )
            ),
            exists(select(1).where(UserJobImport.job_id == Job.id)),
            exists(select(1).where(CoverLetter.job_id == Job.id)),
            exists(select(1).where(InterviewPrep.job_id == Job.id)),
            exists(select(1).where(TailoredResumeDraft.job_id == Job.id)),
            exists(select(1).where(ApplicationEvent.job_id == Job.id)),
            exists(select(1).where(ApplicationFormSession.job_id == Job.id)),
        )
        eligible_stmt = select(Job.id).where(Job.id.in_(candidate_ids), ~protected)
        eligible_ids = list((await self._session.execute(eligible_stmt)).scalars().all())
        deleted_ids: list[int] = []
        if eligible_ids:
            await self._session.execute(
                delete(UserJobMatch).where(
                    UserJobMatch.job_id.in_(eligible_ids),
                    UserJobMatch.origin == "automatic",
                )
            )
            # Recheck protection in the deletion statement. The candidate row
            # lock above handles concurrent FK inserts; this second predicate
            # also protects rows that became durable user content between the
            # eligibility read and this write.
            result = await self._session.execute(
                delete(Job)
                .where(Job.id.in_(eligible_ids), ~protected)
                .returning(Job.id)
            )
            deleted_ids = [int(job_id) for job_id in result.scalars().all()]

        return ExpiredJobPurgeBatch(
            scanned=len(candidate_ids),
            protected=len(candidate_ids) - len(deleted_ids),
            purged=len(deleted_ids),
            next_cursor=next_cursor,
        )

    async def get_by_id(self, job_id: int) -> Job | None:
        """Raw PK lookup — no visibility or ownership checks.

        Prefer :meth:`get_visible_by_id` for user-facing reads.
        """
        return await self._session.get(Job, job_id)

    async def get_visible_by_id(self, *, user_id: int, job_id: int) -> Job | None:
        """Return *job_id* only if it is visible to *user_id*.

        Visibility rules (same predicate as :meth:`list_jobs`):
        - ``visibility = 'public'`` → visible to all authenticated users.
        - ``visibility = 'private'`` → visible ONLY to ``created_by_user_id``.
        - Per-user archived jobs are excluded.
        - Only active jobs are returned.
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

    async def get_archivable_by_id(self, *, user_id: int, job_id: int) -> Job | None:
        """Return an active job the user may archive.

        This deliberately uses the job-detail visibility predicate without
        excluding an already-archived relationship, so repeated archive
        requests remain idempotent while invisible jobs still return no row.
        """
        visibility_filter = or_(
            Job.visibility == "public",
            Job.created_by_user_id == user_id,
        )
        result = await self._session.execute(
            select(Job).where(
                Job.id == job_id,
                Job.status == "active",
                visibility_filter,
            )
        )
        return result.scalar_one_or_none()

    async def get_restorable_by_id(self, *, user_id: int, job_id: int) -> Job | None:
        """Return an active, visible job archived by this user, if any."""
        visibility_filter = or_(
            Job.visibility == "public",
            Job.created_by_user_id == user_id,
        )
        result = await self._session.execute(
            select(Job)
            .join(UserJob, UserJob.job_id == Job.id)
            .where(
                Job.id == job_id,
                Job.status == "active",
                visibility_filter,
                UserJob.user_id == user_id,
                UserJob.status == "archived",
            )
        )
        return result.scalar_one_or_none()

    async def restore_user_job(self, *, user_id: int, job_id: int) -> None:
        """Set one existing archived relationship back to the default state."""
        await self.set_user_job_status(user_id, job_id, "new")

    async def find_by_source_url(self, url: str) -> Job | None:
        """Find a job by its source URL (exact match)."""
        result = await self._session.execute(select(Job).where(Job.source_url == url).limit(1))
        return result.scalar_one_or_none()

    async def find_by_source_id(self, source: str, source_job_id: str) -> int | None:
        """Look up a job_id from job_source_links by (source, source_job_id)."""
        from app.models.job_source_link import JobSourceLink

        result = await self._session.execute(
            select(JobSourceLink.job_id).where(
                JobSourceLink.source == source,
                JobSourceLink.source_job_id == source_job_id,
            )
        )
        row = result.first()
        return int(row[0]) if row else None

    async def list_jobs(
        self,
        *,
        user_id: int,
        q: str | None = None,
        source: str | None = None,
        is_remote: bool | None = None,
        min_salary: float | None = None,
        job_type: str | None = None,
        location_contains: str | None = None,
        company_contains: str | None = None,
        company_exact: str | None = None,
        shift_type: str | None = None,
        source_type: str | None = None,
        imported_only: bool = False,
        archive_state: str = "active",
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[Job], int, str | None]:
        """List jobs visible to *user_id*.

        Visibility:
        - Public jobs (``visibility = 'public'``) are visible to everyone.
        - Private jobs (``visibility = 'private'``) are visible ONLY to their
          ``created_by_user_id``.

        ``archive_state='active'`` excludes this user's archived jobs (the
        default); ``archive_state='archived'`` returns only those jobs.
        """
        # LEFT JOIN user_jobs so we can filter per-user status and enrich with
        # relationship metadata.
        visibility_filter = or_(
            Job.visibility == "public",
            Job.created_by_user_id == user_id,
        )
        filters: list[Any] = [Job.status == "active", visibility_filter]

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
        if min_salary is not None:
            filters.append(Job.salary_max >= min_salary)
        if shift_type is not None:
            filters.append(Job.shift_type == shift_type)
        if job_type is not None:
            filters.append(func.lower(Job.job_type) == job_type.lower())
        if location_contains is not None:
            filters.append(Job.location.ilike(f"%{location_contains}%"))
        if company_exact is not None:
            # ``company_exact`` powers the public employer-card link.  Keep its
            # destination count aligned with the public-only summary and avoid
            # surfacing a user's similarly named private imports in that view.
            filters.extend(
                [
                    Job.visibility == "public",
                    _normalized_company_expression(Job.company)
                    == _normalize_company_name(company_exact),
                ]
            )
        elif company_contains is not None:
            filters.append(Job.company.ilike(f"%{company_contains}%"))
        if source_type == "global":
            filters.append(Job.visibility == "public")
        elif source_type == "imported":
            filters.append(Job.visibility == "private")
        elif source_type == "manual":
            filters.append(and_(Job.visibility == "private", Job.ingest_source == "manual"))
        elif source_type == "extension":
            filters.append(and_(Job.visibility == "private", Job.ingest_source == "extension"))
        if imported_only:
            filters.append(Job.visibility == "private")

        archived_job_ids = select(UserJob.job_id).where(
            UserJob.user_id == user_id,
            UserJob.status == "archived",
        )
        if archive_state == "archived":
            filters.append(Job.id.in_(archived_job_ids))
        else:
            filters.append(Job.id.not_in(archived_job_ids))

        count_stmt = select(func.count()).select_from(Job).where(*filters)
        total = int((await self._session.execute(count_stmt)).scalar_one())

        # Cursor-based pagination using compound key (sort_ts, id)
        sort_ts = func.coalesce(Job.last_seen_at, Job.scraped_at, Job.fetched_at)
        if cursor:
            cursor_ts, cursor_id = _decode_cursor(cursor)
            filters.append(tuple_(sort_ts, Job.id) < (cursor_ts, cursor_id))

        list_stmt = (
            select(Job)
            .where(*filters)
            .order_by(sort_ts.desc(), Job.id.desc())
            .limit(limit + 1)  # fetch one extra to know if there's a next page
        )
        rows = list((await self._session.execute(list_stmt)).scalars().all())

        # Determine next_cursor
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        next_cursor: str | None = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = _encode_cursor(
                sort_ts=last.last_seen_at or last.scraped_at or last.fetched_at,
                job_id=last.id,
            )

        return rows, total, next_cursor

    async def count_active_public_jobs_by_company(self, *, user_id: int, company: str) -> int:
        """Count active public, non-archived catalog jobs for an exact company name.

        This deliberately mirrors the default Jobs feed's active/archive rules,
        while limiting the result to public jobs so private imports cannot be
        inferred from a detail page.
        """
        normalized_company = _normalize_company_name(company)
        if not normalized_company:
            return 0

        archived_job_ids = select(UserJob.job_id).where(
            UserJob.user_id == user_id,
            UserJob.status == "archived",
        )
        statement = select(func.count()).select_from(Job).where(
            Job.status == "active",
            Job.visibility == "public",
            Job.id.not_in(archived_job_ids),
            _normalized_company_expression(Job.company) == normalized_company,
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def list_imported_jobs(
        self,
        *,
        user_id: int,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[tuple[Job, Any, Any]], int, str | None]:
        """Return only jobs the user imported (manual or extension).

        Cursor-based pagination using ``(UserJob.created_at, Job.id)``.
        """
        filters: list[Any] = [
            UserJob.user_id == user_id,
            UserJob.relationship_source.in_(["manual", "extension"]),
            Job.status == "active",
        ]

        if cursor:
            cursor_ts, cursor_id = _decode_cursor(cursor)
            filters.append(tuple_(UserJob.created_at, Job.id) < (cursor_ts, cursor_id))

        count_stmt = (
            select(func.count())
            .select_from(Job)
            .join(UserJob, UserJob.job_id == Job.id)
            .where(*filters)
        )
        total = int((await self._session.execute(count_stmt)).scalar_one())

        list_stmt = (
            select(Job, UserJob.match_score, UserJob.match_reason, UserJob.created_at)
            .join(UserJob, UserJob.job_id == Job.id)
            .where(*filters)
            .order_by(UserJob.created_at.desc(), Job.id.desc())
            .limit(limit + 1)
        )
        rows = list((await self._session.execute(list_stmt)).all())

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        next_cursor: str | None = None
        if has_more and rows:
            last_job = rows[-1][0]
            last_created_at = rows[-1][3]  # UserJob.created_at (4th column)
            next_cursor = _encode_cursor(
                sort_ts=last_created_at,
                job_id=last_job.id,
            )

        return rows, total, next_cursor
