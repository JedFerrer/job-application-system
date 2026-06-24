"""Small helpers shared by every LLM caller.

Ported from hr-ai-nterviewer-saas (``services/llm_utils.py``). Kept separate
from ``llm_client`` so unit tests can exercise them without an OpenAI client.
"""

from __future__ import annotations

import re

# Match an optional leading ```<lang> fence and an optional trailing ``` fence.
# Tolerant of single-line fences, unclosed openers, language hints, and
# surrounding whitespace.
_OPEN_FENCE = re.compile(r"^\s*```[a-zA-Z0-9_+-]*\s*\n?", re.MULTILINE)
_CLOSE_FENCE = re.compile(r"\n?\s*```\s*$")


def strip_code_fences(text: str) -> str:
    """Remove markdown ``` fences from an LLM response and return the body."""
    if not text:
        return ""
    body = text.strip()
    body = _OPEN_FENCE.sub("", body, count=1)
    body = _CLOSE_FENCE.sub("", body, count=1)
    return body.strip()
