"""One-shot backfill: populate `sentence_translation` on existing cloze rows.

Pre-Layer-N (commit 5b8f6c5) lessons were stored without
``generation_metadata['sentence_translations']``, so every cloze card created
from those lessons was stamped with an empty ``sentence_translation``. This
script recovers the SL→EN sentence map from each lesson's TRANSLATED section,
persists it to ``generation_metadata``, then walks cloze collocations whose
``sentence_translation`` is still empty and fills them in. Marks each updated
row's ``dirty_fields`` so a subsequent ``sync_push`` rewrites the corresponding
Anki note's ``Back Extra`` field.

Punctuation-stripped fallback: cloze rows sometimes carry ``source_sentence``
without trailing ``?`` / ``.`` even when the source TRANSLATED phrase has them
(an artifact of an older normalization step that no longer runs). We try an
exact match first, then a normalized-key match.

TT-only writes — no Anki tables touched, no safe_open envelope needed. To
propagate to Anki: close Anki, run this script, then run sync_push.

Usage::

    uv run python -m app.anki.backfill_cloze_sentence_translations --dry-run
    uv run python -m app.anki.backfill_cloze_sentence_translations
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.models.lesson import extract_sentence_translations_from_translated
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore

_TRAILING_PUNCT_RE = re.compile(r"[.?!,;:\s]+$")


def _norm(s: str) -> str:
    return _TRAILING_PUNCT_RE.sub("", (s or "").strip()).strip()


@dataclass
class LessonUpdate:
    lesson_id: str
    new_pairs: dict[str, str]


@dataclass
class ClozeUpdate:
    cid: int
    guid: str
    text: str
    source_sentence: str
    new_sentence_translation: str


@dataclass
class BackfillPlan:
    lesson_updates: list[LessonUpdate] = field(default_factory=list)
    cloze_updates: list[ClozeUpdate] = field(default_factory=list)
    cloze_unmatched: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class BackfillResult:
    lessons_updated: int
    cloze_updated: int
    cloze_unmatched: int


def _list_all_lessons(store: ContentStore) -> list[tuple[str, str, int]]:
    with store._get_conn() as conn:  # noqa: SLF001 — internal access acceptable for a one-shot
        rows = conn.execute("SELECT id, curriculum_id, day FROM lessons ORDER BY rowid ASC").fetchall()
    return [(r["id"], r["curriculum_id"], r["day"]) for r in rows]


def plan_backfill(db: SRSDatabase, store: ContentStore) -> BackfillPlan:
    """Compute everything that would be written, without touching the DB."""
    plan = BackfillPlan()

    # Build merged sentence_translations across all lessons; first occurrence wins.
    merged_exact: dict[str, str] = {}
    merged_norm: dict[str, str] = {}
    for lesson_id, _curr, _day in _list_all_lessons(store):
        lesson = store.get_lesson(lesson_id)
        if lesson is None:
            continue
        # Merge any pre-existing sentence_translations metadata first (it wins
        # over derived).
        existing_meta = lesson.generation_metadata.get("sentence_translations", {})
        for k, v in existing_meta.items():
            merged_exact.setdefault(k, v)
            merged_norm.setdefault(_norm(k), v)
        # Then derive from TRANSLATED section
        derived = extract_sentence_translations_from_translated(lesson)
        new_pairs: dict[str, str] = {}
        for k, v in derived.items():
            if k not in existing_meta:
                new_pairs[k] = v
            merged_exact.setdefault(k, v)
            merged_norm.setdefault(_norm(k), v)
        if new_pairs:
            plan.lesson_updates.append(LessonUpdate(lesson_id=lesson_id, new_pairs=new_pairs))

    # Walk cloze rows with empty sentence_translation
    with db._get_conn() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT id, guid, text, source_sentence FROM collocations "
            "WHERE card_type = 'cloze' AND (sentence_translation IS NULL OR sentence_translation = '')"
        ).fetchall()
    for row in rows:
        sent = row["source_sentence"] or ""
        translation = merged_exact.get(sent) or merged_norm.get(_norm(sent), "")
        if translation:
            plan.cloze_updates.append(
                ClozeUpdate(
                    cid=row["id"],
                    guid=row["guid"],
                    text=row["text"],
                    source_sentence=sent,
                    new_sentence_translation=translation,
                )
            )
        else:
            plan.cloze_unmatched.append((row["text"], sent))

    return plan


def apply_backfill(db: SRSDatabase, store: ContentStore, *, dry_run: bool) -> BackfillResult:
    plan = plan_backfill(db, store)
    if not dry_run:
        # Persist merged sentence_translations into each lesson's metadata.
        for upd in plan.lesson_updates:
            lesson = store.get_lesson(upd.lesson_id)
            if lesson is None:
                continue
            existing = lesson.generation_metadata.setdefault("sentence_translations", {})
            for k, v in upd.new_pairs.items():
                existing.setdefault(k, v)
            with store._get_conn() as conn:  # noqa: SLF001
                row = conn.execute("SELECT curriculum_id, day FROM lessons WHERE id = ?", (upd.lesson_id,)).fetchone()
            if row is None:
                continue
            store.save_lesson(upd.lesson_id, row["curriculum_id"], row["day"], lesson)

        # Update each cloze row + mark dirty.
        for cu in plan.cloze_updates:
            db.set_sentence_translation_dirty(cu.guid, cu.new_sentence_translation)

    return BackfillResult(
        lessons_updated=len(plan.lesson_updates),
        cloze_updated=len(plan.cloze_updates),
        cloze_unmatched=len(plan.cloze_unmatched),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    parser.add_argument("--dry-run", action="store_true", help="show plan without writing")
    parser.add_argument("--tt-db", type=Path, default=None, help="override TT database path")
    args = parser.parse_args(argv)

    tt_path = args.tt_db or Path(settings.database_url.removeprefix("sqlite:///"))
    if not tt_path.exists():
        print(f"TT database not found: {tt_path}", file=sys.stderr)
        return 1

    db = SRSDatabase(str(tt_path))
    store = ContentStore(str(tt_path))
    plan = plan_backfill(db, store)

    print(f"Plan: update {len(plan.lesson_updates)} lesson(s), {len(plan.cloze_updates)} cloze row(s)")
    for upd in plan.lesson_updates:
        print(f"  lesson {upd.lesson_id}: +{len(upd.new_pairs)} sentence translation(s)")
    for cu in plan.cloze_updates:
        print(f"  cloze cid={cu.cid} text={cu.text!r} source={cu.source_sentence!r} -> {cu.new_sentence_translation!r}")
    if plan.cloze_unmatched:
        print(f"\nUnmatched cloze rows ({len(plan.cloze_unmatched)}) — no TRANSLATED-section source:")
        for text, sent in plan.cloze_unmatched:
            print(f"  text={text!r} source={sent!r}")
    if args.dry_run:
        print("\n--dry-run: no changes applied.")
        return 0

    result = apply_backfill(db, store, dry_run=False)
    print(
        f"\nApplied: lessons={result.lessons_updated} cloze={result.cloze_updated} unmatched={result.cloze_unmatched}"
    )
    print("\nNext step: close Anki, then run `uv run python -m app.api.anki sync` (or sync via the UI)")
    print("to push the new Back Extra fields to your Anki notes.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
