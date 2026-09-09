"""HTTP mapping coverage for Unit 2A preparation/package contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import (
    get_application_preparation_service,
    get_application_service,
    get_current_user,
)
from app.api.v1 import applications as applications_module
from app.core.lifespan import register_exception_handlers
from app.domain.applications.exceptions import (
    ApplicationJobNotActionableError,
    ApplicationNotFoundError,
    PackageApprovalConflictError,
    PackageIdempotencyConflictError,
    PreparationDiscardConfirmationRequiredError,
    PreparationSelectionInvalidError,
    PreparationVersionConflictError,
)
from app.domain.applications.schemas import ApplicationPackageResponse
from app.models.user import User

pytestmark = pytest.mark.unit


def _app(*, preparation: object, application: object | None = None) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(applications_module.router)

    async def current_user() -> User:
        return User(id=1, email="owner@example.test", name="Owner")

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_application_preparation_service] = lambda: preparation
    if application is not None:
        app.dependency_overrides[get_application_service] = lambda: application
    return app


class _Preparation:
    def __init__(self, error: Exception | None = None, created: bool = True) -> None:
        self.error = error
        self.created = created

    async def patch(self, **_kwargs: object) -> object:
        if self.error:
            raise self.error
        raise AssertionError("response is not needed for this mapping")

    async def approve(self, **_kwargs: object) -> object:
        if self.error:
            raise self.error
        raise AssertionError("response is not needed for this mapping")

    async def create_package(self, **_kwargs: object) -> tuple[ApplicationPackageResponse, bool]:
        if self.error:
            raise self.error
        return (
            ApplicationPackageResponse(
                id=1,
                application_id=1,
                revision=1,
                content={},
                content_sha256="a" * 64,
                schema_version="application-package-v1",
                created_at=datetime.now(UTC),
            ),
            self.created,
        )


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ApplicationNotFoundError(1), 404),
        (PreparationVersionConflictError(), 409),
        (PreparationSelectionInvalidError("wrong job"), 422),
        (ApplicationJobNotActionableError(), 409),
    ],
)
def test_patch_preparation_maps_owner_selection_version_and_actionability(
    error: Exception, status: int
) -> None:
    response = TestClient(_app(preparation=_Preparation(error))).patch(
        "/applications/1/preparation", json={"expected_version": 0}
    )
    assert response.status_code == status


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ApplicationNotFoundError(1), 404),
        (PackageApprovalConflictError(), 409),
    ],
)
def test_approve_maps_out_of_scope_package_to_404_and_conflict_to_409(
    error: Exception, status: int
) -> None:
    response = TestClient(_app(preparation=_Preparation(error))).post(
        "/applications/1/preparation/approve",
        json={"expected_version": 0, "package_id": 2, "content_sha256": "a" * 64},
    )
    assert response.status_code == status


@pytest.mark.parametrize("created, status", [(True, 201), (False, 200)])
def test_package_create_maps_created_and_reused_statuses(created: bool, status: int) -> None:
    response = TestClient(_app(preparation=_Preparation(created=created))).post(
        "/applications/1/packages",
        headers={"Idempotency-Key": "key"},
        json={"expected_version": 0},
    )
    assert response.status_code == status


def test_package_idempotency_conflict_maps_to_409() -> None:
    response = TestClient(_app(preparation=_Preparation(PackageIdempotencyConflictError()))).post(
        "/applications/1/packages",
        headers={"Idempotency-Key": "key"},
        json={"expected_version": 0},
    )
    assert response.status_code == 409


def test_delete_history_confirmation_maps_to_409() -> None:
    class _Applications:
        async def delete(self, **_kwargs: object) -> None:
            raise PreparationDiscardConfirmationRequiredError()

    response = TestClient(
        _app(preparation=_Preparation(), application=_Applications())
    ).delete("/applications/1")
    assert response.status_code == 409
