"""Tests for the one-shot slicing-provenance backfill.

The backfill exists because ``Phrase.source_word`` / ``Phrase.syllable_span``
were added after these lessons were generated: a stored lesson has no
provenance, so every breakdown chunk is synthesised in isolation even on a
slicing-capable install.

The whole design point is that it **recomputes and compares before it writes**.
Attaching a span positionally without checking is not a smaller version of this
migration, it is a different and wrong one: spans index the CURRENT
syllabifier's output, and the older lessons in this repo were built by an
earlier syllabifier (``le|i|lig|het|en`` for ``leiligheten``, before Norwegian
diphthongs were handled). A span written against text the current code would
not produce points at the wrong syllables, and the renderer would cut audio
that is wrong rather than merely unsliced.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.generation.section_builder import build_key_phrases_section
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.storage.backfill_breakdown_provenance import backfill_breakdown_provenance
from app.storage.store import ContentStore

_VOICES = {"female-1": "nb-NO-PernilleNeural"}
_NARRATOR = "en-US-GuyNeural"


def _lesson(key_phrases: list[dict], *, strip_provenance: bool = True) -> Lesson:
    """A lesson whose KEY_PHRASES section is exactly what the builder produces."""
    section = build_key_phrases_section(key_phrases, _VOICES, _NARRATOR, "no")
    if strip_provenance:
        for phrase in section.phrases:
            phrase.source_word = None
            phrase.syllable_span = None
    return Lesson(
        title="T",
        language_code="no",
        sections=[section],
        key_phrases=[KeyPhraseInfo(phrase=kp["phrase"], translation=kp["translation"]) for kp in key_phrases],
    )


@pytest.fixture
def store(tmp_path):
    return ContentStore(str(tmp_path / "c.db"))


def _spans(lesson: Lesson) -> list[tuple[str, tuple[int, int]]]:
    return [(p.source_word, p.syllable_span) for s in lesson.sections for p in s.phrases if p.syllable_span is not None]


class TestBackfill:
    def test_writes_provenance_when_the_text_still_matches(self, store):
        store.save_lesson("l1", "c1", 1, _lesson([{"phrase": "hagen", "translation": "the garden"}]))

        report = backfill_breakdown_provenance(store)

        assert report.updated == 1
        assert report.skipped == []
        assert _spans(store.get_lesson("l1")), "no chunk came back with a span"

    def test_provenance_equals_a_fresh_build(self, store):
        """The backfilled lesson must be indistinguishable from a new one."""
        kps = [{"phrase": "leiligheten", "translation": "the apartment"}]
        store.save_lesson("l1", "c1", 1, _lesson(kps))

        backfill_breakdown_provenance(store)

        assert _spans(store.get_lesson("l1")) == _spans(_lesson(kps, strip_provenance=False))

    def test_skips_a_lesson_the_current_code_would_build_differently(self, store):
        """The load-bearing case: stale text must never receive fresh spans."""
        lesson = _lesson([{"phrase": "leiligheten", "translation": "the apartment"}])
        # An older syllabifier's output: `le|i|...` where today's gives `lei|...`.
        lesson.sections[0].phrases[4].text = "i"
        store.save_lesson("l1", "c1", 1, lesson)

        report = backfill_breakdown_provenance(store)

        assert report.updated == 0
        assert [lesson_id for lesson_id, _ in report.skipped] == ["l1"]
        assert _spans(store.get_lesson("l1")) == [], "a skipped lesson must be left alone"

    def test_skips_when_the_section_has_unaccounted_trailing_phrases(self, store):
        lesson = _lesson([{"phrase": "hagen", "translation": "the garden"}])
        lesson.sections[0].phrases.append(Phrase(text="extra", voice_id="v", language_code="no"))
        store.save_lesson("l1", "c1", 1, lesson)

        report = backfill_breakdown_provenance(store)

        assert report.updated == 0
        assert report.skipped[0][1].startswith("trailing")

    def test_skips_a_lesson_with_no_key_phrases_section(self, store):
        lesson = Lesson(
            title="T",
            language_code="no",
            sections=[Section(section_type=SectionType.NATURAL_SPEED, phrases=[])],
            key_phrases=[KeyPhraseInfo(phrase="hagen", translation="the garden")],
        )
        store.save_lesson("l1", "c1", 1, lesson)

        report = backfill_breakdown_provenance(store)

        assert report.updated == 0
        assert report.skipped[0][1] == "no key_phrases section"

    def test_is_idempotent(self, store):
        store.save_lesson("l1", "c1", 1, _lesson([{"phrase": "hagen", "translation": "the garden"}]))

        first = backfill_breakdown_provenance(store)
        second = backfill_breakdown_provenance(store)

        assert first.updated == 1
        assert second.updated == 0, "a second run must not rewrite an already-backfilled lesson"

    def test_reports_per_lesson_across_a_mixed_set(self, store):
        store.save_lesson("good", "c1", 1, _lesson([{"phrase": "hagen", "translation": "the garden"}]))
        stale = _lesson([{"phrase": "leiligheten", "translation": "the apartment"}])
        stale.sections[0].phrases[4].text = "i"
        store.save_lesson("stale", "c1", 2, stale)

        report = backfill_breakdown_provenance(store)

        assert report.updated == 1
        assert report.examined == 2
        assert [lesson_id for lesson_id, _ in report.skipped] == ["stale"]


class TestUpdateLessonDataPreservesRowIdentity:
    """``save_lesson`` is INSERT OR REPLACE — wrong tool for an in-place rewrite.

    It assigns a NEW rowid and resets ``created_at`` to now. ``get_lesson_days``
    resolves "the lesson for day N" as ``MAX(rowid)``, so rewriting every lesson
    through ``save_lesson`` can silently change which one the UI shows on a day
    that has more than one.
    """

    def test_update_keeps_rowid_and_created_at(self, tmp_path):
        store = ContentStore(str(tmp_path / "c.db"))
        lesson = _lesson([{"phrase": "hagen", "translation": "the garden"}])
        store.save_lesson("l1", "c1", 1, lesson)

        before = _row(tmp_path / "c.db", "l1")
        lesson.title = "changed"
        store.update_lesson_data("l1", lesson)
        after = _row(tmp_path / "c.db", "l1")

        assert after["rowid"] == before["rowid"]
        assert after["created_at"] == before["created_at"]
        assert store.get_lesson("l1").title == "changed"

    def test_save_lesson_would_not_have(self, tmp_path):
        """Pins the reason this method exists; delete it and this goes red."""
        store = ContentStore(str(tmp_path / "c.db"))
        lesson = _lesson([{"phrase": "hagen", "translation": "the garden"}])
        store.save_lesson("l1", "c1", 1, lesson)
        store.save_lesson("l2", "c1", 2, lesson)

        before = _row(tmp_path / "c.db", "l1")
        store.save_lesson("l1", "c1", 1, lesson)

        assert _row(tmp_path / "c.db", "l1")["rowid"] != before["rowid"]

    def test_update_returns_false_for_an_unknown_lesson(self, tmp_path):
        store = ContentStore(str(tmp_path / "c.db"))
        assert store.update_lesson_data("nope", _lesson([{"phrase": "hagen", "translation": "g"}])) is False

    def test_update_persists_on_an_in_memory_store(self):
        """The in-memory connection needs its own commit; a file one is autocommitted."""
        store = ContentStore(":memory:")
        lesson = _lesson([{"phrase": "hagen", "translation": "the garden"}])
        store.save_lesson("l1", "c1", 1, lesson)

        lesson.title = "changed"
        assert store.update_lesson_data("l1", lesson) is True
        assert store.get_lesson("l1").title == "changed"


def _row(db_path, lesson_id: str) -> dict:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT rowid, created_at FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
        return {"rowid": row["rowid"], "created_at": row["created_at"]}
    finally:
        conn.close()
