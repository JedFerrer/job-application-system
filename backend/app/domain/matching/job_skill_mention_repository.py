"""Persistence operations for extracted job skill mentions."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.matching.skill_types import SkillMention
from app.models.job_skill_mention import JobSkillMention


class JobSkillMentionRepository:
    """Store mentions using the caller's transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_for_job(
        self, job_id: int, mentions: Sequence[SkillMention]
    ) -> None:
        """Replace all mentions for one job without committing the session."""
        await self._session.execute(
            delete(JobSkillMention).where(JobSkillMention.job_id == job_id)
        )
        if not mentions:
            return

        await self._session.execute(
            insert(JobSkillMention).values(
                [
                    {
                        "job_id": job_id,
                        "skill_id": mention.skill_id,
                        "canonical_label": mention.canonical_label,
                        "classification": mention.classification,
                        "job_excerpt": mention.job_excerpt,
                        "start_offset": mention.start_offset,
                        "end_offset": mention.end_offset,
                        "analyzer_version": mention.analyzer_version,
                    }
                    for mention in mentions
                ]
            )
        )

    async def list_for_job(self, job_id: int) -> list[JobSkillMention]:
        """Return one job's mentions in source order for internal verification."""
        result = await self._session.execute(
            select(JobSkillMention)
            .where(JobSkillMention.job_id == job_id)
            .order_by(JobSkillMention.start_offset, JobSkillMention.id)
        )
        return list(result.scalars().all())
