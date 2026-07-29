"""Tests for the one-shot KEY_PHRASES resync.

Distinct from ``backfill_breakdown_provenance``, and deliberately so. The
backfill is conservative: it writes provenance ONLY where recomputation
reproduces the stored text, so it can never disturb a lesson whose audio says
something else. This one *replaces* the stored breakdown with what the current
builder produces — text included — so it is only correct when the caller also
re-renders the audio. It changes what the lesson says.

It exists because the Norwegian syllabifier has been fixed twice since the
older lessons were built (``le|i|lig|het|en`` -> ``lei|lig|het|en`` for the
diphthong, ``mi|stenkt`` -> ``mis|tenkt``) and the ``de`` -> ``deh`` respelling
was dropped. Those lessons cannot be improved by a migration that refuses to
touch text.

The story, the key phrases and every other section are left alone: only the
KEY_PHRASES section is rebuilt, and only from ``lesson.key_phrases``, which is
the same input the generator used.
"""

from __future__ import annotations

import pytest

from app.generation.section_builder import build_key_phrases_section
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.storage.resync_key_phrases import resync_key_phrases
from app.storage.store import ContentStore

_FEMALE = "nb-NO-PernilleNeural"
_NARRATOR = "en-US-GuyNeural"
_KPS = [{"phrase": "leiligheten", "translation": "the apartment"}]


def _lesson(key_phrases=None, *, extra_section: bool = False) -> Lesson:
    kps = key_phrases or _KPS
    sections = [build_key_phrases_section(kps, {"female-1": _FEMALE}, _NARRATOR, "no")]
    if extra_section:
        sections.append(
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text="untouched", voice_id=_FEMALE, language_code="no")],
            )
        )
    return Lesson(
        title="T",
        language_code="no",
        sections=sections,
        key_phrases=[KeyPhraseInfo(phrase=kp["phrase"], translation=kp["translation"]) for kp in kps],
    )


def _stale(lesson: Lesson) -> Lesson:
    """Rewrite the section the way an older syllabifier would have."""
    section = lesson.sections[0]
    section.phrases[4].text = "i"
    for phrase in section.phrases:
        phrase.source_word = None
        phrase.syllable_span = None
    return lesson


@pytest.fixture
def store(tmp_path):
    return ContentStore(str(tmp_path / "c.db"))


def _texts(lesson: Lesson) -> list[str]:
    return [p.text for s in lesson.sections if s.section_type == SectionType.KEY_PHRASES for p in s.phrases]


class TestResync:
    def test_rewrites_stale_text_to_the_current_builder(self, store):
        store.save_lesson("l1", "c1", 1, _stale(_lesson()))

        report = resync_key_phrases(store)

        assert report.updated == 1
        assert _texts(store.get_lesson("l1")) == _texts(_lesson())
        assert "i" not in _texts(store.get_lesson("l1"))

    def test_attaches_provenance_at_the_same_time(self, store):
        store.save_lesson("l1", "c1", 1, _stale(_lesson()))

        resync_key_phrases(store)

        spans = [p.syllable_span for s in store.get_lesson("l1").sections for p in s.phrases]
        assert any(s is not None for s in spans)

    def test_preserves_the_voices_of_the_stored_section(self, store):
        """Rebuilding must not silently re-voice a lesson."""
        store.save_lesson("l1", "c1", 1, _stale(_lesson()))

        resync_key_phrases(store)

        section = store.get_lesson("l1").sections[0]
        assert section.phrases[0].voice_id == _NARRATOR
        l2 = [p for p in section.phrases if p.language_code == "no"]
        assert l2 and all(p.voice_id == _FEMALE for p in l2)

    def test_leaves_other_sections_alone(self, store):
        store.save_lesson("l1", "c1", 1, _stale(_lesson(extra_section=True)))

        resync_key_phrases(store)

        other = [s for s in store.get_lesson("l1").sections if s.section_type == SectionType.NATURAL_SPEED][0]
        assert [p.text for p in other.phrases] == ["untouched"]

    def test_is_idempotent(self, store):
        store.save_lesson("l1", "c1", 1, _stale(_lesson()))

        first = resync_key_phrases(store)
        second = resync_key_phrases(store)

        assert (first.updated, second.updated) == (1, 0)

    def test_dry_run_reports_without_writing(self, store):
        store.save_lesson("l1", "c1", 1, _stale(_lesson()))

        report = resync_key_phrases(store, dry_run=True)

        assert report.updated == 1
        assert "i" in _texts(store.get_lesson("l1")), "dry run must not touch the store"
        assert report.diffs["l1"], "a dry run is only useful if it says what would change"

    def test_reports_the_text_diff(self, store):
        store.save_lesson("l1", "c1", 1, _stale(_lesson()))

        report = resync_key_phrases(store, dry_run=True)

        # The planted staleness must show up on the "stored" side of the diff,
        # paired with whatever today's builder puts at that position.
        stale_side = [old for old, _ in report.diffs["l1"]]
        assert "i" in stale_side, report.diffs["l1"]

    def test_skips_a_lesson_with_no_key_phrases_section(self, store):
        lesson = Lesson(
            title="T",
            language_code="no",
            sections=[Section(section_type=SectionType.NATURAL_SPEED, phrases=[])],
            key_phrases=[KeyPhraseInfo(phrase="hagen", translation="the garden")],
        )
        store.save_lesson("l1", "c1", 1, lesson)

        assert resync_key_phrases(store).updated == 0

    def test_skips_a_lesson_with_no_key_phrases_at_all(self, store):
        """Nothing to rebuild from — rebuilding would empty the section."""
        lesson = _lesson()
        lesson.key_phrases = []
        store.save_lesson("l1", "c1", 1, lesson)

        report = resync_key_phrases(store)

        assert report.updated == 0
        assert _texts(store.get_lesson("l1")), "the section must not be emptied"
