"""Domain exceptions for the resume module.

Services raise these; route handlers / global handlers translate them to HTTP.
"""

from __future__ import annotations


class ResumeError(Exception):
    """Base class for resume domain errors."""


class UnsupportedResumeFormatError(ResumeError):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class ResumeParseError(ResumeError):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class ProfileNotFoundError(ResumeError):
    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        super().__init__(f"No active profile for user {user_id}")
