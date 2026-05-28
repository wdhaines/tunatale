"""Tests for Phase F: function-word detection and cloze text generation."""

from __future__ import annotations

from app.srs.function_words import (
    SLOVENE_FUNCTION_WORDS,
    _hint_text,
    is_function_word,
    make_case_cloze_text,
    make_cloze_text,
)


class TestIsFunctionWord:
    def test_known_function_word_returns_true(self):
        assert is_function_word("je", "sl") is True

    def test_content_word_returns_false(self):
        assert is_function_word("kava", "sl") is False

    def test_case_insensitive_matching(self):
        assert is_function_word("Je", "sl") is True
        assert is_function_word("JE", "sl") is True

    def test_english_out_of_scope(self):
        assert is_function_word("je", "en") is False

    def test_unknown_language_code_returns_false(self):
        assert is_function_word("je", "de") is False

    def test_empty_string_not_in_set(self):
        assert is_function_word("", "sl") is False


class TestMakeClozeText:
    def test_basic_cloze(self):
        result = make_cloze_text("ki", "knjiga, ki je tam")
        assert result == "knjiga, {{c1::ki}} je tam"

    def test_boundary_at_start(self):
        result = make_cloze_text("v", "v centru mesta")
        assert result == "{{c1::v}} centru mesta"

    def test_no_substring_wrap(self):
        result = make_cloze_text("v", "ovsene")
        assert result == "ovsene"

    def test_multi_occurrence(self):
        result = make_cloze_text("ki", "ki je ki")
        assert result == "{{c1::ki}} je {{c1::ki}}"

    def test_empty_sentence(self):
        assert make_cloze_text("ki", "") == ""

    def test_idempotent_already_clozed(self):
        result = make_cloze_text("ki", "{{c1::ki}} je tam")
        assert result == "{{c1::ki}} je tam"

    def test_empty_surface(self):
        result = make_cloze_text("", "knjiga, ki je tam")
        assert result == "knjiga, ki je tam"

    def test_surface_with_regex_metachars(self):
        result = make_cloze_text("je", "to.je.")
        assert result == "to.{{c1::je}}."

    def test_preserves_sentence_punctuation(self):
        result = make_cloze_text("je", "Kje si?")
        assert result == "Kje si?"

    def test_case_insensitive_case_preserving(self):
        result = make_cloze_text("kje", "Kje je banka?")
        assert result == "{{c1::Kje}} je banka?"

    def test_case_insensitive_mixed_case(self):
        result = make_cloze_text("je", "Kje je banka?")
        assert result == "Kje {{c1::je}} banka?"

    def test_multi_occurrence_mixed_case(self):
        result = make_cloze_text("to", "To je to.")
        assert result == "{{c1::To}} je {{c1::to}}."


class TestHintText:
    def test_with_case_and_number(self):
        assert _hint_text("miza", "Gen", "Sing") == "miza, gen sg"

    def test_case_only(self):
        assert _hint_text("voda", "Acc", "") == "voda, acc"

    def test_number_only(self):
        assert _hint_text("prijatelj", "", "Plur") == "prijatelj, pl"

    def test_empty_morphology(self):
        assert _hint_text("mesto", "", "") == "mesto"

    def test_dual_number(self):
        assert _hint_text("roka", "Ins", "Dual") == "roka, ins du"

    def test_plural_accusative(self):
        assert _hint_text("rokavica", "Acc", "Plur") == "rokavica, acc pl"


class TestMakeCaseClozeText:
    def test_basic_case_cloze(self):
        result = make_case_cloze_text(
            "mize",
            "miza",
            "Gen",
            "Sing",
            "Nimam mize.",
        )
        assert result == "Nimam {{c1::mize::miza, gen sg}}."

    def test_empty_sentence(self):
        assert make_case_cloze_text("mize", "miza", "Gen", "Sing", "") == ""

    def test_idempotent_already_clozed(self):
        result = make_case_cloze_text(
            "mize",
            "miza",
            "Gen",
            "Sing",
            "Nimam {{c1::mize}}.",
        )
        assert result == "Nimam {{c1::mize}}."

    def test_empty_surface(self):
        result = make_case_cloze_text(
            "",
            "miza",
            "Gen",
            "Sing",
            "Nimam mize.",
        )
        assert result == "Nimam mize."

    def test_multi_occurrence(self):
        result = make_case_cloze_text(
            "prijateljem",
            "prijatelj",
            "Ins",
            "Sing",
            "S prijateljem grem s prijateljem.",
        )
        assert result == "S {{c1::prijateljem::prijatelj, ins sg}} grem s {{c1::prijateljem::prijatelj, ins sg}}."

    def test_hint_with_dual(self):
        result = make_case_cloze_text(
            "rokama",
            "roka",
            "Ins",
            "Dual",
            "Z rokama delam.",
        )
        assert result == "Z {{c1::rokama::roka, ins du}} delam."

    def test_case_insensitive_case_preserving(self):
        result = make_case_cloze_text(
            "Ljubljano",
            "Ljubljana",
            "Acc",
            "Sing",
            "Grem v Ljubljano.",
        )
        assert result == "Grem v {{c1::Ljubljano::Ljubljana, acc sg}}."


class TestSLOVENE_FUNCTION_WORDS:
    def test_has_expected_entries(self):
        expected = {"je", "kje", "v", "kaj", "se", "na", "za", "tam", "da", "ni"}
        for word in expected:
            assert word in SLOVENE_FUNCTION_WORDS, f"{word!r} should be in SLOVENE_FUNCTION_WORDS"

    def test_no_content_words(self):
        """Verify that obvious content words are NOT in the curated set."""
        content = {"kava", "voda", "banka", "mesto", "hotel", "hvala", "prosim"}
        for word in content:
            assert word not in SLOVENE_FUNCTION_WORDS, f"{word!r} should NOT be in SLOVENE_FUNCTION_WORDS"
