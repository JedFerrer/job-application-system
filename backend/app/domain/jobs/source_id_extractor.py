"""Extract stable platform-native job IDs from posting URLs.

Used by the extension import flow so we can deduplicate against existing
public jobs via ``job_source_links`` before falling back to the text
fingerprint.
"""

from __future__ import annotations

from app.domain.jobs.identity import extract_source_id as _extract_source_id


def extract_source_id(
    source_url: str, ats_type: str | None
) -> tuple[str, str] | None:
    """Return ``(source_name, source_job_id)`` or None.

    Uses ``ats_type`` as a hint to prioritise the correct regex, then falls
    back to scanning all known patterns.
    """
    return _extract_source_id(source_url, ats_type)
