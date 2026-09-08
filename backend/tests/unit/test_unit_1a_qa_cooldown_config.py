"""The isolated Browser-QA stack must not inherit a developer cooldown."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_qa_compose_explicitly_sets_the_sixty_minute_refresh_cooldown() -> None:
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.unit-1a-qa.yml"
    compose = compose_path.read_text(encoding="utf-8")

    assert 'JOB_REFRESH_COOLDOWN_MINUTES: "60"' in compose
