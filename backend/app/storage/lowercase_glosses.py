"""One-shot migration: lowercase ``token_glosses`` keys in stored lessons.

Lessons generated before the fix in ``story.py`` (2026-07-03) have
``token_glosses`` with original-case keys (e.g. ``{"Hvala": "thanks"}``), but
every consumer looks up by lowercase surface/lemma. This migration walks all
stored lessons and lowercases the keys in-place, skipping lessons whose keys
are already all-lowercase.

Run once:

    uv run python -m app.storage.lowercase_glosses

Safe to re-run (idempotent — skips lessons already lowercased).
"""

from __future__ import annotations

import logging

from app.storage.store import ContentStore

logger = logging.getLogger(__name__)


def _is_lowercase(glosses: dict[str, str]) -> bool:
    return all(k == k.lower() for k in glosses)


def _lowercase_keys(glosses: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for k, v in glosses.items():
        lk = k.lower()
        if lk not in result:
            result[lk] = v
    return result


def lowercase_glosses(store: ContentStore) -> int:
    """Lowercase all ``token_glosses`` keys in stored lessons.

    Args:
        store: The content store to walk and mutate.

    Returns:
        Number of lessons whose gloss map was modified.
    """
    updated = 0
    for lesson_id, curriculum_id, day, lesson in store.list_lessons():
        meta = lesson.generation_metadata
        if not meta:
            continue
        glosses = meta.get("token_glosses")
        if not glosses or _is_lowercase(glosses):
            continue
        new_glosses = _lowercase_keys(glosses)
        meta["token_glosses"] = new_glosses
        lesson.generation_metadata = meta
        store.save_lesson(lesson_id, curriculum_id, day, lesson)
        updated += 1
    return updated


def _main() -> None:  # pragma: no cover — CLI wiring, run once
    import argparse

    parser = argparse.ArgumentParser(description="Lowercase token_glosses keys in all stored lessons.")
    parser.parse_args()

    from app.config import settings

    logging.basicConfig(level=logging.INFO)
    store = ContentStore(settings.database_url.replace("sqlite:///", ""))
    try:
        count = lowercase_glosses(store)
        logger.info("Lowercased token_glosses in %d lesson(s)", count)
    finally:
        store.close()


if __name__ == "__main__":  # pragma: no cover — CLI guard
    _main()
