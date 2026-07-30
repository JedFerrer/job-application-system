"""Resume import lifecycle isolation: first imports activate, replacements draft."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.resume.lifecycle import ResumeLifecycleStatus
from app.domain.resume.schemas import ParsedProfile, ResumeResponse
from app.domain.resume.service import ResumeService, build_summary_text


class LifecycleRepository:
    def __init__(self, previous: object | None) -> None:
        self.previous = previous
        self.added_resume: dict[str, object] | None = None

    async def get_active_profile(self, user_id: int) -> object | None:
        del user_id
        return self.previous

    async def lock_user_for_import(self, user_id: int) -> None:
        del user_id

    async def deactivate_existing(self, user_id: int) -> None:
        raise AssertionError(f"draft imports must not deactivate user {user_id}")

    async def add_resume(self, **kwargs: object) -> object:
        self.added_resume = kwargs
        return SimpleNamespace(
            id=9,
            filename=kwargs["filename"],
            is_active=kwargs["is_active"],
            lifecycle_status=getattr(
                kwargs["lifecycle_status"], "value", kwargs["lifecycle_status"]
            ),
        )

    async def add_profile(self, **kwargs: object) -> object:
        return SimpleNamespace(**kwargs)


async def _run_upload(
    monkeypatch: pytest.MonkeyPatch, repository: LifecycleRepository
) -> ResumeResponse:
    profile = ParsedProfile(headline="Backend engineer", skills=["Python"])

    async def parse(_: str) -> ParsedProfile:
        return profile

    monkeypatch.setattr("app.domain.resume.service.extract_text_from_bytes", lambda *_: "text")
    monkeypatch.setattr("app.domain.resume.service.parse_resume_text", parse)
    monkeypatch.setattr("app.domain.resume.service.embed_text", lambda _: _embedding())
    return await ResumeService(repository).upload_and_parse(  # type: ignore[arg-type]
        user_id=7, filename="resume.pdf", content=b"test"
    )


async def _embedding() -> list[float]:
    return [0.1, 0.2]


@pytest.mark.asyncio
async def test_first_import_becomes_active(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = LifecycleRepository(None)
    result = await _run_upload(monkeypatch, repository)

    assert repository.added_resume is not None
    assert repository.added_resume["is_active"] is True
    assert repository.added_resume["lifecycle_status"] == ResumeLifecycleStatus.ACTIVE
    assert result.is_active is True
    assert result.lifecycle_status == "active"
    assert result.matching_profile_changed is True


@pytest.mark.asyncio
async def test_replacement_import_is_draft_and_keeps_active_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = ParsedProfile(headline="Existing engineer", skills=["Python"])
    repository = LifecycleRepository(
        SimpleNamespace(summary_text=build_summary_text(profile), embedding=[0.1, 0.2])
    )
    result = await _run_upload(monkeypatch, repository)

    assert repository.added_resume is not None
    assert repository.added_resume["is_active"] is False
    assert repository.added_resume["lifecycle_status"] == ResumeLifecycleStatus.DRAFT
    assert result.is_active is False
    assert result.lifecycle_status == "draft"
    assert result.matching_profile_changed is False


class ConcurrentRepository(LifecycleRepository):
    """Transactional stand-in proving the per-user lock serializes imports."""

    def __init__(self) -> None:
        super().__init__(None)
        import asyncio

        self._lock = asyncio.Lock()

    async def lock_user_for_import(self, user_id: int) -> None:
        del user_id
        await self._lock.acquire()

    async def add_profile(self, **kwargs: object) -> object:
        result = await super().add_profile(**kwargs)
        if self.previous is None:
            self.previous = SimpleNamespace(
                summary_text=kwargs["summary_text"], embedding=kwargs["embedding"]
            )
        self._lock.release()
        return result


@pytest.mark.asyncio
async def test_concurrent_first_imports_create_only_one_active_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    repository = ConcurrentRepository()
    results = await asyncio.gather(
        _run_upload(monkeypatch, repository),
        _run_upload(monkeypatch, repository),
    )

    assert sum(result.is_active for result in results) == 1
    assert sum(
        result.lifecycle_status == ResumeLifecycleStatus.DRAFT for result in results
    ) == 1
