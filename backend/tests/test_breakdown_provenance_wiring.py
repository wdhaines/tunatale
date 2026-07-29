"""Tests for carrying slicing provenance from the breakdown into ``Phrase``.

The chain is: a language plugin's spans function → ``build_word_breakdown_spans``
→ ``build_key_phrases_section`` → ``Phrase.source_word`` / ``Phrase.syllable_span``
→ the renderer's slicer. If any link drops the provenance the feature is inert
with every test green, so each link is pinned here.
"""

from __future__ import annotations

import pytest

from app.generation.section_builder import (
    build_key_phrases_section,
    build_word_breakdown,
    build_word_breakdown_spans,
)
from app.languages import get_alignment, get_breakdown_spans
from app.models.breakdown import BreakdownChunk
from app.models.lesson import SectionType

_NO_VOICES = {"female-1": "nb-NO-PernilleNeural"}
_NARRATOR = "en-US-JennyNeural"


class TestBuildWordBreakdownSpans:
    @pytest.mark.parametrize(
        "phrase,code",
        [
            ("etterforskningsteamet", "no"),
            ("på flyplassen", "no"),
            ("snømann", "no"),
            ("jeg er her", "no"),
            ("hadde", "no"),
            ("busstasjon", "no"),
            ("prosim", "sl"),
            ("dober dan", "sl"),
        ],
    )
    def test_text_sequence_matches_the_plain_breakdown(self, phrase, code):
        """The spans path must not change a single spoken chunk.

        ``app.audio.cues`` builds its timing manifest from the plain
        ``build_word_breakdown``, so any divergence here silently desynchronises
        every cue in the key-phrases section.
        """
        assert [c.text for c in build_word_breakdown_spans(phrase, code)] == build_word_breakdown(phrase, code)

    def test_norwegian_chunks_carry_provenance(self):
        chunks = build_word_breakdown_spans("etterforskningsteamet", "no")
        sliceable = [c for c in chunks if c.span is not None]
        assert sliceable, "a six-syllable compound must offer something to slice"
        assert {c.source_word for c in sliceable} == {"etterforskningsteamet"}

    def test_slovene_chunks_carry_provenance(self):
        """The generic path now emits provenance, not bare chunks."""
        chunks = build_word_breakdown_spans("prosim", "sl")
        sliceable = [c for c in chunks if c.span is not None]
        assert sliceable, "a two-syllable word must offer something to slice"
        assert all(c.source_word == "prosim" for c in sliceable if c.span is not None)

    def test_empty_phrase(self):
        assert build_word_breakdown_spans("", "no") == []


class TestRegistryWiring:
    def test_norwegian_registers_a_spans_function(self):
        assert get_breakdown_spans("no") is not None

    def test_slovene_registers_none(self):
        assert get_breakdown_spans("sl") is None

    def test_unknown_code_registers_none(self):
        assert get_breakdown_spans("zz") is None
        assert get_alignment("zz") is None

    def test_norwegian_registers_alignment_wiring(self):
        alignment = get_alignment("no")
        assert alignment is not None
        assert alignment.model_id
        assert alignment.vowels
        assert callable(alignment.aligner_factory)
        assert callable(alignment.syllabify_fn)

    def test_alignment_syllabify_agrees_with_the_spans_indices(self):
        """The registered syllabifier is what spans index into — same function,
        or the renderer cuts at the wrong place."""
        alignment = get_alignment("no")
        assert alignment.syllabify_fn("etterforskningsteamet") == [
            "et",
            "ter",
            "forsk",
            "nings",
            "team",
            "et",
        ]

    def test_slovene_registers_no_alignment(self):
        assert get_alignment("sl") is None


class TestKeyPhrasesSectionCarriesProvenance:
    def _section(self, phrase: str, translation: str = "the team", code: str = "no"):
        return build_key_phrases_section(
            [{"phrase": phrase, "translation": translation}],
            _NO_VOICES,
            _NARRATOR,
            code,
        )

    def test_breakdown_phrases_get_source_word_and_span(self):
        section = self._section("etterforskningsteamet")
        provenanced = [p for p in section.phrases if p.syllable_span is not None]
        assert provenanced, "no breakdown Phrase carried a span — the feature is inert"
        assert all(p.source_word == "etterforskningsteamet" for p in provenanced)

    def test_span_indexes_the_registered_syllabifier(self):
        section = self._section("etterforskningsteamet")
        syllabify = get_alignment("no").syllabify_fn
        for phrase in section.phrases:
            if phrase.syllable_span is None:
                continue
            pieces = syllabify(phrase.source_word)
            start, stop = phrase.syllable_span
            assert pieces is not None
            assert 0 <= start < stop <= len(pieces)
            assert "".join(pieces[start:stop])

    def test_english_and_multi_word_lines_have_no_provenance(self):
        """Only single-word chunks are sliced.

        English narrator lines and multi-word running partials are already
        correct — the isolated-fragment bug is intra-word — so re-cutting them
        would be churn. A whole-word *rebuild* step inside the buildup does keep
        its span: it is a contiguous span of the same render, just not stretched.
        """
        section = self._section("på flyplassen", translation="at the airport")
        for phrase in section.phrases:
            if phrase.language_code == "en" or " " in phrase.text:
                assert phrase.syllable_span is None, f"{phrase.text!r} should not be sliced"
                assert phrase.source_word is None

    def test_the_standalone_key_phrase_line_is_never_sliced(self):
        """The key phrase itself is spoken as a phrase, not assembled from cuts."""
        section = self._section("etterforskningsteamet")
        key_phrase_line = section.phrases[1]
        assert key_phrase_line.text == "etterforskningsteamet"
        assert key_phrase_line.syllable_span is None
        assert key_phrase_line.source_word is None

    def test_section_text_sequence_is_unchanged(self):
        section = self._section("på flyplassen", translation="at the airport")
        texts = [p.text for p in section.phrases]
        expected = ["Key Phrases", "på flyplassen", "at the airport", *build_word_breakdown("på flyplassen", "no")]
        assert texts == expected
        assert section.section_type == SectionType.KEY_PHRASES

    def test_slovene_section_carries_provenance(self):
        section = build_key_phrases_section(
            [{"phrase": "prosim", "translation": "please"}],
            {"female-1": "sl-SI-PetraNeural"},
            _NARRATOR,
            "sl",
        )
        provenanced = [p for p in section.phrases if p.syllable_span is not None]
        assert provenanced, "no breakdown Phrase carried a span — the generic path should emit provenance"
        assert all(p.source_word == "prosim" for p in provenanced)


class TestBreakdownChunkDefaults:
    def test_provenance_is_optional(self):
        chunk = BreakdownChunk(text="hei")
        assert chunk.source_word is None
        assert chunk.span is None
