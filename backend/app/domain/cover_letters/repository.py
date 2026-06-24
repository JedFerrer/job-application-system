"""Cover-letter repository (Phase 4)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cover_letter import CoverLetter


class CoverLetterRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: int,
        job_id: int,
        content: str,
        tone: str | None,
        model: str,
    ) -> CoverLetter:
        cover_letter = CoverLetter(
            user_id=user_id,
            job_id=job_id,
            content=content,
            tone=tone,
            model=model,
        )
        self._session.add(cover_letter)
        await self._session.flush()
        await self._session.refresh(cover_letter)
        return cover_letter

    async def get(self, *, user_id: int, cover_letter_id: int) -> CoverLetter | None:
        result = await self._session.execute(
            select(CoverLetter).where(
                CoverLetter.id == cover_letter_id,
                CoverLetter.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def update_content(
        self, *, cover_letter: CoverLetter, content: str
    ) -> CoverLetter:
        cover_letter.content = content
        cover_letter.is_edited = True
        await self._session.flush()
        await self._session.refresh(cover_letter)
        return cover_letter
