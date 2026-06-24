"""Resume domain service: orchestrates parse -> embed -> persist.

Pure-ish business logic; no FastAPI/HTTP concerns. Raises domain exceptions.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logging import get_logger
from app.domain.resume.exceptions import (
    ProfileNotFoundError,
    ResumeParseError,
    UnsupportedResumeFormatError,
)
from app.domain.resume.repository import ResumeRepository
from app.domain.resume.schemas import (
    ParsedProfile,
    ProfileResponse,
    ResumeResponse,
)
from app.infrastructure.embeddings import embed_text
from app.infrastructure.resume_parser import extract_text_from_bytes, parse_resume_text

logger = get_logger("resume_service")

ProgressFn = Callable[..., Awaitable[None]]


def build_summary_text(profile: ParsedProfile) -> str:
    """Build the canonical text used to embed a profile for job matching.

    Concentrates the signal LLM matching cares about: headline, skills,
    preferred roles, and work-history highlights.
    """
    parts: list[str] = []
    if profile.headline:
        parts.append(profile.headline)
    if profile.skills:
        parts.append("Skills: " + ", ".join(profile.skills))
    if profile.preferred_roles:
        parts.append("Preferred roles: " + ", ".join(profile.preferred_roles))
    if profile.years_experience is not None:
        parts.append(f"Experience: {profile.years_experience} years")
    for item in profile.work_history:
        header = " ".join(filter(None, [item.title, "at", item.company]))
        if header.strip():
            parts.append(header)
        parts.extend(item.highlights)
    return "\n".join(p for p in parts if p).strip() or "No profile content"


class ResumeService:
    def __init__(self, repository: ResumeRepository) -> None:
        self._repository = repository

    async def upload_and_parse(
        self, *, user_id: int, filename: str, content: bytes,
        progress: ProgressFn | None = None,
    ) -> ResumeResponse:
        """Extract text, LLM-parse, embed, and persist a new active profile."""

        async def _emit(**kw: Any) -> None:
            if progress is not None:
                await progress(**kw)

        await _emit(status="running", message="Extracting text…")
        try:
            raw_text = extract_text_from_bytes(content, filename)
        except ValueError as exc:
            raise UnsupportedResumeFormatError(str(exc)) from exc

        await _emit(message="Parsing with AI…")
        try:
            profile = await parse_resume_text(raw_text)
        except ValueError as exc:
            raise ResumeParseError(str(exc)) from exc

        await _emit(message="Embedding profile…")
        summary_text = build_summary_text(profile)
        embedding = await embed_text(summary_text)

        await self._repository.deactivate_existing(user_id)
        resume = await self._repository.add_resume(
            user_id=user_id, filename=filename, raw_text=raw_text
        )
        await self._repository.add_profile(
            user_id=user_id,
            resume_id=resume.id,
            parsed=profile.model_dump(),
            summary_text=summary_text,
            embedding=embedding,
        )
        logger.info("resume_parsed", user_id=user_id, resume_id=resume.id)
        return ResumeResponse(
            resume_id=resume.id,
            filename=resume.filename,
            is_active=resume.is_active,
            profile=profile,
        )

    async def get_profile(self, user_id: int) -> ProfileResponse:
        profile_row = await self._repository.get_active_profile(user_id)
        if profile_row is None:
            raise ProfileNotFoundError(user_id)
        return ProfileResponse(
            profile_id=profile_row.id,
            resume_id=profile_row.resume_id,
            profile=ParsedProfile.model_validate(profile_row.parsed),
        )

    async def update_profile(
        self, *, user_id: int, profile: ParsedProfile
    ) -> ProfileResponse:
        """Persist user edits and re-embed the corrected profile."""
        profile_row = await self._repository.get_active_profile(user_id)
        if profile_row is None:
            raise ProfileNotFoundError(user_id)

        summary_text = build_summary_text(profile)
        embedding = await embed_text(summary_text)
        await self._repository.update_profile_parsed(
            profile=profile_row,
            parsed=profile.model_dump(),
            summary_text=summary_text,
            embedding=embedding,
        )
        logger.info("profile_updated", user_id=user_id, profile_id=profile_row.id)
        return ProfileResponse(
            profile_id=profile_row.id,
            resume_id=profile_row.resume_id,
            profile=profile,
        )
