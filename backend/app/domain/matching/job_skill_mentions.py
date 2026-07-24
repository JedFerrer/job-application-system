"""Internal extraction-to-persistence service for job skill mentions."""

from __future__ import annotations

from collections.abc import Callable

from app.core.logging import get_logger
from app.domain.matching.job_skill_mention_repository import JobSkillMentionRepository
from app.domain.matching.skill_classification import classify_skill_mentions
from app.domain.matching.skill_extractor import SkillExtractor, default_skill_extractor
from app.domain.matching.skill_types import SkillExtractionReport

logger = get_logger(__name__)


class JobSkillMentionService:
    """Extract and atomically replace one job's persisted skill evidence."""

    def __init__(
        self,
        repository: JobSkillMentionRepository,
        extractor: SkillExtractor | None = None,
        classifier: Callable[[str, SkillExtractionReport], SkillExtractionReport] = (
            classify_skill_mentions
        ),
    ) -> None:
        self._repository = repository
        self._extractor = extractor
        self._classifier = classifier

    async def refresh_for_description(
        self, *, job_id: int, description: str | None
    ) -> bool:
        """Replace mentions after successful extraction; preserve old rows on error."""
        try:
            extractor = self._extractor or default_skill_extractor()
            extracted = extractor.extract(description or "", source="free_text")
            classified = self._classifier(description or "", extracted).deduplicated()
        except Exception as exc:  # noqa: BLE001 - refresh must not corrupt old evidence
            logger.warning(
                "matching.skill_mentions.refresh_failed",
                job_id=job_id,
                error_type=type(exc).__name__,
            )
            return False

        await self._repository.replace_for_job(job_id, classified.mentions)
        return True
