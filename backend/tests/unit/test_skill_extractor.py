"""Unit coverage for token-aware catalog skill extraction."""

from __future__ import annotations

from app.domain.matching.catalog_models import (
    CatalogMetadata,
    SkillCatalog,
    SkillDefinition,
    normalize_catalog_label,
)
from app.domain.matching.skill_extractor import SkillExtractor


def _catalog(*skills: tuple[str, str, tuple[str, ...]]) -> SkillCatalog:
    definitions = tuple(
        SkillDefinition(skill_id=skill_id, preferred_label=label, alternative_labels=aliases)
        for skill_id, label, aliases in skills
    )
    alias_to_skill_id = {
        normalize_catalog_label(alias): definition.skill_id
        for definition in definitions
        for alias in (definition.preferred_label, *definition.alternative_labels)
    }
    return SkillCatalog(
        metadata=CatalogMetadata("test", "test", None, None, None),
        skills=definitions,
        alias_to_skill_id=alias_to_skill_id,
    )


def test_extractor_normalizes_unicode_whitespace_and_punctuation_with_offsets() -> None:
    extractor = SkillExtractor(
        _catalog(
            ("local:python", "python", ("python",)),
            ("local:node-js", "node.js", ("node.js",)),
            ("local:c-plus-plus", "c++", ("c++",)),
            ("local:c-sharp", "c#", ("c#",)),
            ("local:dot-net", ".net", (".net",)),
            ("local:ci-cd", "ci/cd", ("ci/cd",)),
        )
    )
    text = "Ｐｙｔｈｏｎ\tNode.js C++ C# .NET CI/CD"

    report = extractor.extract(text)

    assert [mention.canonical_label for mention in report.mentions] == [
        "python",
        "node.js",
        "c++",
        "c#",
        ".net",
        "ci/cd",
    ]
    assert report.mentions[0].matched_text == "Ｐｙｔｈｏｎ"
    assert report.mentions[0].start_offset == 0
    assert report.mentions[0].end_offset == len("Ｐｙｔｈｏｎ")
    assert report.mentions[1].job_excerpt == text


def test_extractor_uses_longest_non_overlapping_match_first() -> None:
    extractor = SkillExtractor(
        _catalog(
            ("local:rest", "rest", ("rest",)),
            ("local:rest-api", "rest api", ("rest api",)),
        )
    )

    report = extractor.extract("Required REST API experience")

    assert [mention.skill_id for mention in report.mentions] == ["local:rest-api"]


def test_extractor_rejects_ambiguous_aliases_in_prose_but_allows_structured_fields() -> None:
    extractor = SkillExtractor(
        _catalog(
            ("local:node-js", "node.js", ("node",)),
            ("local:rest-api", "rest api", ("rest",)),
            ("local:python", "python", ("py",)),
        )
    )

    prose = extractor.extract("Rest after traversing the node, then run py tests.")
    structured = extractor.extract("node, rest, py", source="structured")

    assert prose.mentions == ()
    assert [mention.canonical_label for mention in structured.mentions] == [
        "node.js",
        "rest api",
        "python",
    ]
