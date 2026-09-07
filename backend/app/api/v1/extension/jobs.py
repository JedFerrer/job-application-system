"""Extension job import API endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentExtensionUser, ExtensionJobImportServiceDep
from app.domain.extension.schemas import (
    ExtensionJobImportRequest,
    ExtensionJobImportResponse,
    ExtensionJobLookupResponse,
)

router = APIRouter(prefix="/extension/jobs", tags=["extension-jobs"])


@router.post("/import", response_model=ExtensionJobImportResponse)
async def import_job_from_browser(
    payload: ExtensionJobImportRequest,
    current_user: CurrentExtensionUser,
    service: ExtensionJobImportServiceDep,
) -> ExtensionJobImportResponse:
    """Import a job detected by the browser extension.

    Deduplicates by fingerprint. Links to the current user. Queues embedding
    and match scoring. Records a job_imported event.

    Importing a job does NOT create an application record and does NOT mean
    the user applied. It only saves the job for review and analysis.
    """
    return await service.import_job(
        user_id=current_user.id,
        request=payload,
    )


@router.get("/by-url", response_model=ExtensionJobLookupResponse)
async def lookup_job_by_url(
    url: str,
    current_user: CurrentExtensionUser,
    service: ExtensionJobImportServiceDep,
) -> ExtensionJobLookupResponse:
    """Look up an existing job by its source URL."""
    return await service.lookup_by_url(user_id=current_user.id, source_url=url)
