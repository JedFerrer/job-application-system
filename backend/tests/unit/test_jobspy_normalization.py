"""Unit tests for the JobSpy row -> RawJobPost normalization."""

from __future__ import annotations

import math
from datetime import datetime

import pytest

from app.infrastructure.job_sources.jobspy_source import (
    _clean_bool,
    _clean_datetime,
    _clean_float,
    _clean_str,
    _json_safe,
)

pytestmark = pytest.mark.unit


def test_clean_str_drops_nan_and_blanks() -> None:
    assert _clean_str("  hello ") == "hello"
    assert _clean_str("") is None
    assert _clean_str("nan") is None
    assert _clean_str(None) is None


def test_clean_float_handles_nan_and_bad_values() -> None:
    assert _clean_float("120000") == 120000.0
    assert _clean_float(math.nan) is None
    assert _clean_float("not-a-number") is None
    assert _clean_float(None) is None


def test_clean_bool_variants() -> None:
    assert _clean_bool("true") is True
    assert _clean_bool("0") is False
    assert _clean_bool(True) is True
    assert _clean_bool(math.nan) is None
    assert _clean_bool("maybe") is None


def test_clean_datetime_naive_becomes_utc() -> None:
    result = _clean_datetime("2024-01-15")
    assert isinstance(result, datetime)
    assert result.tzinfo is not None


def test_json_safe_coerces_non_primitives() -> None:
    assert _json_safe(math.nan) is None
    assert _json_safe("x") == "x"
    assert _json_safe(5) == 5
    assert _json_safe(object()) is not None  # falls back to str()
