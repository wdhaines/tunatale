"""Tests for the Lemmatizer protocol and LowercaseLemmatizer implementation."""

from __future__ import annotations

from app.srs.lemmatizer import (
    Lemmatizer,
    LowercaseLemmatizer,
    _parse_morphology,
)


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

    # ── analyze() tests ─────────────────────────────────────────────────

    def test_analyze_returns_lowercase_with_empty_morphology(self):
        lemma, case, number = self.lemmatizer.analyze("Miza", "sl")
        assert lemma == "miza"
        assert case == ""
        assert number == ""

    def test_analyze_empty_string(self):
        lemma, case, number = self.lemmatizer.analyze("", "en")
        assert lemma == ""
        assert case == ""
        assert number == ""

    def test_analyze_language_code_ignored(self):
        """LowercaseLemmatizer is language-agnostic — same result for any code."""
        sl_result = self.lemmatizer.analyze("Mize", "sl")
        en_result = self.lemmatizer.analyze("Mize", "en")
        assert sl_result == en_result


class TestParseMorphology:
    def test_full_features(self):
        case, number = _parse_morphology("Case=Gen|Gender=Fem|Number=Sing")
        assert case == "Gen"
        assert number == "Sing"

    def test_no_case(self):
        case, number = _parse_morphology("Gender=Masc|Number=Plur")
        assert case == ""
        assert number == "Plur"

    def test_no_number(self):
        case, number = _parse_morphology("Case=Nom|Gender=Masc")
        assert case == "Nom"
        assert number == ""

    def test_empty_string(self):
        case, number = _parse_morphology("")
        assert case == ""
        assert number == ""

    def test_dual_number(self):
        case, number = _parse_morphology("Case=Ins|Gender=Masc|Number=Dual")
        assert case == "Ins"
        assert number == "Dual"
