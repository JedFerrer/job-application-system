"""Preferences domain exceptions."""

from __future__ import annotations


class PreferenceError(Exception):
    """Base class for preference-domain errors."""


class PreferenceNotFoundError(PreferenceError):
    """Raised when a user has not saved any job preferences yet."""

    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        super().__init__(f"No job preferences saved for user {user_id}")


class PreferenceValidationError(PreferenceError):
    """Raised when submitted preferences fail business validation."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class RefreshCooldownError(PreferenceError):
    """Raised when an on-demand refresh is requested before the cooldown ends."""

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"Refresh on cooldown; retry in {retry_after_seconds} seconds"
        )
