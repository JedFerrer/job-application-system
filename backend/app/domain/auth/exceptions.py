"""Authentication domain exceptions.

Services raise these domain errors; the API layer maps them to HTTP responses
via registered exception handlers (never raise ``HTTPException`` in services).
"""

from __future__ import annotations


class AuthError(Exception):
    """Base class for authentication-domain errors."""


class EmailAlreadyRegisteredError(AuthError):
    """Raised when registering an e-mail that already has a password account."""

    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__("Email already registered")


class InvalidCredentialsError(AuthError):
    """Raised when login fails. Message is intentionally generic (no oracle)."""

    def __init__(self) -> None:
        super().__init__("Invalid email or password")


class InactiveUserError(AuthError):
    """Raised when a deactivated account attempts to authenticate."""

    def __init__(self) -> None:
        super().__init__("Account is inactive")


class InvalidTokenError(AuthError):
    """Raised when an access/refresh token is missing, expired, or invalid."""

    def __init__(self) -> None:
        super().__init__("Invalid or expired token")


class GoogleAuthError(AuthError):
    """Raised when a Google ID token cannot be verified."""

    def __init__(self, detail: str = "Could not verify Google credentials") -> None:
        self.detail = detail
        super().__init__(detail)
