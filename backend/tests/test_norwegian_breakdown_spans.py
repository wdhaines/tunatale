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

import ast
import json
from pathlib import Path

import pytest

from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.plugins.languages.no.norwegian_breakdown import (
    build_norwegian_breakdown,
    build_norwegian_breakdown_spans,
    flat_syllables,
)

# ---- Every phrase that build_norwegian_breakdown is called with in the
#      existing test corpus. Kept in sync mechanically, not by good intentions:
#      test_corpus_covers_every_phrase_in_the_oracle_file parses
#      test_norwegian_breakdown.py and fails naming any phrase missing here.

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
    def test_corpus_covers_every_phrase_in_the_oracle_file(self):
        """``_CORPUS_PHRASES`` must be the WHOLE oracle corpus, not a sample.

        The equality oracle below is only as strong as this list. A comment
        asking the next author to keep it in sync is not a mechanism, so read
        the literals back out of ``test_norwegian_breakdown.py`` and compare.
        Adding a ``build_norwegian_breakdown("…")`` call there without adding
        the phrase here fails HERE, naming the missing phrase.
        """
        oracle_file = Path(__file__).with_name("test_norwegian_breakdown.py")
        tree = ast.parse(oracle_file.read_text(encoding="utf-8"))
        called: set[str] = {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "build_norwegian_breakdown"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        }
        assert called, f"no build_norwegian_breakdown string literals found in {oracle_file}"
        assert called - set(_CORPUS_PHRASES) == set(), (
            "phrases exercised in test_norwegian_breakdown.py but missing from "
            f"_CORPUS_PHRASES: {sorted(called - set(_CORPUS_PHRASES))}"
        )

    @pytest.mark.parametrize("phrase", _CORPUS_PHRASES)
    def test_oracle(self, phrase):
        """Text identity guard — must not diverge from the plain breakdown.

        After the inversion, ``build_norwegian_breakdown`` delegates to the
        spans variant by construction, so this property is tautological — and
        that is exactly the point: anyone who re-implements the plain path
        independently will break here.
        """
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
    @pytest.mark.parametrize("phrase", [p for p in _CORPUS_PHRASES if p])
    def test_span_reproduces_chunk_text_from_source_word(self, phrase):
        """``flat_syllables(source_word)[a:b]`` must rejoin to the chunk's text.

        This is the whole contract Stage 3 consumes: it renders ``source_word``
        once and cuts ``[a:b]`` out of that render. The join may differ from
        ``chunk.text`` only by the geminate/overlap doubling of
        :func:`_spoken_syllable` / :func:`_spoken_part` (``et``->``ett``,
        ``bus``->``buss``) — a real orthographic doubling, not a phonetic hint.
        Anything else means the span points somewhere the text did not come from.

        This used to also permit an invented ``de``->``deh`` respelling. Slicing
        removed the need for it and it was the one divergence a learner could
        actually see, since the caption is ``Phrase.text``.
        """
        for chunk in build_norwegian_breakdown_spans(phrase):
            if chunk.span is None:
                continue
            assert chunk.source_word is not None
            flat = flat_syllables(chunk.source_word)
            assert flat is not None, f"span {chunk.span} on unsliceable source_word {chunk.source_word!r}"
            a, b = chunk.span
            assert 0 <= a < b <= len(flat), (
                f"span {chunk.span} out of range for "
                f"flat_syllables({chunk.source_word!r}) = {flat} "
                f"(chunk text {chunk.text!r})"
            )
            raw = "".join(flat[a:b])
            doubled = raw + raw[-1:]
            assert chunk.text in (raw, doubled), (
                f"span {chunk.span} of flat_syllables({chunk.source_word!r}) = {flat} "
                f"rejoins to {raw!r}, which is not {chunk.text!r} nor its geminate doubling"
            )

    @pytest.mark.parametrize("phrase", [p for p in _CORPUS_PHRASES if p])
    def test_source_word_is_a_word_of_the_phrase(self, phrase):
        """``source_word`` must be a word the caller can actually render.

        Regression: compound chunks used to carry the compound *part*
        (``forsknings``, ``teamet``, ``plassen``) with part-local indices.
        Stage 3 renders ``source_word`` and slices it, so a bare morpheme
        there means synthesizing an isolated fragment — reintroducing the
        word-level-G2P bug this workstream exists to fix — and it costs one
        TTS call per part instead of one per word.
        """
        words = phrase.split()
        for chunk in build_norwegian_breakdown_spans(phrase):
            if chunk.span is None:
                continue
            assert chunk.source_word in words, (
                f"chunk {chunk.text!r} of {phrase!r} names source_word "
                f"{chunk.source_word!r}, which is not one of {words}"
            )

    def test_compound_chunks_index_the_whole_word(self):
        """A compound's syllables are spans of the whole compound's render."""
        chunks = build_norwegian_breakdown_spans("etterforskningsteamet")
        assert flat_syllables("etterforskningsteamet") == [
            "et",
            "ter",
            "forsk",
            "nings",
            "team",
            "et",
        ]
        by_text = {(c.text, c.span) for c in chunks if c.span is not None}
        assert ("team", (4, 5)) in by_text
        assert ("nings", (3, 4)) in by_text
        assert ("ter", (1, 2)) in by_text
        assert {c.source_word for c in chunks if c.span is not None} == {"etterforskningsteamet"}

    def test_compound_of_monosyllables_is_still_sliceable(self):
        """``snø``/``mann`` are cut from one ``snømann`` render, not resynthesized.

        Each part is monosyllabic, so part-local provenance made both chunks
        whole-word spans of a bare morpheme — which Stage 3 skips, leaving the
        compound entirely unsliced.
        """
        chunks = build_norwegian_breakdown_spans("snømann")
        spans = {(c.text, c.source_word, c.span) for c in chunks if c.span is not None}
        assert ("snø", "snømann", (0, 1)) in spans
        assert ("mann", "snømann", (1, 2)) in spans

    def test_compound_inside_multi_word_phrase_indexes_its_word(self):
        chunks = build_norwegian_breakdown_spans("på flyplassen")
        # Inside the compound the inflection is its own piece (``plass|en``),
        # unlike the standalone word (``plas|sen``) — the whole-word flatten is
        # what the spans index, so that is what Stage 3 must render and cut.
        assert flat_syllables("flyplassen") == ["fly", "plass", "en"]
        spans = {(c.text, c.source_word, c.span) for c in chunks if c.span is not None}
        assert ("fly", "flyplassen", (0, 1)) in spans
        assert ("plassen", "flyplassen", (1, 3)) in spans
        assert ("en", "flyplassen", (2, 3)) in spans

    def test_source_word_for_non_compound_stem(self):
        """Single-stem word: non-bookend chunks carry source_word."""
        chunks = build_norwegian_breakdown_spans("forskning")
        for c in chunks:
            if c.span is not None:
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
