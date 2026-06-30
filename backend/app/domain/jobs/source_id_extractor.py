"""Extract stable platform-native job IDs from posting URLs.

Used by the extension import flow so we can deduplicate against existing
public jobs via ``job_source_links`` before falling back to the text
fingerprint.
"""

from __future__ import annotations

import re

# Per-ATS regex patterns: (source_name, regex, group_name_or_index)
_SOURCE_PATTERNS: list[tuple[str, re.Pattern[str], str | int]] = [
    ("greenhouse", re.compile(r"/jobs/(\d+)", re.IGNORECASE), 1),
    ("lever", re.compile(r"jobs\.lever\.co/[^/]+/([a-f0-9-]{36})", re.IGNORECASE), 1),
    ("workday", re.compile(r"(?:requisitions?|job|wd)/(?:[\w-]+/)?([\w-]{10,})", re.IGNORECASE), 1),
    ("linkedin", re.compile(r"(?:/view/|currentJobId=)(\d+)", re.IGNORECASE), 1),
    ("indeed", re.compile(r"[?&]jk=([a-f0-9]+)", re.IGNORECASE), 1),
    ("ashby", re.compile(r"/job/([a-f0-9-]{20,})", re.IGNORECASE), 1),
    ("smartrecruiters", re.compile(r"/([a-f0-9-]{36})", re.IGNORECASE), 1),
    ("bamboohr", re.compile(r"/jobs/(\d+)", re.IGNORECASE), 1),
    ("workable", re.compile(r"/([a-f0-9-]{20,})", re.IGNORECASE), 1),
]


def extract_source_id(
    source_url: str, ats_type: str | None
) -> tuple[str, str] | None:
    """Return ``(source_name, source_job_id)`` or None.

    Uses ``ats_type`` as a hint to prioritise the correct regex, then falls
    back to scanning all known patterns.
    """
    if not source_url:
        return None

    # Try the ATS-specific pattern first (most reliable).
    if ats_type:
        patterns = [p for p in _SOURCE_PATTERNS if p[0] == ats_type] + [
            p for p in _SOURCE_PATTERNS if p[0] != ats_type
        ]
    else:
        patterns = list(_SOURCE_PATTERNS)

    for source_name, regex, group in patterns:
        match = regex.search(source_url)
        if match:
            sid = match.group(group) if isinstance(group, int) else match.group(group)
            if sid:
                return source_name, sid

    return None
