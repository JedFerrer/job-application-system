"""Regression coverage for resume uploads that do not alter match input."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.resume.matching_inputs import build_matching_input_fingerprint
from app.domain.resume.repository import ResumeImportApprovalResult
from app.domain.resume.schemas import ParsedProfile
from app.domain.resume.service import ResumeService, build_summary_text


class FakeResumeRepository:
    def __init__(self, previous_summary: str, previous_parsed: dict[str, object]) -> None:
        self.previous = SimpleNamespace(
            summary_text=previous_summary,
            parsed=previous_parsed,
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


class FakeProfileEditRepository:
    def __init__(self, profile: ParsedProfile) -> None:
        self.previous = SimpleNamespace(
            id=1,
            resume_id=2,
            parsed=profile.model_dump(),
            summary_text=build_summary_text(profile),
            embedding=[0.1, 0.2],
        )

    async def get_active_profile(self, user_id: int) -> object:
        del user_id
        return self.previous

    async def update_profile_parsed(self, **kwargs: object) -> object:
        self.previous.parsed = kwargs["parsed"]
        self.previous.summary_text = kwargs["summary_text"]
        self.previous.embedding = kwargs["embedding"]
        return self.previous


class FakeApprovalRepository:
    def __init__(self, result: ResumeImportApprovalResult) -> None:
        self.result = result

    async def approve_import_draft(
        self, *, user_id: int, resume_id: int
    ) -> ResumeImportApprovalResult:
        del user_id, resume_id
        return self.result


@pytest.mark.asyncio
async def test_unchanged_resume_matching_text_reuses_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = ParsedProfile(headline="Backend engineer", skills=["Python"])
    repository = FakeResumeRepository(build_summary_text(profile), profile.model_dump())

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
async def test_links_projects_languages_and_licenses_do_not_change_matching_input(
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
        languages=[{"language": "English", "proficiency": "Fluent"}],
        licenses=[{"name": "Registered Engineer", "issuer": "State Board"}],
    )
    repository = FakeResumeRepository(
        build_summary_text(previous_profile), previous_profile.model_dump()
    )

    async def parse(_: str) -> ParsedProfile:
        return profile

    async def fail_embed(_: str) -> list[float]:
        raise AssertionError("non-matching profile changes must not be embedded")

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


def test_matching_input_fingerprint_is_stable_and_dictionary_order_independent() -> None:
    profile = ParsedProfile(
        headline="Backend engineer",
        skills=["Python"],
        certifications=["AWS Certified Developer"],
        education=[{"degree": "Computer Science", "institution": "Institute"}],
    )
    payload = profile.model_dump()
    reordered = dict(reversed(list(payload.items())))

    assert build_matching_input_fingerprint(
        summary_text=build_summary_text(profile), parsed_profile=payload
    ) == build_matching_input_fingerprint(
        summary_text=build_summary_text(profile), parsed_profile=reordered
    )


@pytest.mark.parametrize(
    "field",
    [
        "headline",
        "skills",
        "preferred_roles",
        "years_experience",
        "work_history",
        "education",
        "certifications",
    ],
)
def test_matching_input_fingerprint_changes_for_production_matching_fields(
    field: str,
) -> None:
    base = ParsedProfile(
        headline="Backend engineer",
        skills=["Python"],
        preferred_roles=["Platform Engineer"],
        years_experience=3,
        work_history=[{"title": "Engineer", "company": "Acme", "highlights": ["Built APIs"]}],
        education=[{"degree": "Computer Science", "institution": "Institute"}],
        certifications=["AWS Certified Developer"],
    )
    changed = base.model_copy(deep=True)
    if field == "headline":
        changed.headline = "Principal backend engineer"
    elif field == "skills":
        changed.skills = ["Python", "FastAPI"]
    elif field == "preferred_roles":
        changed.preferred_roles = ["Staff Engineer"]
    elif field == "years_experience":
        changed.years_experience = 4
    elif field == "work_history":
        changed.work_history[0].highlights = ["Built distributed APIs"]
    elif field == "education":
        changed.education[0].degree = "Software Engineering"
    else:
        changed.certifications = ["AWS Solutions Architect"]

    assert build_matching_input_fingerprint(
        summary_text=build_summary_text(base), parsed_profile=base.model_dump()
    ) != build_matching_input_fingerprint(
        summary_text=build_summary_text(changed), parsed_profile=changed.model_dump()
    )


def test_matching_input_fingerprint_ignores_fields_excluded_from_matching() -> None:
    base = ParsedProfile(headline="Backend engineer", skills=["Python"])
    changed_data = base.model_dump()
    changed_data.update(
        languages=[{"language": "Spanish", "proficiency": "Fluent"}],
        licenses=[{"name": "Registered Engineer", "issuer": "Board"}],
        links={"github": "https://github.com/example"},
        projects=[{"name": "Forecasting Tool"}],
    )
    changed = ParsedProfile.model_validate(changed_data)

    assert build_matching_input_fingerprint(
        summary_text=build_summary_text(base), parsed_profile=base.model_dump()
    ) == build_matching_input_fingerprint(
        summary_text=build_summary_text(changed), parsed_profile=changed.model_dump()
    )


@pytest.mark.asyncio
async def test_profile_edit_certification_change_refreshes_without_reembedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = ParsedProfile(headline="Engineer", skills=["Python"])
    edited = ParsedProfile(
        headline="Engineer", skills=["Python"], certifications=["AWS"]
    )
    repository = FakeProfileEditRepository(base)

    async def fail_embed(_: str) -> list[float]:
        raise AssertionError("unchanged embedding input must not be embedded")

    monkeypatch.setattr("app.domain.resume.service.embed_text", fail_embed)
    result = await ResumeService(repository).update_profile(  # type: ignore[arg-type]
        user_id=7, profile=edited
    )

    assert result.matching_profile_changed is True


@pytest.mark.asyncio
async def test_profile_edit_language_change_does_not_refresh_or_reembed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = ParsedProfile(headline="Engineer", skills=["Python"])
    edited = ParsedProfile(
        headline="Engineer",
        skills=["Python"],
        languages=[{"language": "Spanish", "proficiency": "Fluent"}],
    )
    repository = FakeProfileEditRepository(base)

    async def fail_embed(_: str) -> list[float]:
        raise AssertionError("unchanged embedding input must not be embedded")

    monkeypatch.setattr("app.domain.resume.service.embed_text", fail_embed)
    result = await ResumeService(repository).update_profile(  # type: ignore[arg-type]
        user_id=7, profile=edited
    )

    assert result.matching_profile_changed is False


@pytest.mark.asyncio
async def test_approval_with_missing_previous_embedding_falls_back_to_refresh() -> None:
    profile = ParsedProfile(headline="Engineer", skills=["Python"])
    result = ResumeImportApprovalResult(
        profile=SimpleNamespace(
            id=2,
            resume_id=3,
            parsed=profile.model_dump(),
            summary_text=build_summary_text(profile),
            embedding=None,
        ),  # type: ignore[arg-type]
        activated=True,
        previous_summary_text=build_summary_text(profile),
        previous_parsed=profile.model_dump(),
        previous_embedding=None,
    )

    response = await ResumeService(FakeApprovalRepository(result)).approve_import_draft(
        user_id=7, resume_id=3
    )

    assert response.matching_profile_changed is True
