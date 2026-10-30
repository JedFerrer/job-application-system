"""Request-local evidence and deterministic cover-letter grounding checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.domain.matching.skill_matcher import extract_known_skills
from app.domain.resume.schemas import ParsedProfile
from app.domain.resume.truthfulness import (
    _PROTECTED_CLAIMS,
    _relationship_claims,
    _substantive_tokens,
    _technical_tokens,
    numeric_claims,
)

_PRESENTATION_TOKENS = frozenset(
    {
        "a",
        "am",
        "an",
        "and",
        "apply",
        "applying",
        "background",
        "bring",
        "candidate",
        "excited",
        "experience",
        "for",
        "have",
        "i",
        "i'm",
        "in",
        "interest",
        "interested",
        "is",
        "it",
        "my",
        "offer",
        "opportunity",
        "our",
        "role",
        "skills",
        "that",
        "the",
        "this",
        "to",
        "we",
        "with",
        "would",
    }
)
_CONTEXT_INTENT_TOKENS = frozenset(
    {
        "apply",
        "application",
        "consider",
        "considering",
        "contribute",
        "contributing",
        "conversation",
        "discuss",
        "discussion",
        "excited",
        "enthusiastic",
        "interest",
        "interested",
        "join",
        "love",
        "opportunity",
        "position",
        "role",
        "thank",
        "thanks",
        "welcome",
    }
)
_CONTEXT_FIXED_TOKENS = frozenset(
    {
        "a",
        "about",
        "am",
        "an",
        "and",
        "application",
        "at",
        "can",
        "company",
        "consider",
        "considering",
        "contribute",
        "contributing",
        "conversation",
        "discuss",
        "discussion",
        "excited",
        "for",
        "forward",
        "how",
        "i",
        "i'm",
        "in",
        "interest",
        "interested",
        "is",
        "it",
        "join",
        "learn",
        "look",
        "love",
        "my",
        "of",
        "on",
        "opportunity",
        "our",
        "position",
        "role",
        "support",
        "team",
        "thank",
        "thanks",
        "the",
        "this",
        "to",
        "with",
        "apply",
        "welcome",
        "would",
        "you",
        "your",
    }
)


@dataclass(frozen=True)
class CoverLetterEvidence:
    evidence_id: str
    category: str
    text: str
    role_index: int | None = None
    company: str | None = None
    title: str | None = None


def build_evidence_catalog(profile: ParsedProfile) -> dict[str, CoverLetterEvidence]:
    """Build a stable, request-local catalog from canonical profile facts."""
    catalog: dict[str, CoverLetterEvidence] = {}
    for role_index, role in enumerate(profile.work_history):
        for highlight_index, text in enumerate(role.highlights):
            catalog[f"w{role_index}:h{highlight_index}"] = CoverLetterEvidence(
                evidence_id=f"w{role_index}:h{highlight_index}",
                category="work_history",
                text=text,
                role_index=role_index,
                company=role.company,
                title=role.title,
            )
    for project_index, project in enumerate(profile.projects):
        if project.description:
            evidence_id = f"p{project_index}:d"
            catalog[evidence_id] = CoverLetterEvidence(evidence_id, "project", project.description)
        for index, technology in enumerate(project.technologies):
            evidence_id = f"p{project_index}:t{index}"
            catalog[evidence_id] = CoverLetterEvidence(evidence_id, "project", technology)
        for index, achievement in enumerate(project.achievements):
            evidence_id = f"p{project_index}:a{index}"
            catalog[evidence_id] = CoverLetterEvidence(evidence_id, "project", achievement)
    for index, skill in enumerate(profile.skills):
        catalog[f"s{index}"] = CoverLetterEvidence(f"s{index}", "skill", skill)
    for index, certification in enumerate(profile.certifications):
        catalog[f"c{index}"] = CoverLetterEvidence(f"c{index}", "certification", certification)
    for index, credential in enumerate(profile.licenses):
        text = credential.name + (f" ({credential.issuer})" if credential.issuer else "")
        catalog[f"l{index}"] = CoverLetterEvidence(f"l{index}", "credential", text)
    for index, education in enumerate(profile.education):
        text = " ".join(
            value for value in (education.degree, education.institution, education.year) if value
        )
        if text:
            catalog[f"e{index}"] = CoverLetterEvidence(f"e{index}", "education", text)
    for index, language in enumerate(profile.languages):
        text = language.language + (f" ({language.proficiency})" if language.proficiency else "")
        catalog[f"lang{index}"] = CoverLetterEvidence(f"lang{index}", "language", text)
    return catalog


def evidence_bundle(catalog: dict[str, CoverLetterEvidence]) -> list[dict[str, object]]:
    """Serialize evidence without exposing request-local IDs outside the prompt."""
    return [
        {
            "evidence_id": item.evidence_id,
            "category": item.category,
            "text": item.text,
            "role_index": item.role_index,
            "company": item.company,
            "title": item.title,
        }
        for item in catalog.values()
    ]


def _role_mentions(text: str, catalog: dict[str, CoverLetterEvidence]) -> list[set[int]]:
    """Resolve each name span, retaining the most specific overlapping label."""
    mentions: list[set[int]] = []
    for field in ("company", "title"):
        spans: dict[tuple[int, int], set[int]] = {}
        for item in catalog.values():
            phrase = getattr(item, field)
            if item.category != "work_history" or item.role_index is None or not phrase:
                continue
            for match in re.finditer(
                rf"(?<!\w){re.escape(phrase.strip())}(?!\w)", text, re.IGNORECASE
            ):
                spans.setdefault(match.span(), set()).add(item.role_index)
        retained: list[tuple[int, int]] = []
        for start, end in sorted(spans, key=lambda span: span[0] - span[1]):
            if any(start < other_end and other_start < end for other_start, other_end in retained):
                continue
            retained.append((start, end))
            mentions.append(spans[(start, end)])
    return mentions


def _application_context(text: str, job_title: str | None, company: str | None) -> str:
    """Mask complete supplied names only after narrow application-intent phrases."""
    title_prefix = r"\b(?:apply|applying)\s+for\s+(?:the\s+)?|\binterested\s+in\s+(?:the\s+)?"
    company_prefix = (
        r"\bto\s+join\s+|\binterested\s+in\s+joining\s+|\b(?:apply|applying)\s+to\s+|"
        rf"(?:{title_prefix})(?:role|position|opportunity)\s+(?:at|with)\s+"
    )
    if job_title and job_title.strip():
        company_prefix += (
            rf"|(?:{title_prefix}){re.escape(job_title.strip())}"
            r"(?:\s+(?:role|position))?\s+(?:at|with)\s+"
        )
    patterns = (
        (job_title, title_prefix),
        (company, company_prefix),
    )
    spans: list[tuple[int, int]] = []
    for name, prefix in patterns:
        if not name or not name.strip():
            continue
        for match in re.finditer(
            rf"(?:{prefix})(?P<name>{re.escape(name.strip())})(?!\w)", text, re.IGNORECASE
        ):
            spans.append(match.span("name"))
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + " " * (end - start) + text[end:]
    return text


def _validate_context(
    text: str,
    *,
    job_title: str | None,
    company: str | None,
) -> None:
    text = _application_context(text, job_title, company)
    if numeric_claims(text) or extract_known_skills(text):
        raise ValueError("context sentence contains candidate factual signals")
    if set(re.findall(r"\b[a-z]+\b", text.casefold())) & _PROTECTED_CLAIMS:
        raise ValueError("context sentence contains protected responsibility language")
    tokens = set(re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*", text.casefold()))
    if not tokens.issubset(_CONTEXT_FIXED_TOKENS):
        raise ValueError("context sentence is outside the allowed application language")
    if not tokens & _CONTEXT_INTENT_TOKENS:
        raise ValueError("context sentence lacks application intent")


def _validate_evidence_sentence(
    text: str,
    cited: list[CoverLetterEvidence],
    missing_skills: set[str],
    catalog: dict[str, CoverLetterEvidence],
) -> None:
    cited_text = "\n".join(item.text for item in cited)
    if numeric_claims(text) - numeric_claims(cited_text):
        raise ValueError("unsupported numeric claim")
    generated_skills = set(extract_known_skills(text))
    if generated_skills & missing_skills:
        raise ValueError("missing JD requirement claimed")
    cited_skills = set(extract_known_skills(cited_text))
    if not generated_skills.issubset(cited_skills):
        raise ValueError("unsupported skill or technology")

    mentions = _role_mentions(text, catalog)
    mentioned_role_indices = set.intersection(*mentions) if mentions else set()
    cited_role_indices = {item.role_index for item in cited if item.category == "work_history"}
    if mentions and (
        len(mentioned_role_indices) != 1 or mentioned_role_indices != cited_role_indices
    ):
        raise ValueError("cross-role attribution")

    if not _technical_tokens(text).issubset(_technical_tokens(cited_text)):
        raise ValueError("unsupported technical token")
    cited_substantive = set().union(*(_substantive_tokens(item.text) for item in cited))
    cited_role_terms = {
        token.casefold()
        for item in cited
        if item.category == "work_history"
        for value in (item.company, item.title)
        if value
        for token in re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*", value)
    }
    if not _substantive_tokens(text).issubset(
        cited_substantive | cited_role_terms | _PRESENTATION_TOKENS
    ):
        raise ValueError("unsupported substantive claim")
    if not _substantive_tokens(text) & cited_substantive:
        raise ValueError("evidence sentence lacks cited factual content")
    cited_relationships = set().union(*(_relationship_claims(item.text) for item in cited))
    if not _relationship_claims(text).issubset(cited_relationships):
        raise ValueError("unsupported relationship claim")
    protected = set(re.findall(r"\b[a-z]+\b", text.casefold())) & _PROTECTED_CLAIMS
    cited_protected = set(re.findall(r"\b[a-z]+\b", cited_text.casefold())) & _PROTECTED_CLAIMS
    if not protected.issubset(cited_protected):
        raise ValueError("unsupported responsibility claim")


def validate_cover_letter_payload(
    payload: Any,
    *,
    catalog: dict[str, CoverLetterEvidence],
    missing_skills: set[str],
    job_title: str | None = None,
    company: str | None = None,
) -> str:
    """Validate structured output and render safe plain-text paragraphs."""
    if not isinstance(payload, dict) or set(payload) != {"paragraphs"}:
        raise ValueError("cover letter output must contain paragraphs only")
    paragraphs = payload["paragraphs"]
    if not isinstance(paragraphs, list) or len(paragraphs) != 3:
        raise ValueError("cover letter must contain exactly three paragraphs")
    rendered: list[str] = []
    for paragraph in paragraphs:
        if not isinstance(paragraph, dict) or set(paragraph) != {"sentences"}:
            raise ValueError("paragraph shape is invalid")
        sentences = paragraph["sentences"]
        if not isinstance(sentences, list) or not sentences:
            raise ValueError("paragraph must contain sentences")
        parts: list[str] = []
        for sentence in sentences:
            if not isinstance(sentence, dict) or set(sentence) != {"text", "kind", "evidence_ids"}:
                raise ValueError("sentence shape is invalid")
            text = sentence["text"]
            kind = sentence["kind"]
            ids = sentence["evidence_ids"]
            if not isinstance(text, str) or not text.strip() or kind not in {"evidence", "context"}:
                raise ValueError("sentence is malformed")
            if (
                not isinstance(ids, list)
                or any(type(item) is not str for item in ids)
                or len(set(ids)) != len(ids)
            ):
                raise ValueError("evidence IDs are malformed")
            if kind == "context":
                if ids:
                    raise ValueError("context sentence cannot cite evidence")
                _validate_context(text, job_title=job_title, company=company)
            else:
                if not ids:
                    raise ValueError("evidence sentence requires evidence IDs")
                cited = [catalog.get(item) for item in ids]
                if any(item is None for item in cited):
                    raise ValueError("unknown evidence ID")
                _validate_evidence_sentence(
                    text,
                    [item for item in cited if item is not None],
                    missing_skills,
                    catalog,
                )
            parts.append(text.strip())
        rendered.append(" ".join(parts))
    return "\n\n".join(rendered)
