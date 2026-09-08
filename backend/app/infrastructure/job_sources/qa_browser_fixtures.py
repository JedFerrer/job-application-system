"""Deterministic, network-free adapters for the isolated Unit 1A Browser QA.

These reserved names are only reachable when the caller has already passed the
strict test-database guard in ``Settings``.  They are not providers and are
never selected by normal source configuration.
"""

from __future__ import annotations

from app.infrastructure.job_sources.base import (
    JobQuery,
    RawJobPost,
    SourceCapabilities,
    SourceDetailResult,
    SourceSearchResult,
)

QA_MIXED_SUCCESS = "qa-unit-1a-success"
QA_MIXED_FAILURE = "qa-unit-1a-failure"
QA_UNCERTAIN_EMPTY = "qa-unit-1a-uncertain-empty"
QA_TOTAL_FAILURE_A = "qa-unit-1a-total-failure-a"
QA_TOTAL_FAILURE_B = "qa-unit-1a-total-failure-b"

# These aliases exist only behind the existing Unit 1A Browser-QA registry
# guard.  They let the unchanged preferences form exercise its real
# Indeed/LinkedIn request shape against deterministic local adapters.
QA_PREFERENCE_ALIASES = {
    "indeed": QA_MIXED_SUCCESS,
    "linkedin": QA_MIXED_FAILURE,
}

QA_SOURCE_NAMES = frozenset(
    {
        QA_MIXED_SUCCESS,
        QA_MIXED_FAILURE,
        QA_UNCERTAIN_EMPTY,
        QA_TOTAL_FAILURE_A,
        QA_TOTAL_FAILURE_B,
    }
)


class Unit1ABrowserFixtureSource:
    """A fixed source outcome used solely by the local QA Compose profile."""

    def __init__(
        self,
        name: str,
        *,
        scenario: str | None = None,
        accepts_preference_query: bool = False,
    ) -> None:
        self.name = name
        self._scenario = scenario or name
        self._accepts_preference_query = accepts_preference_query

    @property
    def capabilities(self) -> SourceCapabilities:
        supported_query_fields: tuple[str, ...] = (
            "search_term",
            "country",
            "hours_old",
            "results_wanted",
        )
        if self._accepts_preference_query:
            supported_query_fields += ("location", "is_remote")
        return SourceCapabilities(
            source=self.name,
            supported_query_fields=supported_query_fields,
            # The normal preferences flow has no country field.  The guarded
            # aliases therefore accept its normal default country, while the
            # reserved Unit 1A IDs retain their QA-only capability contract.
            country_coverage=None if self._accepts_preference_query else ("QA",),
            detail_supported=False,
            pagination_mode="none",
            availability_evidence_supported=False,
            configuration_available=True,
            passive_health="available",
            max_results_per_search=2,
        )

    def search(self, query: JobQuery) -> SourceSearchResult:
        del query
        if self._scenario in {QA_MIXED_FAILURE, QA_TOTAL_FAILURE_A, QA_TOTAL_FAILURE_B}:
            return SourceSearchResult(
                source=self.name, status="failed", reason_code="provider_error"
            )
        if self._scenario == QA_UNCERTAIN_EMPTY:
            return SourceSearchResult(
                source=self.name, status="empty", completeness="unknown"
            )
        post = RawJobPost(
            source=self.name,
            source_url="https://qa.invalid/unit-1a/mixed-success",
            title="QA Deterministic Software Engineer",
            company="QA Fixture Company",
            location="QA Lab",
            is_remote=True,
            description="Synthetic Unit 1A Browser QA listing. No provider was contacted.",
            external_apply_url="https://boards.greenhouse.io/qa-fixture/jobs/424242",
        )
        return SourceSearchResult(
            source=self.name,
            status="success",
            posts=[
                post,
                RawJobPost(
                    source="qa-unit-1a-alternate",
                    source_url="https://jobs.lever.co/qa-fixture/11111111-1111-1111-1111-111111111111",
                    title=post.title,
                    company=post.company,
                    location=post.location,
                    is_remote=True,
                    description=post.description,
                    external_apply_url="https://boards.greenhouse.io/qa-fixture/jobs/424242",
                ),
            ],
            fetched=2,
            completeness="known_complete",
        )

    def fetch(self, query: JobQuery) -> list[RawJobPost]:
        return self.search(query).posts

    def get_detail(self, source_url: str) -> SourceDetailResult:
        del source_url
        return SourceDetailResult(source=self.name, status="unsupported")


def build_qa_fixture_source(name: str) -> Unit1ABrowserFixtureSource | None:
    if name in QA_SOURCE_NAMES:
        return Unit1ABrowserFixtureSource(name)
    scenario = QA_PREFERENCE_ALIASES.get(name)
    if scenario is not None:
        return Unit1ABrowserFixtureSource(
            name,
            scenario=scenario,
            accepts_preference_query=True,
        )
    return None
