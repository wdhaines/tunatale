"""Tests for Phase F: function-word detection and cloze text generation."""

from __future__ import annotations

from app.srs.function_words import (
    SLOVENE_FUNCTION_WORDS,
    _format_morphology_feature,
    is_function_word,
    make_cloze_text,
    make_morphology_cloze_text,
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


class TestFormatMorphologyFeature:
    def test_verb_person_number(self):
        assert _format_morphology_feature("verb:1sg") == "1sg"

    def test_noun_loc_singular(self):
        assert _format_morphology_feature("noun:loc:sg") == "loc sg"

    def test_noun_nom_feminine_plural(self):
        assert _format_morphology_feature("noun:nom:f:pl") == "nom f pl"

    def test_adj_nom_masc_singular(self):
        assert _format_morphology_feature("adj:nom:m:sg") == "nom m sg"

    def test_empty_feature(self):
        assert _format_morphology_feature("") == ""

    def test_pos_only_no_colon(self):
        assert _format_morphology_feature("verb") == ""

    def test_trailing_colon(self):
        # "noun:loc:" splits to ["noun", "loc", ""] -> filter empties -> "loc"
        assert _format_morphology_feature("noun:loc:") == "loc"


class TestMakeMorphologyClozeText:
    def test_basic_verb_conjugation(self):
        result = make_morphology_cloze_text(
            "sem",
            "biti",
            "verb:1sg",
            "Jaz sem doma.",
        )
        assert result == "Jaz {{c1::sem::biti, 1sg}} doma."

    def test_noun_locative(self):
        result = make_morphology_cloze_text(
            "Ljubljani",
            "Ljubljana",
            "noun:loc:sg",
            "Sem v Ljubljani.",
        )
        assert result == "Sem v {{c1::Ljubljani::Ljubljana, loc sg}}."

    def test_adjective_agreement(self):
        result = make_morphology_cloze_text(
            "lepa",
            "lep",
            "adj:nom:f:sg",
            "Hiša je lepa.",
        )
        assert result == "Hiša je {{c1::lepa::lep, nom f sg}}."

    def test_empty_sentence(self):
        assert make_morphology_cloze_text("sem", "biti", "verb:1sg", "") == ""

    def test_idempotent_already_clozed(self):
        result = make_morphology_cloze_text(
            "sem",
            "biti",
            "verb:1sg",
            "Jaz {{c1::sem}} doma.",
        )
        assert result == "Jaz {{c1::sem}} doma."

    def test_empty_surface(self):
        result = make_morphology_cloze_text(
            "",
            "biti",
            "verb:1sg",
            "Jaz sem doma.",
        )
        assert result == "Jaz sem doma."

    def test_multi_occurrence(self):
        result = make_morphology_cloze_text(
            "je",
            "biti",
            "verb:3sg",
            "On je tu, ona je tam.",
        )
        assert result == "On {{c1::je::biti, 3sg}} tu, ona {{c1::je::biti, 3sg}} tam."

    def test_case_insensitive_case_preserving(self):
        result = make_morphology_cloze_text(
            "Ljubljano",
            "Ljubljana",
            "noun:acc:sg",
            "Grem v Ljubljano.",
        )
        assert result == "Grem v {{c1::Ljubljano::Ljubljana, acc sg}}."

    def test_empty_feature_falls_back_to_lemma_only(self):
        result = make_morphology_cloze_text(
            "sem",
            "biti",
            "",
            "Jaz sem doma.",
        )
        assert result == "Jaz {{c1::sem::biti}} doma."


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
