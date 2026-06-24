"""Interview prep domain exceptions."""

from __future__ import annotations


class InterviewError(Exception):
    """Base class for interview-domain errors."""


class InterviewGenerationError(InterviewError):
    """Raised when the LLM fails to produce usable interview questions."""

    def __init__(self, message: str = "Failed to generate interview prep") -> None:
        self.message = message
        super().__init__(message)
