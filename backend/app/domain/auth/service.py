"""Authentication service — register, login, Google sign-in, token issuance.

Business rules live here; the API layer only translates HTTP <-> domain. All
methods raise domain exceptions (never ``HTTPException``).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode

import redis.asyncio as aioredis
from google.auth.exceptions import GoogleAuthError as _GoogleVerifyError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from app.core.config import Settings
from app.core.security import (
    TokenError,
    create_token,
    decode_token,
    decode_token_payload,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.domain.auth.exceptions import (
    EmailAlreadyRegisteredError,
    GoogleAuthError,
    InactiveUserError,
    InvalidCredentialsError,
    InvalidTokenError,
)
from app.domain.auth.repository import UserRepository
from app.domain.auth.web_session_repository import WebSessionRepository
from app.domain.extension.repository import ExtensionRepository
from app.infrastructure.email_service import EmailProvider
from app.models.user import User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    user: User


class AuthService:
    def __init__(
        self,
        repository: UserRepository,
        settings: Settings,
        web_sessions: WebSessionRepository | None = None,
        extension_sessions: ExtensionRepository | None = None,
    ) -> None:
        self._repo = repository
        self._settings = settings
        self._sessions = web_sessions
        self._extension_sessions = extension_sessions

    # -- token helpers -----------------------------------------------------

    def _issue(self, user: User) -> IssuedTokens:
        return IssuedTokens(
            access_token=create_token(
                settings=self._settings, subject=user.id, token_type="access"  # noqa: S106 - token kind
            ),
            refresh_token=create_token(
                settings=self._settings, subject=user.id, token_type="refresh"  # noqa: S106 - token kind
            ),
            expires_in=self._settings.access_token_expire_minutes * 60,
            user=user,
        )

    async def _issue_and_track(self, user: User) -> IssuedTokens:
        """Issue tokens and persist a refresh session when session storage is
        available."""
        tokens = self._issue(user)
        if self._sessions is not None:
            await self._sessions.create_session(
                user_id=user.id, refresh_token=tokens.refresh_token
            )
        return tokens

    # -- email + password --------------------------------------------------

    async def register(self, *, email: str, name: str, password: str) -> IssuedTokens:
        existing = await self._repo.get_by_email(email)
        if existing is not None:
            # Public registration must never attach credentials to an existing
            # account. Adding a password to an OAuth account requires an
            # authenticated, ownership-verified account-settings flow.
            raise EmailAlreadyRegisteredError(email)

        user = await self._repo.create(
            email=email, name=name, hashed_password=hash_password(password)
        )
        return await self._issue_and_track(user)

    async def authenticate(self, *, email: str, password: str) -> IssuedTokens:
        user = await self._repo.get_by_email(email)
        if user is None or user.hashed_password is None:
            raise InvalidCredentialsError
        if not verify_password(password, user.hashed_password):
            raise InvalidCredentialsError
        if not user.is_active:
            raise InactiveUserError

        # Transparently upgrade the hash if argon2 parameters changed.
        if needs_rehash(user.hashed_password):
            await self._repo.update_password(
                user, hashed_password=hash_password(password)
            )
        return await self._issue_and_track(user)

    # -- Google ------------------------------------------------------------

    async def google_auth(self, *, id_token_str: str) -> IssuedTokens:
        if not self._settings.google_client_id:
            raise GoogleAuthError("Google sign-in is not configured")

        try:
            claims = google_id_token.verify_oauth2_token(
                id_token_str,
                google_requests.Request(),
                self._settings.google_client_id,
                clock_skew_in_seconds=30,
            )  # type: ignore[no-untyped-call]
        except (ValueError, _GoogleVerifyError) as exc:
            logger.warning("Google token verification failed: %s", exc)
            raise GoogleAuthError from exc

        google_id = claims.get("sub")
        email = claims.get("email")
        if not google_id or not email or not claims.get("email_verified", False):
            raise GoogleAuthError("Google account email is not verified")

        name = claims.get("name") or email.split("@")[0]
        avatar_url = claims.get("picture")

        user = await self._repo.get_by_google_id(google_id)
        if user is None:
            # Link to an existing password account with the same e-mail, or
            # create a fresh Google-only account.
            existing = await self._repo.get_by_email(email)
            if existing is not None:
                user = await self._repo.link_google(
                    existing, google_id=google_id, avatar_url=avatar_url
                )
            else:
                user = await self._repo.create(
                    email=email,
                    name=name,
                    google_id=google_id,
                    avatar_url=avatar_url,
                )

        if not user.is_active:
            raise InactiveUserError
        return await self._issue_and_track(user)

    # -- refresh -----------------------------------------------------------

    async def refresh(self, *, refresh_token: str) -> IssuedTokens:
        # ALWAYS cryptographically verify the JWT first — even when session
        # storage is available.  This ensures expiry, signature, issuer,
        # audience, and token-type are validated BEFORE the hash lookup.
        try:
            user_id = decode_token(
                settings=self._settings,
                token=refresh_token,
                expected_type="refresh",
            )
        except TokenError as exc:
            raise InvalidTokenError from exc

        # If session storage is available, cross-check the verified token
        # against persisted sessions (rotation, revocation, reuse detection).
        if self._sessions is not None:
            session = await self._sessions.find_by_token(
                refresh_token, for_update=True
            )
            if session is None:
                raise InvalidTokenError

            # Ensure the JWT subject matches the session's user.
            if session.user_id != user_id:
                logger.warning(
                    "refresh.user_mismatch jwt_sub=%s session_user=%s session_id=%s",
                    user_id,
                    session.user_id,
                    session.id,
                )
                raise InvalidTokenError

            if session.revoked_at is not None:
                now = datetime.now(UTC)
                if (
                    session.last_used_at is not None
                    and (now - session.last_used_at).total_seconds()
                    <= self._settings.web_refresh_reuse_grace_seconds
                ):
                    raise InvalidTokenError

                # Reuse detected — revoke the entire token family.
                session_id = session.id
                token_family = session.token_family
                session_user_id = session.user_id
                await self._sessions.revoke_family_persisted(token_family)
                logger.warning(
                    "refresh.reuse_detected session_id=%s family=%s user_id=%s",
                    session_id,
                    token_family,
                    session_user_id,
                )
                raise InvalidTokenError

            # Verify the user still exists and is active.
            user = await self._repo.get_by_id(user_id)
            if user is None or not user.is_active:
                raise InvalidTokenError

            # Rotate: issue new tokens, update the session with the new hash.
            tokens = self._issue(user)
            await self._sessions.rotate(session, tokens.refresh_token)
            return tokens

        # Fallback: stateless JWT (already decoded above).  Backward-
        # compatible when no session repository is configured.
        user = await self._repo.get_by_id(user_id)
        if user is None or not user.is_active:
            raise InvalidTokenError
        return self._issue(user)

    # -- logout ------------------------------------------------------------

    async def logout(self, *, refresh_token: str) -> None:
        """Revoke the refresh session for *refresh_token* (if tracked)."""
        if self._sessions is None:
            return
        session = await self._sessions.find_by_token(refresh_token)
        if session is not None:
            await self._sessions.revoke_session(session)

    # -- password reset ----------------------------------------------------

    async def forgot_password(
        self,
        *,
        email: str,
        email_provider: EmailProvider | None = None,
        cooldown_redis: aioredis.Redis | None = None,
    ) -> dict[str, str]:
        """Generate a password-reset token and optionally deliver its link."""
        user = await self._repo.get_by_email(email)
        if user is None or user.hashed_password is None or not getattr(user, "is_active", True):
            # Don't reveal whether the email exists — always return success.
            return {"message": "If the email is registered, a reset link has been sent."}

        if (
            self._settings.email_delivery_mode == "resend"
            and cooldown_redis is not None
            and self._settings.password_reset_email_cooldown_seconds > 0
        ):
            normalized_email = email.strip().lower()
            digest = hashlib.sha256(normalized_email.encode("utf-8")).hexdigest()
            claimed = await cooldown_redis.set(
                f"password-reset-cooldown:{digest}",
                "1",
                ex=self._settings.password_reset_email_cooldown_seconds,
                nx=True,
            )
            if not claimed:
                logger.info("password_reset_delivery_cooldown_applied")
                return {"message": "If the email is registered, a reset link has been sent."}

        reset_version = await self._repo.advance_password_reset_version(user_id=user.id)
        if reset_version is None:
            return {"message": "If the email is registered, a reset link has been sent."}

        token = create_token(
            settings=self._settings,
            subject=user.id,
            token_type="reset",  # noqa: S106 - token kind
            extra_claims={"reset_version": reset_version},
        )
        is_dev = self._settings.environment == "development"
        response: dict[str, str] = {
            "message": "If the email is registered, a reset link has been sent.",
        }
        if is_dev and self._settings.email_delivery_mode == "console":
            # Development-only: include reset_url for convenience.
            response["reset_url"] = (
                f"{self._settings.password_reset_url}?{urlencode({'token': token})}"
            )
        elif email_provider is not None:
            reset_url = (
                f"{self._settings.password_reset_url}?{urlencode({'token': token})}"
            )
            await email_provider.send_password_reset(
                to_email=email,
                reset_url=reset_url,
                user_id=user.id,
                reset_version=reset_version,
            )
        # else: no email provider configured — generic message only (safe).
        return response

    async def reset_password(self, *, token_str: str, new_password: str) -> None:
        """Validate a reset token, update the password, and revoke all
        existing refresh sessions for the user (forcing re-login everywhere)."""
        try:
            payload = decode_token_payload(
                settings=self._settings,
                token=token_str,
                expected_type="reset",
            )
            subject = payload.get("sub")
            reset_version = payload.get("reset_version")
            if isinstance(reset_version, bool) or not isinstance(reset_version, int):
                raise TokenError("invalid reset version")
            if not isinstance(subject, str) or not subject.isdigit():
                raise TokenError("invalid subject")
            user_id = int(subject)
            if reset_version < 0:
                raise TokenError("invalid reset version")
        except (TokenError, TypeError, ValueError) as exc:
            raise InvalidTokenError from exc

        updated_user_id = await self._repo.consume_password_reset(
            user_id=user_id,
            reset_version=reset_version,
            hashed_password=hash_password(new_password),
        )
        if updated_user_id is None:
            raise InvalidTokenError

        # Revoke all web refresh sessions so old tokens can't be used.
        if self._sessions is not None:
            count = await self._sessions.revoke_all_for_user(updated_user_id)
            if count:
                logger.info(
                    "password_reset.sessions_revoked user_id=%s count=%s",
                    updated_user_id,
                    count,
                )
        if self._extension_sessions is not None:
            count = await self._extension_sessions.revoke_all_for_user(updated_user_id)
            if count:
                logger.info(
                    "password_reset.extension_sessions_revoked user_id=%s count=%s",
                    updated_user_id,
                    count,
                )
