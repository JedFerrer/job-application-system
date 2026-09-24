# ruff: noqa: E501
"""Application repository (Phase 4 minimal CRM)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, cast

from sqlalchemy import and_, case, delete, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.domain.applications.schemas import MANUAL_NEXT_ACTION_EVENT_TYPES
from app.models.application import Application
from app.models.application_artifact import ApplicationArtifact
from app.models.application_event import ApplicationEvent
from app.models.application_package import ApplicationPackage
from app.models.application_preparation import ApplicationPreparation
from app.models.application_review import ApplicationDocumentReview
from app.models.cover_letter import CoverLetter
from app.models.job import Job
from app.models.resume import TailoredResumeDraft
from app.models.user_job_match import UserJobMatch


class ApplicationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def artifacts_for_packages(self, *, user_id: int, package_ids: set[int]) -> dict[int, list[ApplicationArtifact]]:
        if not package_ids:
            return {}
        rows = list((await self._session.scalars(
            select(ApplicationArtifact).options(load_only(
                ApplicationArtifact.id, ApplicationArtifact.package_id,
                ApplicationArtifact.document_type, ApplicationArtifact.format,
                ApplicationArtifact.filename, ApplicationArtifact.byte_length,
                ApplicationArtifact.sha256, ApplicationArtifact.validation_state,
                ApplicationArtifact.failure_category, ApplicationArtifact.created_at,
                raiseload=True,
            )).where(
                ApplicationArtifact.user_id == user_id,
                ApplicationArtifact.package_id.in_(package_ids),
            ).order_by(ApplicationArtifact.created_at.desc(), ApplicationArtifact.id.desc())
        )).all())
        result: dict[int, list[ApplicationArtifact]] = {package_id: [] for package_id in package_ids}
        for row in rows:
            result[row.package_id].append(row)
        return result

    async def create_or_get(self, *, user_id: int, job_id: int) -> Application:
        """Idempotently create a ``saved`` application for ``(user, job)``.

        Uses ON CONFLICT so re-saving the same job returns the existing row
        instead of raising a unique-violation (never SELECT-then-INSERT).
        """
        stmt = pg_insert(Application).values(user_id=user_id, job_id=job_id, status="saved")
        upsert: Any = stmt.on_conflict_do_update(
            index_elements=[Application.user_id, Application.job_id],
            set_={
                "status": case(
                    (Application.status == "closed", "saved"),
                    else_=Application.status,
                ),
                "updated_at": datetime.now(UTC),
            },
        ).returning(Application)
        result = await self._session.execute(upsert)
        application: Application = result.scalar_one()
        return application

    async def get(self, *, user_id: int, application_id: int) -> Application | None:
        result = await self._session.execute(
            select(Application).where(
                Application.id == application_id,
                Application.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_for_update(self, *, user_id: int, application_id: int) -> Application | None:
        result = await self._session.execute(
            select(Application)
            .where(
                Application.id == application_id,
                Application.user_id == user_id,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def workspace_rows(
        self, *, user_id: int, application_id: int, history_limit: int = 10
    ) -> tuple[Application, Job | None, ApplicationPreparation | None, UserJobMatch | None,
                TailoredResumeDraft | None, CoverLetter | None, ApplicationPackage | None,
                ApplicationPackage | None, list[ApplicationPackage]] | None:
        """Load bounded owned workspace records without feed-visibility filtering."""
        row = (
            await self._session.execute(
                select(Application, Job, ApplicationPreparation, UserJobMatch)
                .outerjoin(Job, Job.id == Application.job_id)
                .outerjoin(
                    ApplicationPreparation,
                    ApplicationPreparation.application_id == Application.id,
                )
                .outerjoin(
                    UserJobMatch,
                    and_(
                        UserJobMatch.user_id == user_id,
                        UserJobMatch.job_id == Application.job_id,
                    ),
                )
                .where(Application.id == application_id, Application.user_id == user_id)
            )
        ).one_or_none()
        if row is None:
            return None
        application, job, preparation, match = row
        resume = letter = approved = reviewable = None
        if preparation is not None:
            if preparation.resume_draft_id is not None:
                resume = await self._session.scalar(
                    select(TailoredResumeDraft).where(
                        TailoredResumeDraft.id == preparation.resume_draft_id,
                        TailoredResumeDraft.user_id == user_id,
                    )
                )
            if preparation.cover_letter_id is not None:
                letter = await self._session.scalar(
                    select(CoverLetter).where(
                        CoverLetter.id == preparation.cover_letter_id,
                        CoverLetter.user_id == user_id,
                    )
                )
            if preparation.approved_package_id is not None:
                approved = await self._session.scalar(
                    select(ApplicationPackage).where(
                        ApplicationPackage.id == preparation.approved_package_id,
                        ApplicationPackage.application_id == application.id,
                    )
                )
            if preparation.reviewable_package_id is not None:
                reviewable = await self._session.scalar(
                    select(ApplicationPackage).where(
                        ApplicationPackage.id == preparation.reviewable_package_id,
                        ApplicationPackage.application_id == application.id,
                    )
                )
        history = list(
            (await self._session.execute(
                select(ApplicationPackage)
                .where(ApplicationPackage.application_id == application.id)
                .order_by(ApplicationPackage.created_at.desc(), ApplicationPackage.id.desc())
                .limit(history_limit)
            )).scalars().all()
        )
        return application, job, preparation, match, resume, letter, approved, reviewable, history

    async def current_document_review(self, *, user_id: int, package: ApplicationPackage | None, policy_version: str) -> ApplicationDocumentReview | None:
        if package is None:
            return None
        return cast(ApplicationDocumentReview | None, await self._session.scalar(select(ApplicationDocumentReview).where(
            ApplicationDocumentReview.user_id == user_id,
            ApplicationDocumentReview.package_id == package.id,
            ApplicationDocumentReview.package_content_sha256 == package.content_sha256,
            ApplicationDocumentReview.review_policy_version == policy_version,
        ).order_by(ApplicationDocumentReview.created_at.desc(), ApplicationDocumentReview.id.desc())))

    async def delete(self, *, application: Application) -> None:
        await self._session.execute(delete(Application).where(Application.id == application.id))
        await self._session.flush()

    async def create_event(
        self,
        *,
        user_id: int,
        application_id: int | None,
        job_id: int | None,
        event_type: str,
        metadata: dict[str, object] | None = None,
    ) -> ApplicationEvent:
        event = ApplicationEvent(
            user_id=user_id,
            application_id=application_id,
            job_id=job_id,
            event_type=event_type,
            metadata_=metadata or {},
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def list_manual_activity_events(
        self,
        *,
        user_id: int,
        application_id: int,
        limit: int,
    ) -> list[ApplicationEvent]:
        result = await self._session.execute(
            select(ApplicationEvent)
            .where(
                ApplicationEvent.user_id == user_id,
                ApplicationEvent.application_id == application_id,
                ApplicationEvent.event_type.in_(MANUAL_NEXT_ACTION_EVENT_TYPES),
            )
            .order_by(
                ApplicationEvent.created_at.desc(),
                ApplicationEvent.id.desc(),
            )
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_timeline_events(
        self, *, user_id: int, application_id: int, limit: int,
        cursor: tuple[datetime, str] | None = None,
    ) -> list[ApplicationEvent]:
        filters: list[Any] = [
            ApplicationEvent.user_id == user_id,
            ApplicationEvent.application_id == application_id,
        ]
        if cursor is not None:
            filters.append(
                tuple_(ApplicationEvent.created_at, ApplicationEvent.id) < cursor
            )
        result = await self._session.execute(
            select(ApplicationEvent).where(*filters).order_by(
                ApplicationEvent.created_at.desc(), ApplicationEvent.id.desc()
            ).limit(limit)
        )
        return list(result.scalars().all())

    async def update(
        self,
        *,
        application: Application,
        status: str | None,
        cover_letter_id: int | None,
        notes: str | None,
        next_action_type: str | None,
        next_action_note: str | None,
        next_action_due_date: date | None,
        next_action_completed_at: datetime | None,
        update_cover_letter: bool,
        update_notes: bool,
        update_next_action_type: bool,
        update_next_action_note: bool,
        update_next_action_due_date: bool,
        update_next_action_completed_at: bool,
    ) -> Application:
        if status is not None:
            application.status = status
        if update_cover_letter:
            application.cover_letter_id = cover_letter_id
        if update_notes:
            application.notes = notes
        if update_next_action_type:
            application.next_action_type = next_action_type
        if update_next_action_note:
            application.next_action_note = next_action_note
        if update_next_action_due_date:
            application.next_action_due_date = next_action_due_date
        if update_next_action_completed_at:
            application.next_action_completed_at = next_action_completed_at
        await self._session.flush()
        await self._session.refresh(application)
        return application

    async def list_with_jobs(
        self, *, user_id: int, cursor: str | None = None, limit: int = 50
    ) -> tuple[list[tuple[Application, Job]], int, str | None]:
        """Return applications for *user_id* with cursor-based pagination.

        Cursor is a base64-encoded ``updated_at.isoformat()|id`` pair.
        Returns ``(rows, total, next_cursor)``.
        """
        count = int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(Application)
                    .where(Application.user_id == user_id)
                )
            ).scalar_one()
        )

        filters: list[Any] = [Application.user_id == user_id]
        if cursor:
            cursor_ts, cursor_id = _decode_app_cursor(cursor)
            filters.append(
                __import__("sqlalchemy").tuple_(Application.updated_at, Application.id)
                < (cursor_ts, cursor_id)
            )

        rows_raw = (
            await self._session.execute(
                select(Application, Job)
                .join(Job, Job.id == Application.job_id)
                .where(*filters)
                .order_by(Application.updated_at.desc(), Application.id.desc())
                .limit(limit + 1)
            )
        ).all()

        has_more = len(rows_raw) > limit
        if has_more:
            rows_raw = rows_raw[:limit]

        rows = [(r[0], r[1]) for r in rows_raw]

        next_cursor: str | None = None
        if has_more and rows:
            last_app, _ = rows[-1]
            next_cursor = _encode_app_cursor(last_app.updated_at, last_app.id)

        return rows, count, next_cursor

    async def get_dashboard_status_counts(self, *, user_id: int) -> dict[str, int]:
        statuses = ("saved", "applied", "interview", "offer", "rejected", "closed")
        aggregates: list[Any] = [
            func.count(Application.id).filter(Application.status.in_(statuses))
        ]
        aggregates.extend(
            func.sum(case((Application.status == status, 1), else_=0)) for status in statuses
        )
        result = await self._session.execute(
            select(*aggregates).where(Application.user_id == user_id)
        )
        values = result.one()
        return {
            "total": int(values[0] or 0),
            **{status: int(values[index + 1] or 0) for index, status in enumerate(statuses)},
        }

    async def list_due_next_actions(
        self, *, user_id: int, due_by: date
    ) -> tuple[list[tuple[Application, Job]], int]:
        filters: list[Any] = [
            Application.user_id == user_id,
            Application.next_action_due_date.is_not(None),
            Application.next_action_due_date <= due_by,
            Application.next_action_completed_at.is_(None),
        ]

        count = int(
            (
                await self._session.execute(
                    select(func.count()).select_from(Application).where(*filters)
                )
            ).scalar_one()
        )

        rows_raw = (
            await self._session.execute(
                select(Application, Job)
                .join(Job, Job.id == Application.job_id)
                .where(*filters)
                .order_by(
                    Application.next_action_due_date.asc(),
                    Application.updated_at.desc(),
                    Application.id.desc(),
                )
            )
        ).all()

        return [(r[0], r[1]) for r in rows_raw], count

    async def list_action_center_dated(
        self, *, user_id: int, end_date: date
    ) -> list[tuple[Application, Job]]:
        filters: list[Any] = [
            Application.user_id == user_id,
            Application.next_action_due_date.is_not(None),
            Application.next_action_due_date <= end_date,
            Application.next_action_completed_at.is_(None),
        ]
        rows_raw = await self._session.execute(
            select(Application, Job).join(Job, Job.id == Application.job_id).where(*filters)
        )
        return [(row[0], row[1]) for row in rows_raw.all()]

    async def list_action_center_needs_next_action(
        self, *, user_id: int
    ) -> list[tuple[Application, Job]]:
        active_statuses = ("saved", "applied", "interview")
        no_current_incomplete_action = or_(
            Application.next_action_completed_at.is_not(None),
            and_(
                Application.next_action_type.is_(None),
                Application.next_action_note.is_(None),
                Application.next_action_due_date.is_(None),
            ),
        )
        rows_raw = await self._session.execute(
            select(Application, Job)
            .join(Job, Job.id == Application.job_id)
            .where(
                Application.user_id == user_id,
                Application.status.in_(active_statuses),
                no_current_incomplete_action,
            )
        )
        return [(row[0], row[1]) for row in rows_raw.all()]


# ── Cursor helpers (same pattern as job listing) ──────────────────────────


def _encode_app_cursor(updated_at: datetime, app_id: int) -> str:
    import base64

    raw = f"{updated_at.isoformat()}|{app_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_app_cursor(cursor: str) -> tuple[datetime, int]:
    import base64
    from datetime import UTC, datetime

    raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    ts_str, id_str = raw.rsplit("|", 1)
    return datetime.fromisoformat(ts_str).replace(tzinfo=UTC), int(id_str)
