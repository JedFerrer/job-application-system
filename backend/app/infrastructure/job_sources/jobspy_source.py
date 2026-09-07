"""JobSpy-backed adapter for Indeed / LinkedIn / Glassdoor / ZipRecruiter.

JobSpy (``python-jobspy``) pulls numpy/pandas, which require prebuilt wheels on
Python 3.13. It is an *optional* dependency (``pip install -e ".[scraping]"``),
so it is lazy-imported inside :meth:`JobSpySource.fetch`. Importing this module
never requires jobspy to be installed.

Legal/ethical note: scraping may violate a site's ToS. This is a personal-use
tool — prefer official APIs where available, rate-limit politely, rotate proxies
for LinkedIn, and never redistribute scraped data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import TYPE_CHECKING, Any

from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from app.core.logging import get_logger
from app.infrastructure.job_sources.base import (
    JobQuery,
    RawJobPost,
    SourceCapabilities,
    SourceDetailResult,
    SourceSearchResult,
)

if TYPE_CHECKING:
    import pandas as pd

logger = get_logger(__name__)

# JobSpy site identifiers we support, mapped to our ``source`` tag.
_SUPPORTED_SITES: tuple[str, ...] = ("indeed", "linkedin", "glassdoor", "zip_recruiter", "google")


class JobSpyUnavailableError(RuntimeError):
    """Raised when JobSpy is requested but not installed."""


def _clean_str(value: Any) -> str | None:
    """Return a trimmed string, or ``None`` for empty / NaN / missing values."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return text


def _clean_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        result = float(value)
    except (TypeError, ValueError):
        return None
    # NaN != NaN.
    return None if result != result else result


def _clean_bool(value: Any) -> bool | None:
    if value is None or (isinstance(value, float) and value != value):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _clean_datetime(value: Any) -> datetime | None:
    if value is None or value == "" or _is_nan(value):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    # Stdlib parse first so this works without pandas (the optional extra).
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    try:  # pragma: no cover - only reached for pandas Timestamp / date objects
        import pandas as pd  # noqa: PLC0415

        if pd.isna(value):
            return None
        ts: datetime = pd.Timestamp(value).to_pydatetime()
    except (ImportError, ValueError, TypeError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


class JobSpySource:
    """Adapter that wraps ``jobspy.scrape_jobs`` across multiple sites."""

    name = "jobspy"

    def __init__(self, sites: list[str], proxies: list[str] | None = None) -> None:
        self._sites = [s for s in sites if s in _SUPPORTED_SITES]
        self.name = self._sites[0] if self._sites else "jobspy"
        self._proxies = proxies or None

    @property
    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            source=self._sites[0] if self._sites else self.name,
            supported_query_fields=(
                "search_term", "location", "country", "is_remote", "hours_old",
                "results_wanted", "google_search_term",
            ),
            # JobSpy's provider coverage is not a stable, complete country contract.
            country_coverage=None,
            pagination_mode="internal",
            availability_evidence_supported=False,
            max_results_per_search=200,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=2, max=30),
        reraise=True,
    )
    def search(self, query: JobQuery) -> SourceSearchResult:
        started = perf_counter()
        if not self._sites:
            return SourceSearchResult(
                source=self.name, status="unsupported", reason_code="unknown_source"
            )
        try:
            from jobspy import scrape_jobs  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - exercised only without extra
            raise JobSpyUnavailableError(
                'JobSpy is not installed. Install with: pip install -e ".[scraping]"'
            ) from exc

        logger.info(
            "jobspy.fetch.start",
            sites=self._sites,
            search_term=query.search_term,
            location=query.location,
        )
        # Google Jobs only uses google_search_term; auto-build it from the
        # query if the caller didn't supply one explicitly.
        google_search_term = query.google_search_term
        if google_search_term is None and "google" in self._sites:
            parts = [query.search_term, "jobs"]
            if query.location:
                parts.append(query.location)
            google_search_term = " ".join(parts)
        kwargs: dict[str, Any] = dict(
            site_name=self._sites,
            search_term=query.search_term,
            google_search_term=google_search_term,
            location=query.location,
            country_indeed=query.country,
            results_wanted=query.results_wanted,
            hours_old=query.hours_old,
            linkedin_fetch_description=True,
            description_format="markdown",
            proxies=self._proxies,
        )
        # is_remote must be bool, never None (jobspy >= 1.1.82 validates
        # ScraperInput strictly).
        if query.is_remote is not None:
            kwargs["is_remote"] = query.is_remote

        df: pd.DataFrame = scrape_jobs(**kwargs)
        posts = [self._row_to_post(row) for _, row in df.iterrows()]
        logger.info("jobspy.fetch.done", count=len(posts))
        return SourceSearchResult(
            source=self._sites[0],
            status="success" if posts else "empty",
            posts=posts,
            duration_ms=int((perf_counter() - started) * 1000),
            fetched=len(posts),
            # JobSpy can internally absorb provider failures, so an empty response
            # cannot honestly prove an exhaustive search.
            completeness="unknown",
        )

    def fetch(self, query: JobQuery) -> list[RawJobPost]:
        """Compatibility wrapper retained while callers migrate to search()."""
        return self.search(query).posts

    def get_detail(self, source_url: str) -> SourceDetailResult:
        return SourceDetailResult(
            source=self._sites[0] if self._sites else self.name,
            status="unsupported",
        )

    @staticmethod
    def _row_to_post(row: pd.Series) -> RawJobPost:
        data: dict[str, Any] = row.to_dict()
        site = _clean_str(data.get("site")) or "jobspy"
        return RawJobPost(
            source=site,
            source_url=_clean_str(data.get("job_url")) or "",
            title=_clean_str(data.get("title")) or "",
            company=_clean_str(data.get("company")),
            location=_clean_str(data.get("location")),
            is_remote=_clean_bool(data.get("is_remote")),
            job_type=_clean_str(data.get("job_type")),
            description=_clean_str(data.get("description")),
            salary_min=_clean_float(data.get("min_amount")),
            salary_max=_clean_float(data.get("max_amount")),
            salary_currency=_clean_str(data.get("currency")),
            posted_at=_clean_datetime(data.get("date_posted")),
            company_logo=_clean_str(data.get("company_logo")),
            external_apply_url=_clean_str(data.get("job_url_direct")),
            raw={k: _json_safe(v) for k, v in data.items()},
        )


def _is_nan(value: Any) -> bool:
    return isinstance(value, float) and value != value


def _json_safe(value: Any) -> object:
    """Coerce a pandas/numpy cell into a JSON-serializable primitive."""
    if value is None or _is_nan(value):
        return None
    if isinstance(value, bool | int | float | str):
        return value
    return str(value)
