"""Matching domain exceptions."""

from __future__ import annotations


class MatchingError(Exception):
    """Base class for matching-domain errors."""


class NoActiveProfileError(MatchingError):
    """Raised when ranking/explaining is attempted without a candidate profile."""


class MatchNotFoundError(MatchingError):
    def __init__(self, job_id: int) -> None:
        self.job_id = job_id
        super().__init__(f"No match found for job {job_id}")


class MatchNotReadyError(MatchingError):
    """Raised when a visible job has not finished embedding."""

    def __init__(self, job_id: int) -> None:
        self.job_id = job_id
        super().__init__(f"Job {job_id} is not ready for matching")


class MatchPreparingError(MatchNotReadyError):
    """Raised while another caller is preparing a job embedding."""


class MatchUnavailableError(MatchNotReadyError):
    """Raised when a visible job has no usable description to embed."""
