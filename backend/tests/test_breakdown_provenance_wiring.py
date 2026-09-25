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
            ("magdadala ako ng abuloy", "tl"),
            ("kailan ang libing?", "tl"),
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

    def test_a_one_syllable_word_inside_a_phrase_carries_its_own_provenance(self):
        """tunatale-w4m7.16: the Tagalog planner gives whole words IPA, and the
        renderer only asks it about chunks with provenance. ``ng`` said as text
        by the key-phrase voice is not "nang"."""
        chunks = build_word_breakdown_spans("magdadala ako ng abuloy", "tl")
        ng = next(c for c in chunks if c.text == "ng")
        assert (ng.source_word, ng.span) == ("ng", (0, 1))

    def test_a_one_syllable_phrase_bookend_stays_bare(self):
        # The bookends are the PHRASE, not a word of it, even when the phrase
        # is one word long.
        chunks = build_word_breakdown_spans("Po!", "tl")
        assert [(c.text, c.source_word, c.span) for c in chunks] == [("Po!", None, None), ("Po!", None, None)]


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
            "e",
            "tter",
            "fors",
            "knings",
            "tea",
            "met",
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


class TestSentencePunctuationIsStrippedBeforeSyllabifying:
    """tunatale-w4m7.19: a buildup fragment must not be read as a question.

    A phrase's last word arrives carrying its sentence punctuation, and the
    losslessness check happily passed it through — both sides carried the ``?``,
    so the check had nothing to object to. But these chunks are SLICED from one
    whole-word render of ``source_word``, and that render was spoken with the
    punctuation attached: the learner hears "bong?" and "lubong?" said as
    questions in the middle of a sentence that is not one.

    So the punctuation is removed BEFORE syllabifying. The stripped word is
    then the syllable source, the one-syllable chunk's text, and its
    ``source_word`` — the render is sliced from the same string the fragments
    were built from, so the two cannot drift apart.
    """

    @staticmethod
    def _rows(phrase: str, code: str) -> list[tuple]:
        return [(c.text, c.source_word, c.span) for c in build_word_breakdown_spans(phrase, code)]

    def test_a_question_phrase_loses_its_punctuation_inside_the_fragments(self):
        assert self._rows("kanus-a ang lubong?", "ceb") == [
            # The opening and closing bookends are the PHRASE as given.
            ("kanus-a ang lubong?", None, None),
            # `lubong?` → `lubong`: text, source_word and span all stripped.
            ("bong", "lubong", (1, 2)),
            ("lu", "lubong", (0, 1)),
            ("lubong", "lubong", (0, 2)),
            # A one-syllable word is its own whole-word span, so it is rendered
            # as its own audio and needs stripping on both counts.
            ("ang", "ang", (0, 1)),
            # A multi-word partial is the TAIL of the question and keeps it.
            ("ang lubong?", None, None),
            # `kanus-a` keeps its hyphen — in Cebuano and Tagalog it marks a
            # glottal stop, so it is part of the word, not punctuation.
            ("-a", "kanus-a", (2, 3)),
            ("nus", "kanus-a", (1, 2)),
            ("nus-a", "kanus-a", (1, 3)),
            ("ka", "kanus-a", (0, 1)),
            ("kanus-a", "kanus-a", (0, 3)),
            ("kanus-a ang lubong?", None, None),
        ]

    def test_only_the_last_words_fragments_change(self):
        """A question whose first words carry no punctuation is untouched,
        right down to the partials, which keep the ``?`` they are the tail of."""
        assert self._rows("Pila man ni?", "ceb") == [
            ("Pila man ni?", None, None),
            ("ni", "ni", (0, 1)),
            ("man", "man", (0, 1)),
            ("man ni?", None, None),
            ("la", "Pila", (1, 2)),
            ("pi", "Pila", (0, 1)),
            ("pila", "Pila", (0, 2)),
            ("Pila man ni?", None, None),
        ]

    def test_ellipsis_and_a_leading_quote_are_stripped_too(self):
        """The set is the whole of ``?!.,;:…""`` — an ellipsis is three of the
        characters already in it, and a closing quote would otherwise be left
        dangling on the final syllable."""
        assert self._rows("Si Maria...", "ceb") == [
            ("Si Maria...", None, None),
            ("a", "Maria", (2, 3)),
            ("ri", "Maria", (1, 2)),
            ("ria", "Maria", (1, 3)),
            ("ma", "Maria", (0, 1)),
            # `Maria` is three syllables, so its closing rung spans all three.
            ("maria", "Maria", (0, 3)),
            ("Si", "Si", (0, 1)),
            ("Si Maria...", None, None),
        ]

    def test_an_apostrophe_is_a_contraction_and_is_never_stripped(self):
        """``napulo'g`` is ten, not a possessive and not a quote mark. Stripping
        it would break both the syllabifier's join and the word itself."""
        assert self._rows("napulo'g mao", "ceb") == [
            ("napulo'g mao", None, None),
            ("o", "mao", (1, 2)),
            ("ma", "mao", (0, 1)),
            ("mao", "mao", (0, 2)),
            ("lo'g", "napulo'g", (2, 3)),
            ("pu", "napulo'g", (1, 2)),
            ("pulo'g", "napulo'g", (1, 3)),
            ("na", "napulo'g", (0, 1)),
            ("napulo'g", "napulo'g", (0, 3)),
            ("napulo'g mao", None, None),
        ]

    def test_a_hyphenated_phrase_with_no_punctuation_is_unchanged(self):
        """The control. Every fragment here already ends at a syllable
        boundary and the whole-word renders carry no punctuation, so nothing
        about this phrase may move — including the glottal-stop hyphens, which
        are the exact characters a naive ``-`` strip would have eaten."""
        assert self._rows("salamat sa inyong pag-anhi", "ceb") == [
            ("salamat sa inyong pag-anhi", None, None),
            ("hi", "pag-anhi", (2, 3)),
            ("-an", "pag-anhi", (1, 2)),
            ("-anhi", "pag-anhi", (1, 3)),
            ("pag", "pag-anhi", (0, 1)),
            ("pag-anhi", "pag-anhi", (0, 3)),
            ("yong", "inyong", (1, 2)),
            ("in", "inyong", (0, 1)),
            ("inyong", "inyong", (0, 2)),
            ("inyong pag-anhi", None, None),
            ("sa", "sa", (0, 1)),
            ("sa inyong pag-anhi", None, None),
            ("mat", "salamat", (2, 3)),
            ("la", "salamat", (1, 2)),
            ("lamat", "salamat", (1, 3)),
            ("sa", "salamat", (0, 1)),
            ("salamat", "salamat", (0, 3)),
            ("salamat sa inyong pag-anhi", None, None),
        ]

    def test_norwegian_is_untouched_by_the_generic_rule(self):
        """The control on the OTHER branch. Norwegian registers its own spans
        function and does not go through ``_generic_breakdown_spans`` at all, so
        a rule that leaked past the registry boundary would show here first.

        Pinned as-is, including the ``du``/``None``/``None`` row that the
        registered function already produces: this test is about the generic
        strip never reaching it, not about that row being right."""
        assert self._rows("Hvor kommer du?", "no") == [
            ("Hvor kommer du?", None, None),
            ("du", None, None),
            ("mmer", "kommer", (1, 2)),
            ("ko", "kommer", (0, 1)),
            ("kommer", "kommer", (0, 2)),
            ("kommer du?", None, None),
            ("Hvor", None, None),
            ("Hvor kommer du?", None, None),
        ]

    def test_tagalog_fragments_lose_their_punctuation_too(self):
        """Tagalog shares this path, and its lesson showed the same fault:
        ``bing?`` and ``libing?`` were sliced out of ``libing?``."""
        assert self._rows("kailan ang libing?", "tl") == [
            ("kailan ang libing?", None, None),
            ("bing", "libing", (1, 2)),
            ("li", "libing", (0, 1)),
            ("libing", "libing", (0, 2)),
            ("ang", "ang", (0, 1)),
            ("ang libing?", None, None),
            ("lan", "kailan", (1, 2)),
            ("kai", "kailan", (0, 1)),
            ("kailan", "kailan", (0, 2)),
            ("kailan ang libing?", None, None),
        ]

    def test_a_token_that_is_only_punctuation_is_left_alone(self):
        """There is no word under it to render, and stripping it would put an
        empty chunk into a buildup that gets spoken aloud."""
        assert self._rows("ka ?", "ceb") == [
            ("ka ?", None, None),
            ("?", "?", (0, 1)),
            ("ka", "ka", (0, 1)),
            ("ka ?", None, None),
        ]

    def test_the_text_sequence_still_matches_the_plain_breakdown(self):
        """The invariant both public entry points share: ``app.audio.cues``
        builds its timing manifest from the plain version, so stripping in the
        spans path must strip in both or the cues desynchronise."""
        for phrase, code in [
            ("kanus-a ang lubong?", "ceb"),
            ("Pila man ni?", "ceb"),
            ("kailan ang libing?", "tl"),
            ("Si Maria...", "ceb"),
        ]:
            assert [c.text for c in build_word_breakdown_spans(phrase, code)] == build_word_breakdown(phrase, code)
