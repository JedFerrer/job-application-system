"""Regression coverage for resume uploads that do not alter match input."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.resume.schemas import ParsedProfile
from app.domain.resume.service import ResumeService, build_summary_text


class FakeResumeRepository:
    def __init__(self, previous_summary: str) -> None:
        self.previous = SimpleNamespace(
            summary_text=previous_summary,
            embedding=[0.1, 0.2],
        )

    async def get_active_profile(self, user_id: int) -> object:
        del user_id
        return self.previous

    async def lock_user_for_import(self, user_id: int) -> None:
        del user_id

    async def deactivate_existing(self, user_id: int) -> None:
        del user_id

    async def add_resume(
        self, user_id: int, filename: str, raw_text: str, **kwargs: object
    ) -> object:
        del user_id, raw_text
        return SimpleNamespace(
            id=3,
            filename=filename,
            is_active=kwargs.get("is_active", True),
            lifecycle_status=getattr(kwargs.get("lifecycle_status"), "value", "active"),
        )

    async def add_profile(self, **kwargs: object) -> object:
        return SimpleNamespace(**kwargs)


@pytest.mark.asyncio
async def test_unchanged_resume_matching_text_reuses_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = ParsedProfile(headline="Backend engineer", skills=["Python"])
    repository = FakeResumeRepository(build_summary_text(profile))

    async def parse(_: str) -> ParsedProfile:
        return profile

    async def fail_embed(_: str) -> list[float]:
        raise AssertionError("unchanged matching text must not be embedded")

    monkeypatch.setattr("app.domain.resume.service.extract_text_from_bytes", lambda *_: "text")
    monkeypatch.setattr("app.domain.resume.service.parse_resume_text", parse)
    monkeypatch.setattr("app.domain.resume.service.embed_text", fail_embed)

    result = await ResumeService(repository).upload_and_parse(  # type: ignore[arg-type]
        user_id=7,
        filename="resume.pdf",
        content=b"test",
    )

    assert result.matching_profile_changed is False


@pytest.mark.asyncio
async def test_links_and_projects_only_do_not_change_matching_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_profile = ParsedProfile(
        headline="Backend engineer",
        skills=["Python"],
    )
    profile = ParsedProfile(
        headline="Backend engineer",
        skills=["Python"],
        links={"github": "https://github.com/ada"},
        projects=[{"name": "Forecasting Tool", "technologies": ["Python"]}],
    )
    repository = FakeResumeRepository(build_summary_text(previous_profile))

    async def parse(_: str) -> ParsedProfile:
        return profile

    async def fail_embed(_: str) -> list[float]:
        raise AssertionError("links/projects-only changes must not be embedded")

    monkeypatch.setattr("app.domain.resume.service.extract_text_from_bytes", lambda *_: "text")
    monkeypatch.setattr("app.domain.resume.service.parse_resume_text", parse)
    monkeypatch.setattr("app.domain.resume.service.embed_text", fail_embed)

    assert build_summary_text(profile) == build_summary_text(previous_profile)

    result = await ResumeService(repository).upload_and_parse(  # type: ignore[arg-type]
        user_id=7,
        filename="resume.pdf",
        content=b"test",
    )

    assert result.matching_profile_changed is False
