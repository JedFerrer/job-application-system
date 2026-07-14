"""Deterministic application-readiness scoring for matched jobs."""

from __future__ import annotations

from app.domain.matching.schemas import MatchReadiness

_VALID_CONFIDENCE = {"strong", "moderate", "stretch", "weak"}


def _normalized_confidence(confidence: str | None) -> str | None:
    if confidence is None:
        return None
    value = confidence.strip().lower()
    return value if value in _VALID_CONFIDENCE else None


def _score_reason(score: float | None, confidence: str | None) -> str:
    if score is None:
        return "Match score unavailable"
    if score < 50:
        return "Low match score"
    if score >= 80 or confidence == "strong":
        return "Strong match score"
    if score >= 65 or confidence == "moderate":
        return "Moderate match score"
    if score >= 50 or confidence == "stretch":
        return "Stretch match score"
    return "Low match score"


def _gap_preview(missing_keywords: list[str]) -> str:
    preview = ", ".join(missing_keywords[:3])
    if len(missing_keywords) > 3:
        preview += ", ..."
    return preview


def build_match_readiness(
    *,
    score: float | None,
    confidence: str | None,
    matched_skills: list[str] | None,
    missing_keywords: list[str] | None,
) -> MatchReadiness:
    """Build a cautious deterministic readiness summary."""

    normalized_confidence = _normalized_confidence(confidence)
    reasons: list[str] = []
    if normalized_confidence == "weak":
        reasons.append("Weak match confidence")
    reasons.append(_score_reason(score, normalized_confidence))
    caveats: list[str] = []

    matched_count = len(matched_skills or [])
    if matched_count > 0:
        reasons.append(f"{matched_count} matched skills found")

    known_missing_count = None
    if missing_keywords is not None:
        known_missing_count = len(missing_keywords)
        if known_missing_count > 0:
            reasons.append(f"Known gaps: {_gap_preview(missing_keywords)}")
    else:
        caveats.append("Detailed skill gaps are not available yet")

    if score is None:
        caveats.append("Match score is not available yet")
        decision = "save"
    elif score < 50 or normalized_confidence == "weak":
        decision = "skip"
    elif score >= 80 or normalized_confidence == "strong":
        decision = "apply"
    elif score >= 65 or normalized_confidence == "moderate":
        decision = "save"
    elif score >= 50 or normalized_confidence == "stretch":
        decision = "stretch"
    else:
        decision = "save"

    if decision == "apply" and known_missing_count:
        decision = "save" if known_missing_count <= 2 else "stretch"
    elif decision == "save" and score is not None and score >= 65 and known_missing_count is not None:
        if known_missing_count >= 3:
            decision = "stretch"

    return MatchReadiness(decision=decision, reasons=reasons, caveats=caveats)
