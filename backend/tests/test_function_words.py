"""Tests for Phase F: function-word detection and cloze text generation."""

from __future__ import annotations

import json

from app.srs import function_words as fw
from app.srs.function_words import (
    _ending_blank_split,
    _format_morphology_feature,
    _load_function_word_config,
    format_morphology_hint,
    is_function_word,
    make_cloze_text,
    make_morphology_cloze_text,
    ud_feats_to_tt_feature,
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

    # ── POS-first behavior (classla supplies upos) ───────────────────────────
    def test_pos_catches_aux_not_in_include(self):
        """'ste' isn't in the curated include list, but classla tags it AUX → True.

        This is the whole point: the biti paradigm (ste/smo/so) classifies via POS
        without enumerating surfaces.
        """
        assert is_function_word("ste", "sl") is False  # no analyzer → include-only
        assert is_function_word("ste", "sl", upos="AUX") is True
        assert is_function_word("smo", "sl", upos="AUX") is True

    def test_pos_does_not_catch_content_word(self):
        assert is_function_word("kava", "sl", upos="NOUN") is False

    def test_include_overrides_missing_or_wrong_pos(self):
        """Curated include wins regardless of upos: open-class adverbs we want
        (kje/tam → ADV) and classla's mistag of 'ni' (VERB) still count."""
        assert is_function_word("kje", "sl", upos="ADV") is True
        assert is_function_word("tam", "sl", upos="ADV") is True
        assert is_function_word("ni", "sl", upos="VERB") is True

    def test_unknown_pos_tag_is_false(self):
        assert is_function_word("blah", "sl", upos="ADV") is False  # ADV not in sl pos set


class TestFunctionWordConfig:
    """The per-language data file is the source of truth (replaces the old
    hardcoded SLOVENE_FUNCTION_WORDS frozenset)."""

    def test_curated_include_members(self):
        for word in ("je", "kje", "v", "kaj", "se", "na", "za", "tam", "da", "ni"):
            assert is_function_word(word, "sl") is True, f"{word!r} should be a function word"

    def test_content_words_excluded(self):
        for word in ("kava", "voda", "banka", "mesto", "hotel", "hvala", "prosim"):
            assert is_function_word(word, "sl") is False, f"{word!r} should NOT be a function word"

    def test_biti_paradigm_not_enumerated_in_include(self):
        """ste/smo/so and the lemma 'biti' are deliberately absent from include —
        POS (AUX) handles them. Without an analyzer they're not function words."""
        for word in ("ste", "smo", "so", "biti"):
            assert is_function_word(word, "sl") is False

    def test_missing_language_file_yields_empty_config(self):
        pos, include, exclude = _load_function_word_config("zz")
        assert pos == frozenset() and include == frozenset() and exclude == frozenset()
        assert is_function_word("anything", "zz", upos="AUX") is False

    def test_exclude_force_removes(self, tmp_path, monkeypatch):
        """A synthetic config exercises the exclude branch + the loader end-to-end."""
        (tmp_path / "xx.json").write_text(
            json.dumps({"pos": ["AUX"], "include": ["foo"], "exclude": ["bar"]}),
            encoding="utf-8",
        )
        monkeypatch.setattr(fw, "_FUNCTION_WORD_DATA_DIR", tmp_path)
        _load_function_word_config.cache_clear()
        try:
            assert is_function_word("foo", "xx") is True  # include
            assert is_function_word("baz", "xx", upos="AUX") is True  # pos
            assert is_function_word("bar", "xx", upos="AUX") is False  # exclude beats pos
            assert is_function_word("baz", "xx") is False  # no upos, not in include
        finally:
            _load_function_word_config.cache_clear()


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


class TestEndingBlankSplit:
    def test_regular_verb_split(self):
        assert _ending_blank_split("delam", "delati") == ("dela", "m")

    def test_noun_locative_split(self):
        assert _ending_blank_split("mestu", "mesto") == ("mest", "u")

    def test_adjective_nominative_split(self):
        assert _ending_blank_split("lepa", "lep") == ("lep", "a")

    def test_case_preserving_split(self):
        assert _ending_blank_split("Ljubljano", "Ljubljana") == ("Ljubljan", "o")

    def test_suppletive_returns_none(self):
        assert _ending_blank_split("sem", "biti") is None

    def test_short_stem_lcp_one_returns_none(self):
        assert _ending_blank_split("bom", "biti") is None

    def test_matched_is_prefix_of_lemma_returns_none(self):
        assert _ending_blank_split("delam", "delamkor") is None

    def test_empty_matched_returns_none(self):
        assert _ending_blank_split("", "biti") is None


class TestMakeMorphologyClozeText:
    def test_basic_verb_conjugation(self):
        result = make_morphology_cloze_text(
            "sem",
            "biti",
            "verb:1sg",
            "Jaz sem doma.",
        )
        assert result == "Jaz {{c1::sem}} doma."

    def test_noun_locative(self):
        result = make_morphology_cloze_text(
            "Ljubljani",
            "Ljubljana",
            "noun:loc:sg",
            "Sem v Ljubljani.",
        )
        assert result == "Sem v Ljubljan{{c1::i}}."

    def test_adjective_agreement(self):
        result = make_morphology_cloze_text(
            "lepa",
            "lep",
            "adj:nom:f:sg",
            "Hiša je lepa.",
        )
        assert result == "Hiša je lep{{c1::a}}."

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
        assert result == "On {{c1::je}} tu, ona {{c1::je}} tam."

    def test_case_insensitive_case_preserving(self):
        result = make_morphology_cloze_text(
            "Ljubljano",
            "Ljubljana",
            "noun:acc:sg",
            "Grem v Ljubljano.",
        )
        assert result == "Grem v Ljubljan{{c1::o}}."

    def test_empty_feature(self):
        result = make_morphology_cloze_text(
            "sem",
            "biti",
            "",
            "Jaz sem doma.",
        )
        assert result == "Jaz {{c1::sem}} doma."

    def test_ending_blank_verb_conjugation(self):
        result = make_morphology_cloze_text(
            "delam",
            "delati",
            "verb:1sg",
            "Jaz delam doma.",
        )
        assert result == "Jaz dela{{c1::m}} doma."

    def test_ending_blank_noun_locative(self):
        result = make_morphology_cloze_text(
            "mestu",
            "mesto",
            "noun:loc:sg",
            "Sem v mestu.",
        )
        assert result == "Sem v mest{{c1::u}}."

    def test_ending_blank_adjective(self):
        result = make_morphology_cloze_text(
            "lepa",
            "lep",
            "adj:nom:f:sg",
            "Hiša je lepa.",
        )
        assert result == "Hiša je lep{{c1::a}}."

    def test_ending_blank_case_preserving(self):
        result = make_morphology_cloze_text(
            "Ljubljano",
            "Ljubljana",
            "noun:acc:sg",
            "Grem v Ljubljano.",
        )
        assert result == "Grem v Ljubljan{{c1::o}}."

    def test_suppletive_fallback_preserves_whole_word(self):
        result = make_morphology_cloze_text(
            "sem",
            "biti",
            "verb:1sg",
            "Jaz sem doma.",
        )
        assert result == "Jaz {{c1::sem}} doma."


class TestFormatMorphologyHint:
    def test_verb_1sg(self):
        assert format_morphology_hint("biti", "verb:1sg") == "biti, 1st person singular"

    def test_verb_2sg(self):
        assert format_morphology_hint("biti", "verb:2sg") == "biti, 2nd person singular"

    def test_verb_3sg(self):
        assert format_morphology_hint("biti", "verb:3sg") == "biti, 3rd person singular"

    def test_verb_1pl(self):
        assert format_morphology_hint("biti", "verb:1pl") == "biti, 1st person plural"

    def test_noun_loc_sg(self):
        assert format_morphology_hint("ljubljana", "noun:loc:sg") == "ljubljana, locative singular"

    def test_noun_acc_sg(self):
        assert format_morphology_hint("vodo", "noun:acc:sg") == "vodo, accusative singular"

    def test_noun_nom_pl(self):
        assert format_morphology_hint("vode", "noun:nom:pl") == "vode, nominative plural"

    def test_adj_nom_f_sg(self):
        assert format_morphology_hint("lepa", "adj:nom:f:sg") == "lepa, nominative feminine singular"

    def test_adj_nom_m_pl(self):
        assert format_morphology_hint("lepi", "adj:nom:m:pl") == "lepi, nominative masculine plural"

    def test_empty_feature_returns_lemma(self):
        assert format_morphology_hint("biti", "") == "biti"

    def test_empty_lemma_and_feature(self):
        assert format_morphology_hint("", "") == ""

    def test_unknown_feature_falls_back_to_short_label(self):
        assert format_morphology_hint("biti", "unknown:weird") == "biti, weird"

    def test_unknown_feature_empty_label_returns_lemma(self):
        assert format_morphology_hint("biti", "unknown") == "biti"


class TestIsA1MorphologyFeature:
    def test_a1_verb_prefix_true(self):
        from app.srs.function_words import is_a1_morphology_feature

        assert is_a1_morphology_feature("verb:1sg") is True

    def test_a1_noun_nom_true(self):
        from app.srs.function_words import is_a1_morphology_feature

        assert is_a1_morphology_feature("noun:nom:sg") is True

    def test_a1_noun_acc_true(self):
        from app.srs.function_words import is_a1_morphology_feature

        assert is_a1_morphology_feature("noun:acc:sg") is True

    def test_a1_noun_loc_true(self):
        from app.srs.function_words import is_a1_morphology_feature

        assert is_a1_morphology_feature("noun:loc:sg") is True

    def test_a1_adj_nom_true(self):
        from app.srs.function_words import is_a1_morphology_feature

        assert is_a1_morphology_feature("adj:nom:m:sg") is True

    def test_non_a1_noun_gen_false(self):
        from app.srs.function_words import is_a1_morphology_feature

        assert is_a1_morphology_feature("noun:gen:sg") is False

    def test_non_a1_adj_acc_false(self):
        from app.srs.function_words import is_a1_morphology_feature

        assert is_a1_morphology_feature("adj:acc:m:sg") is False

    def test_empty_string_false(self):
        from app.srs.function_words import is_a1_morphology_feature

        assert is_a1_morphology_feature("") is False

    def test_garbage_string_false(self):
        from app.srs.function_words import is_a1_morphology_feature

        assert is_a1_morphology_feature("xyz:foo") is False


class TestUdFeatsToTtFeature:
    def test_verb_1sg(self):
        assert ud_feats_to_tt_feature("VERB", number="Sing", person="1") == "verb:1sg"

    def test_verb_3pl(self):
        assert ud_feats_to_tt_feature("VERB", number="Plur", person="3") == "verb:3pl"

    def test_aux_1sg(self):
        assert ud_feats_to_tt_feature("AUX", number="Sing", person="1") == "verb:1sg"

    def test_aux_3sg(self):
        assert ud_feats_to_tt_feature("AUX", number="Sing", person="3") == "verb:3sg"

    def test_aux_missing_person_returns_none(self):
        assert ud_feats_to_tt_feature("AUX", number="Sing", person="") is None

    def test_aux_missing_number_returns_none(self):
        assert ud_feats_to_tt_feature("AUX", person="1") is None

    def test_verb_missing_person_returns_none(self):
        assert ud_feats_to_tt_feature("VERB", number="Sing", person="") is None

    def test_verb_missing_number_returns_none(self):
        assert ud_feats_to_tt_feature("VERB", person="1") is None

    def test_noun_nom_sg(self):
        assert ud_feats_to_tt_feature("NOUN", case="Nom", number="Sing") == "noun:nom:sg"

    def test_noun_acc_sg(self):
        assert ud_feats_to_tt_feature("NOUN", case="Acc", number="Sing") == "noun:acc:sg"

    def test_noun_loc_sg(self):
        assert ud_feats_to_tt_feature("NOUN", case="Loc", number="Sing") == "noun:loc:sg"

    def test_noun_gen_returns_none(self):
        assert ud_feats_to_tt_feature("NOUN", case="Gen", number="Sing") is None

    def test_noun_dat_returns_none(self):
        assert ud_feats_to_tt_feature("NOUN", case="Dat", number="Sing") is None

    def test_noun_ins_returns_none(self):
        assert ud_feats_to_tt_feature("NOUN", case="Ins", number="Sing") is None

    def test_noun_nom_pl(self):
        assert ud_feats_to_tt_feature("NOUN", case="Nom", number="Plur") == "noun:nom:pl"

    def test_noun_nom_dual(self):
        assert ud_feats_to_tt_feature("NOUN", case="Nom", number="Dual") == "noun:nom:du"

    def test_adj_nom_masc_sg(self):
        assert ud_feats_to_tt_feature("ADJ", case="Nom", number="Sing", gender="Masc") == "adj:nom:m:sg"

    def test_adj_nom_fem_pl(self):
        assert ud_feats_to_tt_feature("ADJ", case="Nom", number="Plur", gender="Fem") == "adj:nom:f:pl"

    def test_adj_non_nom_returns_none(self):
        assert ud_feats_to_tt_feature("ADJ", case="Gen", number="Sing", gender="Masc") is None

    def test_adj_nom_missing_gender_returns_none(self):
        assert ud_feats_to_tt_feature("ADJ", case="Nom", number="Sing") is None

    def test_unknown_upos_returns_none(self):
        assert ud_feats_to_tt_feature("PROPN", case="Nom", number="Sing") is None

    def test_empty_upos_returns_none(self):
        assert ud_feats_to_tt_feature("") is None
