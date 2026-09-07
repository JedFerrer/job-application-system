"""Conservative, scoped identities for job postings and their source aliases.

Text similarity is deliberately not identity.  A posting is identified only by
an adapter/host validated native identifier, or by a conservative canonical
URL when no such identifier is available.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMETERS = frozenset(
    {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gh_src"}
)


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    """Validated identity material for one source alias."""

    canonical_url: str
    source: str
    source_job_id: str | None
    source_tenant: str | None

    @property
    def stable_token(self) -> str:
        if self.source_job_id is not None:
            return ":".join(
                ("native", self.source, self.source_tenant or "", self.source_job_id)
            )
        return f"url:{self.canonical_url}"


def scoped_key(scope: str, token: str) -> str:
    """Return the opaque, fixed-length identity key stored in the database."""
    return hashlib.sha256(f"{scope}|{token}".encode()).hexdigest()


def scope_for(*, visibility: str, user_id: int | None = None) -> str:
    if visibility == "public":
        return "public"
    if visibility == "private" and user_id is not None:
        return f"user:{user_id}"
    raise ValueError("private job identity requires an owner")


def _is_domain(host: str, domain: str) -> bool:
    """True when host is the registrable domain or a proper subdomain of it."""
    return host == domain or host.endswith("." + domain)


def _has_hexadecimal_content(value: str) -> bool:
    return any(character in "0123456789abcdef" for character in value.casefold())


def _is_lever_native_id(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            value,
            flags=re.IGNORECASE,
        )
    )


def _is_ashby_native_id(value: str) -> bool:
    if not re.fullmatch(r"[0-9a-f-]{20,}", value, flags=re.IGNORECASE):
        return False
    return _has_hexadecimal_content(value)


_WORKDAY_POSTING_PATH = re.compile(
    r"/[a-z]{2}-[a-z]{2}/(?:job|requisition)/[^/]+/"
    r"((?:[A-Za-z][A-Za-z0-9]*_)?JR\d+)/?",
    flags=re.IGNORECASE,
)


def canonicalize_job_url(value: str) -> str | None:
    """Normalize only URL components known not to carry posting identity.

    Fragments and a small allowlist of tracking query parameters are removed.
    Paths are intentionally never decoded, redirects are never followed, and
    unknown query parameters remain part of the identity.
    """
    try:
        parsed = urlsplit(value.strip())
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not host:
        return None
    host = host.lower()
    netloc = host if port is None else f"{host}:{port}"
    query = urlencode(
        [(key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)
         if key.casefold() not in _TRACKING_PARAMETERS],
        doseq=True,
    )
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, query, ""))


def resolve_source_identity(
    source_url: str, *, source_hint: str | None = None, ats_type: str | None = None
) -> SourceIdentity | None:
    """Validate a known ATS identity from its host/path, else retain its URL."""
    canonical = canonicalize_job_url(source_url)
    if canonical is None:
        return None
    parsed = urlsplit(canonical)
    host = (parsed.hostname or "").lower()
    path = parsed.path
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    source = (source_hint or ats_type or host).casefold().strip()
    tenant: str | None = None
    native: str | None = None

    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        match = re.fullmatch(r"/([^/]+)/jobs/(\d+)/?", path, re.IGNORECASE)
        if match:
            source, tenant, native = "greenhouse", match.group(1).casefold(), match.group(2)
    elif host == "jobs.lever.co":
        match = re.fullmatch(r"/([^/]+)/([^/]+)/?", path)
        if match and _is_lever_native_id(match.group(2)):
            source, tenant, native = "lever", match.group(1).casefold(), match.group(2).casefold()
    elif host.endswith(".myworkdayjobs.com"):
        match = _WORKDAY_POSTING_PATH.fullmatch(path)
        if match:
            source, tenant, native = "workday", host, match.group(1)
    elif _is_domain(host, "linkedin.com"):
        match = re.search(r"/jobs/view/(\d+)", path)
        native = match.group(1) if match else query.get("currentJobId")
        if native and native.isdigit():
            source, tenant = "linkedin", host
        else:
            native = None
    elif _is_domain(host, "indeed.com") and path.rstrip("/") == "/viewjob":
        native = query.get("jk")
        if native and re.fullmatch(r"[A-Za-z0-9]+", native):
            source, tenant = "indeed", host
        else:
            native = None
    elif host == "jobs.ashbyhq.com":
        match = re.fullmatch(r"/([^/]+)/([^/]+)/?", path)
        if match and _is_ashby_native_id(match.group(2)):
            source, tenant, native = "ashby", match.group(1).casefold(), match.group(2).casefold()

    return SourceIdentity(canonical, source, native, tenant)


_EMPLOYER_ATS_SOURCES = frozenset({"greenhouse", "lever", "workday", "ashby"})


def strong_employer_apply_url(value: str) -> str | None:
    """Return a job-specific employer ATS URL usable as cross-source merge evidence.

    Only recognized Greenhouse, Lever, Workday, or Ashby posting routes with a
    validated tenant and native job ID qualify. Aggregators, generic careers
    pages, shared application URLs, unrecognized hosts, malformed URLs, and
    URL-only identities are rejected. Redirects are never fetched.
    """
    identity = resolve_source_identity(value)
    if (
        identity is None
        or identity.source not in _EMPLOYER_ATS_SOURCES
        or identity.source_job_id is None
        or identity.source_tenant is None
    ):
        return None
    return identity.canonical_url


def extract_source_id(source_url: str, ats_type: str | None = None) -> tuple[str, str] | None:
    """Compatibility projection for callers that only need native IDs."""
    identity = resolve_source_identity(source_url, ats_type=ats_type)
    if identity is None or identity.source_job_id is None:
        return None
    return identity.source, identity.source_job_id
