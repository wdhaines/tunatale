"""One-shot migration: flip cloze collocation directions from recognition→production.

Prior to this migration, Phase F cloze items were created with the RECOGNITION
direction only. The correct direction for cloze cards is PRODUCTION (the user
must produce the target word from the L1 + context, not just recognise it).

This script scans TT's collocation_directions table, finds cloze collocations
that only have a recognition row, and flips them to production.

No Anki writes. No col.scm bump.

Usage::

    uv run python -m app.anki.migrate_cloze_to_production --dry-run
    uv run python -m app.anki.migrate_cloze_to_production
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from app.config import settings


def _get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def run_migration(
    db: sqlite3.Connection,
    dry_run: bool = False,
) -> dict[str, int]:
    flipped = 0
    skipped_both = 0
    skipped_vocab = 0

    rows = db.execute("""
        SELECT c.id AS coll_id, c.text
        FROM collocations c
        JOIN collocation_directions cd ON cd.collocation_id = c.id
        WHERE c.card_type = 'cloze'
          AND cd.direction = 'recognition'
    """).fetchall()

    for row in rows:
        coll_id = row["coll_id"]

        # Check if a production row already exists for this collocation
        prod_exists = db.execute(
            "SELECT 1 FROM collocation_directions WHERE collocation_id = ? AND direction = 'production'",
            (coll_id,),
        ).fetchone()

        if prod_exists:
            skipped_both += 1
            continue

        if not dry_run:
            db.execute(
                "UPDATE collocation_directions SET direction = 'production' WHERE collocation_id = ? AND direction = 'recognition'",
                (coll_id,),
            )
        flipped += 1

    if not dry_run:
        db.commit()

    return {
        "flipped": flipped,
        "skipped_both_directions": skipped_both,
        "skipped_vocab": skipped_vocab,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate cloze directions to production")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Print counts without writing")
    args = parser.parse_args()

    db_url = settings.database_url
    if not db_url or db_url == "sqlite:///:memory:" or not db_url.startswith("sqlite:///"):
        print(f"SRS DB URL not configured or is :memory: ({db_url})", file=sys.stderr)
        return 1

    db_path = db_url.removeprefix("sqlite:///")
    db_path_obj = Path(db_path)
    if not db_path_obj.exists():
        print(f"SRS DB not found: {db_path}", file=sys.stderr)
        return 1

    db = _get_conn(db_path)
    try:
        result = run_migration(db, dry_run=args.dry_run)
    finally:
        db.close()

    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
