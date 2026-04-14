"""Tests for the collocation span matcher."""

from __future__ import annotations

from app.srs.collocation_matcher import match_spans


class TestMatchSpans:
    def test_empty_tokens_returns_empty(self):
        result = match_spans([], {})
        assert result == []

    def test_single_word_no_index_returns_no_span(self):
        result = match_spans(["zdravo"], {})
        assert result == [(None, False)]

    def test_multiple_words_no_index_all_none(self):
        result = match_spans(["kje", "je", "banka"], {})
        assert result == [(None, False), (None, False), (None, False)]

    def test_matches_two_word_collocation(self):
        index = {("kje", "je"): 42}
        result = match_spans(["kje", "je", "banka"], index)
        assert result[0] == (42, True)
        assert result[1] == (42, False)
        assert result[2] == (None, False)

    def test_matches_collocation_at_end(self):
        index = {("je", "banka"): 7}
        result = match_spans(["kje", "je", "banka"], index)
        assert result[0] == (None, False)
        assert result[1] == (7, True)
        assert result[2] == (7, False)

    def test_longest_match_wins_over_shorter(self):
        index = {("kje", "je"): 10, ("kje", "je", "banka"): 20}
        result = match_spans(["kje", "je", "banka"], index)
        assert result[0] == (20, True)
        assert result[1] == (20, False)
        assert result[2] == (20, False)

    def test_no_overlap_after_first_match(self):
        # "kje je" is consumed; "je banka" cannot match
        index = {("kje", "je"): 10, ("je", "banka"): 20}
        result = match_spans(["kje", "je", "banka"], index)
        assert result[0] == (10, True)
        assert result[1] == (10, False)
        assert result[2] == (None, False)

    def test_multiple_non_overlapping_matches(self):
        index = {("kje", "je"): 10, ("lepa", "hiša"): 20}
        result = match_spans(["kje", "je", "lepa", "hiša"], index)
        assert result[0] == (10, True)
        assert result[1] == (10, False)
        assert result[2] == (20, True)
        assert result[3] == (20, False)

    def test_collocation_start_true_only_for_first_token(self):
        index = {("a", "b", "c"): 99}
        result = match_spans(["a", "b", "c"], index)
        assert result[0][1] is True  # start
        assert result[1][1] is False
        assert result[2][1] is False

    def test_span_id_is_db_item_id(self):
        db_id = 1234
        index = {("prosim", "kavo"): db_id}
        result = match_spans(["prosim", "kavo"], index)
        assert result[0][0] == db_id
        assert result[1][0] == db_id

    def test_single_word_match_not_triggered(self):
        # Index only contains 2+ word tuples; single word tuples should not match
        index = {("banka",): 5}  # degenerate 1-gram in index; match_spans may or may not match
        # The function is only intended for collocations (len >= 2), but it should be safe
        result = match_spans(["banka"], index)
        # We only guarantee no crash; span_id may be None or whatever the impl does
        assert isinstance(result, list)
        assert len(result) == 1
