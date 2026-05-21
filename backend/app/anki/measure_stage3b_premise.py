"""Measure Stage 3b's premise empirically using a pre/post sync snapshot pair.

Stage 3b's claim: `schedule(pre_stored_state, new_revlog_rows_since_last_sync)`
matches Anki's `cards.data` after the sync — i.e., TT's stored state evolves
predictably under forward-step FSRS.

This script reads two TT database snapshots straddling a real production sync,
identifies directions where new tt_revlog rows arrived in that interval, and
asks: did the pre-sync stored state + forward-step over the new rows produce
the post-sync stored state?

Defaults to the snapshot pair sitting in /tmp from the conversation that
produced this script. Pass --pre / --post to use a different pair.

Usage:
    uv run python -m app.anki.measure_stage3b_premise \\
        [--pre /tmp/tt_post_dedup.db] \\
        [--post /tmp/tt_post_sync.db]
"""

from __future__ import annotations

import argparse
import contextlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings


def _parse_dt(s: str | None) -> datetime | None:
    if s is None:
        return None
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def measure(pre_db: Path, post_db: Path, anki_col_path: Path) -> dict:
    from app.anki.safety import safe_open
    from app.models.srs_item import Direction, DirectionState, Rating, SRSItem, SRSState
    from app.models.syntactic_unit import SyntacticUnit
    from app.srs.database import SRSDatabase
    from app.srs.fsrs import schedule
    from app.srs.queue_stats import (
        refresh_col_crt,
        refresh_fsrs_params,
        refresh_learning_steps,
        resolve_col_crt,
        resolve_fsrs_params,
    )

    # Warm caches from current Anki collection (params + col_crt are stable
    # across the sync interval for this measurement).
    with safe_open(anki_col_path, mode="ro") as ctx:
        anki = ctx.conn
        srs = SRSDatabase(str(post_db))
        refresh_col_crt(srs, anki)
        refresh_fsrs_params(srs, anki, settings.anki_deck_name)
        with contextlib.suppress(sqlite3.Error):
            refresh_learning_steps(srs, anki, settings.anki_deck_name)
        params, _ = resolve_fsrs_params(srs)
        col_crt = resolve_col_crt(srs)

    pre = sqlite3.connect(str(pre_db))
    pre.row_factory = sqlite3.Row
    post = sqlite3.connect(str(post_db))
    post.row_factory = sqlite3.Row
    try:
        # Find directions that exist in both snapshots and have new tt_revlog
        # rows in the post snapshot.
        post.execute(f"ATTACH '{pre_db}' AS pre")
        targets = post.execute("""
            SELECT
                pre_cd.collocation_id AS cid,
                pre_cd.direction AS dir,
                pre_cd.anki_card_id AS akid
            FROM pre.collocation_directions pre_cd
            JOIN collocation_directions post_cd
                ON post_cd.collocation_id = pre_cd.collocation_id
                AND post_cd.direction = pre_cd.direction
            WHERE pre_cd.reps > 0
              AND pre_cd.anki_card_id IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM tt_revlog r
                WHERE r.collocation_id = pre_cd.collocation_id
                  AND r.direction = pre_cd.direction
                  AND r.id > IFNULL(
                    (SELECT MAX(p.id) FROM pre.tt_revlog p
                     WHERE p.collocation_id = pre_cd.collocation_id
                       AND p.direction = pre_cd.direction),
                    0
                  )
              )
        """).fetchall()

        results = {
            "total": 0,
            "match": 0,
            "diverge_state_field": 0,
            "diverge_stability": 0,
            "diverge_due_at": 0,
            "examples_match": [],
            "examples_diverge": [],
        }

        for t in targets:
            cid, d_str, akid = t["cid"], t["dir"], t["akid"]
            d_obj = Direction(d_str)

            pre_row = pre.execute(
                "SELECT * FROM collocation_directions WHERE collocation_id = ? AND direction = ?",
                (cid, d_str),
            ).fetchone()
            post_row = post.execute(
                "SELECT * FROM collocation_directions WHERE collocation_id = ? AND direction = ?",
                (cid, d_str),
            ).fetchone()
            if pre_row is None or post_row is None:
                continue

            # Identify new rows in post that weren't in pre.
            pre_max_id = (
                pre.execute(
                    "SELECT MAX(id) FROM tt_revlog WHERE collocation_id = ? AND direction = ?",
                    (cid, d_str),
                ).fetchone()[0]
                or 0
            )
            new_rows = post.execute(
                "SELECT id, button_chosen, factor, review_kind FROM tt_revlog "
                "WHERE collocation_id = ? AND direction = ? AND id > ? "
                "AND id >= 1000000000000 "
                "ORDER BY id ASC",
                (cid, d_str, pre_max_id),
            ).fetchall()
            valid_new = [r for r in new_rows if r["review_kind"] != 4 and r["button_chosen"] in (1, 2, 3, 4)]
            if not valid_new:
                continue

            # Build pre_state from pre_row.
            pre_last_review = _parse_dt(pre_row["last_review"])
            pre_due_at = _parse_dt(pre_row["due_at"]) or datetime.now(UTC)
            try:
                pre_state_enum = SRSState(pre_row["state"])
            except ValueError:
                pre_state_enum = SRSState.NEW
            pre_state = DirectionState(
                direction=d_obj,
                due_at=pre_due_at,
                stability=pre_row["stability"],
                difficulty=pre_row["fsrs_difficulty"],
                reps=pre_row["reps"],
                lapses=pre_row["lapses"],
                state=pre_state_enum,
                last_review=pre_last_review,
                last_review_time_ms=pre_row["last_review_time_ms"] or 0,
                anki_card_id=akid,
                last_rating=pre_row["last_rating"],
                left=pre_row["left"],
            )

            # Apply new rows via schedule.
            other_dir = Direction.PRODUCTION if d_obj == Direction.RECOGNITION else Direction.RECOGNITION
            other_state = DirectionState(direction=other_dir, due_at=pre_due_at)
            unit = SyntacticUnit(text="m", translation="", word_count=1, difficulty=1, source="m", card_type="vocab")
            item = SRSItem(
                syntactic_unit=unit,
                directions={d_obj: pre_state, other_dir: other_state},
                guid="m",
                anki_note_id=None,
            )
            for r in valid_new:
                now_dt = datetime.fromtimestamp(r["id"] / 1000, tz=UTC)
                item = schedule(
                    item,
                    Rating(r["button_chosen"]),
                    review_date=now_dt.date(),
                    direction=d_obj,
                    params=params,
                    time_ms=r["id"],
                    now=now_dt,
                    col_crt=col_crt,
                )
            derived = item.directions[d_obj]

            # Two views: STRICT (bit-exact every field) and PRACTICAL (memory
            # state within tolerance; reps/lapses treated as Anki-passthrough,
            # not replay-derived, since Anki has accounting we don't reproduce).
            results["total"] += 1
            strict_diffs = []
            practical_diffs = []

            stab_delta = abs(post_row["stability"] - derived.stability)
            stab_pct = stab_delta / max(abs(post_row["stability"]), 0.01)
            if stab_delta >= 0.01:
                strict_diffs.append(f"stab {post_row['stability']:.3f}→{derived.stability:.3f}")
                results["diverge_stability"] += 1
            if stab_pct > 0.05:  # 5% relative tolerance
                practical_diffs.append(
                    f"stab {post_row['stability']:.3f}→{derived.stability:.3f} ({stab_pct * 100:.1f}%)"
                )

            diff_delta = abs(post_row["fsrs_difficulty"] - derived.difficulty)
            if diff_delta >= 0.01:
                strict_diffs.append(f"diff {post_row['fsrs_difficulty']:.3f}→{derived.difficulty:.3f}")
            if diff_delta >= 0.1:  # 0.1 absolute on 1-10 scale
                practical_diffs.append(f"diff {post_row['fsrs_difficulty']:.3f}→{derived.difficulty:.3f}")

            if post_row["reps"] != derived.reps:
                strict_diffs.append(f"reps {post_row['reps']}→{derived.reps}")
                results["diverge_state_field"] += 1
            if post_row["lapses"] != derived.lapses:
                strict_diffs.append(f"lapses {post_row['lapses']}→{derived.lapses}")
                results["diverge_state_field"] += 1
            if post_row["state"] != derived.state.value:
                strict_diffs.append(f"state {post_row['state']}→{derived.state.value}")
                practical_diffs.append(f"state {post_row['state']}→{derived.state.value}")
                results["diverge_state_field"] += 1

            post_due = _parse_dt(post_row["due_at"])
            due_delta_sec = abs((post_due - derived.due_at).total_seconds()) if post_due else 0
            if post_due and due_delta_sec >= 86400:
                strict_diffs.append(f"due_at {post_due.date()}→{derived.due_at.date()}")
                results["diverge_due_at"] += 1
            if post_due and due_delta_sec >= 86400 * 3:  # 3-day tolerance
                practical_diffs.append(f"due_at Δ{int(due_delta_sec / 86400)}d")

            if not strict_diffs:
                results["match"] += 1
                if len(results["examples_match"]) < 3:
                    results["examples_match"].append(
                        {
                            "cid": cid,
                            "dir": d_str,
                            "n_new_rows": len(valid_new),
                        }
                    )
            elif len(results["examples_diverge"]) < 8:
                results["examples_diverge"].append(
                    {
                        "cid": cid,
                        "dir": d_str,
                        "n_new_rows": len(valid_new),
                        "diffs": strict_diffs,
                    }
                )

            if not practical_diffs:
                results.setdefault("practical_match", 0)
                results["practical_match"] += 1
            else:
                results.setdefault("practical_diverge_examples", [])
                if len(results["practical_diverge_examples"]) < 5:
                    results["practical_diverge_examples"].append(
                        {
                            "cid": cid,
                            "dir": d_str,
                            "diffs": practical_diffs,
                        }
                    )

        return results
    finally:
        pre.close()
        post.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure Stage 3b premise empirically.")
    parser.add_argument("--pre", default="/tmp/tt_post_dedup.db", help="Pre-sync TT DB snapshot")
    parser.add_argument("--post", default="/tmp/tt_post_sync.db", help="Post-sync TT DB snapshot")
    args = parser.parse_args(argv)

    anki_col_path = Path(settings.anki_collection_path)
    r = measure(Path(args.pre), Path(args.post), anki_col_path)

    total = r["total"]
    match = r["match"]
    pct = 100.0 * match / max(total, 1)

    print("=" * 70)
    print(f"Pre:  {args.pre}")
    print(f"Post: {args.post}")
    print("=" * 70)
    print(f"  {match}/{total} ({pct:.1f}%) directions match post-sync state")
    print(f"    state-field diverges (reps/lapses/state): {r['diverge_state_field']}")
    print(f"    stability diverges (>0.01):               {r['diverge_stability']}")
    print(f"    due_at diverges (>1 day):                 {r['diverge_due_at']}")
    print()
    if r["examples_match"]:
        print("  MATCH examples:")
        for ex in r["examples_match"]:
            print(f"    cid={ex['cid']:>4} dir={ex['dir']:<11} n_new={ex['n_new_rows']}")
    if r["examples_diverge"]:
        print("  DIVERGE examples:")
        for ex in r["examples_diverge"]:
            print(f"    cid={ex['cid']:>4} dir={ex['dir']:<11} n_new={ex['n_new_rows']}: {', '.join(ex['diffs'])}")
    print()
    print("=" * 70)
    print("Stage 3b decision gate:")
    print("=" * 70)
    if pct >= 95:
        print("  ≥95% — Stage 3b simplification claim HOLDS. Proceed to staged cadence.")
    elif pct >= 50:
        print(f"  {pct:.0f}% — between thresholds. Real simplification possible but")
        print("  'take-Anki on divergence' is more than a rare diagnostic.")
        print("  Re-frame as 'merge with Anki precedence on FSRS state'.")
    else:
        print(f"  {pct:.0f}% — below 50%. Incremental replay doesn't reproduce Anki's")
        print("  adjustments often enough. Abandon Stage 3b; keep tt_revlog as event log only.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
