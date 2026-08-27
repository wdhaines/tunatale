#!/usr/bin/env python3
"""Hand-run detector for stored captions that no longer match their own span.

A boundary-rule improvement can stale-invalidate a stored lesson: its caption
text names one set of letters while its ``syllable_span`` now denotes another
(``etterforskningsteamet`` span=(4,5) text='team' now denotes 'tea'). ``plan_chunk``
refuses the mismatch, so the chunk silently degrades to plain synthesis. This
tool makes that staleness LOUD — it is a REPORT only; the remedy (re-rendering
the affected lessons' audio) is a separate, hand-run human decision.

It loads the lessons for the configured language read-only, calls the pure
:func:`~app.storage.caption_staleness.find_stale_captions` for each, prints a
readable report, and exits non-zero when anything is stale.

NOT wired into ``test.sh`` or CI: CI has no lesson database, so the check would
be vacuous there — it would pass by finding nothing.

Usage::

    uv run python scripts/check_caption_staleness.py [--language CODE]

Exit 0 = no stale or unbreakable captions. Exit 1 = at least one.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

from app.config import settings
from app.languages import resolve_db_path
from app.models.lesson import Lesson
from app.storage.caption_staleness import find_stale_captions


def _load_lessons(db_path) -> list[tuple[str, Lesson]]:
    """Every stored lesson as ``(lesson_id, Lesson)``, read-only, no schema writes."""

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT id, data_json FROM lessons ORDER BY created_at").fetchall()
        return [(row[0], Lesson.from_json(row[1])) for row in rows]
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--language", default=settings.target_language, help="language code (default: target_language)")
    args = ap.parse_args(argv)

    db_path = resolve_db_path(args.language, settings)
    if not db_path.exists():
        print(f"check_caption_staleness: no database at {db_path}; nothing to check.", file=sys.stderr)
        return 0

    total: list = []
    unbreakable: list = []
    checkable = 0
    for lesson_id, lesson in _load_lessons(db_path):
        checkable += sum(
            1
            for section in lesson.sections
            for phrase in section.phrases
            if phrase.source_word is not None and phrase.syllable_span is not None
        )
        stale, unbreak = find_stale_captions(lesson_id, lesson)
        total.extend(stale)
        unbreakable.extend(unbreak)

    for record in unbreakable:
        print(
            f"UNBREAKABLE  {record.lesson_id} section {record.section_index} phrase {record.phrase_index} "
            f"{record.source_word!r} span={record.syllable_span} text={record.stored_text!r} "
            "-- word no longer syllabifies"
        )
    for record in total:
        print(
            f"STALE        {record.lesson_id} section {record.section_index} phrase {record.phrase_index} "
            f"{record.source_word!r} span={record.syllable_span} text={record.stored_text!r} now {record.now_text!r}"
        )

    print(f"{db_path}: {checkable} checkable chunks, {len(total)} stale, {len(unbreakable)} unbreakable")
    return 1 if total or unbreakable else 0


if __name__ == "__main__":
    sys.exit(main())
