"""Job ingestion service: fetch -> dedup/upsert -> embed.

Runs in a FastAPI BackgroundTask after the 202 response is sent.  Source
``fetch`` calls are blocking (network + pandas), so they run in a worker thread
via ``anyio.to_thread.run_sync``.  The pipeline is source-agnostic — it only
depends on the ``RawJobPost`` contract — and can move into a Celery task later
without changes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import html2text
from anyio import to_thread

from app.core.logging import get_logger
from app.domain.jobs.dedup import job_fingerprint
from app.domain.jobs.embedding_coordinator import JobEmbeddingCoordinator
from app.domain.jobs.exceptions import JobNotFoundError
from app.domain.jobs.identity import (
    resolve_source_identity,
    scope_for,
    scoped_key,
    strong_employer_apply_url,
)
from app.domain.jobs.repository import JobRepository
from app.domain.jobs.schemas import (
    EmployerSummary,
    IngestRequest,
    IngestResponse,
    JobImportRequest,
    JobListResponse,
    JobResponse,
    JobSourceLinkResponse,
    SourceRunResult,
)
from app.domain.jobs.shift_detector import ShiftDetector, classify_shift_llm
from app.infrastructure.job_sources.base import (
    JobQuery,
    RawJobPost,
    SourceSearchResult,
    fetch_compat_result,
)
from app.infrastructure.job_sources.registry import build_sources

logger = get_logger(__name__)

# Async callable used to stream progress events to the task store.
ProgressFn = Callable[..., Awaitable[None]]

_h2t = html2text.HTML2Text()
_h2t.body_width = 0  # don't wrap lines
_h2t.ignore_links = False
_h2t.ignore_images = True
_h2t.ignore_emphasis = False


def _normalise_description(raw: str | None) -> str | None:
    """Convert HTML descriptions to clean markdown for consistent display.

    Only touches text that contains HTML tags; plain text passes through
    unchanged.  The conversion strips inline styles, scripts, and other
    browser-specific cruft while preserving headings, lists, links, and
    paragraph structure.
    """
    if not raw:
        return raw
    # Quick check — if there are no HTML tags, skip conversion.
    if "<" not in raw or ">" not in raw:
        return raw
    try:
        md = _h2t.handle(raw)
        # Collapse excessive blank lines.
        import re

        md = re.sub(r"\n{3,}", "\n\n", md)
        return md.strip()
    except Exception:
        return raw  # degrade gracefully — keep original


class JobService:
    def __init__(
        self,
        repository: JobRepository,
        user_timezone: str = "Asia/Manila",
        embedding_coordinator: JobEmbeddingCoordinator | None = None,
        *,
        external_enrichment_enabled: bool = True,
    ) -> None:
        self._repo = repository
        self._shift = ShiftDetector(user_timezone)
        self._embedding_coordinator = embedding_coordinator
        # The isolated Browser QA fixture must not make provider calls.  This
        # is explicit at the composition boundary; normal runtime behavior
        # remains enabled by default.
        self._external_enrichment_enabled = external_enrichment_enabled

    async def ingest(
        self,
        request: IngestRequest,
        *,
        default_sources: list[str],
        default_country: str,
        default_results: int,
        proxies: list[str] | None,
        qa_unit_1a_browser: bool = False,
        progress: ProgressFn | None = None,
        user_id: int | None = None,
        preference_id: int | None = None,
    ) -> IngestResponse:
        async def _emit(**kw: Any) -> None:
            if progress is not None:
                await progress(**kw)

        source_names = request.sources or default_sources
        sources = build_sources(
            source_names, proxies=proxies, qa_unit_1a_browser=qa_unit_1a_browser
        )
        query = JobQuery(
            search_term=request.search_term,
            location=request.location,
            country=request.country or default_country,
            is_remote=request.is_remote,
            hours_old=request.hours_old,
            results_wanted=request.results_wanted or default_results,
            google_search_term=request.google_search_term,
        )

        fetched = 0
        inserted = 0
        updated = 0
        embedded = 0
        used_sources: set[str] = set()
        source_results: list[SourceRunResult] = []
        # Every job touched this run (new or refreshed) is linked to the user
        # so it appears in their scoped feed.
        linked_job_ids: set[int] = set()
        embedded_job_ids: list[int] = []

        for source in sources:
            source_name = source.name

            await _emit(
                status="running",
                message=f"Scraping {source_name}…",
                fetched=fetched,
                inserted=inserted,
                updated=updated,
                embedded=embedded,
                source_results=[item.model_dump() for item in source_results],
            )
            capabilities = getattr(source, "capabilities", None)
            unsupported_reason: str | None = None
            if capabilities is not None and capabilities.configuration_available and (
                capabilities.country_coverage is not None
                and query.country not in capabilities.country_coverage
            ):
                unsupported_reason = "unsupported_country"
            elif capabilities is not None and capabilities.configuration_available:
                requested_fields = {
                    "search_term",
                    *(
                        field
                        for field, value in (
                            ("location", query.location),
                            ("is_remote", query.is_remote),
                            ("hours_old", query.hours_old),
                            ("google_search_term", query.google_search_term),
                        )
                        if value is not None
                    ),
                    "country",
                    "results_wanted",
                }
                if not requested_fields.issubset(capabilities.supported_query_fields):
                    unsupported_reason = "unsupported_filter"
            if unsupported_reason is not None:
                source_results.append(
                    SourceRunResult(
                        source=source_name,
                        status="unsupported",
                        reason_code=unsupported_reason,
                    )
                )
                await _emit(
                    status="running",
                    message=f"Skipped unsupported {source_name} query",
                    fetched=fetched,
                    inserted=inserted,
                    updated=updated,
                    embedded=embedded,
                    source_results=[item.model_dump() for item in source_results],
                )
                continue
            try:
                search = getattr(source, "search", None)
                search_result: SourceSearchResult
                if callable(search):
                    search_result = await to_thread.run_sync(search, query)
                else:
                    search_result = await to_thread.run_sync(fetch_compat_result, source, query)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "jobs.ingest.source_failed",
                    source=source_name,
                    exc_info=True,
                )
                reason_code = (
                    "dependency_missing"
                    if exc.__class__.__name__ == "JobSpyUnavailableError"
                    else "provider_error"
                )
                source_results.append(
                    SourceRunResult(
                        source=source_name,
                        status="unavailable" if reason_code == "dependency_missing" else "failed",
                        reason_code=reason_code,
                    )
                )
                await _emit(
                    status="running",
                    message=f"Finished {source_name}",
                    fetched=fetched,
                    inserted=inserted,
                    updated=updated,
                    embedded=embedded,
                    source_results=[item.model_dump() for item in source_results],
                )
                continue

            posts = search_result.posts
            fetched += search_result.fetched
            # Separate upsert pass so we can batch-emit progress before embedding.
            to_embed: list[tuple[int, str, bool]] = []
            # LLM shift fallback: new jobs where regex found no shift keyword.
            to_classify: list[tuple[int, str, str]] = []
            accepted = 0
            rejected = 0
            for post in posts:
                if not post.title or not post.source_url:
                    rejected += 1
                    continue
                accepted += 1
                used_sources.add(post.source)
                shift_type = self._shift.detect_regex(post.title, post.description)
                job_id, was_inserted, description_changed = await self._upsert(
                    post, shift_type=shift_type
                )
                linked_job_ids.add(job_id)
                if was_inserted:
                    inserted += 1
                else:
                    updated += 1
                if (was_inserted or description_changed) and post.description:
                    to_embed.append((job_id, post.description, description_changed))
                    if was_inserted and shift_type is None:
                        to_classify.append((job_id, post.title, post.description))

            if to_embed:
                await _emit(
                    message=f"Embedding {len(to_embed)} new from {source_name}…",
                    fetched=fetched,
                    inserted=inserted,
                    updated=updated,
                    embedded=embedded,
                )
                for job_id, description, force_reembed in to_embed:
                    if await self._embed_job(job_id, description, force=force_reembed):
                        embedded += 1
                        embedded_job_ids.append(job_id)

            # LLM shift classification for new jobs where regex returned None.
            # Best-effort: failures are logged and skipped, never crash ingest.
            if self._external_enrichment_enabled:
                for job_id, title, description in to_classify:
                    llm_shift = await classify_shift_llm(title, description)
                    if llm_shift and llm_shift != "unknown":
                        await self._repo.set_shift_type(job_id, llm_shift)

            source_results.append(
                SourceRunResult(
                    source=search_result.source,
                    status=search_result.status,
                    reason_code=search_result.reason_code,
                    duration_ms=search_result.duration_ms,
                    fetched=search_result.fetched,
                    accepted=accepted,
                    rejected=rejected,
                    truncated=search_result.truncated or len(posts) >= query.results_wanted,
                    truncation_reason=(
                        search_result.truncation_reason
                        or ("result_limit_reached" if len(posts) >= query.results_wanted else None)
                    ),
                    completeness=search_result.completeness,
                )
            )
            await _emit(
                status="running",
                message=f"Finished {source_name}",
                fetched=fetched,
                inserted=inserted,
                updated=updated,
                embedded=embedded,
                source_results=[item.model_dump() for item in source_results],
            )

        if user_id is not None:
            await self._repo.link_user_jobs(
                user_id, list(linked_job_ids), preference_id=preference_id
            )

        logger.info(
            "jobs.ingest.done",
            fetched=fetched,
            inserted=inserted,
            updated=updated,
            embedded=embedded,
        )
        successful = {"success", "empty"}
        success_count = sum(item.status in successful for item in source_results)
        outcome = (
            "success"
            if success_count == len(source_results)
            else "partial_success" if success_count else "failed"
        )
        return IngestResponse(
            fetched=fetched,
            inserted=inserted,
            updated=updated,
            embedded=embedded,
            sources=[item.source for item in source_results],
            outcome=outcome,
            source_results=source_results,
            embedded_job_ids=embedded_job_ids,
        )

    async def link_recent_jobs_batch(self, *, user_ids: list[int], since: datetime) -> int:
        """Link all *user_ids* to jobs fetched since *since*.

        Used after a shared scrape so every subscriber sees the results.
        """
        return await self._repo.link_recent_jobs_batch(user_ids=user_ids, since=since)

    async def _upsert(
        self, post: RawJobPost, shift_type: str | None = None
    ) -> tuple[int, bool, bool]:
        identity = resolve_source_identity(post.source_url, source_hint=post.source)
        if identity is None:
            raise ValueError("job source URL is not a valid HTTP(S) URL")
        scope = scope_for(visibility="public")
        link_key = scoped_key(scope, identity.stable_token)
        existing = await self._repo.find_by_alias_identity(
            scope_key=scope, identity_key=link_key
        )
        apply_url = strong_employer_apply_url(post.external_apply_url or "")
        if existing is None and apply_url is not None:
            existing = await self._repo.find_by_apply_url(
                scope_key=scope, canonical_url=apply_url
            )
        job_key = scoped_key(scope, identity.stable_token)
        fingerprint = job_fingerprint(post.company, post.title, post.location)

        # Convert HTML descriptions to markdown for consistent formatting.
        description = _normalise_description(post.description)
        exists = existing is not None
        previous_description = None if existing is None else existing.description

        values: dict[str, object] = {
            "fingerprint": fingerprint,
            "identity_scope": scope,
            "identity_key": existing.identity_key if existing is not None else job_key,
            "title": post.title,
            "company": post.company,
            "location": post.location,
            "is_remote": post.is_remote,
            "job_type": post.job_type,
            "description": description,
            "salary_min": post.salary_min,
            "salary_max": post.salary_max,
            "salary_currency": post.salary_currency,
            "source": post.source,
            "source_url": post.source_url,
            "posted_at": post.posted_at,
            "shift_type": shift_type,  # None → COALESCE keeps existing value
            "company_logo": post.company_logo,
            "raw_response": post.raw,
        }
        job_id, was_inserted = await self._repo.upsert_job(values)
        await self._repo.attach_source_link(
            job_id=job_id,
            scope_key=scope,
            identity_key=link_key,
            source=identity.source,
            source_job_id=identity.source_job_id,
            source_tenant=identity.source_tenant,
            source_url=post.source_url,
            canonical_url=identity.canonical_url,
            external_apply_url=apply_url,
        )
        description_changed = (
            exists and description is not None and description != previous_description
        )
        return job_id, was_inserted, description_changed

    async def _embed_job(self, job_id: int, description: str, *, force: bool = False) -> bool:
        if not self._external_enrichment_enabled:
            return False
        if self._embedding_coordinator is not None:
            try:
                result = await self._embedding_coordinator.ensure_embedding(job_id, force=force)
            except Exception:  # noqa: BLE001 - embedding remains best-effort during ingest
                logger.warning("jobs.embed.failed", job_id=job_id, exc_info=True)
                return False
            return result.status == "ready"
        try:
            from app.infrastructure.embeddings import embed_text  # noqa: PLC0415

            embedding = await embed_text(description)
        except Exception:  # noqa: BLE001 - embedding is best-effort during ingest
            logger.warning("jobs.embed.failed", job_id=job_id, exc_info=True)
            return False
        await self._repo.set_embedding(job_id, embedding)
        return True

    async def list_jobs(
        self,
        *,
        user_id: int,
        q: str | None = None,
        source: str | None = None,
        is_remote: bool | None = None,
        min_salary: float | None = None,
        job_type: str | None = None,
        location_contains: str | None = None,
        company_contains: str | None = None,
        company_exact: str | None = None,
        shift_type: str | None = None,
        source_type: str | None = None,
        imported_only: bool = False,
        archive_state: str = "active",
        cursor: str | None = None,
        limit: int = 50,
    ) -> JobListResponse:
        rows, total, next_cursor = await self._repo.list_jobs(
            user_id=user_id,
            q=q,
            source=source,
            is_remote=is_remote,
            min_salary=min_salary,
            job_type=job_type,
            location_contains=location_contains,
            company_contains=company_contains,
            company_exact=company_exact,
            shift_type=shift_type,
            source_type=source_type,
            imported_only=imported_only,
            archive_state=archive_state,
            cursor=cursor,
            limit=limit,
        )
        return JobListResponse(
            items=[JobResponse.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            next_cursor=next_cursor,
        )

    async def list_imported_jobs(
        self,
        *,
        user_id: int,
        cursor: str | None = None,
        limit: int = 50,
    ) -> JobListResponse:
        """Return only jobs imported by the current user (manual + extension)."""
        rows, total, next_cursor = await self._repo.list_imported_jobs(
            user_id=user_id,
            cursor=cursor,
            limit=limit,
        )
        items: list[JobResponse] = []
        for row in rows:
            job, match_score, match_reason = row[0], row[1], row[2]
            resp = JobResponse.model_validate(job)
            resp.match_score = float(match_score) if match_score is not None else None
            resp.match_reason = match_reason if isinstance(match_reason, dict) else None
            items.append(resp)
        return JobListResponse(
            items=items,
            total=total,
            limit=limit,
            next_cursor=next_cursor,
        )

    async def archive_job(self, user_id: int, job_id: int) -> None:
        """Archive one active, visible job for this user.

        The archive relationship is created when a public or owner-visible job
        has not yet been linked to the user.  Inaccessible jobs use the same
        non-disclosing not-found contract as job detail.
        """
        job = await self._repo.get_archivable_by_id(user_id=user_id, job_id=job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        await self._repo.archive_user_job(user_id=user_id, job_id=job_id)

    async def restore_job(self, user_id: int, job_id: int) -> None:
        """Restore an archived job without touching applications."""
        job = await self._repo.get_restorable_by_id(user_id=user_id, job_id=job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        await self._repo.restore_user_job(user_id=user_id, job_id=job_id)

    async def get_job(self, *, user_id: int, job_id: int) -> JobResponse:
        """Fetch a single job, scoped to *user_id* visibility.

        Private jobs are only visible to their owner.  Public jobs are visible
        to all authenticated users.  Returns 404 for invisible / non-existent
        jobs (never 403 — existence is not leaked).
        """
        job = await self._repo.get_visible_by_id(user_id=user_id, job_id=job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        response = JobResponse.model_validate(job)
        # Keep the label and URL human-readable while the repository performs
        # the case-insensitive normalized comparison.
        company = " ".join((job.company or "").split())
        if (
            job.visibility != "public"
            or job.ingest_source in {"manual", "extension"}
            or not company
        ):
            return response

        job_count = await self._repo.count_active_public_jobs_by_company(
            user_id=user_id,
            company=company,
        )
        if job_count >= 2:
            response.employer_summary = EmployerSummary(
                company=company,
                company_logo=job.company_logo,
                job_count=job_count,
            )
        return response

    async def list_job_sources(
        self, *, user_id: int, job_id: int
    ) -> list[JobSourceLinkResponse]:
        links = await self._repo.list_visible_source_links(user_id=user_id, job_id=job_id)
        if links is None:
            raise JobNotFoundError(job_id)
        return [
            JobSourceLinkResponse(
                source=link.source,
                source_url=link.source_url,
                canonical_url=link.canonical_url,
                external_apply_url=link.external_apply_url,
                first_seen_at=link.first_seen_at,
                last_seen_at=link.last_seen_at,
                status=link.status,
            )
            for link in links
        ]

    async def import_job(self, payload: JobImportRequest, *, user_id: int) -> JobResponse:
        """Manually import a single job posting without fuzzy identity matching.

        A validated public alias may link the import to that public job.  Every
        other import is owner-private from its initial INSERT.
        """
        identity = resolve_source_identity(payload.url, source_hint="manual")
        if identity is None:
            raise ValueError("job URL is not a valid HTTP(S) URL")
        public_scope = scope_for(visibility="public")
        link_key = scoped_key(public_scope, identity.stable_token)
        public_job = await self._repo.find_by_alias_identity(
            scope_key=public_scope, identity_key=link_key
        )
        scope = public_scope if public_job is not None else scope_for(
            visibility="private", user_id=user_id
        )
        if public_job is not None:
            # An import may associate its user with an exact public identity,
            # but untrusted import fields must never refresh public content.
            job_id, was_inserted = public_job.id, False
        else:
            fingerprint = job_fingerprint(payload.company, payload.title, None)
            values: dict[str, object] = {
                "fingerprint": fingerprint,
                "identity_scope": scope,
                "identity_key": scoped_key(scope, identity.stable_token),
                "visibility": "private",
                "created_by_user_id": user_id,
                "ingest_source": "manual",
                "title": payload.title,
                "company": payload.company,
                "description": payload.description,
                "source": "manual",
                "source_url": payload.url,
                "raw_response": {"imported_from": payload.url},
            }
            job_id, was_inserted = await self._repo.upsert_job(values)

        # If this is a brand-new job, mark it as private to the importing user.
        if was_inserted:
            await self._repo.mark_as_private_import(job_id=job_id, user_id=user_id)
            if payload.description and self._external_enrichment_enabled:
                # Dispatch async embedding via Celery — don't block the response.
                from app.workers.job_imports import embed_imported_job  # noqa: PLC0415

                embed_imported_job.delay(job_id, user_id)
            relationship_source = "manual"
            import_method = "manual"
            uj_status = "imported"
        else:
            relationship_source = "manual"
            import_method = "manual"
            uj_status = "imported"

        # Imported aliases may be written to a public job only for this exact,
        # validated identity; raw import content is never copied into the link.
        await self._repo.attach_source_link(
            job_id=job_id,
            scope_key=scope,
            identity_key=scoped_key(scope, identity.stable_token),
            source=identity.source,
            source_job_id=identity.source_job_id,
            source_tenant=identity.source_tenant,
            source_url=payload.url,
            canonical_url=identity.canonical_url,
        )

        # Link user to the job (or re-link if already linked via different source)
        await self._repo.link_user_jobs(
            user_id,
            [job_id],
            relationship_source=relationship_source,
            import_method=import_method,
            status=uj_status,
        )

        # Store private import metadata
        await self._repo.create_user_job_import(
            user_id=user_id,
            job_id=job_id,
            user_job_user_id=user_id,
            user_job_job_id=job_id,
            import_method="manual",
            source_url=payload.url,
            raw_input=payload.description,
            parsed_title=payload.title,
            parsed_company=payload.company,
        )

        job = await self._repo.get_by_id(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return JobResponse.model_validate(job)
