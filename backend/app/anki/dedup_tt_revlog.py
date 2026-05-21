"""Remove duplicate tt_revlog rows created by Anki-import copying events TT already recorded.

TT-grade rows are written by Stage 0 (``append_revlog``) at grade time.
Later, sync_pull's ``_ingest_anki_revlog_for_card`` (or the Stage 1 bootstrap)
copies the **same event** from Anki's revlog into tt_revlog, but with a different
ID (different millisecond timestamp).  This double-counts the event during replay.

The dedup rule: for each ``(collocation_id, direction)``, adjacent rows within
5000 ms with the same ``button_chosen`` are duplicates.  Keep the earlier one
(the TT-grade row), delete the later one (the Anki-import copy).

Usage:
    uv run python -m app.anki.dedup_tt_revlog [--dry-run]
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from app.config import settings

_WINDOW_MS = 5000


def dedup_tt_revlog(tt_db_path: Path, *, dry_run: bool = False) -> dict:
    tt = sqlite3.connect(str(tt_db_path))
    tt.row_factory = sqlite3.Row
    try:
        # For every direction with >1 row, find adjacent duplicates
        dirs = tt.execute("""
            SELECT collocation_id, direction
            FROM tt_revlog
            GROUP BY collocation_id, direction
            HAVING COUNT(*) > 1
        """).fetchall()

        total_deleted = 0
        total_pairs = 0
        deleted_details: list[dict] = []

        for cid, d in dirs:
            rows = tt.execute(
                "SELECT id, collocation_id, direction, button_chosen, review_kind, anki_card_id "
                "FROM tt_revlog WHERE collocation_id = ? AND direction = ? ORDER BY id",
                (cid, d),
            ).fetchall()

            # Sliding window: for each row, scan forward for any same-ease row
            # within _WINDOW_MS. Rows can be non-adjacent after sort-by-id when
            # an unrelated revlog event lands between a TT-grade and its Anki
            # copy. Keep the earliest of each cluster; mark the rest for deletion.
            to_delete: set[int] = set()
            for i in range(len(rows)):
                r1 = rows[i]
                if r1["id"] in to_delete:
                    continue
                for j in range(i + 1, len(rows)):
                    r2 = rows[j]
                    gap = r2["id"] - r1["id"]
                    if gap >= _WINDOW_MS:
                        break  # sorted by id; further rows are out of window
                    if r2["id"] in to_delete or r1["button_chosen"] != r2["button_chosen"]:
                        continue
                    to_delete.add(r2["id"])
                    total_pairs += 1
                    if len(deleted_details) < 10:
                        deleted_details.append(
                            {
                                "collocation_id": cid,
                                "direction": d,
                                "keep_id": r1["id"],
                                "delete_id": r2["id"],
                                "gap_ms": gap,
                                "button_chosen": r1["button_chosen"],
                            }
                        )

            if to_delete and not dry_run:
                for del_id in to_delete:
                    tt.execute("DELETE FROM tt_revlog WHERE id = ?", (del_id,))
                tt.commit()

            total_deleted += len(to_delete)

        return {
            "deleted": total_deleted,
            "pairs_found": total_pairs,
            "details": deleted_details,
        }
    finally:
        tt.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dedup tt_revlog rows.")
    parser.add_argument("--dry-run", action="store_true", help="Report without deleting.")
    args = parser.parse_args(argv)

    tt_db_path = Path(settings.database_url.removeprefix("sqlite:///"))
    result = dedup_tt_revlog(tt_db_path, dry_run=args.dry_run)

    mode = "DRY-RUN" if args.dry_run else "DELETED"
    print(f"[{mode}] {result['deleted']} duplicate rows removed ({result['pairs_found']} adjacent pairs found)")
    for d in result["details"]:
        print(
            f"  coll={d['collocation_id']} dir={d['direction']}: keep id={d['keep_id']} delete id={d['delete_id']} gap={d['gap_ms']}ms ease={d['button_chosen']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
