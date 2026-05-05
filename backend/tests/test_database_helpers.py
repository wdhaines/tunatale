"""Tests for database helper functions."""

from app.srs.database import _parse_last_review


class TestParseLastReview:
    """Tests for _parse_last_review."""

    def test_returns_none_for_none(self):
        assert _parse_last_review(None) is None

    def test_parses_datetime_string(self):
        dt = _parse_last_review("2026-05-04T10:00:00+00:00")
        assert dt is not None
        assert dt.year == 2026

    def test_parses_date_only_string(self):
        """date-only string is parsed by fromisoformat."""
        dt = _parse_last_review("2026-05-04")
        assert dt is not None
        assert dt.year == 2026

    def test_promotes_naive_datetime(self):
        """Naive datetime gets tzinfo added."""
        dt = _parse_last_review("2026-05-04T10:00:00")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_returns_none_for_invalid(self):
        """Invalid string raises ValueError."""
        import pytest

        with pytest.raises(ValueError):
            _parse_last_review("not a date")
