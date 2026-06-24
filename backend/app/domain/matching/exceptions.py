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
