"""One-shot migration: backfill CurriculumDay.title from the latest lesson.

Before the fix in generation/pipeline.py, api/generation.py, and
storage/lesson_io.py, regenerating or re-importing a lesson with a new title
updated Lesson.title (lessons.data_json) but never synced the change to
CurriculumDay.title (curricula.data_json). The curriculum page therefore showed
the stale planner title instead of the current lesson title.

This function walks every curriculum, compares each day's title against its
latest lesson, and updates the curriculum when they differ.

Safe to re-run (idempotent — skips curricula whose days already match).
"""

from __future__ import annotations

from app.storage.store import ContentStore


def backfill_curriculum_day_titles(store: ContentStore) -> int:
    """Sync CurriculumDay.title from each day's latest lesson.

    Args:
        store: The content store to walk and mutate.

    Returns:
        Number of curricula whose data_json was rewritten.
    """
    updated = 0
    for entry in store.list_curricula():
        curriculum = store.get_curriculum(entry["id"])
        if curriculum is None:
            continue
        dirty = False
        for day in curriculum.days:
            latest = store.get_latest_lesson_by_day(entry["id"], day.day)
            if latest is None:
                continue
            _, lesson = latest
            if lesson.title != day.title:
                day.title = lesson.title
                dirty = True
        if dirty:
            store.save_curriculum(entry["id"], curriculum)
            updated += 1
    return updated
