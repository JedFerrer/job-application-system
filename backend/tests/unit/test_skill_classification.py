"""Unit coverage for deterministic requirement classification."""

from __future__ import annotations

from app.domain.matching.skill_classification import classify_skill_mentions
from app.domain.matching.skill_extractor import default_skill_extractor


def _classify(text: str) -> dict[str, list[str]]:
    report = classify_skill_mentions(
        text,
        default_skill_extractor().extract(text, source="free_text"),
    )
    return {
        classification: [mention.canonical_label for mention in mentions]
        for classification, mentions in report.grouped().items()
    }


def test_classification_inherits_headings_to_bullets_and_reads_modals() -> None:
    groups = _classify(
        "Requirements:\n- Python\n- Docker\n"
        "Preferred:\n- AWS\nKubernetes is required.\nTailwind is a plus."
    )

    assert groups["required"] == ["python", "docker", "kubernetes"]
    assert groups["preferred"] == ["aws", "tailwind css"]
    assert groups["unclassified"] == []


def test_inline_required_modal_does_not_activate_following_bullet() -> None:
    groups = _classify("Docker is required.\n- AWS")

    assert groups["required"] == ["docker"]
    assert groups["unclassified"] == ["aws"]


def test_inline_preferred_modal_does_not_activate_following_bullet() -> None:
    groups = _classify("Docker is a plus.\n- AWS")

    assert groups["preferred"] == ["docker"]
    assert groups["unclassified"] == ["aws"]


def test_heading_lines_propagate_classification_to_contiguous_bullets() -> None:
    groups = _classify("Required Skills:\n- Python\n- Docker")

    assert groups["required"] == ["python", "docker"]


def test_classification_resets_sections_after_blank_lines_and_prose() -> None:
    groups = _classify(
        "Requirements:\n- Python\n\n- Docker\n"
        "Requirements:\n- Kubernetes\nWe use AWS internally.\n- Terraform"
    )

    assert groups["required"] == ["python", "kubernetes"]
    assert groups["unclassified"] == ["docker", "aws", "terraform"]


def test_required_classification_overrides_preferred_and_unclassified() -> None:
    groups = _classify("Preferred: Python\nPython is required.\nWe also use Python.")

    assert groups["required"] == ["python"]
    assert groups["preferred"] == []
    assert groups["unclassified"] == []
