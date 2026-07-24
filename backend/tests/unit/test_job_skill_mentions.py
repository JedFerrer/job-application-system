"""Persistence foundation tests for job skill mentions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import insert, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domain.matching.job_skill_mention_repository import JobSkillMentionRepository
from app.domain.matching.job_skill_mentions import JobSkillMentionService
from app.domain.matching.skill_types import SkillExtractionReport, SkillMention
from app.models.job import Job
from app.models.job_skill_mention import JobSkillMention

pytestmark = pytest.mark.unit


def _mention(skill_id: str, *, classification: str = "required") -> SkillMention:
    return SkillMention(
        skill_id=skill_id,
        canonical_label=skill_id.removeprefix("local:"),
        classification=classification,  # type: ignore[arg-type]
        job_excerpt=f"Requires {skill_id}",
        start_offset=0,
        end_offset=len(skill_id),
        matched_text=skill_id,
        analyzer_version="skill-extractor-v1",
    )


@pytest.mark.asyncio
async def test_repository_replacement_is_scoped_and_empty_clears() -> None:
    session = AsyncMock()
    repository = JobSkillMentionRepository(session)

    await repository.replace_for_job(7, [_mention("local:python")])
    await repository.replace_for_job(7, [])

    assert session.execute.await_count == 3
    first_delete = session.execute.await_args_list[0].args[0]
    assert "job_skill_mentions.job_id" in str(first_delete)
    assert first_delete.compile(dialect=postgresql.dialect()).params == {"job_id_1": 7}
    second_delete = session.execute.await_args_list[2].args[0]
    assert second_delete.compile(dialect=postgresql.dialect()).params == {"job_id_1": 7}


def test_model_declares_required_columns_constraints_and_indexes() -> None:
    table = JobSkillMention.__table__

    assert {
        "id",
        "job_id",
        "skill_id",
        "canonical_label",
        "classification",
        "job_excerpt",
        "start_offset",
        "end_offset",
        "analyzer_version",
        "created_at",
        "updated_at",
    } == set(table.columns.keys())
    assert any(
        constraint.name == "uq_job_skill_mention_job_skill_classification"
        for constraint in table.constraints
    )
    assert {
        index.name
        for index in table.indexes
    } == {
        "ix_job_skill_mentions_job_id",
        "ix_job_skill_mentions_skill_id",
        "ix_job_skill_mentions_classification",
    }
    foreign_key = next(iter(table.c.job_id.foreign_keys))
    assert foreign_key.target_fullname == "jobs.id"
    assert foreign_key.ondelete == "CASCADE"


@pytest.mark.asyncio
async def test_refresh_replaces_with_exact_classified_mentions_and_is_idempotent() -> None:
    repository = AsyncMock()
    mention = _mention("local:python")
    report = SkillExtractionReport(mentions=(mention,))
    extractor = SimpleNamespace(extract=Mock(return_value=report))
    classifier = Mock(return_value=report)
    service = JobSkillMentionService(
        repository,
        extractor=extractor,  # type: ignore[arg-type]
        classifier=classifier,
    )

    assert await service.refresh_for_description(job_id=12, description="Python") is True
    assert await service.refresh_for_description(job_id=12, description="Python") is True

    assert repository.replace_for_job.await_args_list == [
        ((12, (mention,)),),
        ((12, (mention,)),),
    ]


@pytest.mark.asyncio
async def test_refresh_classifier_failure_preserves_existing_rows() -> None:
    repository = AsyncMock()
    extractor = SimpleNamespace(
        extract=Mock(return_value=SkillExtractionReport(mentions=(_mention("local:python"),)))
    )
    service = JobSkillMentionService(
        repository,
        extractor=extractor,  # type: ignore[arg-type]
        classifier=Mock(side_effect=ValueError("invalid report")),
    )

    assert await service.refresh_for_description(job_id=13, description="Python") is False
    repository.replace_for_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_failure_preserves_existing_rows() -> None:
    repository = AsyncMock()
    extractor = SimpleNamespace(extract=Mock(side_effect=RuntimeError("boom")))
    service = JobSkillMentionService(repository, extractor=extractor)  # type: ignore[arg-type]

    refreshed = await service.refresh_for_description(job_id=11, description="Python")

    assert refreshed is False
    repository.replace_for_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_default_extractor_failure_preserves_existing_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = AsyncMock()
    monkeypatch.setattr(
        "app.domain.matching.job_skill_mentions.default_skill_extractor",
        Mock(side_effect=RuntimeError("catalog unavailable")),
    )
    service = JobSkillMentionService(repository)

    assert await service.refresh_for_description(job_id=14, description="Python") is False
    repository.replace_for_job.assert_not_awaited()


@pytest.fixture
async def postgres_session() -> AsyncIterator[AsyncSession]:
    """Provide an isolated session when the migrated PostgreSQL DB is available."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        try:
            async with engine.begin() as connection:
                await connection.execute(text("SELECT 1 FROM job_skill_mentions LIMIT 1"))
        except Exception as exc:  # noqa: BLE001 - unavailable local DB skips integration coverage
            pytest.skip(f"PostgreSQL Phase 2C1 integration database unavailable: {exc}")

        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await session.execute(
                text("TRUNCATE TABLE job_skill_mentions, jobs RESTART IDENTITY CASCADE")
            )
            await session.commit()
            yield session
    finally:
        await engine.dispose()


async def _create_jobs(session: AsyncSession) -> tuple[int, int]:
    result = await session.execute(
        insert(Job)
        .values(
            [
                {
                    "fingerprint": "mention-job-a-01",
                    "title": "Mention Test A",
                    "source": "test",
                    "source_url": "https://example.test/a",
                    "raw_response": {},
                },
                {
                    "fingerprint": "mention-job-b-01",
                    "title": "Mention Test B",
                    "source": "test",
                    "source_url": "https://example.test/b",
                    "raw_response": {},
                },
            ]
        )
        .returning(Job.id)
    )
    job_ids = list(result.scalars().all())
    await session.commit()
    return int(job_ids[0]), int(job_ids[1])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_replacement_persists_exact_mentions(
    postgres_session: AsyncSession,
) -> None:
    job_id, _ = await _create_jobs(postgres_session)
    repository = JobSkillMentionRepository(postgres_session)
    expected = [_mention("local:python"), _mention("local:docker", classification="preferred")]

    await repository.replace_for_job(job_id, expected)
    await postgres_session.commit()

    rows = await repository.list_for_job(job_id)
    assert [(row.skill_id, row.classification, row.job_excerpt) for row in rows] == [
        (mention.skill_id, mention.classification, mention.job_excerpt)
        for mention in expected
    ]
    assert [(row.start_offset, row.end_offset, row.analyzer_version) for row in rows] == [
        (mention.start_offset, mention.end_offset, mention.analyzer_version)
        for mention in expected
    ]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_replacement_is_idempotent_and_empty_clears(
    postgres_session: AsyncSession,
) -> None:
    job_id, _ = await _create_jobs(postgres_session)
    repository = JobSkillMentionRepository(postgres_session)
    mentions = [_mention("local:python")]

    await repository.replace_for_job(job_id, mentions)
    await postgres_session.commit()
    await repository.replace_for_job(job_id, mentions)
    await postgres_session.commit()
    assert len(await repository.list_for_job(job_id)) == 1

    await repository.replace_for_job(job_id, [])
    await postgres_session.commit()
    assert await repository.list_for_job(job_id) == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_replacement_preserves_other_job_rows(
    postgres_session: AsyncSession,
) -> None:
    first_job_id, second_job_id = await _create_jobs(postgres_session)
    repository = JobSkillMentionRepository(postgres_session)
    second_mention = _mention("local:docker")

    await repository.replace_for_job(first_job_id, [_mention("local:python")])
    await repository.replace_for_job(second_job_id, [second_mention])
    await postgres_session.commit()
    await repository.replace_for_job(first_job_id, [])
    await postgres_session.commit()

    rows = await repository.list_for_job(second_job_id)
    assert len(rows) == 1
    assert rows[0].skill_id == second_mention.skill_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_duplicate_skill_classification_is_rejected(
    postgres_session: AsyncSession,
) -> None:
    job_id, _ = await _create_jobs(postgres_session)
    repository = JobSkillMentionRepository(postgres_session)
    duplicate = _mention("local:python")

    with pytest.raises(IntegrityError):
        await repository.replace_for_job(job_id, [duplicate, duplicate])
    await postgres_session.rollback()
