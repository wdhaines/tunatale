"""SRS feedback tests."""

import pytest

from app.models.srs_item import Rating
from app.srs.feedback import ImplicitFeedbackAdapter, PostGenerationFeedback

# ── ImplicitFeedbackAdapter ───────────────────────────────────────────────


def test_no_help_maps_to_good():
    adapter = ImplicitFeedbackAdapter()
    assert adapter.signal_to_rating("no_help") == Rating.GOOD


def test_slowdown_maps_to_hard():
    adapter = ImplicitFeedbackAdapter()
    assert adapter.signal_to_rating("slowdown") == Rating.HARD


def test_translation_request_maps_to_again():
    adapter = ImplicitFeedbackAdapter()
    assert adapter.signal_to_rating("translation_request") == Rating.AGAIN


def test_fast_forward_maps_to_easy():
    adapter = ImplicitFeedbackAdapter()
    assert adapter.signal_to_rating("fast_forward") == Rating.EASY


def test_unknown_signal_raises():
    adapter = ImplicitFeedbackAdapter()
    with pytest.raises((ValueError, KeyError)):
        adapter.signal_to_rating("unknown_signal")


# ── PostGenerationFeedback ────────────────────────────────────────────────


def test_only_used_collocations_marked_reviewed():
    feedback = PostGenerationFeedback()
    provided = ["dober dan", "hvala lepa", "prosim"]
    story_text = "V zgodbi je bilo dober dan in hvala lepa."
    used = feedback.find_used_collocations(provided, story_text)
    assert "dober dan" in used
    assert "hvala lepa" in used
    assert "prosim" not in used


def test_empty_story_marks_nothing():
    feedback = PostGenerationFeedback()
    provided = ["dober dan", "hvala lepa"]
    used = feedback.find_used_collocations(provided, "")
    assert len(used) == 0


def test_case_insensitive_matching():
    feedback = PostGenerationFeedback()
    provided = ["Dober Dan"]
    story_text = "V zgodbi je bilo dober dan."
    used = feedback.find_used_collocations(provided, story_text)
    assert "Dober Dan" in used
