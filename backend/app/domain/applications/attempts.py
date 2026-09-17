"""Application handoff attempts: owned, package-bound browser correlation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.applications.exceptions import (
    ApplicationJobNotActionableError,
    ApplicationNotFoundError,
    PreparationVersionConflictError,
)
from app.domain.applications.schemas import AttemptCreateRequest, AttemptResponse
from app.domain.jobs.identity import (
    SourceIdentity,
    canonicalize_job_url,
    resolve_source_identity,
    safe_employer_navigation_url,
)
from app.models.application import Application
from app.models.application_attempt import ApplicationAttempt
from app.models.application_package import ApplicationPackage
from app.models.application_preparation import ApplicationPreparation
from app.models.job import Job
from app.models.job_source_link import JobSourceLink


class ApplicationAttemptService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, *, user_id: int, application_id: int, request: AttemptCreateRequest
    ) -> AttemptResponse:
        app = await self._session.scalar(
            select(Application)
            .where(Application.id == application_id, Application.user_id == user_id)
            .with_for_update()
        )
        if app is None:
            raise ApplicationNotFoundError(application_id)
        job = await self._session.get(Job, app.job_id)
        if job is None or job.status != "active":
            raise ApplicationJobNotActionableError
        preparation = await self._session.get(ApplicationPreparation, app.id, with_for_update=True)
        if preparation is None or preparation.version != request.expected_version:
            raise PreparationVersionConflictError()
        if preparation.approved_package_id != request.package_id:
            raise PreparationVersionConflictError()
        package = await self._session.scalar(select(ApplicationPackage).where(
            ApplicationPackage.id == request.package_id,
            ApplicationPackage.application_id == app.id,
        ))
        if package is None or package.content_sha256 != request.package_hash:
            raise PreparationVersionConflictError()
        # Re-project the preparation to ensure approval is still current, not merely present.
        from app.domain.applications.preparation import ApplicationPreparationService
        current = await ApplicationPreparationService(session=self._session).get(
            user_id=user_id, application_id=app.id
        )
        if current.state != "ready" or not current.approval_current:
            raise PreparationVersionConflictError()
        target = await self._selected_url(job.id)
        if target is None:
            raise ApplicationJobNotActionableError
        now = datetime.now(UTC)
        attempt = ApplicationAttempt(
            user_id=user_id, application_id=app.id, package_id=package.id,
            expires_at=now + timedelta(hours=24),
        )
        self._session.add(attempt)
        await self._session.flush()
        return AttemptResponse(
            attempt_id=attempt.id, expires_at=attempt.expires_at, external_url=target
        )

    async def resolve(self, *, user_id: int, url: str) -> list[dict[str, object]]:
        canonical = canonicalize_job_url(url)
        if canonical is None:
            return []
        browser_identity = resolve_source_identity(url)
        now = datetime.now(UTC)
        rows = (await self._session.execute(
            select(ApplicationAttempt, Application, Job, ApplicationPreparation)
            .join(Application, Application.id == ApplicationAttempt.application_id)
            .join(Job, Job.id == Application.job_id)
            .join(
                ApplicationPreparation,
                ApplicationPreparation.application_id == ApplicationAttempt.application_id,
            )
            .where(ApplicationAttempt.user_id == user_id, ApplicationAttempt.expires_at > now)
            .order_by(ApplicationAttempt.created_at.desc())
        )).all()
        results: list[dict[str, object]] = []
        from app.domain.applications.preparation import ApplicationPreparationService

        preparation_service = ApplicationPreparationService(session=self._session)
        for attempt, application, job, preparation in rows:
            if preparation.approved_package_id != attempt.package_id:
                continue
            current = await preparation_service.get(
                user_id=user_id, application_id=application.id
            )
            if current.state != "ready" or not current.approval_current:
                continue
            aliases = await self._aliases(job.id)
            if not self._matches_alias(
                canonical=canonical, browser_identity=browser_identity, aliases=aliases
            ):
                continue
            results.append({
                "attempt_id": attempt.id, "application_id": application.id,
                "role": job.title, "company": job.company, "expires_at": attempt.expires_at,
                "progress": await self._progress(attempt.id),
            })
        return results

    @staticmethod
    def _matches_alias(
        *, canonical: str, browser_identity: SourceIdentity | None, aliases: set[str]
    ) -> bool:
        """Match validated source identities before falling back to exact URL aliases."""
        if browser_identity is None:
            return False
        browser_native = (browser_identity.source_job_id, browser_identity.source_tenant)
        for alias in aliases:
            alias_identity = resolve_source_identity(alias)
            if alias_identity is None:
                continue
            alias_native = (alias_identity.source_job_id, alias_identity.source_tenant)
            if all(browser_native) and all(alias_native):
                if (
                    browser_identity.source,
                    *browser_native,
                ) == (
                    alias_identity.source,
                    *alias_native,
                ):
                    return True
            elif canonical == alias:
                return True
        return False

    async def get_valid(self, *, user_id: int, attempt_id: str) -> ApplicationAttempt | None:
        result = await self._session.scalar(select(ApplicationAttempt).where(
            ApplicationAttempt.id == attempt_id,
            ApplicationAttempt.user_id == user_id,
            ApplicationAttempt.expires_at > datetime.now(UTC),
        ))
        return result

    async def _aliases(self, job_id: int) -> set[str]:
        links = list(
            (await self._session.execute(
                select(JobSourceLink).where(JobSourceLink.job_id == job_id)
            )).scalars()
        )
        aliases: set[str] = set()
        for link in links:
            for value in (link.source_url, link.canonical_url, link.external_apply_url):
                canonical = canonicalize_job_url(value) if value else None
                if canonical:
                    aliases.add(canonical)
        return aliases

    async def _selected_url(self, job_id: int) -> str | None:
        links = list(
            (await self._session.execute(
                select(JobSourceLink).where(JobSourceLink.job_id == job_id)
            )).scalars()
        )
        for link in links:
            for candidate in (link.external_apply_url, link.canonical_url):
                if candidate:
                    safe_url = safe_employer_navigation_url(candidate)
                    if safe_url:
                        return safe_url
        return None

    async def _progress(self, attempt_id: str) -> str:
        from app.models.application_event import ApplicationEvent
        event_types = list(
            (await self._session.execute(
                select(ApplicationEvent.event_type)
                .where(ApplicationEvent.attempt_id == attempt_id)
                .order_by(ApplicationEvent.created_at.asc(), ApplicationEvent.id.asc())
            )).scalars()
        )
        precedence = (
            "confirmation_detected", "submission_unconfirmed", "autofill_completed",
            "form_detected", "opened", "cancelled",
        )
        for value in precedence:
            if value in event_types:
                return value
        return "opened"
