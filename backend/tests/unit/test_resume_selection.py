"""Deterministic TFR-4 canonical evidence selection tests."""

from app.domain.resume.schemas import SourceRole, SourceTextItem
from app.domain.resume.selection import select_source_bullets


def _role(*bullets: str) -> SourceRole:
    return SourceRole(
        order=0,
        highlights=[SourceTextItem(text=text, order=index) for index, text in enumerate(bullets)],
    )


def test_short_resume_retains_all_bullets_with_relevant_first_order() -> None:
    roles = [_role("Built React interfaces.", "Built FastAPI APIs.")]
    result = select_source_bullets(roles, "Senior backend engineer FastAPI")
    assert len(result.selected_ids[0]) == 2
    assert result.selected_ids[0][0] == str(roles[0].highlights[1].source_id)


def test_long_resume_keeps_role_floor_and_caps_additions() -> None:
    roles = [_role(*[f"Built FastAPI service {index}." for index in range(6)])]
    roles += [_role("Supported releases.") for _ in range(5)]
    result = select_source_bullets(roles, "FastAPI")
    assert all(len(selected) >= 1 for selected in result.selected_ids)
    assert len(result.selected_ids[0]) <= 4
    assert sum(map(len, result.selected_ids)) == 9


def test_long_resume_does_not_fill_soft_target_with_zero_relevance() -> None:
    roles = [_role("Built FastAPI APIs.")]
    roles += [_role("Coordinated schedules.") for _ in range(10)]
    result = select_source_bullets(roles, "FastAPI")
    assert sum(map(len, result.selected_ids)) == 11


def test_redundant_evidence_is_not_selected_past_role_cap() -> None:
    roles = [_role(*[f"Built React UI {index}." for index in range(6)])]
    roles += [_role("Built FastAPI APIs.") for _ in range(5)]
    result = select_source_bullets(roles, "React FastAPI")
    assert len(result.selected_ids[0]) <= 4
    assert len(result.selected_ids[1]) == 1


def test_selection_uses_canonical_uuid_identity() -> None:
    roles = [_role("Built FastAPI APIs.", "Built FastAPI APIs.")]
    result = select_source_bullets(roles, "FastAPI")
    assert set(result.selected_ids[0]) == {
        str(roles[0].highlights[0].source_id),
        str(roles[0].highlights[1].source_id),
    }
