"""Extension job import service — upsert jobs from the browser extension.

Key responsibilities:
  - Upsert job into the global jobs table (fingerprint dedup)
  - Link job to the importing user via user_jobs
  - Queue embedding if missing
  - Record application events (job_imported)
  - Never create duplicate jobs
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.domain.extension.repository import ExtensionRepository
from app.domain.extension.schemas import (
    ExtensionJobImportRequest,
    ExtensionJobImportResponse,
    ExtensionJobLookupResponse,
)
from app.domain.jobs.dedup import job_fingerprint
from app.domain.jobs.repository import JobRepository
from app.infrastructure.embeddings import embed_text

logger = get_logger("extension_job_import")


class ExtensionJobImportService:
    def __init__(
        self,
        *,
        job_repository: JobRepository,
        extension_repository: ExtensionRepository,
    ) -> None:
        self._jobs = job_repository
        self._ext = extension_repository

    async def import_job(
        self,
        *,
        user_id: int,
        request: ExtensionJobImportRequest,
    ) -> ExtensionJobImportResponse:
        """Import a job from the browser extension.

        Deduplicates by fingerprint. Links to user via user_jobs.
        Queues embedding. Records job_imported event.
        """
        # fingerprint: (company, title, location) — normalized, case/whitespace-insensitive
        # Using location (not source_url) so the same job posted on multiple sites
        # (LinkedIn, Indeed, Greenhouse, etc.) collapses to one row.
        fp = job_fingerprint(request.company, request.title, request.location)

        # Upsert into jobs table
        values = {
            "fingerprint": fp,
            "title": request.title,
            "company": request.company,
            "location": request.location,
            "description": request.description,
            "source": request.source,
            "source_url": request.source_url,
            "raw_response": {
                "source": request.source,
                "ats_type": request.ats_type,
                "salary_text": request.salary_text,
                "external_apply_url": request.external_apply_url,
            },
        }
        job_id, was_inserted = await self._jobs.upsert_job(values)

        # Link to user
        await self._jobs.link_user_jobs(user_id, [job_id])

        # Queue embedding for new jobs with descriptions
        match_status = "existing"
        if was_inserted and request.description:
            # Embed in background — don't block the response
            match_status = "queued"
            try:
                embedding = await embed_text(request.description)
                await self._jobs.set_embedding(job_id, embedding)
            except Exception:
                logger.warning(
                    "extension.job_embed_failed",
                    job_id=job_id,
                    exc_info=True,
                )

        # Record event
        await self._ext.create_event(
            user_id=user_id,
            event_type="job_imported",
            job_id=job_id,
            source_url=request.source_url,
            page_url=request.source_url,
            metadata={
                "title": request.title,
                "company": request.company,
                "source": request.source,
                "was_existing_job": not was_inserted,
            },
        )

        logger.info(
            "extension.job_imported",
            user_id=user_id,
            job_id=job_id,
            was_new=was_inserted,
        )

        return ExtensionJobImportResponse(
            job_id=job_id,
            application_id=None,  # No application created on import
            status="saved",
            was_existing_job=not was_inserted,
            was_existing_application=False,
            match_status=match_status,
        )

    async def lookup_by_url(self, source_url: str) -> ExtensionJobLookupResponse:
        """Look up a job by source URL."""
        job = await self._jobs.find_by_source_url(source_url)

        if job is None:
            return ExtensionJobLookupResponse(job_id=None, exists=False)

        return ExtensionJobLookupResponse(
            job_id=job.id,
            exists=True,
            title=job.title,
            company=job.company,
        )
