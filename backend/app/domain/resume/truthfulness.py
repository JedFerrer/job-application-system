"""Deterministic safety checks for selective resume tailoring output."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.matching.skill_matcher import extract_known_skills
from app.domain.resume.schemas import ParsedProfile

_NUMERIC_CLAIM = re.compile(
    r"(?<![A-Za-z0-9.])"
    r"(?:[$€£₱]\s*)?"
    r"\d+(?:,\d{3})*(?:\.\d+)?"
    r"(?:\s*%|\s*[xX]|\s*[KMBkmb])?"
    r"(?![A-Za-z0-9.])"
)
_TOKEN = re.compile(
    r"(?<!\w)(?:"
    r"\.[A-Za-z0-9]+|"
    r"[A-Za-z0-9]+(?:[+#]+|(?:[./][A-Za-z0-9]+)+)|"
    r"[^\W\d_]+(?:[-'’][^\W\d_]+)*|"
    r"[A-Za-z0-9]+"
    r")(?!\w)",
    re.UNICODE,
)
_NUMERIC_TOKEN = re.compile(r"\d+(?:\.\d+)?(?:[KMBkmb])?\Z")
_CONNECTORS = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "into",
    "of", "on", "or", "the", "through", "to", "using", "via", "with",
}
_RELATIONSHIP_CONNECTORS = {
    "by", "for", "from", "in", "into", "on", "through", "to", "using",
    "via", "with",
}
_RELATIONSHIP_MARKERS = {
    "backed",
    "backing",
    "caused",
    "enabling",
    "integrated",
    "powered",
    "reducing",
    "resulting",
    "supporting",
}
_RELATIONSHIP_PATTERNS = (
    r"\busing\s+[^,.;]+",
    r"\bwith\s+[^,.;]+",
    r"\bthrough\s+[^,.;]+",
    r"\bvia\s+[^,.;]+",
    r"\bleveraging\s+[^,.;]+",
    r"\bbacked\s+by\s+[^,.;]+",
    r"\bpowered\s+by\s+[^,.;]+",
    r"\bintegrated\s+(?:with|into)\s+[^,.;]+",
    r"\bintegrated\s+[^,.;]+\s+into\s+[^,.;]+",
    r"\bto\s+automate\s+[^,.;]+",
    r"\bsupporting\s+[^,.;]+",
    r"\benabling\s+[^,.;]+",
    r"\bresulting\s+in\s+[^,.;]+",
    r"\bleading\s+to\s+[^,.;]+",
    r"\breducing\s+[^,.;]+",
    r"\b(?:serving|powering|improving|increasing|decreasing|causing)\s+[^,.;]+",
)
_CLAUSE_BOUNDARY = r"[.!?;:\u2012\u2013\u2014\u2026\n\r]"
_PROTECTED_CLAIMS = {
    "architected", "architect", "designed", "drove", "led", "managed",
    "mentored", "owned", "oversaw", "spearheaded", "supervised",
}


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    role_index: int
    source_index: int
    text: str
    role_uuid: str | None = None
    source_uuid: str | None = None


def build_evidence_catalog(profile: ParsedProfile) -> dict[str, EvidenceRef]:
    """Build request-local, role-scoped evidence IDs from active profile text."""
    return {
        f"r{role_index}:b{source_index}": EvidenceRef(
            evidence_id=f"r{role_index}:b{source_index}",
            role_index=role_index,
            source_index=source_index,
            text=bullet,
            role_uuid=f"profile-role:{role_index}",
            source_uuid=f"profile-bullet:{role_index}:{source_index}",
        )
        for role_index, role in enumerate(profile.work_history)
        for source_index, bullet in enumerate(role.highlights)
    }


def build_source_evidence_catalog(source_roles: list[Any]) -> dict[str, EvidenceRef]:
    """Build deterministic request aliases backed by canonical source UUIDs."""
    catalog: dict[str, EvidenceRef] = {}
    for role_index, role in enumerate(source_roles):
        role_uuid = str(role.source_id)
        for source_index, bullet in enumerate(role.highlights):
            alias = f"r{role_index}:b{source_index}"
            catalog[alias] = EvidenceRef(
                evidence_id=alias,
                role_index=role_index,
                source_index=source_index,
                text=bullet.text,
                role_uuid=role_uuid,
                source_uuid=str(bullet.source_id),
            )
    return catalog


def numeric_claims(text: str) -> frozenset[str]:
    """Return conservative normalized numeric claims found in text."""
    claims: set[str] = set()
    for match in _NUMERIC_CLAIM.finditer(text):
        value = re.sub(r"\s+", "", match.group()).replace(",", "")
        prefix = value[0] if value[:1] in "$€£₱" else ""
        body = value[len(prefix) :]
        suffix = body[-1].lower() if body[-1:].lower() in {"%", "x", "k", "m", "b"} else ""
        number = body[: -len(suffix)] if suffix else body
        try:
            normalized_number = format(Decimal(number).normalize(), "f")
        except InvalidOperation:
            normalized_number = number
        claims.add(f"{prefix}{normalized_number}{suffix}")
    return frozenset(claims)


def _mentions_missing_skill(text: str, missing_skills: set[str]) -> bool:
    return bool(set(extract_known_skills(text)) & missing_skills)


def _substantive_tokens(text: str) -> frozenset[str]:
    return frozenset(
        token.casefold()
        for token in _TOKEN.findall(text)
        if token.casefold() not in _CONNECTORS
        and not _NUMERIC_TOKEN.fullmatch(token)
    )


def _technical_tokens(text: str) -> frozenset[str]:
    """Return punctuation-bearing identifiers whose exact spelling matters."""
    return frozenset(
        token.casefold()
        for token in _TOKEN.findall(text)
        if (
            any(mark in token for mark in ". /+#".replace(" ", ""))
            and not _NUMERIC_TOKEN.fullmatch(token)
        )
    )


def _relationship_claims(text: str) -> frozenset[str]:
    """Return normalized relationship phrases for semantic policy checks."""
    claims: set[str] = set()
    for pattern in _RELATIONSHIP_PATTERNS:
        claims.update(
            re.sub(r"\s+", " ", match.group()).casefold().strip()
            for match in re.finditer(pattern, text, flags=re.IGNORECASE)
        )
    return frozenset(claims)


def _relationship_structures(text: str) -> frozenset[tuple[str, ...]]:
    """Return normalized, context-complete clauses containing relationships."""
    token_matches = list(_TOKEN.finditer(text))
    numeric_matches = list(_NUMERIC_CLAIM.finditer(text))
    if not token_matches and not numeric_matches:
        return frozenset()

    events: list[tuple[int, int, str, bool]] = []
    token_index = 0
    for numeric_match in numeric_matches:
        while (
            token_index < len(token_matches)
            and token_matches[token_index].end() <= numeric_match.start()
        ):
            token_match = token_matches[token_index]
            events.append(
                (token_match.start(), token_match.end(), token_match.group().casefold(), False)
            )
            token_index += 1
        canonical_number = next(
            iter(numeric_claims(numeric_match.group())), numeric_match.group()
        )
        events.append(
            (
                numeric_match.start(),
                numeric_match.end(),
                f"<number:{canonical_number}>",
                True,
            )
        )
        while (
            token_index < len(token_matches)
            and token_matches[token_index].start() < numeric_match.end()
        ):
            token_index += 1
    while token_index < len(token_matches):
        token_match = token_matches[token_index]
        events.append(
            (token_match.start(), token_match.end(), token_match.group().casefold(), False)
        )
        token_index += 1

    structures: set[tuple[str, ...]] = set()
    clause_tokens: list[str] = []
    has_relationship = False
    has_numeric = False
    previous_end = 0

    def flush_clause() -> None:
        if (has_relationship or has_numeric) and clause_tokens:
            structures.add(("clause", *clause_tokens))

    for start, end, token, is_numeric in events:
        gap = text[previous_end:start]
        if clause_tokens and re.search(_CLAUSE_BOUNDARY, gap):
            flush_clause()
            clause_tokens.clear()
            has_relationship = False
            has_numeric = False
        clause_tokens.append(token)
        if is_numeric:
            has_numeric = True
        has_relationship = has_relationship or token in _RELATIONSHIP_CONNECTORS
        previous_end = end
    flush_clause()
    return frozenset(structures)


def _safe_text(generated: object, source: str, missing_skills: set[str]) -> bool:
    if not isinstance(generated, str) or not generated.strip():
        return False
    if not numeric_claims(generated).issubset(numeric_claims(source)):
        return False
    if _mentions_missing_skill(generated, missing_skills):
        return False
    if _technical_tokens(generated) != _technical_tokens(source):
        return False
    return _substantive_tokens(generated).issubset(_substantive_tokens(source))


def _safe_text_with_evidence(
    generated: object,
    source: str,
    cited: list[EvidenceRef],
    missing_skills: set[str],
) -> bool:
    if not isinstance(generated, str) or not generated.strip():
        return False
    if not numeric_claims(generated).issubset(numeric_claims(source)):
        return False
    if _mentions_missing_skill(generated, missing_skills):
        return False
    # Relationship policy is independent from lexical connector filtering:
    # words such as ``using`` and ``with`` may be omitted from substantive
    # tokens, but a newly introduced relationship must still be rejected.
    if (
        not _relationship_claims(generated).issubset(_relationship_claims(source))
        or not _relationship_structures(generated).issubset(
            _relationship_structures(source)
        )
    ):
        return False

    source_technical = _technical_tokens(source)
    generated_technical = _technical_tokens(generated)
    cited_technical = set().union(*(_technical_tokens(item.text) for item in cited))
    if not source_technical.issubset(generated_technical):
        return False
    if not generated_technical.issubset(source_technical | cited_technical):
        return False

    allowed_tokens = set().union(
        *(_substantive_tokens(item.text) for item in cited)
    )
    if not _substantive_tokens(generated).issubset(allowed_tokens):
        return False

    source_tokens = _substantive_tokens(source)
    generated_tokens = _substantive_tokens(generated)
    added_tokens = generated_tokens - source_tokens
    if added_tokens & _PROTECTED_CLAIMS:
        return False
    if added_tokens & _RELATIONSHIP_MARKERS:
        return False
    return True


def _neutral_combination(
    source: str,
    target_source_index: int,
    cited: list[EvidenceRef],
    missing_skills: set[str],
    evidence_relevance: dict[Any, int],
) -> str | None:
    """Compose one neutral, source-exact same-role combination.

    This is intentionally a tiny fallback grammar: one target bullet plus one
    non-quantified supporting bullet, joined with a semicolon. It never copies
    metrics or protected responsibility claims into another bullet.
    """
    supplements = [
        item
        for item in cited
        if item.source_index != target_source_index
        and item.text.strip() != source.strip()
    ]
    if not supplements:
        return None
    supplement_ref = max(
        supplements,
        key=lambda item: (
            evidence_relevance.get(
                item.source_uuid or item.evidence_id,
                evidence_relevance.get((item.role_index, item.source_index), 0),
            ),
            -item.source_index,
        ),
    )
    supplement = supplement_ref.text.strip()
    if numeric_claims(supplement) or _mentions_missing_skill(supplement, missing_skills):
        return None
    if _substantive_tokens(supplement) & _PROTECTED_CLAIMS:
        return None
    candidate = f"{source.rstrip(' .;')}; {supplement.rstrip(' .;')}."
    return candidate if _safe_text_with_evidence(candidate, source, cited, missing_skills) else None


@dataclass(frozen=True)
class SanitizedTailoring:
    professional_summary: str | None
    tailored_highlights: list[list[str]]
    summary_fallback: bool
    role_fallbacks: int
    bullet_fallbacks: int
    rejected_patches: int
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    multi_evidence_selections: int = 0


def sanitize_tailoring_output(
    profile: ParsedProfile,
    payload: Any,
    missing_skills: list[str],
    eligible_bullets: dict[int, set[Any]],
    bullet_orders: list[list[Any]],
    evidence_catalog: dict[str, EvidenceRef] | None = None,
    evidence_relevance: dict[Any, int] | None = None,
    source_roles: list[Any] | None = None,
    source_order_ids: list[list[str]] | None = None,
) -> SanitizedTailoring:
    """Apply sparse, source-indexed rewrites while preserving source structure."""
    if not isinstance(payload, dict):
        raise ValueError("Tailoring output must be a JSON object")
    raw_rewrites = payload.get("rewrites")
    if not isinstance(raw_rewrites, list):
        raise ValueError("Tailoring output rewrites must be a list")

    missing_set = set(missing_skills)
    rewrites: dict[str, str] = {}
    rejected = 0
    reasons: Counter[str] = Counter()
    selected_multi_evidence = 0
    relevance = evidence_relevance or {}

    def reject(reason: str) -> None:
        nonlocal rejected
        rejected += 1
        reasons[reason] += 1

    roles = source_roles if source_roles is not None else profile.work_history
    catalog = evidence_catalog or build_evidence_catalog(profile)
    # ``bullet_orders`` is the exact selected source set for this request.
    # Enforce it here rather than relying on the prompt: same-role evidence
    # that was omitted from the tailored output is not valid rewrite evidence.
    selected_source_ids: set[str] = set()
    selected_source_indices: set[int] = set()
    for role_order in bullet_orders:
        for value in role_order:
            if isinstance(value, int) and not isinstance(value, bool):
                selected_source_indices.add(value)
            elif isinstance(value, str):
                selected_source_ids.add(value)
    selected_evidence_ids = {
        ref.source_uuid
        for ref in catalog.values()
        if ref.source_uuid is not None
        and (
            ref.source_uuid in selected_source_ids
            or ref.source_index in selected_source_indices
        )
    }
    for raw in raw_rewrites:
        if not isinstance(raw, dict):
            reject("malformed_patch")
            continue
        target_alias = raw.get("target_alias")
        evidence_aliases = raw.get("evidence_aliases")
        role_index = raw.get("role_index")
        source_index = raw.get("source_index")
        if isinstance(target_alias, str):
            target_ref = (evidence_catalog or {}).get(target_alias)
            if target_ref is None:
                reject("invalid_evidence_id")
                continue
            role_index, source_index = target_ref.role_index, target_ref.source_index
        if isinstance(evidence_aliases, list):
            raw["evidence_ids"] = evidence_aliases
        rewritten = raw.get("rewritten_text")
        if (
            not isinstance(role_index, int)
            or isinstance(role_index, bool)
            or not isinstance(source_index, int)
            or isinstance(source_index, bool)
        ):
            reject("malformed_patch")
            continue
        eligible_keys = eligible_bullets.get(role_index, set())
        target_ref = catalog.get(target_alias or f"r{role_index}:b{source_index}")
        if target_ref is None or target_ref.source_uuid is None:
            reject("invalid_evidence_id")
            continue
        key = target_ref.source_uuid
        if (
            role_index < 0
            or role_index >= len(roles)
            or source_index < 0
            or (
                target_ref.source_uuid not in eligible_keys
                and source_index not in eligible_keys
            )
            or key in rewrites
            or not isinstance(rewritten, str)
            or not rewritten.strip()
        ):
            reject("malformed_patch")
            continue
        source = (
            source_roles[role_index].highlights[source_index].text
            if source_roles is not None
            else profile.work_history[role_index].highlights[source_index]
        )
        if rewritten.strip() == source.strip():
            continue
        raw_evidence_ids = raw.get("evidence_ids")
        if raw_evidence_ids is None:
            reject("missing_target_evidence")
            continue
        if not isinstance(raw_evidence_ids, list):
            reject("invalid_evidence_id")
            continue
        if (
            not raw_evidence_ids
            or any(type(item) is not str for item in raw_evidence_ids)
            or len(set(raw_evidence_ids)) != len(raw_evidence_ids)
        ):
            reject("invalid_evidence_id")
            continue
        refs = [catalog.get(item) for item in raw_evidence_ids]
        if any(ref is None for ref in refs):
            reject("invalid_evidence_id")
            continue
        valid_refs = [ref for ref in refs if ref is not None]
        if any(ref.source_uuid not in selected_evidence_ids for ref in valid_refs):
            reject("omitted_evidence")
            continue
        own_id = target_ref.evidence_id
        if own_id not in raw_evidence_ids or any(
            ref.role_uuid != target_ref.role_uuid for ref in valid_refs
        ):
            reason = (
                "cross_role_evidence"
                if any(ref.role_uuid != target_ref.role_uuid for ref in valid_refs)
                else "missing_target_evidence"
            )
            reject(reason)
            continue
        if (
            not _relationship_claims(rewritten).issubset(_relationship_claims(source))
            or not _relationship_structures(rewritten).issubset(
                _relationship_structures(source)
            )
        ):
            reject("relationship_inference")
            continue
        if _safe_text_with_evidence(rewritten, source, valid_refs, missing_set):
            rewrites[key] = rewritten.strip()
        else:
            composed = _neutral_combination(
                source,
                source_index,
                valid_refs,
                missing_set,
                relevance,
            )
            if composed is not None:
                rewrites[key] = composed
                if len(valid_refs) > 2:
                    selected_multi_evidence += 1
                continue
            if numeric_claims(rewritten) - numeric_claims(source):
                reject("numeric_claim")
            elif (
                _substantive_tokens(rewritten) - _substantive_tokens(source)
            ) & _PROTECTED_CLAIMS:
                reject("protected_claim")
            elif (
                _substantive_tokens(rewritten) - _substantive_tokens(source)
            ) & _RELATIONSHIP_MARKERS:
                reject("relationship_inference")
            else:
                reject("unsupported_token")

    sanitized_roles: list[list[str]] = []
    bullet_fallbacks = rejected
    roles = source_roles if source_roles is not None else profile.work_history
    for role_index, source_role in enumerate(roles):
        source_bullets = (
            [item.text for item in source_role.highlights]
            if source_roles is not None
            else list(source_role.highlights)
        )
        ordered = bullet_orders[role_index]
        ordered_ids = source_order_ids[role_index] if source_order_ids is not None else ordered
        sanitized_roles.append(
            [
                rewrites.get(
                    catalog[alias].source_uuid or "",
                    source_bullets[catalog[alias].source_index],
                )
                for alias in (
                    [
                        f"r{role_index}:b{source_index}"
                        if isinstance(source_index, int)
                        else next(
                            alias
                            for alias, ref in catalog.items()
                            if ref.source_uuid == source_index
                        )
                        for source_index in ordered_ids
                    ]
                )
            ]
        )

    return SanitizedTailoring(
        professional_summary=profile.headline,
        tailored_highlights=sanitized_roles,
        summary_fallback=False,
        role_fallbacks=0,
        bullet_fallbacks=bullet_fallbacks,
        rejected_patches=rejected,
        rejection_reasons=dict(reasons),
        multi_evidence_selections=selected_multi_evidence,
    )
