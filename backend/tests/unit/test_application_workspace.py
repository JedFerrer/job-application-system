"""DB-backed regressions for the owned, side-effect-free workspace projection."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.applications.document_review import (
    DocumentReviewService,
    ReviewConflictError,
    ReviewNotFoundError,
)
from app.domain.applications.exceptions import ApplicationNotFoundError
from app.domain.applications.preparation import ApplicationPreparationService
from app.domain.applications.repository import ApplicationRepository
from app.domain.applications.review_prompts import REVIEW_POLICY_VERSION
from app.domain.applications.schemas import (
    DocumentReviewQueuedResponse,
    DocumentReviewResponse,
    ReviewAcknowledgmentItem,
    ReviewAcknowledgmentRequest,
)
from app.domain.applications.service import ApplicationService
from app.domain.jobs.repository import JobRepository
from app.infrastructure.task_store import TaskStore
from app.models.application_package import ApplicationPackage
from app.models.application_preparation import ApplicationPreparation
from app.models.application_review import ApplicationDocumentReview
from app.models.job import Job
from tests.unit.test_application_preparation import _factory, _seed

pytestmark = pytest.mark.integration


class _ReviewTaskStore(TaskStore):
    def __init__(self) -> None:
        self.created: list[tuple[str, int, str]] = []

    async def create(self, task_type: str, user_id: int) -> str:
        task_id = f"review-task-{len(self.created) + 1}"
        self.created.append((task_type, user_id, task_id))
        return task_id

    async def update(self, task_id: str, **fields: Any) -> None:
        return None


class _WorkspacePreparationService(ApplicationPreparationService):
    """Fixture projection for package/review tests that do not model source selections."""

    async def get(self, *, user_id: int, application_id: int):  # type: ignore[no-untyped-def]
        result = await super().get(user_id=user_id, application_id=application_id)
        preparation = await self._session.get(ApplicationPreparation, application_id)
        if preparation and preparation.reviewable_package_id:
            return result.model_copy(update={"state": "needs_review"})
        return result


class _PointerReviewService(DocumentReviewService):
    """Keep cache/race fixtures focused on pointer admission, not source selection."""

    async def _current_owned_package(
        self, *, user_id: int, application_id: int, package_id: int
    ) -> ApplicationPackage:
        package = await self._owned_package(
            user_id=user_id, application_id=application_id, package_id=package_id
        )
        preparation = await self._session.get(ApplicationPreparation, application_id)
        if preparation is None or preparation.reviewable_package_id != package.id:
            raise ReviewConflictError("Package is no longer current for review")
        return package


@pytest.fixture(scope="module")
def engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


@pytest.fixture(autouse=True)
async def clean_db(engine: AsyncEngine) -> None:
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as db:
        await db.execute(text("TRUNCATE TABLE users CASCADE"))
        await db.commit()


def _service(db: AsyncSession) -> ApplicationService:
    return ApplicationService(
        repository=ApplicationRepository(db),
        job_repository=JobRepository(db),
        preparation_service=_WorkspacePreparationService(session=db),
    )


async def test_workspace_is_owned_and_missing_preparation_stays_unwritten(
    engine: AsyncEngine,
) -> None:
    async with _factory(engine)() as db:
        application = await _seed(db)
        result = await _service(db).workspace(user_id=1, application_id=application.id)
        assert result.application.id == application.id
        assert result.preparation.state == "not_started"
        assert result.package_history == []
        assert await db.scalar(
            select(ApplicationPreparation).where(
                ApplicationPreparation.application_id == application.id
            )
        ) is None
        with pytest.raises(ApplicationNotFoundError):
            await _service(db).workspace(user_id=2, application_id=application.id)


async def test_workspace_keeps_expired_owned_history_readable(engine: AsyncEngine) -> None:
    async with _factory(engine)() as db:
        application = await _seed(db)
        job = await db.get(Job, application.job_id)
        assert job is not None
        job.status = "expired"
        await db.commit()
        result = await _service(db).workspace(user_id=1, application_id=application.id)
        assert result.application.status == "saved"
        assert result.job.live_context_available is False
        assert result.job.open_job_available is False


@pytest.mark.parametrize(
    "state,failure_code",
    [("queued", None), ("running", None), ("completed", None), ("failed", "unavailable")],
)
async def test_workspace_projects_current_review_lifecycle(
    engine: AsyncEngine, state: str, failure_code: str | None
) -> None:
    async with _factory(engine)() as db:
        application = await _seed(db)
        package = ApplicationPackage(
            application_id=application.id, revision=1, content={}, content_sha256="a" * 64,
            schema_version="application-package-v1", idempotency_key="package",
        )
        db.add(package)
        await db.commit()
        db.add(
            ApplicationPreparation(
                application_id=application.id,
                approved_package_id=package.id, reviewable_package_id=package.id,
            )
        )
        review = ApplicationDocumentReview(
            id="review-current",
            user_id=1,
            application_id=application.id,
            package_id=package.id,
            package_content_sha256=package.content_sha256,
            review_policy_version=REVIEW_POLICY_VERSION,
            idempotency_key="review",
            task_id="task-current",
            state=state,
            failure_code=failure_code,
        )
        db.add(review)
        await db.commit()

        result = await _service(db).workspace(user_id=1, application_id=application.id)

        assert result.current_document_review is not None
        assert result.current_document_review.id == review.id
        assert result.current_document_review.task_id == "task-current"
        assert result.current_document_review.state == state
        assert result.current_document_review.failure_code == failure_code


async def test_workspace_excludes_stale_hash_and_policy_reviews(engine: AsyncEngine) -> None:
    async with _factory(engine)() as db:
        application = await _seed(db)
        package = ApplicationPackage(
            application_id=application.id, revision=1, content={}, content_sha256="a" * 64,
            schema_version="application-package-v1", idempotency_key="package",
        )
        db.add(package)
        await db.commit()
        db.add(
            ApplicationPreparation(
                application_id=application.id,
                approved_package_id=package.id, reviewable_package_id=package.id,
            )
        )
        db.add_all([
            ApplicationDocumentReview(
                id="hash-mismatch", user_id=1, application_id=application.id,
                package_id=package.id, package_content_sha256="b" * 64,
                review_policy_version=REVIEW_POLICY_VERSION, idempotency_key="hash",
                task_id="task-hash", state="failed",
            ),
            ApplicationDocumentReview(
                id="policy-mismatch", user_id=1, application_id=application.id,
                package_id=package.id, package_content_sha256=package.content_sha256,
                review_policy_version="old-policy", idempotency_key="policy",
                task_id="task-policy", state="failed",
            ),
        ])
        await db.commit()

        result = await _service(db).workspace(user_id=1, application_id=application.id)
        assert result.current_document_review is None


async def test_review_admission_cache_acknowledgment_and_package_invariants(
    engine: AsyncEngine,
) -> None:
    async with _factory(engine)() as db:
        application = await _seed(db)
        content: dict[str, object] = {
            "resume": {
                "content": {"summary": "Built APIs"},
                "provenance": {"source_revision_sha256": "a"},
            },
            "cover_letter": {"content": "Built APIs for this role"},
        }
        encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        package = ApplicationPackage(
            application_id=application.id,
            revision=1,
            content=content,
            content_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
            schema_version="application-package-v1",
            idempotency_key="package",
        )
        db.add(package)
        await db.commit()
        preparation = ApplicationPreparation(
            application_id=application.id,
            approved_package_id=package.id,
            reviewable_package_id=package.id,
        )
        db.add(preparation)
        await db.commit()

        original_content = deepcopy(package.content)
        original_approval = preparation.approved_package_id
        store = _ReviewTaskStore()
        reviews = _PointerReviewService(db)
        first = await reviews.start(
            user_id=1, application_id=application.id, package_id=package.id,
            idempotency_key="first", rerun=False, task_store=store,
        )
        assert isinstance(first, DocumentReviewQueuedResponse)
        replay = await reviews.start(
            user_id=1, application_id=application.id, package_id=package.id,
            idempotency_key="first", rerun=False, task_store=store,
        )
        assert isinstance(replay, DocumentReviewQueuedResponse)
        active = await reviews.start(
            user_id=1, application_id=application.id, package_id=package.id,
            idempotency_key="different", rerun=False, task_store=store,
        )
        assert isinstance(active, DocumentReviewQueuedResponse)
        assert replay.review_id == first.review_id and replay.task_id == first.task_id
        assert active.review_id == first.review_id and active.task_id == first.task_id
        assert store.created == [("document_review", 1, first.task_id)]

        first_row = await db.get(ApplicationDocumentReview, first.review_id)
        assert first_row is not None
        first_row.state = "completed"
        first_row.completed_at = datetime.now(UTC)
        first_row.findings = [{
            "id": "f1", "document": "resume", "category": "repetition", "severity": "suggestion",
            "excerpt": "Built APIs", "locator": "/resume/content/summary",
            "rationale": "Repeated wording",
            "evidence": [{
                "role": "target", "document": "resume",
                "locator": "/resume/content/summary", "excerpt": "Built APIs",
            }], "proposed_action": "Vary the wording",
        }]
        await db.commit()
        cached = await reviews.start(
            user_id=1, application_id=application.id, package_id=package.id,
            idempotency_key="cache", rerun=False, task_store=store,
        )
        assert isinstance(cached, DocumentReviewResponse)
        assert cached.id == first.review_id
        assert len(store.created) == 1

        same_key_after_completion = await reviews.start(
            user_id=1, application_id=application.id, package_id=package.id,
            idempotency_key="first", rerun=False, task_store=store,
        )
        assert isinstance(same_key_after_completion, DocumentReviewResponse)
        assert same_key_after_completion.id == first.review_id

        rerun = await reviews.start(
            user_id=1, application_id=application.id, package_id=package.id,
            idempotency_key="rerun", rerun=True, task_store=store,
        )
        assert isinstance(rerun, DocumentReviewQueuedResponse)
        assert rerun.review_id != first.review_id and rerun.task_id != first.task_id
        assert len(store.created) == 2
        rerun_row = await db.get(ApplicationDocumentReview, rerun.review_id)
        assert rerun_row is not None
        rerun_row.state = "failed"
        await db.commit()
        after_failed = await reviews.start(
            user_id=1, application_id=application.id, package_id=package.id,
            idempotency_key="after-failed", rerun=False, task_store=store,
        )
        assert isinstance(after_failed, DocumentReviewQueuedResponse)
        assert after_failed.review_id not in {first.review_id, rerun.review_id}
        assert len(store.created) == 3
        after_failed_row = await db.get(ApplicationDocumentReview, after_failed.review_id)
        assert after_failed_row is not None
        after_failed_row.state = "running"
        await db.commit()
        running = await reviews.start(
            user_id=1, application_id=application.id, package_id=package.id,
            idempotency_key="while-running", rerun=False, task_store=store,
        )
        assert isinstance(running, DocumentReviewQueuedResponse)
        assert running.review_id == after_failed.review_id
        assert len(store.created) == 3

        after_failed_row.state = "completed"
        after_failed_row.completed_at = datetime.now(UTC)
        await db.commit()
        newest_cached = await reviews.start(
            user_id=1, application_id=application.id, package_id=package.id,
            idempotency_key="newest-cache", rerun=False, task_store=store,
        )
        assert isinstance(newest_cached, DocumentReviewResponse)
        assert newest_cached.id == after_failed.review_id

        successful_rerun = await reviews.start(
            user_id=1, application_id=application.id, package_id=package.id,
            idempotency_key="successful-rerun", rerun=True, task_store=store,
        )
        assert isinstance(successful_rerun, DocumentReviewQueuedResponse)
        successful_rerun_row = await db.get(
            ApplicationDocumentReview, successful_rerun.review_id
        )
        assert successful_rerun_row is not None
        successful_rerun_row.state = "completed"
        successful_rerun_row.completed_at = datetime.now(UTC)
        await db.commit()
        successful_rerun_cached = await reviews.start(
            user_id=1, application_id=application.id, package_id=package.id,
            idempotency_key="successful-rerun-cache", rerun=False, task_store=store,
        )
        assert isinstance(successful_rerun_cached, DocumentReviewResponse)
        assert successful_rerun_cached.id == successful_rerun.review_id

        failed_replay = await reviews.start(
            user_id=1, application_id=application.id, package_id=package.id,
            idempotency_key="failed-replay", rerun=True, task_store=store,
        )
        assert isinstance(failed_replay, DocumentReviewQueuedResponse)
        failed_replay_row = await db.get(ApplicationDocumentReview, failed_replay.review_id)
        assert failed_replay_row is not None
        failed_replay_row.state = "failed"
        await db.commit()
        failed_replay_result = await reviews.start(
            user_id=1, application_id=application.id, package_id=package.id,
            idempotency_key="failed-replay", rerun=False, task_store=store,
        )
        assert isinstance(failed_replay_result, DocumentReviewResponse)
        assert failed_replay_result.id == failed_replay.review_id

        valid = ReviewAcknowledgmentRequest(
            package_content_sha256=package.content_sha256,
            acknowledgments=[ReviewAcknowledgmentItem(finding_id="f1", disposition="keep_wording")],
        )
        acknowledged = await reviews.acknowledge(
            user_id=1, application_id=application.id, package_id=package.id,
            review_id=first.review_id, payload=valid,
        )
        assert acknowledged.id == first.review_id
        with pytest.raises(ReviewNotFoundError):
            await reviews.acknowledge(
                user_id=1, application_id=application.id, package_id=package.id,
                review_id=first.review_id,
                payload=valid.model_copy(update={
                    "acknowledgments": [
                        ReviewAcknowledgmentItem(
                            finding_id="foreign", disposition="keep_wording"
                        )
                    ]
                }),
            )
        with pytest.raises(ReviewNotFoundError):
            await reviews.acknowledge(
                user_id=1, application_id=application.id, package_id=package.id + 1,
                review_id=first.review_id, payload=valid,
            )
        with pytest.raises(ReviewConflictError):
            await reviews.acknowledge(
                user_id=1, application_id=application.id, package_id=package.id,
                review_id=first.review_id,
                payload=valid.model_copy(update={"package_content_sha256": "b" * 64}),
            )
        assert package.content == original_content
        assert preparation.approved_package_id == original_approval


async def test_review_admission_queues_after_failed_only_review(engine: AsyncEngine) -> None:
    async with _factory(engine)() as db:
        application = await _seed(db)
        content: dict[str, object] = {
            "resume": {
                "content": {"summary": "Built APIs"},
                "provenance": {"source_revision_sha256": "a"},
            },
        }
        encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        package = ApplicationPackage(
            application_id=application.id, revision=1, content=content,
            content_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
            schema_version="application-package-v1", idempotency_key="package",
        )
        db.add(package)
        await db.flush()
        db.add(ApplicationPreparation(
            application_id=application.id, reviewable_package_id=package.id
        ))
        failed = ApplicationDocumentReview(
            id="failed-review", user_id=1, application_id=application.id,
            package_id=package.id, package_content_sha256=package.content_sha256,
            review_policy_version=REVIEW_POLICY_VERSION, idempotency_key="failed",
            task_id="failed-task", state="failed",
        )
        db.add(failed)
        await db.commit()

        result = await _PointerReviewService(db).start(
            user_id=1, application_id=application.id, package_id=package.id,
            idempotency_key="new-request", rerun=False, task_store=_ReviewTaskStore(),
        )

        assert isinstance(result, DocumentReviewQueuedResponse)
        assert result.review_id != failed.id


@pytest.mark.parametrize("stale_identity", ["hash", "policy"])
async def test_review_admission_excludes_stale_completed_identity(
    engine: AsyncEngine, stale_identity: str
) -> None:
    async with _factory(engine)() as db:
        application = await _seed(db)
        content: dict[str, object] = {
            "resume": {
                "content": {"summary": "Built APIs"},
                "provenance": {"source_revision_sha256": "a"},
            },
        }
        encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        package = ApplicationPackage(
            application_id=application.id, revision=1, content=content,
            content_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
            schema_version="application-package-v1", idempotency_key="package",
        )
        db.add(package)
        await db.flush()
        db.add(ApplicationPreparation(
            application_id=application.id, reviewable_package_id=package.id
        ))
        package_hash = "b" * 64 if stale_identity == "hash" else package.content_sha256
        policy_version = REVIEW_POLICY_VERSION if stale_identity == "hash" else "old-policy"
        stale = ApplicationDocumentReview(
            id=f"stale-{stale_identity}", user_id=1, application_id=application.id,
            package_id=package.id, package_content_sha256=package_hash,
            review_policy_version=policy_version, idempotency_key="stale",
            task_id=f"stale-task-{stale_identity}", state="completed",
            completed_at=datetime.now(UTC),
        )
        db.add(stale)
        await db.commit()

        result = await _PointerReviewService(db).start(
            user_id=1, application_id=application.id, package_id=package.id,
            idempotency_key="current-request", rerun=False, task_store=_ReviewTaskStore(),
        )

        assert isinstance(result, DocumentReviewQueuedResponse)
        assert result.review_id != stale.id
