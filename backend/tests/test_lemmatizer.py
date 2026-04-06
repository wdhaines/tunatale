"""Tests for the Lemmatizer protocol and LowercaseLemmatizer implementation."""

from __future__ import annotations

from app.srs.lemmatizer import Lemmatizer, LowercaseLemmatizer


class TestLowercaseLemmatizer:
    def setup_method(self):
        self.lemmatizer = LowercaseLemmatizer()

    def test_lowercases_capitalized_word(self):
        assert self.lemmatizer.lemmatize("Zdravo", "sl") == "zdravo"

    def test_lowercases_all_caps(self):
        assert self.lemmatizer.lemmatize("COFFEE", "en") == "coffee"

    def test_already_lowercase_is_idempotent(self):
        assert self.lemmatizer.lemmatize("already", "en") == "already"

    def test_handles_accented_characters(self):
        assert self.lemmatizer.lemmatize("Čaj", "sl") == "čaj"

    def test_language_code_does_not_affect_result(self):
        assert self.lemmatizer.lemmatize("Word", "en") == self.lemmatizer.lemmatize("Word", "sl")

    def test_empty_string(self):
        assert self.lemmatizer.lemmatize("", "en") == ""

    def test_satisfies_lemmatizer_protocol(self):
        assert isinstance(self.lemmatizer, Lemmatizer)
