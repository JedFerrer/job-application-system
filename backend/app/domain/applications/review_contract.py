"""Immutable server-owned evidence rules for document-review findings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

DocumentType = Literal["job", "resume", "cover_letter"]
EvidenceRole = Literal["target", "comparison", "requirement"]


@dataclass(frozen=True)
class ReviewCategoryContract:
    expected_evidence_count: int
    roles_by_document: Mapping[DocumentType, EvidenceRole]
    required_roles: frozenset[EvidenceRole]


_TARGET_DOCUMENTS: Mapping[DocumentType, EvidenceRole] = MappingProxyType({
    "resume": "target",
    "cover_letter": "target",
})

REVIEW_CATEGORY_CONTRACTS: Mapping[str, ReviewCategoryContract] = MappingProxyType({
    **{
        category: ReviewCategoryContract(1, _TARGET_DOCUMENTS, frozenset({"target"}))
        for category in ("unsupported_claim", "repetition", "clarity", "keyword_stuffing")
    },
    "cross_document_mismatch": ReviewCategoryContract(
        2,
        MappingProxyType({"resume": "target", "cover_letter": "comparison"}),
        frozenset({"target", "comparison"}),
    ),
    **{
        category: ReviewCategoryContract(
            2,
            MappingProxyType({"job": "requirement", **_TARGET_DOCUMENTS}),
            frozenset({"requirement", "target"}),
        )
        for category in ("job_document_mismatch", "omitted_verified_evidence")
    },
})
