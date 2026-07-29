"""Tests for deleting ONE lesson version.

``delete_lessons_for_day`` deletes every version for a curriculum day, which is
right for "regenerate this day" and useless for "drop the superseded copy".
A day can hold several lesson rows — `get_lesson_days` surfaces only
``MAX(rowid)``, so the others are invisible in the UI while still holding audio
on disk — and until now there was no way to remove one without taking the live
lesson (and its review/listen history) with it.

The store returns the audio paths rather than unlinking them, matching
``render_service.render_lesson_audio``: the storage layer owns rows, the caller
owns the filesystem.
"""

from __future__ import annotations

import pytest

from app.models.lesson import Lesson, Section, SectionType
from app.storage.store import ContentStore


def _lesson(title: str = "T") -> Lesson:
    return Lesson(
        title=title,
        language_code="no",
        sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=[])],
    )


@pytest.fixture
def store(tmp_path):
    return ContentStore(str(tmp_path / "c.db"))


class TestDeleteLesson:
    def test_removes_only_the_named_version(self, store):
        """The load-bearing case: two versions on one day, one must survive."""
        store.save_lesson("old", "c1", 4, _lesson("old"))
        store.save_lesson("new", "c1", 4, _lesson("new"))

        store.delete_lesson("old")

        assert store.get_lesson("old") is None
        assert store.get_lesson("new") is not None

    def test_leaves_the_days_surviving_lesson_addressable(self, store):
        store.save_lesson("old", "c1", 4, _lesson("old"))
        store.save_lesson("new", "c1", 4, _lesson("new"))

        store.delete_lesson("old")

        assert [d["lesson_id"] for d in store.get_lesson_days("c1")] == ["new"]

    def test_returns_the_audio_paths_it_orphaned(self, store):
        store.save_lesson("l1", "c1", 1, _lesson())
        store.save_audio_file("a1", "l1", "/tmp/a1.opus")
        store.save_audio_file("a2", "l1", "/tmp/a2.opus", section_index=0, section_type="key_phrases")

        paths = store.delete_lesson("l1")

        assert sorted(paths) == ["/tmp/a1.opus", "/tmp/a2.opus"]

    def test_removes_the_audio_rows_too(self, store):
        store.save_lesson("l1", "c1", 1, _lesson())
        store.save_audio_file("a1", "l1", "/tmp/a1.opus")

        store.delete_lesson("l1")

        assert store.list_audio_files_for_lesson("l1") == []

    def test_does_not_touch_another_lessons_audio(self, store):
        store.save_lesson("l1", "c1", 1, _lesson())
        store.save_lesson("l2", "c1", 2, _lesson())
        store.save_audio_file("a1", "l1", "/tmp/a1.opus")
        store.save_audio_file("a2", "l2", "/tmp/a2.opus")

        store.delete_lesson("l1")

        assert [r["id"] for r in store.list_audio_files_for_lesson("l2")] == ["a2"]

    def test_unknown_lesson_returns_empty_and_does_not_raise(self, store):
        assert store.delete_lesson("nope") == []

    def test_works_on_an_in_memory_store(self):
        store = ContentStore(":memory:")
        store.save_lesson("l1", "c1", 1, _lesson())

        store.delete_lesson("l1")

        assert store.get_lesson("l1") is None
