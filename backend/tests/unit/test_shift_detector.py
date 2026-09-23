"""Focused tests for safe shift-detector provider diagnostics."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AuthenticationError

from app.domain.jobs import shift_detector
from app.infrastructure import llm_client

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_recognized_provider_failure_omits_exception_details_from_shift_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DiagnosticLogger:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict[str, object]]] = []

        def warning(self, event: str, **fields: object) -> None:
            self.events.append((event, fields))

    error = AuthenticationError(
        "SECRET_PROVIDER_MESSAGE_SENTINEL",
        response=httpx.Response(
            401,
            request=httpx.Request("POST", "https://provider.invalid/v1/chat/completions"),
        ),
        body={"detail": "SECRET_PROVIDER_BODY_SENTINEL"},
    )
    diagnostic_logger = DiagnosticLogger()
    monkeypatch.setattr(llm_client, "llm_chat_completion", AsyncMock(side_effect=error))
    monkeypatch.setattr(shift_detector, "logger", diagnostic_logger)

    result = await shift_detector.classify_shift_llm("Support Engineer", "Work nights")

    assert result is None
    assert diagnostic_logger.events == [
        ("shift_llm.provider_failed", {"provider_failure_category": "auth_error"})
    ]
    assert "SECRET_" not in repr(diagnostic_logger.events)
