"""Tests for language configuration registry (app.languages)."""

from types import SimpleNamespace

import pytest

from app.languages import (
    LanguageConfig,
    LanguageContext,
    card_surface_variants,
    format_vocab_headword,
    get_deck_name,
    get_infinitive_marker,
    get_language,
    get_mint_deck_name,
    get_morphology_profile,
    get_planner_example,
    get_preprocessor,
    get_syllabifier,
    get_tts_locale,
    get_tts_voice,
    get_variant_separator,
    get_vocab_notetype,
    known_language_codes,
    language_name_for_tts_locale,
    resolve_language_context,
)
from app.models.language import NARRATOR_VOICE, Language
from app.plugins.languages.ceb.preprocessor import CebuanoPreprocessor
from app.plugins.languages.no.preprocessor import NorwegianPreprocessor
from app.plugins.languages.sl.preprocessor import SlovenePreprocessor
from app.plugins.languages.tl.preprocessor import TagalogPreprocessor


class TestBreakdownAndMorphologyFlags:
    """Per-language dispatch flags that replaced hardcoded `== "no"` / `{"sl": ...}`."""

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


class TestInfinitiveMarker:
    """The å infinitive marker on Norwegian verb vocab-card headwords.

    Matches the reference convention of the user's Anki deck (every verb note's
    ``Article="å"``, rendered "å dekke"); TT-minted cards carry the marker in the
    L2 field itself since TT's own notetype has no separate article field.
    """

    def test_norwegian_infinitive_marker_is_aa(self):
        assert get_infinitive_marker("no") == "å"

    def test_slovene_has_no_infinitive_marker(self):
        assert get_infinitive_marker("sl") is None

    def test_unknown_code_has_no_infinitive_marker(self):
        assert get_infinitive_marker("zz") is None

    def test_norwegian_verb_gets_infinitive_marker(self):
        assert format_vocab_headword("lyve", "VERB", "no") == "å lyve"

    def test_norwegian_non_verb_unchanged(self):
        assert format_vocab_headword("hus", "NOUN", "no") == "hus"

    def test_norwegian_unknown_upos_unchanged(self):
        assert format_vocab_headword("hus", None, "no") == "hus"

    def test_slovene_verb_unchanged(self):
        assert format_vocab_headword("biti", "VERB", "sl") == "biti"

    def test_unknown_code_verb_unchanged(self):
        assert format_vocab_headword("foo", "VERB", "zz") == "foo"


class TestVerbHeadwordFn:
    """The registry's per-language verb-headword function (tunatale-w4m7.6).

    ``format_vocab_headword`` passes a VERB lemma through it BEFORE the
    infinitive-marker logic — it is how a root-keyed lemma table derives the
    surface a card front shows (Tagalog roots → actor-focus infinitives).
    """

    def test_a_registered_fn_shapes_the_verb_front(self):
        assert format_vocab_headword("kain", "VERB", "tl") == "kumain"

    def test_the_fn_is_consulted_before_the_infinitive_marker(self, monkeypatch):
        from app.languages import _CONFIGS

        # A language that HAS a marker but gains a fn: the fn output, not the
        # marker-prefixed lemma, is the front — pins the precedence order.
        monkeypatch.setattr(_CONFIGS["no"], "verb_headword_fn", lambda lemma: f"[{lemma}]")
        assert format_vocab_headword("lyve", "VERB", "no") == "[lyve]"

    def test_a_non_verb_headword_still_ignores_the_fn(self):
        assert format_vocab_headword("bahay", "NOUN", "tl") == "bahay"


class TestKnownLanguageCodes:
    def test_returns_the_configured_codes(self):
        assert known_language_codes() == frozenset({"sl", "en", "no", "tl", "ceb"})

    def test_the_sorted_roster_is_pinned(self):
        # Stated as a SORTED LIST rather than a set so the registry roster is
        # readable in one line. "ceb" is TT's first 3-letter code (ISO 639-3;
        # 639-1 has no Cebuano), and it sorts FIRST — which is the whole reason
        # the planner-example guard below exists.
        assert sorted(known_language_codes()) == ["ceb", "en", "no", "sl", "tl"]

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

    def test_returns_cebuano_for_ceb(self):
        lang = get_language("ceb")
        assert isinstance(lang, Language)
        assert lang.code == "ceb"
        assert lang.name == "Cebuano"
        assert lang.native_name == "Binisaya"
        assert lang.script == "latin"

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

    def test_norwegian_female_roles_are_distinct_voices(self):
        """female-1 and female-2 must not collapse to one voice.

        They did until the Azure port: both mapped to Pernille, so in 5 of the 6
        stored Norwegian lessons two female characters conversed in a single
        voice — across 462 phrases on female-2 against 90 on female-1. Azure
        serves nb-NO-IselinNeural, and the retired adapter did not, so this
        only became fixable once the provider moved.

        Slovene had no native equivalent — Petra + Rok is that catalogue's
        entirety — and that is why its role-2 slots are Multilingual voices as
        of 2026-09-12 (tunatale-rag.4); see
        test_every_dialogue_role_gets_its_own_voice, which covers both languages.
        """
        voices = get_language("no").tts_voice_map
        assert voices["female-1"] != voices["female-2"]

    @pytest.mark.parametrize("code", sorted(known_language_codes() - {"en"}))
    def test_every_dialogue_role_gets_its_own_voice(self, code):
        """The four roles the prompts use must be four different voices.

        Stated as an invariant over the registry rather than as one assertion
        per language, so a new plugin inherits the guard instead of needing its
        own copy. English is excluded on purpose: it is the narrator/L1
        language, never a dialogue target, and its map is a placeholder.

        What made this fixable is measurement, not catalogue size: the native
        catalogues are three voices for nb-NO and TWO for sl-SI, so Slovene
        could not fill four roles natively at all. Azure's Multilingual Neural
        voices auto-detect the language, and on 2026-09-12 they measured
        indistinguishable from native by machine — see the oracle numbers on
        tunatale-rag.4.
        """
        voices = get_language(code).tts_voice_map
        picked = [voices[role] for role in ("female-1", "female-2", "male-1", "male-2")]
        assert len(set(picked)) == 4, f"{code} collapses dialogue roles: {picked}"

    @pytest.mark.parametrize("code", sorted(known_language_codes() - {"en"}))
    def test_a_non_native_dialogue_voice_is_multilingual(self, code):
        """A dialogue voice from another locale MUST be a Multilingual voice.

        ``AzureTTSService._build_ssml`` derives ``xml:lang`` by slicing the
        locale off the voice id, so an ordinary ``en-US``/``de-DE`` voice in a
        Slovene slot would read the Slovene line *as English/German* — the one
        failure mode this change could introduce. The Multilingual Neurals are
        immune because they auto-detect the language and ignore the wrapper
        (measured: identical PCM with and without ``<lang>``), which is why they
        are the only sanctioned exception.

        The target locale is read off ``male-1``/``female-1`` rather than mapped
        from the code, keeping the language literal out of the assertion.
        """
        voices = get_language(code).tts_voice_map
        native_locales = {"-".join(voices[r].split("-")[:2]) for r in ("female-1", "male-1")}
        assert len(native_locales) == 1, f"{code}: role-1 voices disagree on locale: {native_locales}"
        target = native_locales.pop()
        for role in ("female-1", "female-2", "male-1", "male-2"):
            voice = voices[role]
            if "-".join(voice.split("-")[:2]) != target:
                assert "Multilingual" in voice, (
                    f"{code} {role}={voice} is neither {target} nor a Multilingual voice — "
                    "it would speak the lesson in its own language"
                )

    def test_norwegian_has_legacy_aliases(self):
        lang = get_language("no")
        assert "female" in lang.tts_voice_map
        assert "male" in lang.tts_voice_map

    def test_norwegian_voices_are_nb_no(self):
        lang = get_language("no")
        assert "nb-NO" in lang.tts_voice_map["female-1"]
        assert "nb-NO" in lang.tts_voice_map["male-1"]

    def test_norwegian_widened_cast_has_eight_distinct_dialogue_voices(self):
        """The numbered dialogue cast must be eight DISTINCT voices.

        Regression guard for the William/Finn class: until rag.6, male-1 and
        male-2 measured Finn 97.0 Hz vs William 101.9 Hz — 4.9 Hz and 0.005
        harmonicity apart — so two male characters conversed in a near-
        duplicate. ``narrator`` and the bare ``female``/``male`` aliases are
        deliberately excluded here: they are not cast members.
        """
        voices = get_language("no").tts_voice_map
        cast = {
            role: voices[role]
            for role in (
                "female-1",
                "female-2",
                "female-3",
                "female-4",
                "male-1",
                "male-2",
                "male-3",
                "male-4",
            )
        }
        assert len(set(cast.values())) == 8, f"collapsed cast: {cast}"

    def test_every_norwegian_map_voice_has_a_measured_gain(self):
        """Every voice named in the no voice map also has a tts_voice_gain_db entry.

        An unmapped voice renders at 0.0 dB — silently un-normalised against
        the rest of the cast. The narrator (en-US-GuyNeural, measured on
        ENGLISH text) is covered too.
        """
        lang = get_language("no")
        missing = set(lang.tts_voice_map.values()) - set(lang.tts_voice_gain_db)
        assert not missing, f"voices without a measured gain: {missing}"

    def test_william_left_the_map_but_stays_in_the_gain_table(self):
        """en-AU-William was male-2 until rag.6; legacy audio must stay normalised.

        A stored lesson pins a RESOLVED voice_id per phrase, so the Norwegian
        lessons on disk still name William on male-2. ``get_tts_voice_gain_db``
        returns 0.0 for an unknown voice, which would silently un-normalise
        legacy audio on the next re-render — the table is keyed by VOICE, not
        role, precisely so it can outlive a role reassignment.
        """
        voices = get_language("no").tts_voice_map
        assert "en-AU-WilliamMultilingualNeural" not in set(voices.values())
        assert "en-AU-WilliamMultilingualNeural" in get_language("no").tts_voice_gain_db

    @pytest.mark.parametrize("code,locale", [("sl", "sl-SI"), ("no", "nb-NO"), ("en", "en-US"), ("ceb", "ceb-PH")])
    def test_tts_locale_is_declared_per_language(self, code, locale):
        assert get_tts_locale(code) == locale

    def test_tts_locale_raises_for_an_unknown_code(self):
        with pytest.raises(KeyError):
            get_tts_locale("xyz")

    @pytest.mark.parametrize("code", sorted(known_language_codes()))
    def test_the_declared_locale_matches_the_native_voices(self, code):
        """tts_locale and the role-1 voices must agree, or the wrapper is a lie.

        The adapter compares this locale against the voice's own to decide
        whether to emit ``<lang>``. Declare "sl-SL" by mistake and every native
        Slovene line would suddenly be wrapped — a new cache key for the whole
        corpus and a locale Azure does not know. Tying the declaration to the
        voices that are native by construction is what stops that being a
        silent, one-character mistake.
        """
        lang = get_language(code)
        role_1_locales = {"-".join(lang.tts_voice_map[r].split("-")[:2]) for r in ("female-1", "male-1")}

        assert role_1_locales == {lang.tts_locale}

    @pytest.mark.parametrize("code", sorted(known_language_codes()))
    def test_no_voice_map_names_a_paid_hd_voice(self, code):
        """Azure Neural HD is ruled OUT, and on cost rather than on quality.

        It is a SEPARATE billing line from Standard Neural — $22 per 1M
        characters as of March 2026 — and it is excluded from the F0 free
        allowance, so every HD character bills from the first one. Standard and
        Multilingual Neural voices are ordinary neural voices and draw on that
        allowance. The user ruled HD out explicitly on 2026-09-12 after seeing a
        session's spend.

        It is not a quality judgement: Andrew-HD had the best WER of any
        candidate (0.000) and was the user's ear-test favourite. It also cannot
        be regression-tested — Azure documents it as varying its prosody on
        every render, and it measured NONDETERMINISTIC, so no hash-based
        pronunciation oracle exists for it (tunatale-rag.4).

        An HD voice id is spelled with a colon (``en-US-Andrew:DragonHDLatestNeural``),
        which is the only thing in the catalogue that is, so this catches the
        whole family rather than one name.
        """
        for role, voice in get_language(code).tts_voice_map.items():
            assert ":" not in voice and "DragonHD" not in voice, (
                f"{code} {role}={voice} is a paid Neural HD voice: separate billing line, "
                "$22/1M, no free-tier allowance, and nondeterministic so untestable"
            )

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

    def test_returns_tagalog_preprocessor_for_tl(self):
        pp = get_preprocessor("tl")
        assert isinstance(pp, TagalogPreprocessor)

    def test_returns_cebuano_preprocessor_for_ceb(self):
        pp = get_preprocessor("ceb")
        assert isinstance(pp, CebuanoPreprocessor)

    def test_tagalog_preprocessor_passes_through(self):
        from app.models.lesson import SectionType

        pp = get_preprocessor("tl")
        text = "Magkano po ito?"
        result = pp.preprocess(text, SectionType.NATURAL_SPEED)
        assert result == text

    def test_cebuano_preprocessor_passes_through(self):
        from app.models.lesson import SectionType

        pp = get_preprocessor("ceb")
        text = "Pila ka imong kaon?"
        result = pp.preprocess(text, SectionType.NATURAL_SPEED)
        assert result == text

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
        from app.cards.vocab_notetype import SLOVENE_VOCAB

        assert get_vocab_notetype("sl") is SLOVENE_VOCAB

    def test_returns_norwegian_vocab_for_no(self):
        from app.cards.vocab_notetype import NORWEGIAN_VOCAB

        assert get_vocab_notetype("no") is NORWEGIAN_VOCAB

    def test_returns_none_for_english(self):
        # en is the gloss language — TT never mints into an English notetype.
        assert get_vocab_notetype("en") is None

    def test_returns_none_for_unknown_code(self):
        assert get_vocab_notetype("xyz") is None

    def test_returns_cebuano_vocab_for_ceb(self):
        from app.cards.vocab_notetype import CEBUANO_VOCAB

        assert get_vocab_notetype("ceb") is CEBUANO_VOCAB
        assert get_vocab_notetype("ceb").name == "Cebuano Vocabulary"


class TestGetSyllabifier:
    """Per-language syllabifier dispatch routed through the registry."""

    def test_returns_norwegian_syllabifier_for_no(self):
        from app.plugins.languages.no.syllabify import syllabify_norwegian_word

        assert get_syllabifier("no") is syllabify_norwegian_word

    def test_returns_slovene_syllabifier_for_sl(self):
        from app.plugins.languages.sl.syllabify import syllabify_slovene_word

        assert get_syllabifier("sl") is syllabify_slovene_word

    def test_unknown_code_falls_back_to_default(self):
        from app.generation.syllabify import default_syllabifier

        assert get_syllabifier("xx") is default_syllabifier

    def test_norwegian_syllabifier_actually_works(self):
        result = get_syllabifier("no")("snakke")
        assert result == ["snak", "ke"]

    def test_slovene_syllabifier_actually_works(self):
        result = get_syllabifier("sl")("prosim")
        assert result == ["pro", "sim"]


class TestResolveDbPath:
    """The sanctioned way to turn a language code into a database path.

    Every caller that needs "the TT db for this language" must come through
    here. Hand-rolling it as `settings.database_url.removeprefix("sqlite:///")`
    silently yields the SINGULAR setting — one fixed language regardless of the
    code — which is how `grave_ignored_lemma_cards --language no` spent a month
    querying the Slovene db and reporting "Nothing to grave"
    (`scripts/check_singular_database_url.py` now fails the gate on that shape).
    """

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

    def test_configured_language_gets_its_own_path(self):
        from pathlib import Path

        from app.languages import resolve_db_path

        s = self._settings(
            database_urls={"sl": "sqlite:///./tunatale_sl.db", "no": "sqlite:///./tunatale_no.db"},
        )

        assert resolve_db_path("no", s) == Path("./tunatale_no.db")
        assert resolve_db_path("sl", s) == Path("./tunatale_sl.db")

    def test_unconfigured_code_falls_back_to_the_singular_default(self):
        from pathlib import Path

        from app.languages import resolve_db_path

        s = self._settings(database_urls={})

        assert resolve_db_path("no", s) == Path("./tunatale_sl.db")
        assert resolve_db_path(None, s) == Path("./tunatale_sl.db")

    def test_strips_only_the_sqlite_scheme(self):
        """An absolute path keeps its leading slash: sqlite:////abs → /abs."""
        from pathlib import Path

        from app.languages import resolve_db_path

        s = self._settings(database_urls={"no": "sqlite:////var/lib/tt_no.db"})

        assert resolve_db_path("no", s) == Path("/var/lib/tt_no.db")


class TestKeyPhrasesVoice:
    """tunatale-w4m7.16: a language may voice its key-phrase breakdown with a
    voice outside the dialogue cast. Tagalog's fil-PH voices ignore IPA, so its
    breakdown is spoken by a Multilingual voice that honours it."""

    def test_tagalog_names_a_multilingual_key_phrases_voice(self):
        voice = get_language("tl").tts_voice_map["key-phrases"]
        # A voice from another locale must be Multilingual, or it would read
        # the plain-text Tagalog in the section as its own language.
        assert "Multilingual" in voice

    def test_every_tagalog_map_voice_has_a_measured_gain(self):
        lang = get_language("tl")
        missing = set(lang.tts_voice_map.values()) - set(lang.tts_voice_gain_db)
        assert not missing, f"voices without a measured gain: {missing}"

    def test_the_key_phrases_role_is_not_offered_to_the_story_writer(self):
        from app.generation.prompts import _l2_roles_line

        line = _l2_roles_line(get_language("tl"))
        assert line.startswith("- Use ONLY these 4 L2 voices")
        assert "key-phrases" not in line


class TestCebuanoRegistration:
    """tunatale-u8nz.3 — the ``ceb`` skeleton, pinned value by value.

    Registration is inert at runtime (``app/main.py`` builds everything from
    ``DATABASE_URLS`` and no Cebuano db is configured anywhere), so nothing
    downstream exercises these fields. That is exactly why they are asserted
    here rather than left to be discovered: a skeleton nobody checks is a
    skeleton nobody finishes.
    """

    def test_voice_map_is_the_declared_gemini_cast(self):
        assert get_language("ceb").tts_voice_map == {
            "narrator": NARRATOR_VOICE,
            "female-1": "ceb-PH-KoreGemini",
            "female-2": "ceb-PH-DespinaGemini",
            "male-1": "ceb-PH-CharonGemini",
            "male-2": "ceb-PH-OrusGemini",
            "female": "ceb-PH-KoreGemini",
            "male": "ceb-PH-CharonGemini",
        }

    def test_every_cast_voice_has_its_measured_gain(self):
        # Integrated loudness of the 8-sentence Cebuano sample set per voice
        # (ffmpeg ebur128, target -20.0 LUFS), 2026-09-25, tunatale-u8nz.2.
        # The narrator gain is the one value copied across every plugin table.
        assert get_language("ceb").tts_voice_gain_db == {
            "ceb-PH-KoreGemini": -6.0,
            "ceb-PH-DespinaGemini": -5.6,
            "ceb-PH-CharonGemini": -3.1,
            "ceb-PH-OrusGemini": -5.0,
            "en-US-GuyNeural": -0.9,
        }

    def test_deck_and_mint_deck_names(self):
        assert get_deck_name("ceb") == "3. Bisaya"
        assert get_mint_deck_name("ceb", default="ignored") == "3. Bisaya::TunaTale"

    def test_the_facets_later_beads_own_are_left_unset(self):
        """Absent, not defaulted to something plausible.

        Each of these is deliberately omitted in the plugin with a comment
        naming the bead that owns it; setting one here would quietly take the
        capability the owning bead is meant to decide.
        """
        from app.languages import _CONFIGS

        config = _CONFIGS["ceb"]
        assert config.planner_example is None
        assert config.wordfreq_lang is None
        assert config.lemma_table_path is None
        assert config.lemmatizer_type == "lowercase"  # the shared default, not a choice
        assert config.l2_scorer is None
        # Exactly TT's own vocab notetype, read by field name (u8nz.7: the first
        # Cebuano mint failed its sync without it). An imported deck's profile
        # is still a later bead's; tests/test_anki_cebuano_vocab_read.py pins this.
        assert set(config.notetype_profiles) == {"Cebuano Vocabulary"}
        # NOT asserted unset: phoneme_planner_factory now ships (tunatale-u8nz.1),
        # and tests/test_ceb_phoneme_plan.py pins what it plans; syllabifier_fn
        # ships with its owning bead (tunatale-u8nz.6), pinned by
        # tests/test_ceb_syllabify.py.

    def test_the_style_guide_guards_against_tagalog(self):
        """Tagalog is the drift risk: close kin, and dominant in training data.

        The guide must name the leaks AND their Cebuano counterparts, and state
        the verb-affix rule with the bare-root error the 2026-09-22 Groq probe
        actually produced (tunatale-u8nz.4)."""
        from app.languages import get_style_notes

        notes = get_style_notes("ceb")
        for tagalog, cebuano in (('"hindi"', '"dili"'), ('"ano"', '"unsa"'), ('"magkano"', '"pila"')):
            assert f"{tagalog} → {cebuano}" in notes
        assert 'No "po" or "opo"' in notes
        assert '"Palit ko og isda" is an error' in notes
        assert "Mopalit ko og isda" in notes

    def test_the_story_prompt_carries_the_style_guide(self):
        from app.generation.prompts import build_story_system_prompt

        assert "Cebuano (Bisaya) Authenticity Rules" in build_story_system_prompt(get_language("ceb"))

    def test_function_words_are_the_cebuano_markers_and_particles(self):
        from app.srs.function_words import is_function_word

        for word in ("ang", "sa", "og", "ug", "si", "ni", "kang", "mga", "nga", "ba", "pud", "sad", "man", "gyud"):
            assert is_function_word(word, "ceb", upos=None) is True, word
        # The conjunction and preposition the user ruled closed-class on 2026-09-26:
        # with no Cebuano analyzer the POS list is inert, so they need naming.
        for word in ("pero", "para"):
            assert is_function_word(word, "ceb", upos=None) is True, word
        # Tagalog's markers are not Cebuano function words, and content words never are.
        for word in ("ng", "po", "isda", "balay"):
            assert is_function_word(word, "ceb", upos=None) is False, word

    @pytest.mark.parametrize(("word", "value"), [("duha", 2), ("napulo", 10), ("kawhaan", 20), ("gatos", 100)])
    def test_native_numbers_resolve(self, word, value):
        from app.cards.number_image import number_value

        assert number_value(word, "ceb") == value

    @pytest.mark.parametrize("word", ["usa", "baynte", "singko", "alas"])
    def test_usa_and_the_spanish_numbers_do_not_resolve(self, word):
        """usa is 'one' AND the article ('usa ka bata' = a child), like tl isa.
        Spanish-derived numbers belong to clocks and prices, not a counting heap."""
        from app.cards.number_image import number_value

        assert number_value(word, "ceb") is None


class TestPlannerExampleIsNeverTheLowestSortingLanguage:
    """Why these four rows are pinned together, in one test.

    ``get_planner_example`` shows the planner a worked day written in a
    language OTHER than the target, and it picks the lowest-sorting language
    that supplies an example. ``ceb`` sorts before every other registered code
    — so the day a future bead gives it a ``planner_example`` would replace the
    example shown to EVERY other language at once, and Tagalog, a close
    relative, would be shown Cebuano. That is precisely the contamination the
    selector exists to prevent, and it would be invisible: every other test
    here would still pass, because each only checks that the example is not the
    target.

    So the guard is the concrete mapping, asserted per target. If a future bead
    adds a Cebuano planner example, this test is what says it changed everyone
    else's prompt.
    """

    @pytest.mark.parametrize(
        "target,expected_example_language",
        [
            ("ceb", "no"),
            ("tl", "no"),
            ("sl", "no"),
            ("no", "sl"),
        ],
    )
    def test_the_selected_example_language(self, target, expected_example_language):
        assert get_planner_example(target).language_code == expected_example_language


class TestLanguageNameForTtsLocale:
    """The registry-side lookup a Gemini ``input.prompt`` needs.

    The instruction the Gemini adapter sends names the language in words
    ("Say only this one Cebuano syllable or word"), and the only thing it has to
    go on is the locale sliced off the voice id — a string the registry never
    hands to an adapter directly. An unresolved locale must read as "say no
    name", not as a guess, so a prompt that is vaguer is better than one that
    misnames the language.
    """

    def test_a_registered_locale_names_its_language(self):
        assert language_name_for_tts_locale("ceb-PH") == "Cebuano"
        assert language_name_for_tts_locale("nb-NO") == "Norwegian"

    def test_every_declared_locale_round_trips(self):
        """Whatever the plugins declare is what a voice id of that locale reads back.

        Otherwise a language could render every fragment through a prompt that
        either names it wrongly or not at all, and no other test would notice:
        the adapter's own tests use one locale.
        """
        for code in sorted(known_language_codes()):
            locale = get_language(code).tts_locale
            if locale is not None:
                assert language_name_for_tts_locale(locale) == get_language(code).name, code

    def test_an_unregistered_locale_has_no_name(self):
        assert language_name_for_tts_locale("xx-XX") is None

    def test_an_ambiguous_locale_has_no_name(self):
        """Two languages on one locale, and the name is not a coin flip.

        fil-PH is the real case: Tagalog declares it today, and a second
        language sharing it would otherwise make the prompt name one of the two.
        """
        from app.languages import _select_name_for_tts_locale

        def _config(code: str, name: str, locale: str) -> LanguageConfig:
            return LanguageConfig(
                language=Language(code=code, name=name, native_name=name, script="latin", tts_locale=locale)
            )

        shared = {"a": _config("aa", "Aaa", "zz-ZZ"), "b": _config("bb", "Bbb", "zz-ZZ")}
        assert _select_name_for_tts_locale("zz-ZZ", shared) is None
        # ...and one language alone on the locale is not ambiguous.
        assert _select_name_for_tts_locale("zz-ZZ", {"a": _config("aa", "Aaa", "zz-ZZ")}) == "Aaa"
        assert _select_name_for_tts_locale("yy-YY", shared) is None
