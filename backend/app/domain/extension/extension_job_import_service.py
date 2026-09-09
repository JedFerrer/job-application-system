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

from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.extension.repository import ExtensionRepository
from app.domain.extension.schemas import (
    ExtensionJobImportRequest,
    ExtensionJobImportResponse,
    ExtensionJobLookupResponse,
)
from app.domain.jobs.dedup import job_fingerprint
from app.domain.jobs.identity import resolve_source_identity, scope_for, scoped_key
from app.domain.jobs.repository import JobRepository

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

        An extension import may link to an existing public posting only through
        the exact, validated source identity. Otherwise it is private from the
        initial insert and is scoped to the importing user.

        Raw extension payload is always stored in user_job_imports.
        """
        identity = resolve_source_identity(
            request.source_url, source_hint=request.source, ats_type=request.ats_type
        )
        if identity is None:
            raise ValueError("job URL is not a valid HTTP(S) URL")
        observed_at = datetime.now(UTC)
        public_scope = scope_for(visibility="public")
        public_job = await self._jobs.find_by_alias_identity(
            scope_key=public_scope,
            identity_key=scoped_key(public_scope, identity.stable_token),
        )
        scope = public_scope if public_job is not None else scope_for(
            visibility="private", user_id=user_id
        )
        if public_job is not None:
            # Exact public identity permits linking, never replacement of
            # shared fields with untrusted extension payload content.
            job_id, was_inserted = public_job.id, False
        else:
            fp = job_fingerprint(request.company, request.title, request.location)
            values: dict[str, object] = {
                "fingerprint": fp,
                "identity_scope": scope,
                "identity_key": scoped_key(scope, identity.stable_token),
                "visibility": "private",
                "created_by_user_id": user_id,
                "ingest_source": "extension",
                "title": request.title,
                "company": request.company,
                "location": request.location,
                "description": request.description,
                "source": request.source,
                "source_url": request.source_url,
                "raw_response": {},
            }
            values["first_seen_at"] = observed_at
            values["last_seen_at"] = observed_at
            job_id, was_inserted = await self._jobs.upsert_job(values, observed_at=observed_at)

        # ── Privacy: mark new jobs as private ──────────────────────────
        if was_inserted:
            await self._jobs.mark_as_private_import(
                job_id=job_id, user_id=user_id, ingest_source="extension"
            )

        await self._jobs.attach_source_link(
            job_id=job_id,
            scope_key=scope,
            identity_key=scoped_key(scope, identity.stable_token),
            source=identity.source,
            source_job_id=identity.source_job_id,
            source_tenant=identity.source_tenant,
            source_url=request.source_url,
            canonical_url=identity.canonical_url,
            external_apply_url=request.external_apply_url,
            observed_at=observed_at,
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
        if (
            was_inserted
            and request.description
            and not get_settings().qa_unit_1a_browser_fixtures_enabled
        ):
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

    async def lookup_by_url(
        self, *, user_id: int, source_url: str
    ) -> ExtensionJobLookupResponse:
        """Look up a job by source URL."""
        canonical = resolve_source_identity(source_url)
        job = (
            None
            if canonical is None
            else await self._jobs.resolve_visible_by_url(
                user_id=user_id, canonical_url=canonical.canonical_url
            )
        )

        if job is None:
            return ExtensionJobLookupResponse(job_id=None, exists=False)

        return ExtensionJobLookupResponse(
            job_id=job.id,
            exists=True,
            title=job.title,
            company=job.company,
        )
