"""Deterministic, no-network responses for the isolated portfolio test profile."""

from __future__ import annotations

import json
import re

from openai.types.chat import ChatCompletion

from app.core.config import get_settings

MODEL = "portfolio-synthetic-v1"


def _response(payload: dict[str, object]) -> tuple[ChatCompletion, str]:
    response = ChatCompletion.model_validate(
        {
            "id": "portfolio-synthetic",
            "object": "chat.completion",
            "created": 0,
            "model": MODEL,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": json.dumps(payload)},
                }
            ],
        }
    )
    return response, MODEL


def _require_profile() -> None:
    if not get_settings().portfolio_synthetic_providers_enabled:
        raise RuntimeError("Portfolio synthetic provider is not enabled")


async def synthetic_document_review() -> tuple[ChatCompletion, str]:
    """Return a valid advisory review with no invented findings."""
    _require_profile()
    return _response({"findings": []})


async def synthetic_interview(messages: list[dict[str, str]]) -> tuple[ChatCompletion, str]:
    """Return source-linked prompts without sending source text to a provider."""
    _require_profile()
    match = re.search(r"Prepare exactly (\d+)", messages[0]["content"])
    if match is None:
        raise ValueError("Unexpected interview prompt")
    context = json.loads(messages[1]["content"])
    source = "resume:" if context["source_label"] == "confirmed_submission" else "profile:"
    passages = context["passages"]
    candidates = [
        key
        for key in sorted(passages)
        if key.startswith(source) and not key.endswith((".source_id", ".source_role_id"))
    ]
    preferred_parts = ("tailored_highlights", "role_context", "skills", "headline")
    preferred = [key for key in candidates if any(part in key for part in preferred_parts)]
    locators = preferred or candidates
    if not locators:
        raise ValueError("No synthetic interview evidence locator")
    prompts = (
        "What was your contribution to",
        "What result would you highlight from",
        "What tradeoff would you discuss while describing",
        "How would you explain the context of",
    )
    questions = [
        {
            "question": (
                f"{prompts[(index - 1) % len(prompts)]} "
                f"{passages[locators[(index - 1) % len(locators)]].strip()[:100].rstrip('.')}?"
            ),
            "suggested_answer": "Use the cited source passage; add no unsupported details.",
            "source_locators": [locators[(index - 1) % len(locators)]],
        }
        for index in range(1, int(match.group(1)) + 1)
    ]
    return _response({"questions": questions})
