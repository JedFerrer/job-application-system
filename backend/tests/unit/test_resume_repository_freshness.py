"""Repository boundary tests for authoritative profile fingerprints."""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any, cast

import pytest

from app.domain.matching.profile_fingerprint import profile_matching_hash

# Repository behavior can be exercised without importing the application's
# database model package (which requires the optional asyncpg driver).
models = ModuleType("app.models")
resume_models = ModuleType("app.models.resume")
resume_models.CandidateProfile = object  # type: ignore[attr-defined]
resume_models.Resume = object  # type: ignore[attr-defined]
resume_models.TailoredResumeDraft = object  # type: ignore[attr-defined]
sys.modules.setdefault("app.models", models)
sys.modules.setdefault("app.models.resume", resume_models)

from app.domain.resume import repository as repository_module  # noqa: E402
from app.domain.resume.repository import ResumeRepository  # noqa: E402


class FakeSession:
    def __init__(self) -> None:
        self.added: object | None = None

    def add(self, value: object) -> None:
        self.added = value

    async def flush(self) -> None:
        return None


class FakeProfile:
    profile_matching_hash: str | None

    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)
        self.profile_matching_hash = cast(str | None, kwargs.get("profile_matching_hash"))


@pytest.mark.asyncio
async def test_create_and_update_compute_hash_at_repository_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repository_module, "CandidateProfile", FakeProfile)
    repo = ResumeRepository(FakeSession())  # type: ignore[arg-type]
    parsed: dict[str, object] = {
        "headline": "Backend engineer",
        "skills": ["Python"],
        "work_history": [{"title": "Engineer", "company": "Acme", "highlights": ["Built APIs"]}],
        "education": [{"degree": "BS Computer Science", "institution": "State U", "year": "2018"}],
    }
    created = await repo.add_profile(
        user_id=1, resume_id=2, parsed=parsed, summary_text="x", embedding=[0.1]
    )
    assert created.profile_matching_hash == profile_matching_hash(parsed)
    changed = {**parsed, "seniority": "senior"}
    await repo.update_profile_parsed(
        profile=created, parsed=changed, summary_text="y", embedding=[0.2]
    )
    assert created.profile_matching_hash == profile_matching_hash(changed)
    changes: tuple[tuple[str, object], ...] = (
        ("certifications", ["AWS"]),
        ("skills", ["Python", "SQL"]),
        ("preferred_roles", ["Platform Engineer"]),
        ("headline", "Platform Engineer"),
        ("years_experience", 8),
        ("work_history", [{"title": "Staff Engineer", "highlights": ["Built APIs"]}]),
        ("work_history", [{"title": "Engineer", "company": "Other", "highlights": ["Built APIs"]}]),
        ("education", [{
            "degree": "MS Computer Science", "institution": "State U", "year": "2018",
        }]),
        ("education", [{
            "degree": "BS Computer Science", "institution": "Other U", "year": "2018",
        }]),
    )
    for field, value in changes:
        candidate: dict[str, object] = {**parsed, field: value}
        await repo.update_profile_parsed(
            profile=created, parsed=candidate, summary_text="x", embedding=[0.1]
        )
        assert created.profile_matching_hash == profile_matching_hash(candidate)
    irrelevant = {**parsed, "email": "new@example.com"}
    await repo.update_profile_parsed(
        profile=created, parsed=irrelevant, summary_text="x", embedding=[0.1]
    )
    assert created.profile_matching_hash == profile_matching_hash(parsed)
    legacy = FakeProfile(parsed=parsed, profile_matching_hash=None)
    legacy_changed = {
        **parsed,
        "work_history": [{
            "title": "Engineer", "company": "New Co", "highlights": ["Built APIs"],
        }],
        "education": [{
            "degree": "MS Computer Science", "institution": "State U", "year": "2018",
        }],
    }
    await repo.update_profile_parsed(
        profile=cast(Any, legacy),
        parsed=legacy_changed,
        summary_text="x",
        embedding=[0.1],
    )
    assert legacy.profile_matching_hash == profile_matching_hash(legacy_changed)
    with pytest.raises(TypeError):
        await repo.add_profile(  # type: ignore[call-arg]
            user_id=1, resume_id=2, parsed=parsed, summary_text="x", embedding=[0.1],
            profile_matching_hash="arbitrary",
        )
