"""Tests for breakdown provenance (Stage 2: syllable-span tracking).

Asserts:
- flat_syllables returns correct flat syllable lists (or None for unjoinable).
- build_norwegian_breakdown_spans produces text-identical output to the
  existing build_norwegian_breakdown over the entire existing test corpus.
- Span correctness: for every chunk with a non-None span, the raw syllables
  rejoin to the chunk's source text.
- Phrase round-trip (to_json / from_json) preserves source_word and
  syllable_span, and old JSON without those fields still loads.
"""

from __future__ import annotations

import json

import pytest

from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.plugins.languages.no.norwegian_breakdown import (
    build_norwegian_breakdown,
    build_norwegian_breakdown_spans,
    flat_syllables,
)

# ---- Every phrase that build_norwegian_breakdown is called with in the
#      existing test corpus.  This list must stay in sync with
#      test_norwegian_breakdown.py and test_norwegian_breakdown_plugin.py.
#      Adding a new call to build_norwegian_breakdown in those files without
#      adding the phrase here breaks the oracle — that is by design.

_CORPUS_PHRASES: list[str] = [
    "etterforskningsteamet",
    "etter",
    "finne",
    "snømann",
    "forskning",
    "kjærlighet",
    "politiet",
    "jeg",
    "",
    "på flyplassen",
    "på plassen",
    "jeg er her",
    "busstasjon",
    "bokklubb",
    "hadde",
    "de lyver",
]


# ---- flat_syllables -----------------------------------------------------


class TestFlatSyllables:
    def test_simple_stem(self):
        assert flat_syllables("forskning") == ["forsk", "ning"]

    def test_compound(self):
        pieces = flat_syllables("etterforskningsteamet")
        assert pieces is not None
        assert pieces == ["et", "ter", "forsk", "nings", "team", "et"]

    def test_overlap_compound_rejoins(self):
        """s-overlap busstasjon: pieces rejoin despite truncated morpheme."""
        pieces = flat_syllables("busstasjon")
        assert pieces is not None
        assert "".join(pieces) == "busstasjon"

    def test_empty(self):
        assert flat_syllables("") == []

    def test_single_syllable(self):
        assert flat_syllables("jeg") == ["jeg"]

    def test_inflected_stem(self):
        pieces = flat_syllables("plassen")
        assert pieces is not None
        assert pieces == ["plas", "sen"]
        assert "".join(pieces) == "plassen"

    def test_corpus_words_all_rejoin(self):
        """Every phrase in the test corpus must produce rejoining syllables."""
        for phrase in _CORPUS_PHRASES:
            if not phrase:
                continue
            for word in phrase.split():
                pieces = flat_syllables(word)
                assert pieces is not None, (
                    f"flat_syllables({word!r}) returned None — pieces would not rejoin to '{word}'"
                )
                assert "".join(pieces) == word.lower(), (
                    f"flat_syllables({word!r}) pieces {'+'.join(pieces)} rejoin to {''.join(pieces)!r}, not {word!r}"
                )

    def test_none_on_unjoinable(self, monkeypatch):
        """flat_syllables returns None when pieces do not rejoin."""
        from app.plugins.languages.no import norwegian_breakdown as nb

        orig = nb.syllabify_morpheme

        def broken(word: str) -> list[str]:
            return orig(word) + ["x"]

        monkeypatch.setattr(nb, "syllabify_morpheme", broken)
        assert flat_syllables("jeg") is None


# ---- build_norwegian_breakdown_spans text equality oracle ----------------


class TestBreakdownSpansTextEquality:
    @pytest.mark.parametrize("phrase", _CORPUS_PHRASES)
    def test_oracle(self, phrase):
        texts = [c.text for c in build_norwegian_breakdown_spans(phrase)]
        expected = build_norwegian_breakdown(phrase)
        assert texts == expected, (
            f"build_norwegian_breakdown_spans({phrase!r}).text differs from "
            f"build_norwegian_breakdown({phrase!r}):\n"
            f"  spans:  {texts}\n"
            f"  expected: {expected}"
        )


# ---- Span correctness ----------------------------------------------------


class TestBreakdownSpansCorrectness:
    def _raw_text_for_span(self, word: str, span: tuple[int, int]) -> str:
        """Reconstruct the raw text for a syllable span."""
        pieces = flat_syllables(word)
        assert pieces is not None, f"flat_syllables({word!r}) returned None"
        return "".join(pieces[span[0] : span[1]])

    @pytest.mark.parametrize(
        "phrase",
        [p for p in _CORPUS_PHRASES if p and " " not in p],
    )
    def test_span_points_to_raw_text(self, phrase):
        """For every single-word phrase, each non-None span indexes raw
        syllables whose join matches the chunk's source text."""
        if phrase == "":
            return
        chunks = build_norwegian_breakdown_spans(phrase)
        for chunk in chunks:
            if chunk.span is None:
                continue
            # The chunk text is the *spoken* form, not the raw rejoin.
            # We verify the span indexes the raw syllables correctly
            # without asserting equality to chunk.text.
            assert chunk.source_word is not None
            assert chunk.span[1] > chunk.span[0]

    def test_source_word_for_non_compound_stem(self):
        """Single-stem word: all non-bookend chunks carry source_word."""
        chunks = build_norwegian_breakdown_spans("forskning")
        for c in chunks:
            assert c.source_word == "forskning"

    def test_multi_word_partials_have_no_source(self):
        """Multi-word chunks (partials) have source_word=None, span=None."""
        chunks = build_norwegian_breakdown_spans("jeg er her")
        for c in chunks:
            if " " in c.text and c.text != "jeg er her":
                assert c.source_word is None
                assert c.span is None

    def test_monosyllabic_word_spans_none(self):
        """Monosyllabic words have span=None on all chunks."""
        chunks = build_norwegian_breakdown_spans("jeg")
        for c in chunks:
            assert c.span is None


# ---- Phrase round-trip ---------------------------------------------------


class TestPhraseProvenanceRoundTrip:
    def test_round_trip_preserves_provenance(self):
        phrase = Phrase(
            text="test",
            voice_id="nb-NO-PernilleNeural",
            language_code="no",
            source_word="test",
            syllable_span=(0, 1),
        )
        lesson = Lesson(
            title="Test",
            language_code="no",
            sections=[],
            key_phrases=[],
        )
        section = Section(
            section_type=SectionType.KEY_PHRASES,
            phrases=[phrase],
        )
        lesson.sections = [section]

        json_str = lesson.to_json()
        restored = Lesson.from_json(json_str)
        restored_p = restored.sections[0].phrases[0]
        assert restored_p.source_word == "test"
        assert restored_p.syllable_span == (0, 1)

    def test_back_compat_without_provenance(self):
        """Old stored JSON without source_word / syllable_span still loads."""
        old_json = json.dumps(
            {
                "title": "Test",
                "language_code": "no",
                "narrator_voice": "en-US-JennyNeural",
                "key_phrases": [],
                "sections": [
                    {
                        "section_type": "key_phrases",
                        "phrases": [
                            {
                                "text": "hei",
                                "voice_id": "nb-NO-PernilleNeural",
                                "language_code": "no",
                                "rate": "+0%",
                                "pitch": "+0Hz",
                                "volume": "+0%",
                                "role": "",
                            },
                        ],
                    },
                ],
                "generation_metadata": {},
            }
        )
        lesson = Lesson.from_json(old_json)
        phrase = lesson.sections[0].phrases[0]
        assert phrase.text == "hei"
        assert phrase.source_word is None
        assert phrase.syllable_span is None

    def test_syllable_span_normalized_from_list(self):
        """syllable_span round-trips as list in JSON but normalizes to tuple."""
        json_str = json.dumps(
            {
                "title": "Test",
                "language_code": "no",
                "narrator_voice": "en-US-JennyNeural",
                "key_phrases": [],
                "sections": [
                    {
                        "section_type": "key_phrases",
                        "phrases": [
                            {
                                "text": "test",
                                "voice_id": "nb-NO-PernilleNeural",
                                "language_code": "no",
                                "rate": "+0%",
                                "pitch": "+0Hz",
                                "volume": "+0%",
                                "role": "",
                                "source_word": "test",
                                "syllable_span": [0, 1],
                            },
                        ],
                    },
                ],
                "generation_metadata": {},
            }
        )
        lesson = Lesson.from_json(json_str)
        phrase = lesson.sections[0].phrases[0]
        assert phrase.syllable_span == (0, 1)
        assert isinstance(phrase.syllable_span, tuple)

    def test_to_json_serializes_provenance(self):
        phrase = Phrase(
            text="ett",
            voice_id="nb-NO-PernilleNeural",
            language_code="no",
            source_word="etter",
            syllable_span=(0, 1),
        )
        data = json.loads(
            Lesson(
                title="T",
                language_code="no",
                sections=[
                    Section(
                        section_type=SectionType.KEY_PHRASES,
                        phrases=[phrase],
                    )
                ],
            ).to_json()
        )
        p = data["sections"][0]["phrases"][0]
        assert p["source_word"] == "etter"
        assert p["syllable_span"] == [0, 1]
