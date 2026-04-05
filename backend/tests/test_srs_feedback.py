"""SRS feedback tests."""

import pytest

from app.models.srs_item import Rating
from app.srs.feedback import ImplicitFeedbackAdapter, PostGenerationFeedback


class TestImplicitFeedbackAdapter:
    """Tests for ImplicitFeedbackAdapter signal-to-rating mappings."""

    @pytest.mark.parametrize(
        "signal, expected_rating",
        [
            ("no_help", Rating.GOOD),
            ("slowdown", Rating.HARD),
            ("translation_request", Rating.AGAIN),
            ("fast_forward", Rating.EASY),
        ],
    )
    def test_signal_to_rating(self, signal, expected_rating):
        adapter = ImplicitFeedbackAdapter()
        assert adapter.signal_to_rating(signal) == expected_rating, f"signal={signal!r}"

    def test_unknown_signal_raises(self):
        adapter = ImplicitFeedbackAdapter()
        with pytest.raises((ValueError, KeyError)):
            adapter.signal_to_rating("unknown_signal")


class TestPostGenerationFeedback:
    """Tests for PostGenerationFeedback collocation usage detection."""

    def test_only_used_collocations_marked_reviewed(self):
        feedback = PostGenerationFeedback()
        provided = ["dober dan", "hvala lepa", "prosim"]
        story_text = "V zgodbi je bilo dober dan in hvala lepa."
        used = feedback.find_used_collocations(provided, story_text)
        assert "dober dan" in used
        assert "hvala lepa" in used
        assert "prosim" not in used

    def test_empty_story_marks_nothing(self):
        feedback = PostGenerationFeedback()
        provided = ["dober dan", "hvala lepa"]
        used = feedback.find_used_collocations(provided, "")
        assert len(used) == 0

    def test_case_insensitive_matching(self):
        feedback = PostGenerationFeedback()
        provided = ["Dober Dan"]
        story_text = "V zgodbi je bilo dober dan."
        used = feedback.find_used_collocations(provided, story_text)
        assert "Dober Dan" in used
