"""Tests for rating_from_input unified input parser."""

import pytest

from app.models.srs_item import Rating
from app.srs.feedback import rating_from_input


class TestRatingFromInput:
    def test_explicit_rating_again(self):
        assert rating_from_input(rating="again") == Rating.AGAIN

    def test_explicit_rating_hard(self):
        assert rating_from_input(rating="hard") == Rating.HARD

    def test_explicit_rating_good(self):
        assert rating_from_input(rating="good") == Rating.GOOD

    def test_explicit_rating_easy(self):
        assert rating_from_input(rating="easy") == Rating.EASY

    def test_explicit_rating_case_insensitive(self):
        assert rating_from_input(rating="GOOD") == Rating.GOOD
        assert rating_from_input(rating="Hard") == Rating.HARD

    def test_signal_no_help(self):
        assert rating_from_input(signal="no_help") == Rating.GOOD

    def test_signal_slowdown(self):
        assert rating_from_input(signal="slowdown") == Rating.HARD

    def test_signal_translation_request(self):
        assert rating_from_input(signal="translation_request") == Rating.AGAIN

    def test_signal_fast_forward(self):
        assert rating_from_input(signal="fast_forward") == Rating.EASY

    def test_both_provided_raises(self):
        with pytest.raises(ValueError, match="exactly one"):
            rating_from_input(rating="good", signal="no_help")

    def test_neither_provided_raises(self):
        with pytest.raises(ValueError, match="exactly one"):
            rating_from_input()

    def test_unknown_rating_raises(self):
        with pytest.raises(ValueError):
            rating_from_input(rating="perfect")

    def test_unknown_signal_raises(self):
        with pytest.raises(ValueError):
            rating_from_input(signal="unknown_signal")
