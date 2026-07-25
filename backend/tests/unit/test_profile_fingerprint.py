"""Pure profile freshness fingerprint tests."""

from app.domain.matching.profile_fingerprint import profile_matching_hash


def profile() -> dict[str, object]:
    return {
        "headline": "Backend Engineer",
        "skills": ["Python", "FastAPI"],
        "certifications": ["AWS"],
        "preferred_roles": ["Backend Engineer"],
        "seniority": "senior",
        "years_experience": 7,
        "work_history": [{
            "title": "Engineer", "company": "Acme", "highlights": ["Built APIs"],
        }],
        "education": [{"degree": "BS Computer Science", "institution": "State U", "year": "2018"}],
        "email": "irrelevant@example.com",
    }


def test_matching_relevant_fields_change_hash() -> None:
    base = profile()
    for field, value in (
        ("certifications", ["CPA"]),
        ("seniority", "lead"),
        ("skills", ["Python"]),
        ("preferred_roles", ["Platform Engineer"]),
        ("headline", "Platform Engineer"),
        ("years_experience", 8),
        ("work_history", [{"title": "Engineer", "highlights": ["Led migrations"]}]),
    ):
        changed = dict(base)
        changed[field] = value
        assert profile_matching_hash(changed) != profile_matching_hash(base)


def test_irrelevant_metadata_and_unordered_collections_do_not_change_hash() -> None:
    base = profile()
    reordered = dict(base)
    reordered["skills"] = ["FastAPI", "Python"]
    reordered["certifications"] = ["AWS"]
    reordered["preferred_roles"] = ["Backend Engineer"]
    reordered["email"] = "other@example.com"
    assert profile_matching_hash(reordered) == profile_matching_hash(base)


def test_hash_is_stable_and_legacy_missing_hash_is_representable() -> None:
    assert profile_matching_hash(profile()) == profile_matching_hash(profile())


def test_work_history_title_and_highlight_changes_change_hash() -> None:
    base = profile()
    title = dict(base)
    title["work_history"] = [{
        "title": "Staff Engineer", "company": "Acme", "highlights": ["Built APIs"],
    }]
    highlight = dict(base)
    highlight["work_history"] = [{
        "title": "Engineer", "company": "Acme", "highlights": ["Designed APIs"],
    }]
    assert profile_matching_hash(title) != profile_matching_hash(base)
    assert profile_matching_hash(highlight) != profile_matching_hash(base)


def test_company_and_education_evidence_fields_change_hash() -> None:
    base = profile()
    company = dict(base)
    company["work_history"] = [{
        "title": "Engineer", "company": "Other", "highlights": ["Built APIs"],
    }]
    degree = dict(base)
    degree["education"] = [{
        "degree": "MS Computer Science", "institution": "State U", "year": "2018",
    }]
    institution = dict(base)
    institution["education"] = [{
        "degree": "BS Computer Science", "institution": "Other U", "year": "2018",
    }]
    year_only = dict(base)
    year_only["education"] = [{
        "degree": "BS Computer Science", "institution": "State U", "year": "2020",
    }]
    assert profile_matching_hash(company) != profile_matching_hash(base)
    assert profile_matching_hash(degree) != profile_matching_hash(base)
    assert profile_matching_hash(institution) != profile_matching_hash(base)
    assert profile_matching_hash(year_only) == profile_matching_hash(base)


def test_case_and_whitespace_are_equivalent() -> None:
    changed = dict(profile())
    changed["headline"] = "  BACKEND   ENGINEER "
    changed["skills"] = [" PYTHON ", "FASTAPI"]
    assert profile_matching_hash(changed) == profile_matching_hash(profile())


def test_missing_and_empty_nested_collections_are_equivalent() -> None:
    base = profile()
    missing = dict(base)
    missing.pop("education")
    empty = dict(base)
    empty["education"] = []
    assert profile_matching_hash(missing) == profile_matching_hash(empty)
