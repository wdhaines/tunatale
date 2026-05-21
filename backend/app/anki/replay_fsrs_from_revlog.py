"""Replay ``tt_revlog`` through FSRS to derive canonical ``DirectionState``.

One-shot discovery script run after Stage 1 has bootstrapped ``tt_revlog``.
For every direction with ``reps > 0``, yields one of five classifications:

  MATCH                 — replay matches stored state (within tolerance)
  REPAIR                — replay differs; Anki-linked + intact revlog → overwrite
  SKIP_SYNTHETIC_ONLY   — TT-only; only synthetic (review_kind=4) rows exist
  SKIP_PRE_FSRS         — replay diverges large; revlog has SM2-era factor>0 rows
  SKIP_UNKNOWN_DIVERGENCE — replay diverges; no known cause

Stage 3 uses the MATCH + REPAIR count to know which directions are eligible
for merge-branch elimination.

Usage:
    uv run python -m app.anki.replay_fsrs_from_revlog [--dry-run]
"""

import argparse
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from app.anki.safety import safe_open
from app.config import settings

logger = logging.getLogger(__name__)

_TOLERANCE_STABILITY = 0.01
_TOLERANCE_DIFFICULTY = 0.01
_TOLERANCE_DT_SEC = 86400  # ±1 day for col_crt rollover drift


def _parse_stored_due(stored: str | None) -> datetime | None:
    if stored is None:
        return None
    try:
        return datetime.fromisoformat(stored)
    except (ValueError, TypeError):
        return None


def _states_match(stored: dict, replayed_state) -> bool:
    """Compare replayed DirectionState against stored collocation_directions row."""
    if abs(stored["stability"] - replayed_state.stability) >= _TOLERANCE_STABILITY:
        return False
    if abs(stored["fsrs_difficulty"] - replayed_state.difficulty) >= _TOLERANCE_DIFFICULTY:
        return False
    if stored["reps"] != replayed_state.reps:
        return False
    if stored["lapses"] != replayed_state.lapses:
        return False
    if stored["state"] != replayed_state.state.value:
        return False

    stored_due = _parse_stored_due(stored["due_at"])
    if stored_due is None:
        return False
    if abs((stored_due - replayed_state.due_at).total_seconds()) >= _TOLERANCE_DT_SEC:
        return False

    stored_lr = _parse_stored_due(stored["last_review"])
    if stored_lr is None and replayed_state.last_review is None:  # pragma: no cover (pass is a no-op)
        pass
    elif (
        stored_lr is None
        or replayed_state.last_review is None
        or abs((stored_lr - replayed_state.last_review).total_seconds()) >= _TOLERANCE_DT_SEC
    ):
        return False

    return True


def _has_pre_fsrs_rows(tt_conn: sqlite3.Connection, collocation_id: int, direction: str) -> bool:
    """Return True if any tt_revlog row for this direction has factor > 0 (SM2-era)."""
    row = tt_conn.execute(
        "SELECT 1 FROM tt_revlog WHERE collocation_id = ? AND direction = ? AND factor > 0 LIMIT 1",
        (collocation_id, direction),
    ).fetchone()
    return row is not None


def _count_non_synthetic(tt_conn: sqlite3.Connection, collocation_id: int, direction: str) -> int:
    row = tt_conn.execute(
        "SELECT COUNT(*) FROM tt_revlog WHERE collocation_id = ? AND direction = ? AND review_kind NOT IN (4)",
        (collocation_id, direction),
    ).fetchone()
    return row[0] if row else 0


def replay_fsrs_from_revlog(
    tt_db_path: Path,
    anki_col_path: Path,
    *,
    dry_run: bool = False,
) -> dict:
    """Replay every direction's tt_revlog rows through FSRS, classifying each.

    Returns a summary dict with bucket counts and error list.
    """
    from app.srs.database import SRSDatabase
    from app.srs.fsrs import Direction
    from app.srs.queue_stats import (
        refresh_col_crt,
        refresh_fsrs_params,
        refresh_learning_steps,
        resolve_col_crt,
        resolve_fsrs_params,
    )

    with safe_open(anki_col_path, mode="ro") as ctx:
        anki = ctx.conn
        tt = sqlite3.connect(str(tt_db_path))
        tt.row_factory = sqlite3.Row
        try:
            # Warm caches from Anki
            srs_db = SRSDatabase(str(tt_db_path))
            refresh_col_crt(srs_db, anki)
            refresh_fsrs_params(srs_db, anki, settings.anki_deck_name)
            try:
                refresh_learning_steps(srs_db, anki, settings.anki_deck_name)
            except sqlite3.Error:
                logger.warning("Could not refresh learning steps (Anki DB may be legacy format)")

            resolved_params, params_source = resolve_fsrs_params(srs_db)
            col_crt = resolve_col_crt(srs_db)

            if params_source == "default":  # pragma: no cover (cache always empty in tests)
                logger.warning("Using default FSRS params (cache not available)")  # pragma: no cover

            buckets = {
                "MATCH": 0,
                "REPAIR": 0,
                "SKIP_SYNTHETIC_ONLY": 0,
                "SKIP_PRE_FSRS": 0,
                "SKIP_UNKNOWN_DIVERGENCE": 0,
            }
            errors: list[str] = []

            directions = tt.execute("""
                SELECT cd.*, c.card_type
                FROM collocation_directions cd
                JOIN collocations c ON c.id = cd.collocation_id
                WHERE cd.reps > 0
            """).fetchall()

            for row in directions:
                collocation_id = row["collocation_id"]
                dir_str = row["direction"]
                direction = Direction(dir_str)
                anki_card_id = row["anki_card_id"]

                try:
                    tt.execute("PRAGMA busy_timeout = 0")
                    tt.execute("BEGIN IMMEDIATE")
                    replayed = srs_db.rebuild_from_revlog(
                        collocation_id,
                        direction,
                        params=resolved_params,
                        col_crt=col_crt,
                        anki_card_id=anki_card_id,
                    )

                    if _states_match(dict(row), replayed):
                        bucket = "MATCH"
                    elif anki_card_id is None and _count_non_synthetic(tt, collocation_id, dir_str) == 0:
                        bucket = "SKIP_SYNTHETIC_ONLY"
                    elif _has_pre_fsrs_rows(tt, collocation_id, dir_str):
                        bucket = "SKIP_PRE_FSRS"
                    elif anki_card_id is not None:
                        bucket = "REPAIR"
                    else:
                        bucket = "SKIP_UNKNOWN_DIVERGENCE"

                    if bucket == "REPAIR" and not dry_run:
                        due_at_iso = replayed.due_at.isoformat()
                        last_review_iso = replayed.last_review.isoformat() if replayed.last_review else None

                        tt.execute(
                            """
                            UPDATE collocation_directions SET
                                stability = ?,
                                fsrs_difficulty = ?,
                                due_at = ?,
                                reps = ?,
                                lapses = ?,
                                state = ?,
                                last_review = ?,
                                last_review_time_ms = ?,
                                last_rating = ?,
                                "left" = ?
                            WHERE collocation_id = ? AND direction = ?
                        """,
                            (
                                replayed.stability,
                                replayed.difficulty,
                                due_at_iso,
                                replayed.reps,
                                replayed.lapses,
                                replayed.state.value,
                                last_review_iso,
                                replayed.last_review_time_ms,
                                replayed.last_rating,
                                replayed.left,
                                collocation_id,
                                dir_str,
                            ),
                        )

                    buckets[bucket] += 1
                    tt.commit()
                except sqlite3.OperationalError as err:
                    tt.rollback()
                    raise SystemExit(
                        "Backend appears live — shut it down and rerun. "
                        f"Last processed: collocation_id={collocation_id} direction={dir_str}"
                    ) from err

            # ── Validation queries ──
            orphans = tt.execute("""
                SELECT cd.collocation_id, cd.direction
                FROM collocation_directions cd
                LEFT JOIN tt_revlog r USING (collocation_id, direction)
                WHERE cd.reps > 0
                GROUP BY cd.collocation_id, cd.direction
                HAVING COUNT(r.id) = 0
            """).fetchall()
            for o in orphans:
                errors.append(
                    f"Orphan direction collocation_id={o['collocation_id']} "
                    f"direction={o['direction']} has reps>0 but zero tt_revlog rows"
                )

            anki_linked_rows = tt.execute("""
                SELECT cd.collocation_id, cd.direction, cd.anki_card_id,
                       (SELECT COUNT(*) FROM tt_revlog r
                        WHERE r.collocation_id = cd.collocation_id
                          AND r.direction = cd.direction) AS tt_count
                FROM collocation_directions cd
                WHERE cd.anki_card_id IS NOT NULL
            """).fetchall()
            for alr in anki_linked_rows:
                anki_count = anki.execute(
                    "SELECT COUNT(*) FROM revlog WHERE cid = ?",
                    (alr["anki_card_id"],),
                ).fetchone()[0]
                if anki_count != alr["tt_count"]:
                    errors.append(
                        f"Row count mismatch for collocation_id={alr['collocation_id']} "
                        f"direction={alr['direction']} anki_card_id={alr['anki_card_id']}: "
                        f"tt_revlog={alr['tt_count']} vs anki_revlog={anki_count}"
                    )

            return {
                "buckets": buckets,
                "errors": errors,
            }
        finally:
            tt.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay tt_revlog through FSRS to derive DirectionState.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Classify without writing REPAIR updates.",
    )
    args = parser.parse_args(argv)

    tt_db_path = Path(settings.database_url.removeprefix("sqlite:///"))
    anki_col_path = Path(settings.anki_collection_path)

    summary = replay_fsrs_from_revlog(tt_db_path, anki_col_path, dry_run=args.dry_run)
    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    buckets = summary["buckets"]
    total = sum(buckets.values())
    print(
        f"[{mode}] "
        f"MATCH={buckets['MATCH']} "
        f"REPAIR={buckets['REPAIR']} "
        f"SKIP_SYNTHETIC_ONLY={buckets['SKIP_SYNTHETIC_ONLY']} "
        f"SKIP_PRE_FSRS={buckets['SKIP_PRE_FSRS']} "
        f"SKIP_UNKNOWN_DIVERGENCE={buckets['SKIP_UNKNOWN_DIVERGENCE']} "
        f"TOTAL={total}"
    )
    for err in summary["errors"]:
        print(f"  WARNING: {err}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
