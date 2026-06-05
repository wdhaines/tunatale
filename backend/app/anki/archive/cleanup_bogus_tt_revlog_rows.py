"""Clean up pre-Stage-0 PK-bug rows in ``tt_revlog``.

One-shot script. Deletes ``tt_revlog`` rows whose ``id`` is below a safe
cutoff that distinguishes the buggy PK shape from real wall-clock-ms IDs.

**The bug** (fixed in Stage 0, commit ``753bc3b``). The first version of
``build_revlog_row`` used the ``DrillRequest.time_ms`` field (elapsed time
the user spent on the card) as the PK. That value lives in [0, 60000] for
normal grades. Real ``revlog.id`` values are wall-clock ms since epoch —
Anki post-dates 2008 so real IDs are above ``1.2 × 10¹²``. Anything below
that cutoff is the buggy shape: the row's ``id`` equals its ``taken_millis``
(which the Stage 0 fix correctly separated).

**Why ``1_000_000_000_000`` as the cutoff.** Not a date but a safe ceiling:
- Above the buggy elapsed-ms range (taken_millis was 0-60000, well under 1e12).
- Below any real wall-clock ms: ``1e12`` ms = 2001-09-09 UTC; Anki collections
  post-date 2008 so every real ``revlog.id`` exceeds ``1.2e12``.
- The 1e12 cutoff leaves ~7 years of slack on each side.

Usage:
    uv run python -m app.anki.cleanup_bogus_tt_revlog_rows [--dry-run] [--verbose]

Idempotent: running again after the first apply finds zero rows and exits clean.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# Wall-clock ms since epoch lower bound for any real ``revlog.id``.
# See module docstring for the cutoff derivation.
BOGUS_ID_CUTOFF = 1_000_000_000_000


def cleanup_bogus_tt_revlog_rows(
    tt_db_path: Path,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict:
    """Delete ``tt_revlog`` rows with ``id < BOGUS_ID_CUTOFF``.

    Returns a summary dict with the deleted count and the affected
    ``(collocation_id, direction)`` pairs.
    """
    tt = sqlite3.connect(str(tt_db_path))
    tt.row_factory = sqlite3.Row
    try:
        rows = tt.execute(
            "SELECT id, collocation_id, direction, button_chosen, taken_millis FROM tt_revlog WHERE id < ? ORDER BY id",
            (BOGUS_ID_CUTOFF,),
        ).fetchall()

        affected_pairs: set[tuple[int, str]] = set()
        for r in rows:
            affected_pairs.add((r["collocation_id"], r["direction"]))
            if verbose:
                logger.info(
                    "bogus row id=%d coll=%d dir=%s ease=%d taken_ms=%d",
                    r["id"],
                    r["collocation_id"],
                    r["direction"],
                    r["button_chosen"],
                    r["taken_millis"],
                )

        if not dry_run and rows:
            tt.execute("DELETE FROM tt_revlog WHERE id < ?", (BOGUS_ID_CUTOFF,))
            tt.commit()

        return {
            "bogus_rows_found": len(rows),
            "bogus_rows_deleted": len(rows) if not dry_run else 0,
            "affected_directions": len(affected_pairs),
        }
    finally:
        tt.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Delete pre-Stage-0 PK-bug rows from tt_revlog.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report counts without deleting.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Log each bogus row's (id, coll, direction, ease, taken_ms).",
    )
    args = parser.parse_args(argv)

    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    tt_db_path = Path(settings.database_url.removeprefix("sqlite:///"))
    summary = cleanup_bogus_tt_revlog_rows(
        tt_db_path,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )
    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(
        f"[{mode}] "
        f"bogus_rows_found={summary['bogus_rows_found']} "
        f"bogus_rows_deleted={summary['bogus_rows_deleted']} "
        f"affected_directions={summary['affected_directions']}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
