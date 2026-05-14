"""One-shot reset of cloze rows auto-graded by the pre-Layer-1 /listen endpoint.

Before the Layer 1 /listen redesign (May 2026), clicking "Mark as Listened"
created cloze rows for function words and immediately auto-graded them with
a Good rating, advancing their state to 'learning' (or 'relearning'). This
inflated `count_new_introduced_today` and created the 16-vs-30 divergence
between TT and Anki new-card counts.

This script finds and resets those rows to TT-default new state, making them
eligible for proper introduction via the next sync_create_new cycle. TT-only
write — no Anki tables touched, no safe_open envelope needed.

Usage::

    uv run python -m app.anki.reset_listen_graded_clozes --dry-run
    uv run python -m app.anki.reset_listen_graded_clozes
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

from app.config import settings

_RESET_COLS = """
    state = 'new',
    reps = 0,
    lapses = 0,
    last_review = NULL,
    last_review_time_ms = 0,
    introduced_at = NULL,
    prior_state = NULL,
    prior_left = NULL,
    prior_stability = NULL,
    left = NULL,
    due_at = NULL,
    stability = 1.0,
    fsrs_difficulty = 5.0,
    due_date = ?,
    dirty_fsrs = 0
"""


def find_graded_cloze_rows(conn: sqlite3.Connection) -> list[tuple[int, int, str]]:
    """Return (dir_id, collocation_id, lemma) for graded cloze rows.

    Matches collocation_directions rows belonging to cloze collocations
    where state is 'learning' or 'relearning' and the collocation has no
    anki_note_id (not yet synced).
    """
    rows = conn.execute(
        """
        SELECT d.rowid, c.id, c.lemma
        FROM collocation_directions d
        JOIN collocations c ON c.id = d.collocation_id
        WHERE c.card_type = 'cloze'
          AND c.anki_note_id IS NULL
          AND d.state IN ('learning', 'relearning')
        """,
    ).fetchall()
    return [(r[0], r[1], r[2] if r[2] is not None else "") for r in rows]


def apply_reset(conn: sqlite3.Connection) -> int:
    """Reset every graded cloze row to TT-default new state. Returns count."""
    today = date.today().isoformat()
    rows = find_graded_cloze_rows(conn)
    if not rows:
        return 0
    dir_ids = [r[0] for r in rows]
    placeholders = ",".join("?" for _ in dir_ids)
    conn.execute(
        f"UPDATE collocation_directions SET {_RESET_COLS} WHERE rowid IN ({placeholders})",
        (today, *dir_ids),
    )
    conn.execute(
        "UPDATE collocations SET updated_at = datetime('now') WHERE id IN "
        f"(SELECT collocation_id FROM collocation_directions WHERE rowid IN ({placeholders}))",
        (*dir_ids,),
    )
    conn.commit()
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    parser.add_argument("--dry-run", action="store_true", help="show plan without writing")
    parser.add_argument("--tt-db", type=Path, default=None, help="override TT database path")
    args = parser.parse_args(argv)

    tt_path = args.tt_db or Path(settings.database_url.removeprefix("sqlite:///"))
    if not tt_path.exists():
        print(f"TT database not found: {tt_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    try:
        rows = find_graded_cloze_rows(conn)
        if not rows:
            print("No graded cloze rows found.")
            return 0
        print(f"Plan: reset {len(rows)} graded cloze row(s)")
        for dir_id, cid, lemma in rows:
            print(f"  dir_id={dir_id} collocation_id={cid} lemma={lemma!r}")
        if args.dry_run:
            print("--dry-run: no changes applied.")
            return 0
        count = apply_reset(conn)
        print(f"Reset: {count}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
