"""Cover-letter domain exceptions."""

from __future__ import annotations


class CoverLetterError(Exception):
    """Base class for cover-letter-domain errors."""


class CoverLetterNotFoundError(CoverLetterError):
    def __init__(self, cover_letter_id: int) -> None:
        self.cover_letter_id = cover_letter_id
        super().__init__(f"Cover letter {cover_letter_id} not found")


class CoverLetterGenerationError(CoverLetterError):
    """Raised when the LLM fails to produce a usable cover letter."""
