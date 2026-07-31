from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.resume.exceptions import ResumeTextTooLongError
from app.domain.resume.schemas import ParsedProfile
from app.domain.resume.service import ResumeService
from app.infrastructure import resume_parser
from app.infrastructure.resume_parser import ExtractedResumeText

pytestmark = pytest.mark.unit


class NoSideEffectRepository:
    def __init__(self, previous: object | None = None) -> None:
        self.previous = previous
        self.lock_called = False
        self.add_resume = AsyncMock(side_effect=AssertionError("resume persisted"))
        self.add_profile = AsyncMock(side_effect=AssertionError("profile persisted"))

    async def get_active_profile(self, user_id: int) -> object | None:
        del user_id
        return self.previous

    async def lock_user_for_import(self, user_id: int) -> None:
        del user_id
        self.lock_called = True


async def _oversized_import(
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    repository: NoSideEffectRepository | None = None,
) -> tuple[ResumeTextTooLongError, NoSideEffectRepository, AsyncMock, AsyncMock]:
    repository = repository or NoSideEffectRepository()
    parse = AsyncMock(side_effect=AssertionError("LLM parsing called"))
    embed = AsyncMock(side_effect=AssertionError("embedding called"))
    monkeypatch.setattr(
        "app.domain.resume.service.extract_text_from_bytes",
        lambda *_args: ExtractedResumeText(
            "x" * (resume_parser.MAX_RESUME_TEXT_CHARS + 1), []
        ),
    )
    monkeypatch.setattr("app.domain.resume.service.parse_resume_text", parse)
    monkeypatch.setattr("app.domain.resume.service.embed_text", embed)

    with pytest.raises(ResumeTextTooLongError) as raised:
        await ResumeService(repository).upload_and_parse(  # type: ignore[arg-type]
            user_id=7, filename=filename, content=b"source"
        )
    return raised.value, repository, parse, embed


@pytest.mark.asyncio
async def test_exact_character_limit_preserves_parse_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = ParsedProfile(headline="Engineer")
    repository = NoSideEffectRepository()
    repository.add_resume.side_effect = None
    repository.add_resume.return_value = SimpleNamespace(
        id=1,
        filename="resume.pdf",
        is_active=True,
        lifecycle_status="active",
    )
    repository.add_profile.side_effect = None
    repository.add_profile.return_value = SimpleNamespace()
    monkeypatch.setattr(
        "app.domain.resume.service.extract_text_from_bytes",
        lambda *_args: ExtractedResumeText(
            "x" * resume_parser.MAX_RESUME_TEXT_CHARS, []
        ),
    )
    monkeypatch.setattr(
        "app.domain.resume.service.parse_resume_text",
        AsyncMock(return_value=profile),
    )
    monkeypatch.setattr(
        "app.domain.resume.service.embed_text",
        AsyncMock(return_value=[0.1, 0.2]),
    )

    result = await ResumeService(repository).upload_and_parse(  # type: ignore[arg-type]
        user_id=7, filename="resume.pdf", content=b"source"
    )

    assert result.profile == profile
    assert repository.lock_called is True


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["resume.pdf", "resume.docx"])
async def test_oversized_pdf_and_docx_fail_before_all_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
) -> None:
    error, repository, parse, embed = await _oversized_import(monkeypatch, filename)

    assert error.actual_chars == resume_parser.MAX_RESUME_TEXT_CHARS + 1
    assert error.max_chars == resume_parser.MAX_RESUME_TEXT_CHARS
    assert str(error) == (
        "This resume contains 60001 extracted characters, exceeding the "
        "60,000-character import limit. The resume was not imported."
    )
    assert "x" * 100 not in str(error)
    parse.assert_not_awaited()
    embed.assert_not_awaited()
    repository.add_resume.assert_not_awaited()
    repository.add_profile.assert_not_awaited()
    assert repository.lock_called is False


@pytest.mark.asyncio
async def test_oversized_replacement_leaves_active_profile_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = SimpleNamespace(summary_text="existing", embedding=[0.1, 0.2])
    error, repository, parse, embed = await _oversized_import(
        monkeypatch, "replacement.pdf", NoSideEffectRepository(existing)
    )

    assert "The resume was not imported." in str(error)
    assert repository.previous is existing
    parse.assert_not_awaited()
    embed.assert_not_awaited()
    repository.add_resume.assert_not_awaited()
    repository.add_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_background_task_preserves_actionable_failure_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1 import resume as resume_api

    error = ResumeTextTooLongError(
        resume_parser.MAX_RESUME_TEXT_CHARS + 1,
        resume_parser.MAX_RESUME_TEXT_CHARS,
    )

    class FakeDb:
        def __init__(self) -> None:
            self.rollback = AsyncMock()
            self.commit = AsyncMock()

        async def __aenter__(self) -> FakeDb:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    class FakeStore:
        def __init__(self) -> None:
            self.updates: list[dict[str, object]] = []

        async def update(self, _task_id: str, **values: object) -> None:
            self.updates.append(values)

    class FailingService:
        def __init__(self, _repository: object) -> None:
            pass

        async def upload_and_parse(self, **_kwargs: object) -> None:
            raise error

    db = FakeDb()
    store = FakeStore()
    monkeypatch.setattr(resume_api, "get_redis", lambda: object())
    monkeypatch.setattr(resume_api, "TaskStore", lambda _redis: store)
    monkeypatch.setattr(resume_api, "async_session_factory", lambda: db)
    monkeypatch.setattr("app.domain.resume.service.ResumeService", FailingService)

    await resume_api._run_resume_bg("task-1", 7, "resume.pdf", b"source")

    assert db.rollback.await_count == 1
    assert db.commit.await_count == 0
    assert store.updates == [
        {"status": "failed", "error": str(error), "message": str(error)}
    ]
