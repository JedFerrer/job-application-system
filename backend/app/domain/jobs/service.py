"""Job ingestion service: fetch -> dedup/upsert -> embed.

Runs in a FastAPI BackgroundTask after the 202 response is sent.  Source
``fetch`` calls are blocking (network + pandas), so they run in a worker thread
via ``anyio.to_thread.run_sync``.  The pipeline is source-agnostic — it only
depends on the ``RawJobPost`` contract — and can move into a Celery task later
without changes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from anyio import to_thread

from app.core.logging import get_logger
from app.domain.jobs.dedup import job_fingerprint
from app.domain.jobs.exceptions import JobNotFoundError
from app.domain.jobs.repository import JobRepository
from app.domain.jobs.schemas import (
    IngestRequest,
    IngestResponse,
    JobListResponse,
    JobResponse,
)
from app.domain.jobs.shift_detector import ShiftDetector, classify_shift_llm
from app.infrastructure.embeddings import embed_text
from app.infrastructure.job_sources.base import JobQuery, RawJobPost
from app.infrastructure.job_sources.registry import build_sources

logger = get_logger(__name__)

# Async callable used to stream progress events to the task store.
ProgressFn = Callable[..., Awaitable[None]]


class JobService:
    def __init__(self, repository: JobRepository, user_timezone: str = "Asia/Manila") -> None:
        self._repo = repository
        self._shift = ShiftDetector(user_timezone)

    async def ingest(
        self,
        request: IngestRequest,
        *,
        default_sources: list[str],
        default_country: str,
        default_results: int,
        proxies: list[str] | None,
        progress: ProgressFn | None = None,
    ) -> IngestResponse:
        async def _emit(**kw: Any) -> None:
            if progress is not None:
                await progress(**kw)

        source_names = request.sources or default_sources
        sources = build_sources(source_names, proxies=proxies)
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

        for source in sources:
            # Human-friendly name for progress messages (one site per adapter).
            source_name = (
                getattr(source, "_sites", None) or [getattr(source, "name", "source")]
            )[0]

            await _emit(
                status="running",
                message=f"Scraping {source_name}…",
                fetched=fetched,
                inserted=inserted,
                updated=updated,
                embedded=embedded,
            )
            try:
                posts = await to_thread.run_sync(source.fetch, query)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "jobs.ingest.source_failed",
                    source=source_name,
                    exc_info=True,
                )
                continue

            fetched += len(posts)
            # Separate upsert pass so we can batch-emit progress before embedding.
            to_embed: list[tuple[int, str]] = []
            # LLM shift fallback: new jobs where regex found no shift keyword.
            to_classify: list[tuple[int, str, str]] = []
            for post in posts:
                if not post.title or not post.source_url:
                    continue
                used_sources.add(post.source)
                shift_type = self._shift.detect_regex(post.title, post.description)
                job_id, was_inserted = await self._upsert(post, shift_type=shift_type)
                if was_inserted:
                    inserted += 1
                    if post.description:
                        to_embed.append((job_id, post.description))
                        if shift_type is None:
                            to_classify.append((job_id, post.title, post.description))
                else:
                    updated += 1

            if to_embed:
                await _emit(
                    message=f"Embedding {len(to_embed)} new from {source_name}…",
                    fetched=fetched,
                    inserted=inserted,
                    updated=updated,
                    embedded=embedded,
                )
                for job_id, description in to_embed:
                    if await self._embed_job(job_id, description):
                        embedded += 1

            # LLM shift classification for new jobs where regex returned None.
            # Best-effort: failures are logged and skipped, never crash ingest.
            for job_id, title, description in to_classify:
                llm_shift = await classify_shift_llm(title, description)
                if llm_shift and llm_shift != "unknown":
                    await self._repo.set_shift_type(job_id, llm_shift)

        logger.info(
            "jobs.ingest.done",
            fetched=fetched,
            inserted=inserted,
            updated=updated,
            embedded=embedded,
        )
        return IngestResponse(
            fetched=fetched,
            inserted=inserted,
            updated=updated,
            embedded=embedded,
            sources=sorted(used_sources),
        )

    async def _upsert(self, post: RawJobPost, shift_type: str | None = None) -> tuple[int, bool]:
        fingerprint = job_fingerprint(post.company, post.title, post.location)
        values: dict[str, object] = {
            "fingerprint": fingerprint,
            "title": post.title,
            "company": post.company,
            "location": post.location,
            "is_remote": post.is_remote,
            "job_type": post.job_type,
            "description": post.description,
            "salary_min": post.salary_min,
            "salary_max": post.salary_max,
            "salary_currency": post.salary_currency,
            "source": post.source,
            "source_url": post.source_url,
            "posted_at": post.posted_at,
            "shift_type": shift_type,  # None → COALESCE keeps existing value
            "raw_response": post.raw,
        }
        return await self._repo.upsert_job(values)

    async def _embed_job(self, job_id: int, description: str) -> bool:
        try:
            embedding = await embed_text(description)
        except Exception:  # noqa: BLE001 - embedding is best-effort during ingest
            logger.warning("jobs.embed.failed", job_id=job_id, exc_info=True)
            return False
        await self._repo.set_embedding(job_id, embedding)
        return True

    async def list_jobs(
        self,
        *,
        source: str | None,
        is_remote: bool | None,
        min_salary: float | None,
        job_type: str | None,
        location_contains: str | None,
        include_archived: bool = False,
        limit: int,
        offset: int,
    ) -> JobListResponse:
        rows, total = await self._repo.list_jobs(
            source=source,
            is_remote=is_remote,
            min_salary=min_salary,
            job_type=job_type,
            location_contains=location_contains,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )
        return JobListResponse(
            items=[JobResponse.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def archive_job(self, job_id: int, *, archived: bool) -> JobResponse:
        job = await self._repo.set_archived(job_id, archived=archived)
        if job is None:
            raise JobNotFoundError(job_id)
        return JobResponse.model_validate(job)
