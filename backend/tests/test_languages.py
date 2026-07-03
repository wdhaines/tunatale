"""Tests for language configuration registry (app.languages)."""

import pytest

from app.audio.preprocessing.norwegian import NorwegianPreprocessor
from app.audio.preprocessing.slovene import SlovenePreprocessor
from app.languages import get_deck_name, get_language, get_preprocessor, get_tts_voice, get_vocab_notetype
from app.models.language import Language


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
