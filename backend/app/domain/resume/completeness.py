"""Conservative, deterministic checks for likely resume-parser omissions."""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from app.domain.resume.schemas import ExtractionWarning, ParsedProfile

COMPLETENESS_VALIDATOR_VERSION = "completeness-validator-v2"

_EMAIL_RE = re.compile(
    r"(?<![\w.+-])[\w.!#$%&'*+/=?^`{|}~-]+@"
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?![\w.-])"
)
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_BULLET_RE = re.compile(r"^(?:[-*+•]|\d+[.)])\s+")

_COMMON_SECTION_HEADINGS = frozenset(
    {
        "awards",
        "contact",
        "contact information",
        "coursework",
        "interests",
        "languages",
        "objective",
        "professional summary",
        "profile",
        "projects",
        "publications",
        "references",
        "skills",
        "summary",
        "volunteer experience",
    }
)

_SECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "missing_work_history",
        re.compile(
            r"^(?:professional\s+)?(?:work\s+experience|work\s+history|experience|employment\s+history|professional\s+experience)\s*:?$",
            re.IGNORECASE,
        ),
    ),
    (
        "missing_education",
        re.compile(r"^(?:academic\s+)?education\s*:?$", re.IGNORECASE),
    ),
    (
        "missing_certifications",
        re.compile(r"^(?:professional\s+)?certifications?\s*:?$", re.IGNORECASE),
    ),
    (
        "missing_languages",
        re.compile(r"^(?:languages|language\s+skills)\s*:?$", re.IGNORECASE),
    ),
)

_MESSAGES = {
    "source_email_not_parsed": (
        "An email address was detected in the resume but is missing from the "
        "imported profile. Review the contact details."
    ),
    "source_linkedin_not_parsed": (
        "A LinkedIn link was detected in the resume but is missing from the "
        "imported profile. Review the professional links."
    ),
    "source_github_not_parsed": (
        "A GitHub link was detected in the resume but is missing from the "
        "imported profile. Review the professional links."
    ),
    "missing_work_history": (
        "A work-history section was detected but no work history was imported. "
        "Review the imported profile."
    ),
    "missing_education": (
        "An education section was detected but no education was imported. "
        "Review the imported profile."
    ),
    "missing_certifications": (
        "A certifications section was detected but no certifications were "
        "imported. Review the imported profile."
    ),
    "missing_languages": (
        "A languages section was detected but no languages were imported. "
        "Review the imported profile."
    ),
}


def _warning(code: str) -> ExtractionWarning:
    return ExtractionWarning(code=code, message=_MESSAGES[code])


def _canonical_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value.rstrip(".,;:)]}"))
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        return None
    hostname = hostname.lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    netloc = hostname
    if port is not None:
        netloc = f"{hostname}:{port}"
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))


def _source_urls(text: str, domain: str) -> set[str]:
    urls: set[str] = set()
    for match in _URL_RE.findall(text):
        canonical = _canonical_url(match)
        if canonical is None:
            continue
        hostname = (urlsplit(canonical).hostname or "").removeprefix("www.")
        if hostname == domain:
            urls.add(canonical)
    return urls


def _parsed_link_urls(profile: ParsedProfile, field: str) -> set[str]:
    value = getattr(profile.links, field, None)
    if not isinstance(value, str) or not value.strip():
        return set()
    canonical = _canonical_url(value)
    return {canonical} if canonical is not None else set()


def _looks_like_heading(value: str) -> bool:
    """Recognize bounded, heading-shaped lines without treating prose as headings."""
    heading = value.strip().rstrip(":").strip()
    if not heading or len(heading) > 60 or len(heading.split()) > 4:
        return False
    if _BULLET_RE.match(heading) or heading[-1:] in ".?!":
        return False
    normalized = heading.casefold()
    if normalized in _COMMON_SECTION_HEADINGS:
        return True
    return heading.isupper() and any(character.isalpha() for character in heading)


def _has_substantive_following_text(lines: list[str], index: int) -> bool:
    for candidate in lines[index + 1 :]:
        text = candidate.strip()
        if not text:
            continue
        if _looks_like_heading(text) or any(
            pattern.fullmatch(text) for _, pattern in _SECTION_PATTERNS
        ):
            return False
        return len(text) >= 3
    return False


def find_completeness_warnings(text: str, profile: ParsedProfile) -> list[ExtractionWarning]:
    """Return stable advisory warnings for only clear source/profile omissions."""
    warnings: list[ExtractionWarning] = []

    source_emails = {match.lower() for match in _EMAIL_RE.findall(text)}
    parsed_email = profile.email.strip().lower() if profile.email else ""
    if source_emails and parsed_email not in source_emails:
        warnings.append(_warning("source_email_not_parsed"))

    for field, domain, code in (
        ("linkedin", "linkedin.com", "source_linkedin_not_parsed"),
        ("github", "github.com", "source_github_not_parsed"),
    ):
        source_urls = _source_urls(text, domain)
        if source_urls and not source_urls.intersection(_parsed_link_urls(profile, field)):
            warnings.append(_warning(code))

    lines = text.splitlines()
    profile_fields = {
        "missing_work_history": "work_history",
        "missing_education": "education",
        "missing_certifications": "certifications",
        "missing_languages": "languages",
    }
    for code, pattern in _SECTION_PATTERNS:
        collection = getattr(profile, profile_fields[code])
        if collection:
            continue
        if any(
            pattern.fullmatch(line.strip()) and _has_substantive_following_text(lines, index)
            for index, line in enumerate(lines)
        ):
            warnings.append(_warning(code))

    return warnings
