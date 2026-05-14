"""One-shot repair of `introduced_at` stamps left dangling on `state='new'` rows.

The WordSpan word-click endpoint cycled card state through new → learning →
known → ignored → new. When `set_state_by_id` ran in the back-end before
2026-05-14, it wrote the new state value but did not clear `introduced_at` or
`prior_state`. If sync_pull had previously stamped `introduced_at` from Anki's
revlog (because the card had transitioned NEW→REVIEW in Anki), cycling the TT
state back to NEW left an inconsistent row:

  state='new' AND introduced_at IS NOT NULL

These rows inflate `count_new_introduced_today` and skew the daily-new badge
shown on TT vs Anki (TT under-shows, Anki shows the full cap).

The bug is fixed at the source (`set_state_by_id` now clears
`introduced_at`/`prior_state` on state→NEW); this script cleans up the
already-corrupted rows. TT-only write — no Anki tables touched, no safe_open
envelope needed.

Usage::

    uv run python -m app.anki.repair_inconsistent_introduced_at --dry-run
    uv run python -m app.anki.repair_inconsistent_introduced_at
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from app.config import settings


def find_inconsistent_rows(conn: sqlite3.Connection) -> list[tuple[int, str, str | None, str | None]]:
    """Return (collocation_id, direction, introduced_at, prior_state) tuples."""
    rows = conn.execute(
        """
        SELECT collocation_id, direction, introduced_at, prior_state
        FROM collocation_directions
        WHERE state = 'new'
          AND introduced_at IS NOT NULL
        """,
    ).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def apply_repair(conn: sqlite3.Connection) -> int:
    """Clear introduced_at + prior_state on every state='new' row that has them set."""
    rows = find_inconsistent_rows(conn)
    if not rows:
        return 0
    conn.execute(
        """
        UPDATE collocation_directions
        SET introduced_at = NULL, prior_state = NULL
        WHERE state = 'new' AND introduced_at IS NOT NULL
        """,
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
        rows = find_inconsistent_rows(conn)
        if not rows:
            print("No inconsistent introduced_at rows found.")
            return 0
        print(f"Plan: clear introduced_at on {len(rows)} state='new' row(s)")
        for cid, direction, intro, prior in rows:
            print(f"  cid={cid} direction={direction} introduced_at={intro!r} prior_state={prior!r}")
        if args.dry_run:
            print("--dry-run: no changes applied.")
            return 0
        count = apply_repair(conn)
        print(f"Cleared: {count}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
