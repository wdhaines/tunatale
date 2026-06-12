"""Bootstrap ``tt_revlog`` from Anki revlog and TT state.

One-shot script run after Stage 0 has landed. For every direction with an
``anki_card_id``, copies all matching Anki ``revlog`` rows into ``tt_revlog``.
For TT-only directions (no ``anki_card_id``, ``reps > 0``), writes a single
synthetic row so every non-zero-reps direction has at least one revlog entry.

Usage:
    uv run python -m app.anki.bootstrap_tt_revlog [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path

from app.anki.safety import safe_open
from app.config import settings

logger = logging.getLogger(__name__)


def bootstrap_tt_revlog(
    tt_db_path: Path,
    anki_col_path: Path,
    *,
    dry_run: bool = False,
) -> dict:
    """Copy Anki revlog into tt_revlog for every linked direction.

    Returns a summary dict with counts of linked directions, anki rows,
    tt-only directions, synthetic rows, and orphan directions.
    """
    with safe_open(anki_col_path, mode="ro") as ctx:
        anki = ctx.conn
        tt = sqlite3.connect(str(tt_db_path))
        tt.row_factory = sqlite3.Row
        try:
            linked_directions = 0
            anki_rows = 0
            tt_only_directions = 0
            synthetic_rows = 0
            errors: list[str] = []

            # ── Part A: Anki-linked directions ──
            linked = tt.execute("""
                SELECT cd.collocation_id, cd.direction, cd.anki_card_id,
                       c.card_type
                FROM collocation_directions cd
                JOIN collocations c ON c.id = cd.collocation_id
                WHERE cd.anki_card_id IS NOT NULL
            """).fetchall()

            linked_directions = len(linked)
            for r in linked:
                card_type = r["card_type"] or "vocab"
                anki_card_id = r["anki_card_id"]
                dir_str = r["direction"]

                card = anki.execute(
                    "SELECT ord FROM cards WHERE id = ?",
                    (anki_card_id,),
                ).fetchone()
                if card is None:
                    errors.append(f"Anki card {anki_card_id} not found for collocation {r['collocation_id']}")
                    continue

                ord_ = card[0]
                expected_dir = "production" if card_type == "cloze" else "recognition" if ord_ == 0 else "production"

                if expected_dir != dir_str:
                    logger.warning(
                        "Direction mismatch for collocation %d: "
                        "collocation_directions says '%s' but Anki ord=%d "
                        "card_type='%s' => '%s' — using TT's value",
                        r["collocation_id"],
                        dir_str,
                        ord_,
                        card_type,
                        expected_dir,
                    )

                revlog_rows = anki.execute(
                    "SELECT id, ease, ivl, lastIvl, factor, time, type FROM revlog WHERE cid = ? ORDER BY id",
                    (anki_card_id,),
                ).fetchall()

                anki_rows += len(revlog_rows)
                if not dry_run:
                    for rev in revlog_rows:
                        tt.execute(
                            """INSERT OR IGNORE INTO tt_revlog
                               (id, collocation_id, direction, button_chosen,
                                interval, last_interval, factor, taken_millis,
                                review_kind, anki_card_id)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                rev["id"],
                                r["collocation_id"],
                                dir_str,
                                rev["ease"],
                                rev["ivl"],
                                rev["lastIvl"],
                                rev["factor"],
                                rev["time"],
                                rev["type"],
                                anki_card_id,
                            ),
                        )
                tt.commit()

            # ── Part B: TT-only directions ──
            tt_only = tt.execute("""
                SELECT cd.collocation_id, cd.direction,
                       cd.last_review_time_ms, cd.last_rating
                FROM collocation_directions cd
                WHERE cd.anki_card_id IS NULL AND cd.reps > 0
            """).fetchall()

            tt_only_directions = len(tt_only)
            for r in tt_only:
                last_review_ms = r["last_review_time_ms"]
                if last_review_ms == 0:
                    errors.append(
                        f"TT-only direction collocation={r['collocation_id']} "
                        f"direction={r['direction']} has reps>0 but "
                        f"last_review_time_ms=0 — skipping synthetic row"
                    )
                    continue

                # Encode direction into low bit to avoid PK collision between
                # sibling directions reviewed in the same millisecond.
                row_id = last_review_ms + (1 if r["direction"] == "production" else 0)

                # Stage 2 convention: treat unknown rating as Good (3).
                # Anki's ease values are 1-4 (Again/Hard/Good/Easy); 0 is
                # unused by Anki but may exist in TT's column from early data.
                button_chosen = r["last_rating"] if r["last_rating"] is not None else 3

                synthetic_rows += 1
                if not dry_run:
                    tt.execute(
                        """INSERT OR IGNORE INTO tt_revlog
                           (id, collocation_id, direction, button_chosen,
                            interval, last_interval, factor, taken_millis,
                            review_kind, anki_card_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            row_id,
                            r["collocation_id"],
                            r["direction"],
                            button_chosen,
                            0,
                            0,
                            0,
                            0,
                            4,  # Manual — no actual Anki review recorded
                            None,
                        ),
                    )
                tt.commit()

            # ── Validation: every reps>0 direction should have a row ──
            orphans = tt.execute("""
                SELECT cd.collocation_id, cd.direction
                FROM collocation_directions cd
                LEFT JOIN tt_revlog r USING (collocation_id, direction)
                WHERE cd.reps > 0
                GROUP BY cd.collocation_id, cd.direction
                HAVING COUNT(r.id) = 0
            """).fetchall()

            if not dry_run:
                tt.commit()

            return {
                "linked_directions": linked_directions,
                "anki_rows": anki_rows,
                "anki_rows_applied": anki_rows if not dry_run else 0,
                "tt_only_directions": tt_only_directions,
                "synthetic_rows": synthetic_rows,
                "synthetic_rows_applied": synthetic_rows if not dry_run else 0,
                "orphans": len(orphans),
                "errors": errors,
            }
        finally:
            tt.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap tt_revlog from Anki revlog history.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report counts without writing.",
    )
    args = parser.parse_args(argv)

    tt_db_path = Path(settings.database_url.removeprefix("sqlite:///"))
    anki_col_path = Path(settings.anki_collection_path)

    summary = bootstrap_tt_revlog(tt_db_path, anki_col_path, dry_run=args.dry_run)
    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    anki_count = summary["anki_rows_applied"] or summary["anki_rows"]
    synth_count = summary["synthetic_rows_applied"] or summary["synthetic_rows"]
    print(
        f"[{mode}] "
        f"linked_directions={summary['linked_directions']} "
        f"anki_rows={anki_count} "
        f"tt_only_directions={summary['tt_only_directions']} "
        f"synthetic_rows={synth_count} "
        f"orphans={summary['orphans']}"
    )
    for err in summary["errors"]:
        print(f"  WARNING: {err}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
