"""Backfill UPOS tags on stored lessons' chunk phrases.

Usage:
    cd backend && uv run python scripts/backfill_chunk_upos.py --language no [--apply]

Default is --dry-run: prints what would change but writes nothing.
--apply writes the UPOS tag back into the lesson data via update_lesson_data.
"""

from __future__ import annotations

import argparse
import logging

logger = logging.getLogger(__name__)


def backfill_lesson(
    lesson_id: str,
    lesson: object,
    *,
    dry_run: bool = True,
    srs_db: object | None = None,
    lemmatizer: object | None = None,
    model_version: str = "",
) -> int:
    """Annotate chunk phrases in a single lesson. Returns count tagged.

    Shared between the CLI script and the test harness.
    """
    from app.api.generation import annotate_chunk_upos
    from app.srs.database import SRSDatabase

    if not model_version:
        return 0

    own_db = False
    if srs_db is None:
        srs_db = SRSDatabase(":memory:")
        own_db = True
    try:
        count = annotate_chunk_upos(lesson, srs_db, lemmatizer=lemmatizer, model_version=model_version)  # type: ignore[arg-type]
    finally:
        if own_db:
            srs_db.close()  # type: ignore[union-attr]

    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill UPOS tags on stored lessons")
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    parser.add_argument("--language", default="", help="Language code (default: target_language)")
    args = parser.parse_args()

    dry_run = not args.apply

    from app.api.generation import annotate_chunk_upos
    from app.config import settings
    from app.languages import resolve_db_path
    from app.srs.database import SRSDatabase
    from app.srs.lemmatizer import get_lemmatizer, model_version_for
    from app.storage.store import ContentStore

    language_code = args.language or settings.target_language
    db_path = str(resolve_db_path(language_code, settings))
    store = ContentStore(db_path)
    lessons = store.list_lessons()

    total_tagged = 0
    total_lessons = 0

    for lesson_id, _curriculum_id, _day, lesson in lessons:
        language_code = lesson.language_code
        lemmatizer = get_lemmatizer(language_code)
        mv = model_version_for(lemmatizer)
        if not mv:
            print(f"  {lesson_id}: no model version, skipping")
            continue

        total_lessons += 1

        with SRSDatabase(":memory:") as srs_db:
            count = annotate_chunk_upos(lesson, srs_db, lemmatizer=lemmatizer, model_version=mv)

        total_tagged += count

        total_chunks = sum(1 for s in lesson.sections for p in s.phrases if p.source_word is not None)
        print(f"  {lesson_id}: {count} phrases tagged, {total_chunks - count} chunks left untagged")

        if not dry_run and count > 0:
            store.update_lesson_data(lesson_id, lesson)

    mode = "dry-run" if dry_run else "APPLIED"
    print(f"\n{mode}: {total_tagged} phrases tagged across {total_lessons} lessons")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
