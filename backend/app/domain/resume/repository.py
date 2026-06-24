"""Resume/profile persistence. Repositories own all DB access for the module."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resume import CandidateProfile, Resume


class ResumeRepository:
    """Data access for ``resumes`` and ``candidate_profiles``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def deactivate_existing(self, user_id: int) -> None:
        """Mark all of a user's resumes inactive (single active profile)."""
        await self._session.execute(
            update(Resume).where(Resume.user_id == user_id).values(is_active=False)
        )

    async def add_resume(self, user_id: int, filename: str, raw_text: str) -> Resume:
        resume = Resume(
            user_id=user_id,
            filename=filename,
            raw_text=raw_text,
            is_active=True,
        )
        self._session.add(resume)
        await self._session.flush()
        return resume

    async def add_profile(
        self,
        *,
        user_id: int,
        resume_id: int,
        parsed: dict[str, object],
        summary_text: str,
        embedding: list[float],
    ) -> CandidateProfile:
        profile = CandidateProfile(
            user_id=user_id,
            resume_id=resume_id,
            parsed=parsed,
            summary_text=summary_text,
            embedding=embedding,
        )
        self._session.add(profile)
        await self._session.flush()
        return profile

    async def get_active_profile(self, user_id: int) -> CandidateProfile | None:
        """Return the profile tied to the user's active resume, if any."""
        result = await self._session.execute(
            select(CandidateProfile)
            .join(Resume, Resume.id == CandidateProfile.resume_id)
            .where(CandidateProfile.user_id == user_id, Resume.is_active.is_(True))
            .order_by(CandidateProfile.updated_at.desc())
        )
        return result.scalars().first()

    async def update_profile_parsed(
        self,
        *,
        profile: CandidateProfile,
        parsed: dict[str, object],
        summary_text: str,
        embedding: list[float],
    ) -> CandidateProfile:
        profile.parsed = parsed
        profile.summary_text = summary_text
        profile.embedding = embedding
        await self._session.flush()
        return profile
