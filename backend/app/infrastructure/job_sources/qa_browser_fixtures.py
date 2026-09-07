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

    def __init__(self, name: str) -> None:
        self.name = name

    @property
    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            source=self.name,
            supported_query_fields=(
                "search_term",
                "country",
                "hours_old",
                "results_wanted",
            ),
            country_coverage=("QA",),
            detail_supported=False,
            pagination_mode="none",
            availability_evidence_supported=False,
            configuration_available=True,
            passive_health="available",
            max_results_per_search=1,
        )

    def search(self, query: JobQuery) -> SourceSearchResult:
        del query
        if self.name in {QA_MIXED_FAILURE, QA_TOTAL_FAILURE_A, QA_TOTAL_FAILURE_B}:
            return SourceSearchResult(
                source=self.name, status="failed", reason_code="provider_error"
            )
        if self.name == QA_UNCERTAIN_EMPTY:
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
        )
        return SourceSearchResult(
            source=self.name,
            status="success",
            posts=[post],
            fetched=1,
            completeness="known_complete",
        )

    def fetch(self, query: JobQuery) -> list[RawJobPost]:
        return self.search(query).posts

    def get_detail(self, source_url: str) -> SourceDetailResult:
        del source_url
        return SourceDetailResult(source=self.name, status="unsupported")


def build_qa_fixture_source(name: str) -> Unit1ABrowserFixtureSource | None:
    return Unit1ABrowserFixtureSource(name) if name in QA_SOURCE_NAMES else None
