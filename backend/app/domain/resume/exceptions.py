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


class ResumeImportDraftNotFoundError(ResumeError):
    """Raised when an owned resume is not a current import draft."""

    def __init__(self, resume_id: int) -> None:
        self.resume_id = resume_id
        super().__init__(f"Resume import draft {resume_id} not found")


class TailoredResumeDraftNotFoundError(ResumeError):
    def __init__(self, draft_id: int) -> None:
        self.draft_id = draft_id
        super().__init__(f"Tailored resume draft {draft_id} not found")
