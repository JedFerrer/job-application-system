"""Extension auth service — pairing code flow, token management, session lifecycle.

The extension auth flow is separate from the web app's cookie-based flow:
  1. Extension requests a pairing code (via web app, not this service)
  2. User enters the code in the extension popup
  3. Extension calls /complete with the code → gets access + refresh tokens
  4. Extension stores tokens in chrome.storage.session
  5. Refresh rotates tokens; logout revokes the session.

Pairing codes are stored in Redis with automatic TTL expiry. Falls back to
an in-memory dict when Redis is unavailable (dev-only safety net).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.security import TokenError, create_token
from app.domain.auth.exceptions import InvalidTokenError
from app.domain.extension.exceptions import (
    ExtensionSessionRevokedError,
    PairingCodeExpiredError,
    PairingCodeInvalidError,
)
from app.domain.extension.repository import ExtensionRepository
from app.domain.extension.schemas import (
    ExtensionPairingCompleteResponse,
    ExtensionRefreshResponse,
    ExtensionUserResponse,
)

if TYPE_CHECKING:
    import redis.asyncio as aioredis

logger = get_logger("extension_auth")

# Redis key prefix for pairing codes
_PAIRING_KEY_PREFIX = "pairing:"

# In-memory fallback store (only used when Redis is unavailable).
# Key: pairing code → Value: (user_id, expires_at)
_pairing_codes_fallback: dict[str, tuple[int, datetime]] = {}


class ExtensionAuthService:
    def __init__(
        self,
        *,
        repository: ExtensionRepository,
        settings: Settings,
        redis: aioredis.Redis | None = None,
    ) -> None:
        self._repo = repository
        self._settings = settings
        self._redis = redis
        self._pairing_code_ttl_seconds = 300  # 5 minutes

    # ── Pairing Code Management (Redis-backed) ──────────────────────────

    async def generate_pairing_code(self, *, user_id: int) -> tuple[str, int]:
        """Generate a one-time pairing code for the given user.

        Stored in Redis with automatic TTL expiry. Falls back to in-memory
        dict when Redis is not available.

        Returns (code, expires_in_seconds).
        """
        code = secrets.token_hex(4)  # 8-character hex code
        key = f"{_PAIRING_KEY_PREFIX}{code}"

        if self._redis is not None:
            # Redis: store user_id with TTL (atomic set + expire via SETEX)
            await self._redis.setex(
                key,
                self._pairing_code_ttl_seconds,
                str(user_id),
            )
        else:
            # In-memory fallback
            now = datetime.now(UTC)
            # Clean expired entries
            expired = [
                c
                for c, (_, exp) in _pairing_codes_fallback.items()
                if exp < now
            ]
            for c in expired:
                del _pairing_codes_fallback[c]
            expires_at = now + timedelta(seconds=self._pairing_code_ttl_seconds)
            _pairing_codes_fallback[code] = (user_id, expires_at)

        logger.info("extension.pairing_code_generated", user_id=user_id)
        return code, self._pairing_code_ttl_seconds

    async def consume_pairing_code(self, code: str) -> int:
        """Validate and consume a pairing code, returning the user_id.

        One-time use: the code is deleted after successful consumption.

        Raises PairingCodeExpiredError or PairingCodeInvalidError.
        """
        key = f"{_PAIRING_KEY_PREFIX}{code}"

        if self._redis is not None:
            # Atomic get + delete via GETDEL (Redis 6.2+)
            raw = await self._redis.getdel(key)
            if raw is None:
                # Check if it existed but expired separately
                raise PairingCodeInvalidError from None
            try:
                return int(raw)
            except (ValueError, TypeError):
                raise PairingCodeInvalidError from None
        else:
            # In-memory fallback
            entry = _pairing_codes_fallback.get(code)
            if entry is None:
                raise PairingCodeInvalidError

            user_id, expires_at = entry
            if datetime.now(UTC) > expires_at:
                del _pairing_codes_fallback[code]
                raise PairingCodeExpiredError

            # One-time use: delete after consumption
            del _pairing_codes_fallback[code]
            return user_id

    # ── Session Management ──────────────────────────────────────────────

    async def complete_pairing(
        self,
        code: str,
        *,
        device_name: str | None = None,
        browser: str | None = None,
    ) -> ExtensionPairingCompleteResponse:
        """Exchange a pairing code for extension tokens + session."""
        user_id = await self.consume_pairing_code(code)

        # Create the stable session before issuing the final token carrying its ID.
        refresh_hash = hashlib.sha256(secrets.token_bytes(32)).hexdigest()

        session = await self._repo.create_session(
            user_id=user_id,
            refresh_token_hash=refresh_hash,
            device_name=device_name,
            browser=browser,
        )

        # Issue tokens carrying the session id so the extension dependency can
        # verify the session is still active on every request.
        session_claim = {"session_id": str(session.id)}
        access_token = create_token(
            settings=self._settings,
            subject=user_id,
            token_type="access",  # noqa: S106 - token kind
            extra_claims=session_claim,
        )
        # Re-issue refresh token with the same session claim for consistency.
        refresh_token = create_token(
            settings=self._settings,
            subject=user_id,
            token_type="refresh",  # noqa: S106 - token kind
            extra_claims=session_claim,
        )
        # Keep the legacy column synchronized for compatibility with older readers.
        new_refresh_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        session.refresh_token_hash = new_refresh_hash
        await self._repo.create_refresh_token(
            session_id=str(session.id),
            token_hash=new_refresh_hash,
            token_family=str(uuid4()),
            expires_at=datetime.now(UTC)
            + timedelta(days=self._settings.refresh_token_expire_days),
        )
        await self._repo.touch_session(str(session.id))

        logger.info("extension.pairing_complete", user_id=user_id, session_id=session.id)

        return ExtensionPairingCompleteResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=self._settings.access_token_expire_minutes * 60,
            user=ExtensionUserResponse(
                id=user_id,
                email="",  # Will be populated by the API layer
                name="",
            ),
            session_id=str(session.id),
        )

    async def refresh(
        self,
        refresh_token: str,
    ) -> ExtensionRefreshResponse:
        """Rotate extension tokens using a refresh token."""
        # Decode and verify the refresh token — we need the full payload to
        # extract the session_id claim.
        from app.core.security import decode_token_payload

        try:
            payload = decode_token_payload(
                settings=self._settings,
                token=refresh_token,
                expected_type="refresh",
            )
        except TokenError as exc:
            raise InvalidTokenError from exc
        try:
            user_id = int(payload["sub"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidTokenError from exc
        claimed_session_id = payload.get("session_id")
        if claimed_session_id is not None and (
            not isinstance(claimed_session_id, str) or not claimed_session_id
        ):
            raise InvalidTokenError

        token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        token_record = await self._repo.find_refresh_token(token_hash, for_update=True)
        if token_record is None:
            raise InvalidTokenError

        session_id = str(token_record.extension_session_id)
        if claimed_session_id is not None and claimed_session_id != session_id:
            raise InvalidTokenError
        session = await self._repo.get_session(token_record.extension_session_id)
        if session is None or session.user_id != user_id:
            raise InvalidTokenError

        if (
            token_record.used_at is not None
            or token_record.revoked_at is not None
            or session.revoked_at is not None
        ):
            await self._repo.revoke_session(str(session.id))
            raise ExtensionSessionRevokedError

        if token_record.expires_at <= datetime.now(UTC):
            raise InvalidTokenError

        # Issue new token pair carrying the same session id.
        claims = {"session_id": str(session.id)}
        new_access = create_token(
            settings=self._settings,
            subject=user_id,
            token_type="access",  # noqa: S106 - token kind
            extra_claims=claims,
        )
        new_refresh = create_token(
            settings=self._settings,
            subject=user_id,
            token_type="refresh",  # noqa: S106 - token kind
            extra_claims=claims,
        )

        # Atomically consume the old record and create the successor in its family.
        new_hash = hashlib.sha256(new_refresh.encode()).hexdigest()
        session.refresh_token_hash = new_hash
        await self._repo.rotate_refresh_token(
            token_record,
            new_hash=new_hash,
            expires_at=datetime.now(UTC)
            + timedelta(days=self._settings.refresh_token_expire_days),
        )
        await self._repo.touch_session(str(session.id))

        return ExtensionRefreshResponse(
            access_token=new_access,
            refresh_token=new_refresh,
            expires_in=self._settings.access_token_expire_minutes * 60,
        )

    async def logout(self, session_id: str) -> None:
        """Revoke an extension session."""
        await self._repo.revoke_session(session_id)
        logger.info("extension.session_revoked", session_id=session_id)
