"""Authentication service — register, login, Google sign-in, token issuance.

Business rules live here; the API layer only translates HTTP <-> domain. All
methods raise domain exceptions (never ``HTTPException``).
"""

from __future__ import annotations

from dataclasses import dataclass

from google.auth.exceptions import GoogleAuthError as _GoogleVerifyError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from app.core.config import Settings
from app.core.security import (
    TokenError,
    create_token,
    decode_token,
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
from app.models.user import User


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    user: User


class AuthService:
    def __init__(self, repository: UserRepository, settings: Settings) -> None:
        self._repo = repository
        self._settings = settings

    # -- token helpers -----------------------------------------------------

    def _issue(self, user: User) -> IssuedTokens:
        return IssuedTokens(
            access_token=create_token(
                settings=self._settings, subject=user.id, token_type="access"
            ),
            refresh_token=create_token(
                settings=self._settings, subject=user.id, token_type="refresh"
            ),
            expires_in=self._settings.access_token_expire_minutes * 60,
            user=user,
        )

    # -- email + password --------------------------------------------------

    async def register(self, *, email: str, name: str, password: str) -> IssuedTokens:
        existing = await self._repo.get_by_email(email)
        if existing is not None and existing.hashed_password is not None:
            raise EmailAlreadyRegisteredError(email)

        if existing is not None:
            # Google-only account adding a password — attach, don't duplicate.
            user = await self._repo.update_password(
                existing, hashed_password=hash_password(password)
            )
        else:
            user = await self._repo.create(
                email=email, name=name, hashed_password=hash_password(password)
            )
        return self._issue(user)

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
        return self._issue(user)

    # -- Google ------------------------------------------------------------

    async def google_auth(self, *, id_token_str: str) -> IssuedTokens:
        if not self._settings.google_client_id:
            raise GoogleAuthError("Google sign-in is not configured")

        try:
            claims = google_id_token.verify_oauth2_token(
                id_token_str,
                google_requests.Request(),
                self._settings.google_client_id,
            )
        except (ValueError, _GoogleVerifyError) as exc:
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
        return self._issue(user)

    # -- refresh -----------------------------------------------------------

    async def refresh(self, *, refresh_token: str) -> IssuedTokens:
        try:
            user_id = decode_token(
                settings=self._settings,
                token=refresh_token,
                expected_type="refresh",
            )
        except TokenError as exc:
            raise InvalidTokenError from exc

        user = await self._repo.get_by_id(user_id)
        if user is None or not user.is_active:
            raise InvalidTokenError
        return self._issue(user)
