#!/usr/bin/env python
"""Delete the rows that point at a card which no longer exists (tunatale-vpn).

    uv run python scripts/clean_fk_orphans.py --db <path/to/tunatale_xx.db>            # dry run
    uv run python scripts/clean_fk_orphans.py --db <path/to/tunatale_xx.db> --apply

The app cannot create these: every SRS connection turns ``foreign_keys`` on and
both relationships cascade on delete. They come from one-off scripts that opened
the DB with a raw ``sqlite3.connect`` (foreign keys OFF) and deleted collocations
by hand. The restore drill reports them as "pre-existing FK orphan row(s)".

What is listed is exactly what ``PRAGMA foreign_key_check`` reports, so no
relationship can be missed by a hand-written join. Only rows of the tables in
``CLEANABLE`` are deleted — a review-log entry or media row of a card that is
gone, which nothing reads. A violation in any other table needs a diagnosis, not
a delete, so it is printed and ``--apply`` refuses.

Media FILES are never touched: the same file can back an Anki note, and a stray
file costs kilobytes while a wrongly deleted one costs a picture.

Run it on the LIVE side (``./switch.sh status``), once per learner DB, and back
the DB up first.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

CLEANABLE = frozenset({"tt_revlog", "media"})


def find_orphans(conn: sqlite3.Connection) -> list[tuple[str, int, str]]:
    """``(table, rowid, parent)`` for every foreign-key violation in the DB."""
    return [(table, rowid, parent) for table, rowid, parent, _fkid in conn.execute("PRAGMA foreign_key_check")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, required=True, help="the TT database file")
    parser.add_argument("--apply", action="store_true", help="delete (default: dry run)")
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"No database at {args.db}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(args.db)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        orphans = find_orphans(conn)
        counts = Counter((table, parent) for table, _rowid, parent in orphans)
        for (table, parent), n in sorted(counts.items()):
            print(f"  {table} -> {parent}: {n}")
        print(f"{len(orphans)} orphan row(s).")
        other = sorted({table for table, _rowid, _parent in orphans} - CLEANABLE)
        if other:
            print(f"REFUSING: violations in {', '.join(other)} need a diagnosis, not a delete.", file=sys.stderr)
            return 2
        if not args.apply or not orphans:
            if orphans:
                print("DRY RUN — nothing written. Re-run with --apply.")
            return 0
        for table, rowid, _parent in orphans:
            conn.execute(f"DELETE FROM {table} WHERE rowid = ?", (rowid,))  # noqa: S608 — table is from CLEANABLE
        conn.commit()
        left = len(find_orphans(conn))
        print(f"Deleted {len(orphans)} row(s); {left} violation(s) left.")
        return 0 if left == 0 else 3
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
