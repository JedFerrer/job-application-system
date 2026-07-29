"""Shift-type detection for job postings.

Two-stage pipeline
==================
1. **Regex** (fast, free, offline):

   a. Keyword scan for explicit shift names ("night shift", "2nd shift", etc.)
   b. Clock-time extraction — finds times like "8 AM CST" or "22:00 EST" and
      converts them to the user's configured local timezone so the result
      answers *"what time of day am I working?"* from the worker's perspective.
      This is critical for remote roles: a "9 AM CST" job is 11 PM PHT → night.

2. **LLM fallback** (only when regex returns ``None`` and description is
   non-empty): a single zero-temperature prompt returning one of
   ``day | mid | night | rotating | unknown``.  Failures are swallowed and
   return ``None`` so ingest never crashes on a classification error.

Shift boundaries (in user-local time, configurable, default ``Asia/Manila`` UTC+8)
-----------------------------------------------------------------------------------
    day    05:00 – 13:00
    mid    13:00 – 21:00
    night  21:00 – 05:00  (wraps midnight)

Why timezone conversion matters for remote workers
---------------------------------------------------
A Philippine-based remote worker serving US clients experiences:

    "8 AM CST"   = UTC-6 → 14:00 UTC → 22:00 PHT  → **night**
    "10 PM EST"  = UTC-5 → 03:00 UTC → 11:00 PHT  → **day**
    "9 PM PST"   = UTC-8 → 05:00 UTC → 13:00 PHT  → **mid**

Without timezone conversion, all three would be labeled as the employer's
local shift, which is meaningless for the job seeker.

Timezone abbreviation ambiguity
--------------------------------
Where an abbreviation is overloaded we resolve to the most common
interpretation in English-language job postings (predominantly US + major
outsourcing hubs):

    CST → US Central Standard Time (UTC−6)  — NOT China Standard (UTC+8)
    PST → US Pacific Standard Time (UTC−8)  — NOT Philippine; use PHT instead
    BST → British Summer Time (UTC+1)       — NOT Bangladesh Standard (UTC+6)
    IST → India Standard Time (UTC+5:30)    — NOT Israel Standard (UTC+2)

DST note: fixed offsets are used (EDT/CDT/MDT/PDT for summer).  One-hour
DST errors are tolerated because they rarely change the day/mid/night bucket.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app.core.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# UTC-offset map (minutes east of UTC)
# ---------------------------------------------------------------------------

_TZ_OFFSET_MIN: dict[str, int] = {
    # United States
    "EST": -300, "EDT": -240,
    "CST": -360, "CDT": -300,
    "MST": -420, "MDT": -360,
    "PST": -480, "PDT": -420,
    "AKST": -540, "AKDT": -480,
    "HST": -600,
    # UTC / GMT
    "UTC": 0, "GMT": 0,
    # Europe
    "BST": 60, "CET": 60, "CEST": 120,
    # Asia-Pacific (common outsourcing hubs)
    "IST": 330,    # India Standard Time UTC+5:30
    "PHT": 480,    # Philippine Time UTC+8
    "SGT": 480,    # Singapore Time UTC+8
    "HKT": 480,    # Hong Kong Time UTC+8
    "WIB": 420,    # Western Indonesia Time UTC+7
    "ICT": 420,    # Indochina Time UTC+7
    "JST": 540,    # Japan Standard Time UTC+9
    "KST": 540,    # Korea Standard Time UTC+9
    "AEST": 600, "AEDT": 660,
    "NZST": 720, "NZDT": 780,
}

# Build an alternation group with longest abbreviations first so the regex
# engine is greedy (AKST matches before ST, AEST before EST, etc.).
_TZ_ALT_CAP = (
    "(" + "|".join(sorted(_TZ_OFFSET_MIN, key=len, reverse=True)) + ")"
)

# ---------------------------------------------------------------------------
# Keyword patterns (highest confidence — explicit shift names in the text)
# ---------------------------------------------------------------------------

_NIGHT_KW = re.compile(
    r"\b(?:night\s+shift|graveyard\s+shift|overnight\s+shift"
    r"|3rd\s+shift|third\s+shift|night\s+differential)\b",
    re.I,
)
_MID_KW = re.compile(
    r"\b(?:(?:mid|swing|afternoon|evening)\s+shift|2nd\s+shift|second\s+shift)\b",
    re.I,
)
_DAY_KW = re.compile(
    r"\b(?:day\s+shift|morning\s+shift|1st\s+shift|first\s+shift)\b",
    re.I,
)
_ROTATING_KW = re.compile(
    r"\b(?:rotating\s+(?:shift|schedule)|shift\s+rotation|split\s+shift)\b",
    re.I,
)

# ---------------------------------------------------------------------------
# Time + timezone patterns
# ---------------------------------------------------------------------------
# At least one space required between the time and the timezone abbreviation
# to avoid false matches like "8AMCST".

# 12-hour:  "8 AM CST"  "8:30 AM PST"  "10PM - 6AM EST"  "8 AM – 5 PM CST"
_TIME12_TZ = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b"
    r"(?:\s*[-–/to]+\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)\b)?"  # optional range end
    r"\s+"  # require whitespace between time and tz
    + _TZ_ALT_CAP
    + r"\b",
    re.I,
)

# 24-hour:  "22:00 CST"  "08:00 – 17:00 EST"  "0800 - 1700 CST" (no colon not handled)
_TIME24_TZ = re.compile(
    r"\b([01]?\d|2[0-3]):([0-5]\d)\b"
    r"(?:\s*[-–/to]+\s*(?:[01]?\d|2[0-3]):[0-5]\d\b)?"
    r"\s+"
    + _TZ_ALT_CAP
    + r"\b",
    re.I,
)

# ---------------------------------------------------------------------------
# Shift boundaries (hour of day 0-23, in user-local timezone)
# ---------------------------------------------------------------------------

_DAY_START = 6    # 06:00 inclusive  → day   06:00–13:00
_DAY_END   = 13   # 13:00 exclusive  → mid   13:00–20:00
_MID_END   = 20   # 20:00 exclusive  → night 20:00–06:00

_VALID_SHIFTS = frozenset({"day", "mid", "night", "rotating", "unknown"})


def _hour_to_shift(hour: int) -> str:
    if _DAY_START <= hour < _DAY_END:
        return "day"
    if _DAY_END <= hour < _MID_END:
        return "mid"
    return "night"


def _to_local_hour(
    src_hour: int,
    src_minute: int,
    tz_abbrev: str,
    user_offset_min: int,
) -> int:
    """Convert a source-timezone start time to the user's local hour (0-23)."""
    src_offset = _TZ_OFFSET_MIN.get(tz_abbrev.upper(), 0)
    utc_min = src_hour * 60 + src_minute - src_offset
    local_min = (utc_min + user_offset_min) % 1440
    return local_min // 60


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class ShiftDetector:
    """Detect shift type from a job posting's title and description.

    Parameters
    ----------
    user_timezone:
        IANA timezone name for the job seeker's location (e.g.
        ``"Asia/Manila"``, ``"America/New_York"``).  Controls what "day" vs
        "night" means from the worker's perspective after timezone conversion.
    """

    def __init__(self, user_timezone: str = "Asia/Manila") -> None:
        self._user_offset_min = _resolve_tz_offset(user_timezone)

    def detect_regex(self, title: str, description: str | None) -> str | None:
        """Return shift type using only text patterns, or ``None`` if undetermined.

        Order of precedence:
        1. Clock-time + timezone pair — converts to user-local time first.
           This takes priority over keywords because an employer writing
           "9 AM EST day shift" means night for a PH worker; time conversion
           gives the correct answer while the keyword would not.
        2. Explicit shift keywords (fallback when no clock time is present).
        """
        raw = f"{title} {description or ''}"
        # LinkedIn (and other scrapers) escape hyphens and special chars with a
        # backslash (e.g. "9 AM \\- 5 PM EST"). Strip those escape prefixes so
        # the time-range regex can match the separator between start and end.
        text = raw.replace("\\-", "-").replace("\\(", "(").replace("\\)", ")")
        return self._from_times(text) or self._from_keywords(text)

    # ------------------------------------------------------------------

    def _from_keywords(self, text: str) -> str | None:
        if _NIGHT_KW.search(text):
            return "night"
        if _MID_KW.search(text):
            return "mid"
        if _DAY_KW.search(text):
            return "day"
        if _ROTATING_KW.search(text):
            return "rotating"
        return None

    def _from_times(self, text: str) -> str | None:
        # 12-hour format first (most common in US job listings).
        m = _TIME12_TZ.search(text)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2)) if m.group(2) else 0
            ampm = m.group(3).lower()
            tz_abbrev = m.group(4).upper()
            # Convert to 24-hour.
            if ampm == "pm" and hour != 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
            local_h = _to_local_hour(hour, minute, tz_abbrev, self._user_offset_min)
            logger.debug(
                "shift_detector.time_match",
                raw=m.group(0),
                local_hour=local_h,
                user_offset_min=self._user_offset_min,
            )
            return _hour_to_shift(local_h)

        # 24-hour format.
        m = _TIME24_TZ.search(text)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2))
            tz_abbrev = m.group(3).upper()
            local_h = _to_local_hour(hour, minute, tz_abbrev, self._user_offset_min)
            logger.debug(
                "shift_detector.time24_match",
                raw=m.group(0),
                local_hour=local_h,
                user_offset_min=self._user_offset_min,
            )
            return _hour_to_shift(local_h)

        return None


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _resolve_tz_offset(iana_name: str) -> int:
    """Return the current UTC offset in minutes for an IANA timezone name.

    Accounts for DST by using the current real-world offset at the time the
    ShiftDetector is instantiated.  Re-instantiate daily if DST accuracy is
    critical.
    """
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415

        tz = ZoneInfo(iana_name)
        now = datetime.now(tz=UTC)
        offset = now.astimezone(tz).utcoffset()
        return int(offset.total_seconds() // 60) if offset else 0
    except Exception:  # noqa: BLE001
        logger.warning("shift_detector.tz_resolve_failed", iana=iana_name)
        return 480  # Fallback to UTC+8 (Asia/Manila).


async def classify_shift_llm(title: str, description: str) -> str | None:
    """LLM fallback for shift classification.

    Called only when regex returns ``None`` and the description is non-empty.
    Returns one of ``day | mid | night | rotating | unknown``, or ``None`` if
    the LLM call fails, so ingest never crashes on a classification error.
    """
    from app.infrastructure.llm_client import (  # noqa: PLC0415
        LLMUnavailableError,
        llm_chat_completion,
    )

    prompt = (
        "You are classifying a job posting for a remote worker based in the Philippines "
        "(PHT = UTC+8).\n"
        "Determine what shift the WORKER will be on, from the worker's perspective in "
        "Philippine Time.\n"
        "If the posting gives a clock time with a timezone (e.g. '9 AM EST'), convert it "
        "to PHT first.\n"
        "Reply with ONLY one lowercase word — no punctuation, no explanation:\n"
        "  day      (worker's shift starts roughly 5 AM – 1 PM Philippine Time)\n"
        "  mid      (worker's shift starts roughly 1 PM – 9 PM Philippine Time)\n"
        "  night    (worker's shift starts roughly 9 PM – 5 AM Philippine Time)\n"
        "  rotating (alternating or variable shifts)\n"
        "  unknown  (no shift information available)\n\n"
        f"Title: {title}\n\n"
        f"Description (first 500 chars):\n{description[:500]}"
    )
    try:
        response, _ = await llm_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        raw = (response.choices[0].message.content or "").strip().lower()
        # Accept the first valid token; guard against a verbose response.
        for token in raw.split():
            token = token.strip(".,;")
            if token in _VALID_SHIFTS:
                return token
        logger.warning("shift_llm.unexpected_response", raw=raw[:120])
        return None
    except LLMUnavailableError:
        return None
    except Exception:  # noqa: BLE001
        logger.warning("shift_llm.failed", exc_info=True)
        return None
