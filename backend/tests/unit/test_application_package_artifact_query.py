"""The package response reads artifact metadata without fetching stored bytes."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.domain.applications.preparation import ApplicationPreparationService
from app.models.application_artifact import ApplicationArtifact
from app.models.application_package import ApplicationPackage

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_package_response_artifact_query_selects_metadata_without_content() -> None:
    created_at = datetime(2026, 1, 2, tzinfo=UTC)
    package = ApplicationPackage(
        id=7,
        application_id=3,
        revision=2,
        content={"resume": {}},
        content_sha256="a" * 64,
        schema_version="application-package-v1",
        idempotency_key="test",
        created_at=created_at,
    )
    artifact = ApplicationArtifact(
        id=11,
        package_id=7,
        application_id=3,
        user_id=1,
        document_type="resume",
        format="pdf",
        filename="resume.pdf",
        byte_length=123,
        sha256="b" * 64,
        validation_state="validated",
        failure_category=None,
        created_at=created_at,
    )
    session = MagicMock()
    session.scalars = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[artifact])))

    response = await ApplicationPreparationService(session=session)._package_response(package)

    statement = session.scalars.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))  # type: ignore[no-untyped-call]
    selected = sql.split(" FROM application_artifacts", 1)[0]
    assert "application_artifacts.content" not in selected
    for name in (
        "id",
        "package_id",
        "document_type",
        "format",
        "filename",
        "byte_length",
        "sha256",
        "validation_state",
        "failure_category",
        "created_at",
    ):
        assert f"application_artifacts.{name}" in selected
    assert response.artifacts[0].model_dump() == {
        "id": 11,
        "package_id": 7,
        "document_type": "resume",
        "format": "pdf",
        "filename": "resume.pdf",
        "byte_length": 123,
        "sha256": "b" * 64,
        "validation_state": "validated",
        "failure_category": None,
        "created_at": created_at,
    }
