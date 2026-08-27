"""Tests for the stored-caption staleness detector.

A stored chunk phrase carries ``source_word``, ``syllable_span`` and ``text``.
The text was the caption at render time; when a boundary rule improves, the span
comes to denote different letters and the stored text no longer matches. The
detector REPORTS this — it never rewrites the stored text, because that text
describes audio already on disk. These tests pin what is and is not a report,
all against a synthetic lesson (never a real database).

Most cases inject a fake ``syllabify`` so the expected output is explicit and
language-independent; one case exercises the default registry resolution to
cover the real path. If a test failed, the code being wrong is stated in that
test's docstring.
"""

from __future__ import annotations

from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.storage.caption_staleness import _resolve_span_syllabifier, find_stale_captions


def _phrase(text: str, *, source_word: str | None = "hagen", span: tuple[int, int] | None = (0, 1)) -> Phrase:
    return Phrase(
        text=text,
        voice_id="nb-NO-PernilleNeural",
        language_code="no",
        source_word=source_word,
        syllable_span=span,
    )


def _lesson(*phrases: Phrase) -> Lesson:
    return Lesson(
        title="T",
        language_code="no",
        sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=list(phrases))],
    )


class TestFindStaleCaptions:
    def test_stored_text_matching_its_span_is_not_reported(self):
        """A chunk whose caption still equals its span's letters must produce no record;
        if it did, healthy lessons would read as stale and the detector would be noise."""
        lesson = _lesson(_phrase("ha", span=(0, 1)))

        stale, unbreakable = find_stale_captions("l1", lesson, syllabify=lambda _w: ["ha", "gen"])

        assert stale == []
        assert unbreakable == []

    def test_moved_boundary_is_reported_with_the_now_text(self):
        """A chunk whose span now denotes different letters must be reported as stale with the
        text the span denotes NOW; if the now-text were missing, the report could not tell the
        human what the span actually selects and re-rendering would be blind."""
        lesson = _lesson(_phrase("haa", span=(0, 1)))

        stale, unbreakable = find_stale_captions("l1", lesson, syllabify=lambda _w: ["ha", "gen"])

        assert [r.stored_text for r in stale] == ["haa"]
        assert [r.now_text for r in stale] == ["ha"]
        assert [r.source_word for r in stale] == ["hagen"]
        assert [r.syllable_span for r in stale] == [(0, 1)]
        assert unbreakable == []

    def test_lesson_id_and_positioning_are_carried_on_a_stale_record(self):
        """The report must identify the lesson and the phrase, or the human cannot find and
        re-render the one stale chunk in a large corpus."""
        lesson = Lesson(
            title="T",
            language_code="no",
            sections=[
                Section(section_type=SectionType.NATURAL_SPEED, phrases=[_phrase("aa", span=(0, 1))]),
                Section(section_type=SectionType.KEY_PHRASES, phrases=[_phrase("bb", span=(0, 1))]),
            ],
        )

        stale, _unbreakable = find_stale_captions("lesson-7", lesson, syllabify=lambda _w: ["aa", "bb"])

        assert [(r.section_index, r.phrase_index, r.lesson_id) for r in stale] == [(1, 0, "lesson-7")]

    def test_chunk_with_no_source_word_is_skipped(self):
        """A chunk without a source_word has nothing to syllabify, so it cannot be checked;
        it must be skipped rather than reported, or every non-provenance chunk would be flagged."""
        lesson = _lesson(_phrase("ha", source_word=None))

        stale, unbreakable = find_stale_captions("l1", lesson, syllabify=lambda _w: ["ha", "gen"])

        assert stale == []
        assert unbreakable == []

    def test_chunk_with_no_syllable_span_is_skipped(self):
        """A chunk without a syllable_span has no span to compare, so it cannot be checked; it
        must be skipped rather than reported, or whole-phrase bookends would be flagged."""
        lesson = _lesson(_phrase("ha", span=None))

        stale, unbreakable = find_stale_captions("l1", lesson, syllabify=lambda _w: ["ha", "gen"])

        assert stale == []
        assert unbreakable == []

    def test_stale_whole_word_span_is_reported(self):
        """A WHOLE-word span can go stale too and must be reported; if it were excluded, a
        changed spelling that shifts the whole word's letters would silently pass (this pins
        that plan_chunk's whole-word exclusion was NOT copied into the detector)."""
        lesson = _lesson(_phrase("hagens", source_word="hagen", span=(0, 2)))

        stale, unbreakable = find_stale_captions("l1", lesson, syllabify=lambda _w: ["ha", "gen"])

        assert [(r.stored_text, r.now_text) for r in stale] == [("hagens", "hagen")]
        assert unbreakable == []

    def test_source_word_that_no_longer_syllabifies_is_reported_distinguishably(self):
        """A chunk whose source_word the syllabifier rejects (None) cannot be compared to a
        moved boundary; it must be reported SEPARATELY from a stale record, or the two failures
        would be conflated and a re-render decision could target the wrong class of damage."""
        lesson = _lesson(_phrase("team", source_word="etterforskningsteamet", span=(4, 5)))

        stale, unbreakable = find_stale_captions("l1", lesson, syllabify=lambda _w: None)

        assert stale == []
        assert [r.source_word for r in unbreakable] == ["etterforskningsteamet"]
        assert [r.stored_text for r in unbreakable] == ["team"]
        assert unbreakable[0].syllable_span == (4, 5)

    def test_empty_syllabifier_output_is_also_unbreakable(self):
        """An EMPTY syllable list is as unresolvable as None and must be reported in the same
        distinguishable class, not silently skipped like a missing span."""
        lesson = _lesson(_phrase("ha", span=(0, 1)))

        stale, unbreakable = find_stale_captions("l1", lesson, syllabify=lambda _w: [])

        assert stale == []
        assert len(unbreakable) == 1

    def test_stale_and_unbreakable_lessons_mix_without_leaking(self):
        """A lesson that is both stale in one chunk and unbreakable in another must report each
        in its own list, proving the two failure classes do not bleed into one another."""
        lesson = _lesson(
            _phrase("haa", span=(0, 1)),
            _phrase("team", source_word="etterforskningsteamet", span=(4, 5)),
        )

        stale, unbreakable = find_stale_captions(
            "l1", lesson, syllabify=lambda w: None if w == "etterforskningsteamet" else ["ha", "gen"]
        )

        assert [(r.stored_text, r.now_text) for r in stale] == [("haa", "ha")]
        assert [r.source_word for r in unbreakable] == ["etterforskningsteamet"]

    def test_default_registry_resolution_uses_the_alignment_syllabifier(self):
        """When no syllabify is injected, the detector must resolve through the registry and use
        the aligner's syllabifier (whose output spans index), reporting real Norwegian staleness
        exactly as the live corpus does; if it used the wrong syllabifier it would miss or invent
        staleness."""
        lesson = _lesson(
            _phrase("team", source_word="etterforskningsteamet", span=(4, 5)),
            _phrase("forskningsteamet", source_word="etterforskningsteamet", span=(2, 6)),
        )

        stale, unbreakable = find_stale_captions("l1", lesson)

        assert [(r.stored_text, r.now_text) for r in stale] == [("team", "tea")]
        assert unbreakable == []


class TestResolveSpanSyllabifier:
    def test_language_with_alignment_uses_the_aligner_syllabifier(self):
        """A language with alignment wiring indexes spans into alignment.syllabify_fn (not the
        plain syllabifier_fn — Norwegian's differ); if the detector used the wrong one it would
        compute a different 'now' text than the span actually selects."""
        fn = _resolve_span_syllabifier("no")

        assert fn("etterforskningsteamet") == ["e", "tter", "fors", "knings", "tea", "met"]

    def test_language_without_alignment_falls_back_to_the_plain_syllabifier(self):
        """A language with no alignment wiring indexes spans into its plain syllabifier; if it
        returned nothing the detector could not resolve spans for that language at all."""
        fn = _resolve_span_syllabifier("sl")

        assert callable(fn)
        assert isinstance(fn("prst"), list)
