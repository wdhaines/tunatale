"""Repair stale ``collocation_directions.due_date`` values to match ``anki_due``.

Background:
    Prior to today's fix, ``SRSDatabase.upsert_by_guid`` had a ``reps > 0``
    idempotency branch that refreshed Anki-bookkeeping fields (state,
    anki_card_id, anki_due, left, due_at) but left ``due_date`` pinned to the
    first-import value. Every subsequent import advanced ``anki_due`` while
    ``due_date`` rotted. The badge query (``count_review_due_collocations``)
    filters on ``due_date``, so each stale row was counted as due today, every
    day, until it was graded.

This script reads each TT review-state direction with ``anki_card_id`` set,
looks up the live Anki ``cards.queue`` and ``cards.due``, and rewrites
``due_date`` to ``compute_due_date(queue, due, col_crt)`` when it disagrees.

Touches only ``tunatale.db``. The Anki collection is opened read-only.

Usage:
    uv run python -m app.anki.backfill_due_date_from_anki_due [--dry-run]
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from app.anki.safety import safe_open
from app.anki.sqlite_reader import compute_due_at
from app.config import settings


def repair_due_dates(tt_db_path: Path, anki_col_path: Path, *, dry_run: bool = False) -> dict:
    """Recompute ``due_date`` from Anki's live ``cards.due`` for every linked direction.

    Returns a summary dict with ``checked``, ``mismatched``, and ``written``.
    """
    with safe_open(anki_col_path, mode="ro") as ctx:
        anki = ctx.conn
        crt_row = anki.execute("SELECT crt FROM col").fetchone()
        col_crt = int(crt_row[0]) if crt_row else 0

        tt = sqlite3.connect(tt_db_path)
        tt.row_factory = sqlite3.Row
        try:
            rows = tt.execute(
                """
                SELECT collocation_id, direction, due_at, anki_card_id
                FROM collocation_directions
                WHERE anki_card_id IS NOT NULL
                  AND state IN ('review', 'learning', 'relearning', 'buried')
                """
            ).fetchall()

            mismatched: list[tuple[int, str, str, str]] = []
            for r in rows:
                a = anki.execute(
                    "SELECT queue, due, type FROM cards WHERE id = ?",
                    (r["anki_card_id"],),
                ).fetchone()
                if a is None:
                    continue
                queue, due_raw, card_type = a[0], a[1], a[2] or 0
                expected = compute_due_at(queue, due_raw, col_crt, card_type=card_type)
                if expected.isoformat() != r["due_at"]:
                    mismatched.append((r["collocation_id"], r["direction"], r["due_at"], expected.isoformat()))

            written = 0
            if not dry_run:
                for coll_id, direction, _old, new_iso in mismatched:
                    tt.execute(
                        "UPDATE collocation_directions SET due_at = ? WHERE collocation_id = ? AND direction = ?",
                        (new_iso, coll_id, direction),
                    )
                    written += 1
                tt.commit()

            return {"checked": len(rows), "mismatched": len(mismatched), "written": written}
        finally:
            tt.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report counts without writing.")
    args = parser.parse_args(argv)

    tt_db_path = Path(settings.database_url.removeprefix("sqlite:///"))
    anki_col_path = Path(settings.anki_collection_path)

    summary = repair_due_dates(tt_db_path, anki_col_path, dry_run=args.dry_run)
    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(f"[{mode}] checked={summary['checked']} mismatched={summary['mismatched']} written={summary['written']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
