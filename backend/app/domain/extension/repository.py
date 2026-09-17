"""Extension repository — CRUD for extension sessions, events, and form sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.application_event import ApplicationEvent
from app.models.application_form_field import ApplicationFormField
from app.models.application_form_session import ApplicationFormSession
from app.models.extension_refresh_token import ExtensionRefreshToken
from app.models.extension_session import ExtensionSession


@dataclass(frozen=True)
class EventReservation:
    event: ApplicationEvent
    created: bool


_EVENT_ID_UNIQUE_CONSTRAINT = "uq_application_event_user_event_id"
_UNIQUE_VIOLATION_SQLSTATE = "23505"


def _is_event_id_unique_violation(exc: IntegrityError) -> bool:
    """Return whether ``exc`` is the event-id uniqueness violation we recover."""
    pending: list[BaseException] = [exc]
    visited: set[int] = set()
    has_unique_sqlstate = False
    has_event_id_constraint = False

    while pending and len(visited) < 4:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))

        sqlstate = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        has_unique_sqlstate = has_unique_sqlstate or sqlstate == _UNIQUE_VIOLATION_SQLSTATE
        has_event_id_constraint = has_event_id_constraint or (
            getattr(current, "constraint_name", None) == _EVENT_ID_UNIQUE_CONSTRAINT
        )

        for related in (
            getattr(current, "orig", None),
            current.__cause__,
            current.__context__,
        ):
            if isinstance(related, BaseException):
                pending.append(related)

    return has_unique_sqlstate and has_event_id_constraint


class ExtensionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Extension Sessions ──────────────────────────────────────────────

    async def create_session(
        self,
        *,
        user_id: int,
        refresh_token_hash: str,
        device_name: str | None = None,
        browser: str | None = None,
    ) -> ExtensionSession:
        session = ExtensionSession(
            user_id=user_id,
            refresh_token_hash=refresh_token_hash,
            device_name=device_name,
            browser=browser,
        )
        self._session.add(session)
        await self._session.flush()
        return session

    async def create_refresh_token(
        self,
        *,
        session_id: str,
        token_hash: str,
        token_family: str,
        expires_at: datetime,
    ) -> ExtensionRefreshToken:
        token = ExtensionRefreshToken(
            extension_session_id=session_id,
            token_hash=token_hash,
            token_family=token_family,
            expires_at=expires_at,
        )
        self._session.add(token)
        await self._session.flush()
        return token

    async def find_refresh_token(
        self, token_hash: str, *, for_update: bool = False
    ) -> ExtensionRefreshToken | None:
        query = select(ExtensionRefreshToken).where(
            ExtensionRefreshToken.token_hash == token_hash
        )
        if for_update:
            query = query.with_for_update()
        result = await self._session.execute(query)
        return result.scalar_one_or_none()

    async def rotate_refresh_token(
        self,
        token: ExtensionRefreshToken,
        *,
        new_hash: str,
        expires_at: datetime,
    ) -> ExtensionRefreshToken:
        now = datetime.now(UTC)
        token.used_at = now
        successor = ExtensionRefreshToken(
            extension_session_id=token.extension_session_id,
            token_hash=new_hash,
            token_family=token.token_family,
            expires_at=expires_at,
        )
        self._session.add(successor)
        await self._session.flush()
        return successor

    async def get_session(self, session_id: str) -> ExtensionSession | None:
        return await self._session.get(ExtensionSession, session_id)

    async def touch_session(self, session_id: str) -> None:
        await self._session.execute(
            update(ExtensionSession)
            .where(ExtensionSession.id == session_id)
            .values(last_used_at=datetime.now(UTC))
        )

    async def revoke_session(self, session_id: str) -> None:
        await self._session.execute(
            update(ExtensionSession)
            .where(ExtensionSession.id == session_id)
            .values(revoked_at=datetime.now(UTC))
        )
        await self._session.execute(
            update(ExtensionRefreshToken)
            .where(
                ExtensionRefreshToken.extension_session_id == session_id,
                ExtensionRefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )

    async def revoke_owned_session(self, *, session_id: str, user_id: int) -> bool:
        result = await self._session.execute(
            update(ExtensionSession)
            .where(
                ExtensionSession.id == session_id,
                ExtensionSession.user_id == user_id,
                ExtensionSession.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        if not cast(CursorResult[Any], result).rowcount:
            return False
        await self._session.execute(
            update(ExtensionRefreshToken)
            .where(
                ExtensionRefreshToken.extension_session_id == session_id,
                ExtensionRefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        return True

    async def revoke_all_for_user(self, user_id: int) -> int:
        result = await self._session.execute(
            update(ExtensionSession)
            .where(
                ExtensionSession.user_id == user_id,
                ExtensionSession.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        await self._session.execute(
            update(ExtensionRefreshToken)
            .where(
                ExtensionRefreshToken.revoked_at.is_(None),
                ExtensionRefreshToken.extension_session_id.in_(
                    select(ExtensionSession.id).where(ExtensionSession.user_id == user_id)
                ),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        return cast(CursorResult[Any], result).rowcount or 0

    async def list_user_sessions(self, user_id: int) -> list[ExtensionSession]:
        result = await self._session.execute(
            select(ExtensionSession)
            .where(ExtensionSession.user_id == user_id)
            .order_by(ExtensionSession.created_at.desc())
        )
        return list(result.scalars().all())

    # ── Application Events ──────────────────────────────────────────────

    async def create_event(
        self,
        *,
        user_id: int,
        event_type: str,
        application_id: int | None = None,
        job_id: int | None = None,
        source_url: str | None = None,
        page_url: str | None = None,
        metadata: dict[str, object] | None = None,
        attempt_id: str | None = None,
        event_id: str | None = None,
    ) -> ApplicationEvent:
        return (await self.reserve_event(
            user_id=user_id, event_type=event_type, application_id=application_id,
            job_id=job_id, source_url=source_url, page_url=page_url, metadata=metadata,
            attempt_id=attempt_id, event_id=event_id,
        )).event

    async def reserve_event(
        self,
        *,
        user_id: int,
        event_type: str,
        application_id: int | None = None,
        job_id: int | None = None,
        source_url: str | None = None,
        page_url: str | None = None,
        metadata: dict[str, object] | None = None,
        attempt_id: str | None = None,
        event_id: str | None = None,
    ) -> EventReservation:
        event = ApplicationEvent(
            user_id=user_id, application_id=application_id, job_id=job_id,
            event_type=event_type, source_url=source_url, page_url=page_url,
            metadata_=metadata or {}, attempt_id=attempt_id, event_id=event_id,
        )
        if event_id is None:
            self._session.add(event)
            await self._session.flush()
            return EventReservation(event=event, created=True)
        try:
            async with self._session.begin_nested():
                self._session.add(event)
                await self._session.flush()
            return EventReservation(event=event, created=True)
        except IntegrityError as exc:
            if not _is_event_id_unique_violation(exc):
                raise
            existing = await self.get_event_by_id(user_id=user_id, event_id=event_id)
            if existing is None:
                raise
            return EventReservation(event=existing, created=False)

    async def get_event_by_id(self, *, user_id: int, event_id: str) -> ApplicationEvent | None:
        result = await self._session.execute(select(ApplicationEvent).where(
            ApplicationEvent.user_id == user_id, ApplicationEvent.event_id == event_id
        ))
        return result.scalar_one_or_none()

    async def list_events_for_application(
        self, *, application_id: int, user_id: int
    ) -> list[ApplicationEvent]:
        result = await self._session.execute(
            select(ApplicationEvent)
            .where(
                ApplicationEvent.application_id == application_id,
                ApplicationEvent.user_id == user_id,
            )
            .order_by(ApplicationEvent.created_at.asc())
        )
        return list(result.scalars().all())

    # ── Form Sessions ───────────────────────────────────────────────────

    async def create_form_session(
        self,
        *,
        user_id: int,
        application_id: int | None = None,
        job_id: int | None = None,
        domain: str | None = None,
        ats_type: str | None = None,
        field_count: int = 0,
    ) -> ApplicationFormSession:
        session = ApplicationFormSession(
            user_id=user_id,
            application_id=application_id,
            job_id=job_id,
            domain=domain,
            ats_type=ats_type,
            field_count=field_count,
        )
        self._session.add(session)
        await self._session.flush()
        return session

    async def complete_form_session(
        self,
        session_id: str,
        *,
        filled_count: int,
        skipped_count: int,
        status: str = "completed",
    ) -> None:
        await self._session.execute(
            update(ApplicationFormSession)
            .where(ApplicationFormSession.id == session_id)
            .values(
                filled_count=filled_count,
                skipped_count=skipped_count,
                status=status,
                completed_at=datetime.now(UTC),
            )
        )

    async def add_form_field(
        self,
        *,
        form_session_id: str,
        field_key: str | None = None,
        field_label: str | None = None,
        field_type: str | None = None,
        profile_path: str | None = None,
        confidence: float | None = None,
        was_filled: bool = False,
        reason_skipped: str | None = None,
    ) -> ApplicationFormField:
        field = ApplicationFormField(
            form_session_id=form_session_id,
            field_key=field_key,
            field_label=field_label,
            field_type=field_type,
            profile_path=profile_path,
            confidence=confidence,
            was_filled=was_filled,
            reason_skipped=reason_skipped,
        )
        self._session.add(field)
        await self._session.flush()
        return field
