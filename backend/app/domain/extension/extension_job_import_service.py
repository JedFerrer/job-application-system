"""Extension job import service — upsert jobs from the browser extension.

Key responsibilities:
  - Upsert job into the global jobs table (fingerprint + source-link dedup)
  - Mark new jobs as private (visibility='private', created_by_user_id)
  - Link job to the importing user via user_jobs
  - Store private raw payload in user_job_imports
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
from app.domain.jobs.source_id_extractor import extract_source_id

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

        Dedup order:
        1. Extract stable source ID → check job_source_links for existing public job.
        2. Fall back to fingerprint (company, title, location).

        Privacy: new jobs are created as private (visibility='private',
        created_by_user_id=user_id). If an existing public job is found, we
        link to it without creating a duplicate.

        Raw extension payload is always stored in user_job_imports.
        """
        # ── Dedup: try stable source ID first ──────────────────────────
        source_id = extract_source_id(request.source_url, request.ats_type)
        existing_job_id: int | None = None
        if source_id:
            existing_job_id = await self._lookup_by_source_id(
                source_id[0], source_id[1]
            )

        # ── Dedup fallback: fingerprint ────────────────────────────────
        if existing_job_id is None:
            fp = job_fingerprint(request.company, request.title, request.location)
            values: dict[str, object] = {
                "fingerprint": fp,
                "title": request.title,
                "company": request.company,
                "location": request.location,
                "description": request.description,
                "source": request.source,
                "source_url": request.source_url,
            }
            job_id, was_inserted = await self._jobs.upsert_job(values)
        else:
            job_id = existing_job_id
            was_inserted = False

        # ── Privacy: mark new jobs as private ──────────────────────────
        if was_inserted:
            await self._jobs.mark_as_private_import(
                job_id=job_id, user_id=user_id, ingest_source="extension"
            )

        # ── Link user → job ────────────────────────────────────────────
        await self._jobs.link_user_jobs(
            user_id,
            [job_id],
            relationship_source="extension",
            import_method="extension",
            status="imported",
        )

        # ── Store private import metadata ──────────────────────────────
        await self._jobs.create_user_job_import(
            user_id=user_id,
            job_id=job_id,
            user_job_user_id=user_id,
            user_job_job_id=job_id,
            import_method="extension",
            source_url=request.source_url,
            raw_payload={
                "title": request.title,
                "company": request.company,
                "location": request.location,
                "description": request.description,
                "salary_text": request.salary_text,
                "source_url": request.source_url,
                "source": request.source,
                "ats_type": request.ats_type,
                "external_apply_url": request.external_apply_url,
            },
            raw_html_hash=request.raw_html_hash,
            parsed_title=request.title,
            parsed_company=request.company,
            parsed_location=request.location,
            parsed_salary=request.salary_text,
        )

        # ── Embedding (async via Celery) ────────────────────────────────
        match_status = "existing"
        if was_inserted and request.description:
            match_status = "queued"
            from app.workers.job_imports import embed_imported_job  # noqa: PLC0415

            embed_imported_job.delay(job_id, user_id)

        # ── Record event ───────────────────────────────────────────────
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
            application_id=None,
            status="saved",
            was_existing_job=not was_inserted,
            was_existing_application=False,
            match_status=match_status,
        )

    async def _lookup_by_source_id(
        self, source: str, source_job_id: str
    ) -> int | None:
        """Check job_source_links for an existing public job."""
        return await self._jobs.find_by_source_id(source, source_job_id)

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
