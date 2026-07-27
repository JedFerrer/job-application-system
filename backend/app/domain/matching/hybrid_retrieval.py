"""Pure utilities for fusing hybrid matching retrieval results."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetrievalCandidate:
    """One ranked result from one retrieval source."""

    job_id: int
    score: float


@dataclass
class FusedCandidate:
    """A deduplicated candidate with safe retrieval provenance."""

    job_id: int
    rrf_score: float
    source_ranks: dict[str, int] = field(default_factory=dict)
    source_scores: dict[str, float] = field(default_factory=dict)

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(sorted(self.source_ranks))

    def metadata(self) -> dict[str, object]:
        """Return source/rank metadata without source text or query contents."""
        return {
            "sources": list(self.sources),
            "source_ranks": dict(sorted(self.source_ranks.items())),
            "source_scores": dict(sorted(self.source_scores.items())),
            "rrf_score": round(self.rrf_score, 12),
        }


def fuse_ranked_candidates(
    ranked_sources: Mapping[str, Iterable[RetrievalCandidate]],
    *,
    k: int = 60,
    limit: int = 400,
) -> list[FusedCandidate]:
    """Fuse ranked source lists with Reciprocal Rank Fusion.

    Ranks are one-based, and ties are resolved by ascending job ID so the
    output is stable across database plans and repeated refreshes.
    """
    if k <= 0:
        raise ValueError("RRF k must be positive")
    if limit < 0:
        raise ValueError("RRF limit must be non-negative")

    fused: dict[int, FusedCandidate] = {}
    for source_name in sorted(ranked_sources):
        seen_in_source: set[int] = set()
        for candidate in ranked_sources[source_name]:
            if candidate.job_id in seen_in_source:
                continue
            seen_in_source.add(candidate.job_id)
            rank = len(seen_in_source)
            current = fused.get(candidate.job_id)
            contribution = 1.0 / (k + rank)
            if current is None:
                current = FusedCandidate(job_id=candidate.job_id, rrf_score=0.0)
                fused[candidate.job_id] = current
            current.rrf_score += contribution
            current.source_ranks.setdefault(source_name, rank)
            current.source_scores.setdefault(source_name, float(candidate.score))

    return sorted(fused.values(), key=lambda item: (-item.rrf_score, item.job_id))[:limit]
