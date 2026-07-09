"""Password hashing (argon2) and JWT issuance/verification.

Security choices follow the backend instructions:

* Passwords use **argon2id** via ``argon2-cffi`` — never SHA/MD5.
* Access tokens are short-lived (15 min) and sent as ``Authorization: Bearer``.
* Refresh tokens are long-lived (7 days) and stored in an httpOnly cookie.
* Tokens carry ``iss``/``aud``/``exp``/``iat`` and a ``type`` claim so an access
  token can never be used where a refresh token is expected (and vice versa).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import Settings

_hasher = PasswordHasher()

TokenType = Literal["access", "refresh", "reset"]


class TokenError(Exception):
    """Raised when a JWT is malformed, expired, or has the wrong type."""


def hash_password(password: str) -> str:
    """Return an argon2id hash for ``password``."""
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Timing-safe verification of ``password`` against an argon2 ``hashed``."""
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    """True when the stored hash should be upgraded to current argon2 params."""
    return _hasher.check_needs_rehash(hashed)


def create_token(
    *,
    settings: Settings,
    subject: int,
    token_type: TokenType,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Create a signed JWT for ``subject`` (the user id).

    ``extra_claims`` are merged into the JWT payload (e.g. ``session_id`` for
    extension sessions).  Reserved claims (sub, type, jti, iss, aud, iat, exp)
    cannot be overridden via extra_claims.
    """
    now = datetime.now(UTC)
    if token_type == "access":
        expires = now + timedelta(minutes=settings.access_token_expire_minutes)
    elif token_type == "reset":
        expires = now + timedelta(minutes=15)
    else:
        expires = now + timedelta(days=settings.refresh_token_expire_days)

    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "jti": str(uuid4()),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    if extra_claims:
        # Merge extra claims — never allow overwriting reserved claims.
        reserved = {"sub", "type", "jti", "iss", "aud", "iat", "exp"}
        for k, v in extra_claims.items():
            if k in reserved:
                raise ValueError(
                    f"extra_claims key '{k}' is reserved and cannot be overridden"
                )
        payload.update(extra_claims)
    return jwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_token(
    *,
    settings: Settings,
    token: str,
    expected_type: TokenType,
) -> int:
    """Verify a JWT and return its subject (user id).

    Raises ``TokenError`` on any failure: bad signature, expiry, wrong
    issuer/audience, or a token-type mismatch.
    """
    payload = _decode_and_validate(settings, token, expected_type)
    subject = payload.get("sub")
    try:
        return int(subject)
    except (TypeError, ValueError) as exc:
        raise TokenError("invalid subject") from exc


def decode_token_payload(
    *,
    settings: Settings,
    token: str,
    expected_type: TokenType,
) -> dict[str, Any]:
    """Verify a JWT and return the full decoded payload dict.

    Use this when you need claims beyond ``sub`` (e.g. ``session_id`` for
    extension session validation).  Raises ``TokenError`` on any failure.
    """
    return _decode_and_validate(settings, token, expected_type)


def _decode_and_validate(
    settings: Settings,
    token: str,
    expected_type: TokenType,
) -> dict[str, Any]:
    """Shared decode + validation logic used by both decode variants."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.InvalidTokenError as exc:
        raise TokenError(str(exc)) from exc

    if payload.get("type") != expected_type:
        raise TokenError("unexpected token type")

    if not payload.get("sub"):
        raise TokenError("missing subject")

    return payload
