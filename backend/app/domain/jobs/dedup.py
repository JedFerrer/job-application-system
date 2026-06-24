"""Stable fingerprint for cross-source job deduplication."""

from __future__ import annotations

import hashlib


def job_fingerprint(company: str | None, title: str, location: str | None) -> str:
    """Return a stable 16-char fingerprint for a posting.

    The same role advertised on multiple sources collapses to one fingerprint
    so ``INSERT ... ON CONFLICT (fingerprint)`` merges them into a single row.
    """
    norm = "|".join(
        (
            (company or "").lower().strip(),
            title.lower().strip(),
            (location or "").lower().strip(),
        )
    )
    return hashlib.sha256(norm.encode()).hexdigest()[:16]
