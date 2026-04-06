"""Tests for the tokenize() utility."""

from __future__ import annotations

from app.srs.tokenizer import tokenize


class TestTokenize:
    def test_strips_trailing_punctuation(self):
        assert tokenize("Zdravo,") == ["Zdravo"]

    def test_strips_trailing_period(self):
        assert tokenize("svet.") == ["svet"]

    def test_strips_exclamation(self):
        assert tokenize("Zdravo!") == ["Zdravo"]

    def test_strips_question_mark(self):
        assert tokenize("Kje?") == ["Kje"]

    def test_multiple_words(self):
        assert tokenize("Zdravo, svet!") == ["Zdravo", "svet"]

    def test_preserves_accented_characters(self):
        assert tokenize("Čaj.") == ["Čaj"]

    def test_empty_string(self):
        assert tokenize("") == []

    def test_whitespace_only(self):
        assert tokenize("   ") == []

    def test_word_without_punctuation(self):
        assert tokenize("banka") == ["banka"]

    def test_multiple_punctuation(self):
        assert tokenize("Prosim...") == ["Prosim"]

    def test_interior_hyphen_preserved(self):
        # Hyphens inside a word are preserved (only edge punctuation stripped)
        assert tokenize("ne-vem") == ["ne-vem"]

    def test_full_sentence(self):
        result = tokenize("Kje je banka?")
        assert result == ["Kje", "je", "banka"]

    def test_leading_punctuation_stripped(self):
        assert tokenize('"Zdravo"') == ["Zdravo"]
