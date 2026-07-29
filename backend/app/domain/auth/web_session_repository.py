"""Repository for ``WebRefreshSession`` — create, rotate, revoke, detect reuse."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.web_refresh_session import WebRefreshSession


def _hash_token(token: str) -> str:
    """SHA-256 hex digest of *token* (same pattern as extension sessions)."""
    return hashlib.sha256(token.encode()).hexdigest()


class WebSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- create ------------------------------------------------------------

    async def create_session(
        self, *, user_id: int, refresh_token: str
    ) -> WebRefreshSession:
        """Create a new session for a freshly-issued refresh token."""
        session = WebRefreshSession(
            user_id=user_id,
            refresh_token_hash=_hash_token(refresh_token),
            token_family=str(uuid4()),
        )
        self._session.add(session)
        await self._session.flush()
        await self._session.refresh(session)
        return session

    # -- lookup ------------------------------------------------------------

    async def find_by_token(self, refresh_token: str) -> WebRefreshSession | None:
        """Find a (possibly revoked) session by its refresh-token hash."""
        token_hash = _hash_token(refresh_token)
        result = await self._session.execute(
            select(WebRefreshSession).where(
                WebRefreshSession.refresh_token_hash == token_hash
            )
        )
        return result.scalar_one_or_none()

    # -- rotate ------------------------------------------------------------

    async def rotate(
        self, session: WebRefreshSession, new_refresh_token: str
    ) -> WebRefreshSession:
        """Rotate *session*: mark it used, revoke it, then create a new
        session row in the same token family with the new hash."""
        now = datetime.now(UTC)
        # Record last usage before revoking.
        session.last_used_at = now
        session.revoked_at = now
        # Create a new session row in the same family.
        new_session = WebRefreshSession(
            user_id=session.user_id,
            refresh_token_hash=_hash_token(new_refresh_token),
            token_family=session.token_family,
        )
        self._session.add(new_session)
        await self._session.flush()
        await self._session.refresh(new_session)
        return new_session

    # -- revoke ------------------------------------------------------------

    async def revoke_session(self, session: WebRefreshSession) -> None:
        """Revoke a single session (logout)."""
        session.revoked_at = datetime.now(UTC)
        await self._session.flush()

    async def revoke_family(self, token_family: str) -> int:
        """Revoke ALL sessions in *token_family* (reuse detected).

        Returns the number of rows revoked.
        """
        result = await self._session.execute(
            update(WebRefreshSession)
            .where(
                WebRefreshSession.token_family == token_family,
                WebRefreshSession.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        await self._session.flush()
        return cast(CursorResult[Any], result).rowcount or 0

    async def revoke_all_for_user(self, user_id: int) -> int:
        """Revoke all active sessions for *user_id* (password reset)."""
        result = await self._session.execute(
            update(WebRefreshSession)
            .where(
                WebRefreshSession.user_id == user_id,
                WebRefreshSession.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        await self._session.flush()
        return cast(CursorResult[Any], result).rowcount or 0
