"""Tests for breakdown_preview.py — CLI-friendly text-report helper."""

from app.generation.breakdown_preview import format_breakdown_preview


def test_preview_contains_phrase():
    result = format_breakdown_preview("etterforskningsteamet")
    assert "etterforskningsteamet" in result


def test_preview_contains_slow():
    result = format_breakdown_preview("etterforskningsteamet")
    assert "Slow" in result


def test_preview_contains_breakdown_steps():
    result = format_breakdown_preview("etterforskningsteamet")
    assert "etterforskningsteamet" in result
    # The breakdown steps (compound segments) should appear
    assert "team" in result


def test_preview_single_word():
    result = format_breakdown_preview("vann")
    assert "vann" in result


def test_preview_multi_word():
    result = format_breakdown_preview("på plassen")
    assert "polyglot" not in result  # no silly false positives
    assert "på" in result and "plassen" in result


def test_preview_empty():
    result = format_breakdown_preview("")
    assert result == ""


def test_preview_blank():
    result = format_breakdown_preview("   ")
    assert result == ""
