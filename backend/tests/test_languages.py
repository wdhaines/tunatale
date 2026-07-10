"""Tests for language configuration registry (app.languages)."""

from types import SimpleNamespace

import pytest

from app.audio.preprocessing.norwegian import NorwegianPreprocessor
from app.audio.preprocessing.slovene import SlovenePreprocessor
from app.languages import (
    LanguageContext,
    card_surface_variants,
    get_deck_name,
    get_language,
    get_morphology_profile,
    get_preprocessor,
    get_syllabifier,
    get_tts_voice,
    get_variant_separator,
    get_vocab_notetype,
    known_language_codes,
    resolve_language_context,
    uses_compound_word_breakdown,
)
from app.models.language import Language


class TestBreakdownAndMorphologyFlags:
    """Per-language dispatch flags that replaced hardcoded `== "no"` / `{"sl": ...}`."""

    def test_norwegian_uses_compound_word_breakdown(self):
        assert uses_compound_word_breakdown("no") is True

    def test_slovene_uses_generic_breakdown(self):
        assert uses_compound_word_breakdown("sl") is False

    def test_unknown_code_uses_generic_breakdown(self):
        assert uses_compound_word_breakdown("zz") is False

    def test_slovene_has_slavic_morphology_profile(self):
        assert get_morphology_profile("sl") == "slavic"

    def test_norwegian_has_no_morphology_profile(self):
        assert get_morphology_profile("no") is None


class TestCardSurfaceVariants:
    """Comma-separated spelling-variant fronts (Norwegian 'mot, imot') are ONE
    lexical item with multiple accepted surfaces — not a multi-word collocation."""

    def test_norwegian_variant_separator_is_comma(self):
        assert get_variant_separator("no") == ","

    def test_slovene_has_no_variant_separator(self):
        assert get_variant_separator("sl") is None

    def test_unknown_code_has_no_variant_separator(self):
        assert get_variant_separator("zz") is None

    def test_norwegian_comma_front_splits_into_variants(self):
        assert card_surface_variants("no", "mot, imot") == ["mot", "imot"]

    def test_norwegian_variant_split_strips_whitespace(self):
        assert card_surface_variants("no", "fram,frem") == ["fram", "frem"]

    def test_norwegian_three_way_variant(self):
        assert card_surface_variants("no", "a, b, c") == ["a", "b", "c"]

    def test_norwegian_single_word_returns_itself(self):
        assert card_surface_variants("no", "politiet") == ["politiet"]

    def test_norwegian_real_phrase_with_comma_not_split(self):
        # A genuine phrase where a comma-part is multi-word is NOT a variant list.
        assert card_surface_variants("no", "hei, hvordan går det") == ["hei, hvordan går det"]

    def test_slovene_comma_front_not_split(self):
        # Slovene has no variant separator, so commas never split.
        assert card_surface_variants("sl", "mot, imot") == ["mot, imot"]

    def test_unknown_code_returns_text_unchanged(self):
        assert card_surface_variants("zz", "mot, imot") == ["mot, imot"]

    def test_empty_variant_parts_dropped(self):
        # Trailing separator must not yield an empty surface.
        assert card_surface_variants("no", "mot, imot,") == ["mot", "imot"]

    def test_unknown_code_has_no_morphology_profile(self):
        assert get_morphology_profile("zz") is None


class TestKnownLanguageCodes:
    def test_returns_the_configured_codes(self):
        assert known_language_codes() == frozenset({"sl", "en", "no"})

    def test_is_a_frozenset(self):
        assert isinstance(known_language_codes(), frozenset)


class TestResolveLanguageContext:
    """resolve_language_context — the single-source per-language wiring (weakness #4)."""

    @staticmethod
    def _settings(**over):
        base = {
            "database_urls": {},
            "database_url": "sqlite:///./tunatale_sl.db",
            "anki_deck_name": "1. Slovene",
            "target_language": "sl",
        }
        base.update(over)
        return SimpleNamespace(**base)

    def test_configured_language_retargets_db_deck_target(self):
        s = self._settings(database_urls={"no": "sqlite:///./tunatale_no.db"})
        ctx = resolve_language_context("no", s)
        assert isinstance(ctx, LanguageContext)
        assert ctx.db_url == "sqlite:///./tunatale_no.db"
        assert ctx.deck_name == "0. 6000 Most Frequent Norwegian Words [Part 1]"
        assert ctx.target_language == "no"
        # static registry facets attached
        assert ctx.language is not None and ctx.language.code == "no"
        assert ctx.lemmatizer_type == "stanza"
        assert ctx.preprocessor_factory is NorwegianPreprocessor

    def test_none_falls_back_to_defaults(self):
        ctx = resolve_language_context(None, self._settings())
        assert ctx.db_url == "sqlite:///./tunatale_sl.db"
        assert ctx.deck_name == "1. Slovene"
        assert ctx.target_language == "sl"
        assert ctx.language is None
        assert ctx.preprocessor_factory is None
        assert ctx.lemmatizer_type == "lowercase"

    def test_known_code_absent_from_urls_falls_back_but_keeps_registry_facets(self):
        # code IS a known language but NOT configured in database_urls → default
        # db/deck/target, yet the static registry facets still resolve (this pins
        # the config-present-in-the-fallback-branch path).
        s = self._settings(database_urls={"sl": "sqlite:///./tunatale_sl.db"})
        ctx = resolve_language_context("no", s)  # "no" absent from database_urls
        assert ctx.db_url == "sqlite:///./tunatale_sl.db"
        assert ctx.deck_name == "1. Slovene"
        assert ctx.target_language == "sl"
        assert ctx.language is not None and ctx.language.code == "no"
        assert ctx.lemmatizer_type == "stanza"

    def test_unknown_code_has_no_registry_facets(self):
        ctx = resolve_language_context("zz", self._settings())
        assert ctx.db_url == "sqlite:///./tunatale_sl.db"
        assert ctx.language is None
        assert ctx.preprocessor_factory is None
        assert ctx.lemmatizer_type == "lowercase"
        assert ctx.vocab_notetype is None


class TestGetTtsVoice:
    def test_returns_slovene_female_voice_by_default(self):
        # The default role ("female-1") must equal the old hardcoded DEFAULT_VOICE
        # so every media caller relying on the "sl" default keeps its behavior.
        assert get_tts_voice("sl") == "sl-SI-PetraNeural"

    def test_returns_norwegian_female_voice(self):
        assert get_tts_voice("no") == "nb-NO-PernilleNeural"

    def test_returns_requested_role(self):
        assert get_tts_voice("no", role="male-1") == "nb-NO-FinnNeural"

    def test_raises_keyerror_for_unknown_code(self):
        with pytest.raises(KeyError, match="xyz"):
            get_tts_voice("xyz")

    def test_raises_valueerror_for_missing_role(self):
        with pytest.raises(ValueError, match="no-such-role"):
            get_tts_voice("sl", role="no-such-role")


class TestGetLanguage:
    def test_returns_slovene_for_sl(self):
        lang = get_language("sl")
        assert isinstance(lang, Language)
        assert lang.code == "sl"
        assert lang.name == "Slovene"

    def test_returns_english_for_en(self):
        lang = get_language("en")
        assert isinstance(lang, Language)
        assert lang.code == "en"
        assert lang.name == "English"

    def test_returns_norwegian_for_no(self):
        lang = get_language("no")
        assert isinstance(lang, Language)
        assert lang.code == "no"
        assert lang.name == "Norwegian"
        assert lang.native_name == "norsk"

    def test_raises_keyerror_for_unknown_code(self):
        with pytest.raises(KeyError, match="xyz"):
            get_language("xyz")

    def test_raises_keyerror_for_empty_code(self):
        with pytest.raises(KeyError):
            get_language("")

    def test_slovene_has_female_voice(self):
        lang = get_language("sl")
        assert "female" in lang.tts_voice_map
        assert "sl-SI" in lang.tts_voice_map["female"]

    def test_slovene_has_role_keys(self):
        lang = get_language("sl")
        for key in ("narrator", "female-1", "male-1"):
            assert key in lang.tts_voice_map, f"missing '{key}' in Slovene voice map"

    def test_norwegian_has_all_roles(self):
        lang = get_language("no")
        for key in ("narrator", "female-1", "female-2", "male-1", "male-2"):
            assert key in lang.tts_voice_map, f"missing '{key}' in Norwegian voice map"

    def test_norwegian_has_legacy_aliases(self):
        lang = get_language("no")
        assert "female" in lang.tts_voice_map
        assert "male" in lang.tts_voice_map

    def test_norwegian_voices_are_nb_no(self):
        lang = get_language("no")
        assert "nb-NO" in lang.tts_voice_map["female-1"]
        assert "nb-NO" in lang.tts_voice_map["male-1"]

    def test_narrator_is_english(self):
        lang_en = get_language("en")
        lang_no = get_language("no")
        lang_sl = get_language("sl")
        for lang in (lang_en, lang_no, lang_sl):
            assert "en-US" in lang.tts_voice_map["narrator"], f"narrator for {lang.code} should be English"


class TestGetPreprocessor:
    def test_returns_slovene_preprocessor_for_sl(self):
        pp = get_preprocessor("sl")
        assert isinstance(pp, SlovenePreprocessor)

    def test_returns_norwegian_preprocessor_for_no(self):
        pp = get_preprocessor("no")
        assert isinstance(pp, NorwegianPreprocessor)

    def test_raises_keyerror_for_unknown_code(self):
        with pytest.raises(KeyError, match="xyz"):
            get_preprocessor("xyz")

    def test_raises_valueerror_for_english(self):
        with pytest.raises(ValueError, match="en"):
            get_preprocessor("en")

    def test_norwegian_preprocessor_passes_through(self):
        from app.models.lesson import SectionType

        pp = get_preprocessor("no")
        text = "Hei, hvordan går det?"
        result = pp.preprocess(text, SectionType.NATURAL_SPEED)
        assert result == text

    def test_preprocessor_returns_string(self):
        from app.models.lesson import SectionType

        pp = get_preprocessor("sl")
        result = pp.preprocess("dober dan", SectionType.NATURAL_SPEED)
        assert isinstance(result, str)

    def test_slovene_is_slovene_preprocessor_type(self):
        pp = get_preprocessor("sl")
        assert type(pp).__name__ == "SlovenePreprocessor"


class TestGetDeckName:
    def test_returns_slovene_deck(self):
        assert get_deck_name("sl") == "1. Slovene"

    def test_returns_norwegian_deck(self):
        assert get_deck_name("no") == "0. 6000 Most Frequent Norwegian Words [Part 1]"

    def test_raises_keyerror_for_unknown_code(self):
        with pytest.raises(KeyError, match="xyz"):
            get_deck_name("xyz")

    def test_raises_valueerror_for_language_without_deck(self):
        # en is the gloss language — no TT-managed deck of its own.
        with pytest.raises(ValueError, match="en"):
            get_deck_name("en")


class TestGetVocabNotetype:
    def test_returns_slovene_vocab_for_sl(self):
        from app.anki.vocab_notetype import SLOVENE_VOCAB

        assert get_vocab_notetype("sl") is SLOVENE_VOCAB

    def test_returns_norwegian_vocab_for_no(self):
        from app.anki.vocab_notetype import NORWEGIAN_VOCAB

        assert get_vocab_notetype("no") is NORWEGIAN_VOCAB

    def test_returns_none_for_english(self):
        # en is the gloss language — TT never mints into an English notetype.
        assert get_vocab_notetype("en") is None

    def test_returns_none_for_unknown_code(self):
        assert get_vocab_notetype("xyz") is None


class TestGetSyllabifier:
    """Per-language syllabifier dispatch routed through the registry."""

    def test_returns_norwegian_syllabifier_for_no(self):
        from app.generation.syllabify import syllabify_norwegian_word

        assert get_syllabifier("no") is syllabify_norwegian_word

    def test_returns_slovene_syllabifier_for_sl(self):
        from app.generation.syllabify import syllabify_slovene_word

        assert get_syllabifier("sl") is syllabify_slovene_word

    def test_unknown_code_falls_back_to_slovene(self):
        from app.generation.syllabify import syllabify_slovene_word

        assert get_syllabifier("xx") is syllabify_slovene_word

    def test_norwegian_syllabifier_actually_works(self):
        result = get_syllabifier("no")("snakke")
        assert result == ["snak", "ke"]

    def test_slovene_syllabifier_actually_works(self):
        result = get_syllabifier("sl")("prosim")
        assert result == ["pro", "sim"]
