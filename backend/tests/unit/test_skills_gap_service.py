"""Unit tests for the matching service skills-gap aggregation."""

from __future__ import annotations

import pytest

from app.domain.matching.service import MatchingService


class FakeMatchingRepository:
    def __init__(
        self, pairs: list[tuple[str, int]], total: int
    ) -> None:
        self._pairs = pairs
        self._total = total
        self.requested_top_n: int | None = None

    async def aggregate_missing_keywords(
        self, *, user_id: int, top_n: int
    ) -> list[tuple[str, int]]:
        self.requested_top_n = top_n
        return self._pairs[:top_n]

    async def count_matches_with_gaps(self, user_id: int) -> int:
        return self._total


def _service(repo: FakeMatchingRepository) -> MatchingService:
    service = MatchingService.__new__(MatchingService)
    service._repo = repo  # type: ignore[attr-defined]
    return service


@pytest.mark.asyncio
async def test_skills_gap_summary_maps_pairs_to_items() -> None:
    repo = FakeMatchingRepository(
        pairs=[("kubernetes", 9), ("graphql", 4)], total=12
    )
    service = _service(repo)

    summary = await service.skills_gap_summary(user_id=1, top_n=5)

    assert summary.total_matches_considered == 12
    assert [(i.keyword, i.frequency) for i in summary.items] == [
        ("kubernetes", 9),
        ("graphql", 4),
    ]
    assert repo.requested_top_n == 5


@pytest.mark.asyncio
async def test_skills_gap_summary_empty() -> None:
    repo = FakeMatchingRepository(pairs=[], total=0)
    service = _service(repo)

    summary = await service.skills_gap_summary(user_id=1)

    assert summary.items == []
    assert summary.total_matches_considered == 0
