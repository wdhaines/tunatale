"""Tests for stale "Day N:" prefix stripping in lesson/curriculum titles."""

import pytest

from app.common.titles import strip_day_prefix


class TestStripDayPrefix:
    """The planner/story LLMs prefix titles with their own (unreliable) day number."""

    @pytest.mark.parametrize(
        "raw",
        [
            "Day 5: The Trail Ends in the Garden",
            "day 5: The Trail Ends in the Garden",
            "DAY 5: The Trail Ends in the Garden",
            "Day 5 - The Trail Ends in the Garden",
            "Day 5 – The Trail Ends in the Garden",
            "Day 5 — The Trail Ends in the Garden",
            "Day 5. The Trail Ends in the Garden",
            "Day 5) The Trail Ends in the Garden",
            "Day5:The Trail Ends in the Garden",
            "  Day 5:   The Trail Ends in the Garden  ",
        ],
    )
    def test_strips_day_number_prefix(self, raw):
        assert strip_day_prefix(raw) == "The Trail Ends in the Garden"

    def test_keeps_day_word_without_a_number(self):
        """ "Day" as an ordinary word is not a numbering prefix."""
        assert strip_day_prefix("Day Trip to the Coast") == "Day Trip to the Coast"

    def test_keeps_day_number_that_is_not_a_prefix(self):
        assert strip_day_prefix("The Trail Ends on Day 5") == "The Trail Ends on Day 5"

    def test_requires_a_separator(self):
        """Without punctuation after the number this is a real title, not a prefix."""
        assert strip_day_prefix("Day 5 Reasons to Visit") == "Day 5 Reasons to Visit"

    def test_keeps_a_title_that_is_only_a_prefix(self):
        """Stripping would leave nothing to show, so leave it alone."""
        assert strip_day_prefix("Day 5:") == "Day 5:"

    def test_leaves_a_clean_title_untouched(self):
        assert strip_day_prefix("A Woman Is Gone") == "A Woman Is Gone"
