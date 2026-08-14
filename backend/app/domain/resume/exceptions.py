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


class ResumeTextTooLongError(ResumeError):
    """Raised when extracted resume text exceeds the application limit."""

    def __init__(self, actual_chars: int, max_chars: int) -> None:
        self.actual_chars = actual_chars
        self.max_chars = max_chars
        super().__init__(
            f"This resume contains {actual_chars} extracted characters, "
            f"exceeding the {max_chars:,}-character import limit. "
            "The resume was not imported."
        )


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


class TailoredResumeSnapshotInvalidError(ResumeError):
    """A submitted fresh source-backed snapshot failed canonical validation."""

    def __init__(self) -> None:
        # Keep this deliberately non-specific: the response must not reveal
        # whether a submitted source identifier exists or belongs to anyone.
        super().__init__("The tailored resume is no longer valid; please regenerate it.")


class ResumeSourceLayoutUnavailableError(ResumeError):
    """The verified source ledger exists but semantic tailoring layout is unavailable."""

    def __init__(self) -> None:
        super().__init__(
            "Canonical source layout is unavailable; source-based tailoring cannot run."
        )
