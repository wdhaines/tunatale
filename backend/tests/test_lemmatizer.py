"""Tests for the Lemmatizer protocol, implementations, and factory."""

from __future__ import annotations

import pytest

from app.srs.lemmatizer import (
    ClasslaLemmatizer,
    LowercaseLemmatizer,
    TokenAnalysis,
    _parse_morphology,
    _parse_person,
    get_lemmatizer,
)
from tests._helpers.lemmatizer import StubLemmatizer, assert_satisfies_lemmatizer_protocol


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
        assert_satisfies_lemmatizer_protocol(self.lemmatizer)

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
        sl_result = self.lemmatizer.analyze("Mize", "sl")
        en_result = self.lemmatizer.analyze("Mize", "en")
        assert sl_result == en_result

    # ── analyze_sentence() tests ────────────────────────────────────────

    def test_analyze_sentence_splits_on_whitespace(self):
        results = self.lemmatizer.analyze_sentence("To je miza", "sl")
        assert len(results) == 3
        assert results[0] == TokenAnalysis(surface="To", lemma="to")
        assert results[1] == TokenAnalysis(surface="je", lemma="je")
        assert results[2] == TokenAnalysis(surface="miza", lemma="miza")

    def test_analyze_sentence_empty_string(self):
        results = self.lemmatizer.analyze_sentence("", "sl")
        assert results == []

    def test_analyze_sentence_single_word(self):
        results = self.lemmatizer.analyze_sentence("Miza", "sl")
        assert len(results) == 1
        assert results[0].surface == "Miza"
        assert results[0].lemma == "miza"


class TestTokenAnalysis:
    def test_frozen_dataclass(self):
        ta = TokenAnalysis(surface="hotelu", lemma="hotel", upos="NOUN", case="Loc", number="Sing")
        assert ta.surface == "hotelu"
        assert ta.lemma == "hotel"
        assert ta.upos == "NOUN"
        assert ta.case == "Loc"
        assert ta.number == "Sing"
        assert ta.person == ""
        assert ta.gender == ""

    def test_defaults(self):
        ta = TokenAnalysis(surface="word", lemma="word")
        assert ta.upos == ""
        assert ta.case == ""
        assert ta.number == ""
        assert ta.person == ""
        assert ta.gender == ""


class TestParseMorphology:
    def test_full_features(self):
        case, number, gender = _parse_morphology("Case=Gen|Gender=Fem|Number=Sing")
        assert case == "Gen"
        assert number == "Sing"
        assert gender == "Fem"

    def test_no_case(self):
        case, number, gender = _parse_morphology("Gender=Masc|Number=Plur")
        assert case == ""
        assert number == "Plur"
        assert gender == "Masc"

    def test_no_number(self):
        case, number, gender = _parse_morphology("Case=Nom|Gender=Masc")
        assert case == "Nom"
        assert number == ""
        assert gender == "Masc"

    def test_empty_string(self):
        case, number, gender = _parse_morphology("")
        assert case == ""
        assert number == ""
        assert gender == ""

    def test_dual_number(self):
        case, number, gender = _parse_morphology("Case=Ins|Gender=Masc|Number=Dual")
        assert case == "Ins"
        assert number == "Dual"
        assert gender == "Masc"

    def test_no_gender(self):
        case, number, gender = _parse_morphology("Case=Nom|Number=Sing")
        assert case == "Nom"
        assert number == "Sing"
        assert gender == ""


class TestParsePerson:
    def test_person_present(self):
        assert _parse_person("Person=1|Number=Sing") == "1"

    def test_person_absent(self):
        assert _parse_person("Case=Nom|Gender=Masc") == ""

    def test_empty_string(self):
        assert _parse_person("") == ""

    def test_with_case_and_number(self):
        assert _parse_person("Case=Nom|Number=Sing|Person=3") == "3"


class TestGetLemmatizer:
    def test_default_is_lowercase(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "lemmatizer_type", "lowercase")
        get_lemmatizer.cache_clear()
        lemmatizer = get_lemmatizer()
        assert isinstance(lemmatizer, LowercaseLemmatizer)
        get_lemmatizer.cache_clear()

    def test_classla_config_returns_classla(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "lemmatizer_type", "classla")
        import types

        fake_classla = types.ModuleType("classla")

        class FakePipeline:
            pass

        fake_classla.Pipeline = FakePipeline
        monkeypatch.setitem(__import__("sys").modules, "classla", fake_classla)
        get_lemmatizer.cache_clear()
        lemmatizer = get_lemmatizer()
        assert isinstance(lemmatizer, ClasslaLemmatizer)
        get_lemmatizer.cache_clear()

    def test_classla_import_error_falls_back(self, monkeypatch, caplog):
        from app.config import settings

        monkeypatch.setattr(settings, "lemmatizer_type", "classla")
        monkeypatch.delitem(__import__("sys").modules, "classla", raising=False)
        import builtins

        original_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "classla":
                raise ImportError("No module named classla")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        get_lemmatizer.cache_clear()
        lemmatizer = get_lemmatizer()
        assert isinstance(lemmatizer, LowercaseLemmatizer)
        assert "classla not installed" in caplog.text
        get_lemmatizer.cache_clear()


def test_suite_pins_lowercase_lemmatizer_regardless_of_env():
    """Regression: the autouse _settings_overrides fixture pins the lemmatizer to
    lowercase so the suite never depends on the developer's .env lemmatizer_type.

    Without the pin, `lemmatizer_type=classla` in a local .env leaks into the
    suite and breaks lemma-sensitive listen/story/transcript tests (caught by a
    full ./test.sh run after the flag was set in .env)."""
    import app.api.srs as srs_mod
    from app.config import settings
    from app.srs.lemmatizer import LowercaseLemmatizer

    assert settings.lemmatizer_type == "lowercase"
    assert isinstance(srs_mod._lemmatizer, LowercaseLemmatizer)


@pytest.fixture(scope="session")
def classla_lemmatizer():
    """Session-scoped ClasslaLemmatizer — one pipeline load for all classla tests.

    Requires ``classla`` to be installed (``uv pip install classla``).
    """
    pytest.importorskip("classla")
    from app.srs.lemmatizer import ClasslaLemmatizer

    lem = ClasslaLemmatizer()
    # Trigger one-time lazy pipeline load so the first test doesn't pay the
    # cost as part of its own timing.
    lem.analyze_sentence("test", "sl")
    return lem


@pytest.mark.classla
class TestClasslaIntegration:
    """Opt-in tests exercising the real classla Slovene pipeline.

    These tests are gated behind ``@pytest.mark.classla`` (skipped unless
    ``--run-classla`` is passed). On top of the marker they also use
    ``pytest.importorskip("classla")`` so CI with ``--run-classla --ignore``
    stays green when classla is not installed.
    """

    def test_analyze_hotel(self, classla_lemmatizer):
        results = classla_lemmatizer.analyze_sentence("v hotelu", "sl")
        assert len(results) >= 2
        ta = results[1]
        assert ta.lemma == "hotel"
        assert ta.upos == "NOUN"

    def test_analyze_dober_from_dobro(self, classla_lemmatizer):
        # "dobro" isolated can be ADV → lemma "dobro"; in adjectival context
        # ("Je to dobro?") it's ADJ → lemma "dober".
        results = classla_lemmatizer.analyze_sentence("Je to dobro?", "sl")
        lookup = {r.surface: r for r in results}
        assert lookup["dobro"].lemma == "dober"
        assert lookup["dobro"].upos == "ADJ"

    def test_arrival_in_ljubljana_transcript(self, classla_lemmatizer):
        text = "Grem v Ljubljano. Sem v hotelu. Je to dobro? Si dober?"
        results = classla_lemmatizer.analyze_sentence(text, "sl")
        lookup = {r.surface.casefold(): r for r in results}
        assert lookup["hotelu"].lemma == "hotel"
        assert lookup["hotelu"].upos == "NOUN"
        assert lookup["sem"].lemma == "biti"
        assert lookup["sem"].upos == "AUX"
        assert lookup["je"].lemma == "biti"
        assert lookup["je"].upos == "AUX"
        assert lookup["dobro"].lemma == "dober"
        assert lookup["dobro"].upos == "ADJ"

    def test_analyze_sentence_is_cached(self, classla_lemmatizer):
        """Re-analyzing the same sentence returns the cached list, so the
        transcript endpoint doesn't re-run the pipeline on every refetch."""
        first = classla_lemmatizer.analyze_sentence("Sem v hotelu.", "sl")
        second = classla_lemmatizer.analyze_sentence("Sem v hotelu.", "sl")
        assert second is first


class TestStubLemmatizer:
    def test_satisfies_protocol(self):
        assert_satisfies_lemmatizer_protocol(StubLemmatizer())

    def test_default_fallback(self):
        stub = StubLemmatizer()
        assert stub.lemmatize("unregistered", "sl") == "unregistered"

    def test_registered_lemma(self):
        stub = StubLemmatizer()
        stub.set_lemma("hotelu", "hotel")
        assert stub.lemmatize("hotelu", "sl") == "hotel"

    def test_registered_analysis(self):
        stub = StubLemmatizer()
        stub.set_analysis("hotelu", "hotel", "Loc", "Sing")
        lemma, case, number = stub.analyze("hotelu", "sl")
        assert lemma == "hotel"
        assert case == "Loc"
        assert number == "Sing"

    def test_sentence_analysis(self):
        stub = StubLemmatizer()
        analyses = [
            TokenAnalysis(surface="v", lemma="v", upos="ADP"),
            TokenAnalysis(surface="hotelu", lemma="hotel", upos="NOUN", case="Loc", number="Sing"),
        ]
        stub.set_sentence("v hotelu", analyses)
        results = stub.analyze_sentence("v hotelu", "sl")
        assert len(results) == 2
        assert results[0].lemma == "v"
        assert results[1].lemma == "hotel"

    def test_set_analysis_preserves_person(self):
        stub = StubLemmatizer()
        stub.set_analysis("sem", "biti", person="1", number="Sing", upos="AUX")
        result = stub.analyze("sem", "sl")
        assert result == ("biti", "", "Sing")

    def test_analyze_sentence_fallback_uses_analyses(self):
        stub = StubLemmatizer()
        stub.set_analysis("hotelu", "hotel", case="Loc", number="Sing", upos="NOUN", gender="Masc")
        stub.set_lemma("hotelu", "hotel")
        results = stub.analyze_sentence("v hotelu", "sl")
        assert len(results) == 2
        assert results[0].surface == "v"
        assert results[0].lemma == "v"
        assert results[0].upos == ""
        assert results[1].surface == "hotelu"
        assert results[1].lemma == "hotel"
        assert results[1].upos == "NOUN"
        assert results[1].case == "Loc"
        assert results[1].number == "Sing"
        assert results[1].gender == "Masc"

    def test_analyze_sentence_fallback_uses_lemma_without_analysis(self):
        stub = StubLemmatizer()
        stub.set_lemma("hotelu", "hotel")
        results = stub.analyze_sentence("v hotelu", "sl")
        assert results[1].lemma == "hotel"
        assert results[1].upos == ""


class TestLemmatizeSurfacesInContext:
    """The sentence-aware helper that fixes POS-ambiguous lemmas (dobro→dober)."""

    def test_context_lemma_wins_with_single_word_fallback(self):
        """In-context lemma is used when the surface is in the analysis; otherwise
        we fall back to single-word ``lemmatize`` (covers both branches)."""
        from app.srs.lemmatizer import lemmatize_surfaces_in_context

        stub = StubLemmatizer()
        # Sentence analysis resolves the adjective reading: dobro -> dober.
        stub.set_sentence(
            "Vse je dobro",
            [
                TokenAnalysis(surface="Vse", lemma="ves"),
                TokenAnalysis(surface="je", lemma="biti"),
                TokenAnalysis(surface="dobro", lemma="dober"),
            ],
        )
        # Single-word fallback would mis-key "dobro" as the adverb — context must win.
        stub.set_lemma("dobro", "dobro")
        # "neznano" is absent from the analysis → exercises the fallback branch.
        stub.set_lemma("neznano", "neznan")

        result = lemmatize_surfaces_in_context(["dobro", "neznano"], "Vse je dobro", stub, "sl")
        assert result == ["dober", "neznan"]

    def test_lowercase_lemmatizer_is_unchanged(self):
        """For the default lemmatizer the helper equals the old per-surface path."""
        from app.srs.lemmatizer import LowercaseLemmatizer, lemmatize_surfaces_in_context

        lem = LowercaseLemmatizer()
        assert lemmatize_surfaces_in_context(["Dobro", "jutro"], "Dobro jutro", lem, "sl") == ["dobro", "jutro"]

    def test_lowercases_capitalized_lemmas_to_match_keyspace(self):
        """Proper-noun lemmas come back capitalized (Ženeve→Ženeva), but the card
        keyspace is lowercase — both the context and fallback paths must lowercase."""
        from app.srs.lemmatizer import TokenAnalysis, lemmatize_surfaces_in_context

        stub = StubLemmatizer()
        stub.set_sentence("Ženeve Pariz", [TokenAnalysis(surface="Ženeve", lemma="Ženeva", upos="PROPN")])
        stub.set_lemma("Pariz", "Pariz")  # absent from the sentence analysis → fallback path

        result = lemmatize_surfaces_in_context(["Ženeve", "Pariz"], "Ženeve Pariz", stub, "sl")
        assert result == ["ženeva", "pariz"]
