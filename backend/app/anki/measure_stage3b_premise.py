"""Measure Stage 3b's premise empirically using a pre/post sync snapshot quad.

Stage 3b's claim: `schedule(pre_stored_state, new_revlog_rows_since_last_sync)`
matches Anki's `cards.data` after the sync — i.e., forward-step FSRS over
TT's stored state reproduces what Anki computes per-grade.

This script reads four database snapshots straddling an Anki-only grading
interval (TT pre, TT post, Anki pre, Anki post). It identifies directions
where new tt_revlog rows arrived in that interval, replays them via
`schedule()` from the pre TT state, and compares the derived FSRS memory
state (`stability`, `difficulty`) against Anki's actual post `cards.data`.

Per `docs/stage-3b-empirical-measurement.md`, `reps`/`lapses`/`state`/`due_at`
are pass-through-from-Anki fields under the refined Stage 3b design and are
NOT used in the MATCH/DIVERGE classification (`due_at` is tracked as a side
stat for forward-step fuzz reliability).

Usage:
    uv run python -m app.anki.measure_stage3b_premise \\
        --pre /tmp/tt_pre_anki_only.db \\
        --post /tmp/tt_post_anki_only.db \\
        --anki-pre /tmp/anki_pre_anki_only.db \\
        --anki-post /tmp/anki_post_anki_only.db
"""

from __future__ import annotations

import argparse
import contextlib
import json
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


def measure(pre_db: Path, post_db: Path, anki_pre_db: Path, anki_post_db: Path) -> dict:
    from app.anki.safety import _register_anki_collations
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

    # Warm caches from the Anki post snapshot (params + col_crt are stable
    # across the sync interval). Use plain sqlite3 with collations registered,
    # NOT safe_open — these are static snapshot files, not the live collection.
    anki_post = sqlite3.connect(f"file:{anki_post_db}?mode=ro", uri=True)
    _register_anki_collations(anki_post)
    anki_post.row_factory = sqlite3.Row
    srs = SRSDatabase(str(post_db))
    refresh_col_crt(srs, anki_post)
    refresh_fsrs_params(srs, anki_post, settings.anki_deck_name)
    with contextlib.suppress(sqlite3.Error):
        refresh_learning_steps(srs, anki_post, settings.anki_deck_name)
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
            "practical_match": 0,
            "diverge_stability": 0,
            "skipped_sm2": 0,
            "skipped_no_anki_card": 0,
            "stability_deltas_pct": [],
            "difficulty_deltas_abs": [],
            "due_at_match_within_1h": 0,
            "due_at_match_within_1d": 0,
            "n_new_rows_histogram": {},
            "examples_match": [],
            "examples_diverge": [],
            "examples_practical_diverge": [],
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
                anki_due=pre_row["anki_due"],
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

            # Compare derived (forward-step from pre TT state) against Anki's
            # actual post cards.data. Per the refined Stage 3b design, only
            # stability and difficulty are replay-derived; reps/lapses/state/
            # due_at are pass-through-from-Anki and excluded from MATCH
            # classification (due_at tracked as side stat for fuzz reliability).
            anki_card = anki_post.execute("SELECT data FROM cards WHERE id = ?", (akid,)).fetchone()
            if anki_card is None:
                results["skipped_no_anki_card"] += 1
                continue
            try:
                anki_data = json.loads(anki_card["data"]) if anki_card["data"] else {}
            except json.JSONDecodeError:
                anki_data = {}
            if "s" not in anki_data or "d" not in anki_data:
                results["skipped_sm2"] += 1
                continue
            anki_s = float(anki_data["s"])
            anki_d = float(anki_data["d"])

            results["total"] += 1
            bucket = results["n_new_rows_histogram"]
            bucket[len(valid_new)] = bucket.get(len(valid_new), 0) + 1

            stab_delta = abs(anki_s - derived.stability)
            stab_pct = stab_delta / max(abs(anki_s), 0.01)
            diff_delta = abs(anki_d - derived.difficulty)

            results["stability_deltas_pct"].append(stab_pct)
            results["difficulty_deltas_abs"].append(diff_delta)

            # Side stat: due_at match rate (forward-step fuzz reliability).
            post_due = _parse_dt(post_row["due_at"])
            if post_due:
                due_delta_sec = abs((post_due - derived.due_at).total_seconds())
                if due_delta_sec < 3600:
                    results["due_at_match_within_1h"] += 1
                if due_delta_sec < 86400:
                    results["due_at_match_within_1d"] += 1

            strict_diffs = []
            practical_diffs = []

            if stab_delta >= 0.01:
                strict_diffs.append(f"stab anki={anki_s:.4f} derived={derived.stability:.4f}")
                results["diverge_stability"] += 1
            if stab_pct > 0.05:  # 5% relative tolerance
                practical_diffs.append(
                    f"stab anki={anki_s:.4f} derived={derived.stability:.4f} ({stab_pct * 100:.1f}%)"
                )

            if diff_delta >= 0.01:
                strict_diffs.append(f"diff anki={anki_d:.4f} derived={derived.difficulty:.4f}")
            if diff_delta >= 0.1:  # 0.1 absolute on 1-10 scale
                practical_diffs.append(f"diff anki={anki_d:.4f} derived={derived.difficulty:.4f}")

            if not strict_diffs:
                results["match"] += 1
                if len(results["examples_match"]) < 3:
                    results["examples_match"].append(
                        {
                            "cid": cid,
                            "dir": d_str,
                            "n_new_rows": len(valid_new),
                            "stab": derived.stability,
                            "diff": derived.difficulty,
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
                results["practical_match"] += 1
            elif len(results["examples_practical_diverge"]) < 5:
                results["examples_practical_diverge"].append(
                    {
                        "cid": cid,
                        "dir": d_str,
                        "n_new_rows": len(valid_new),
                        "diffs": practical_diffs,
                    }
                )

        return results
    finally:
        pre.close()
        post.close()
        anki_post.close()


def _pct(num: float, denom: int) -> float:
    return 100.0 * num / max(denom, 1)


def _summarize_deltas(label: str, deltas: list[float]) -> str:
    if not deltas:
        return f"  {label}: (no data)"
    s = sorted(deltas)
    n = len(s)
    return (
        f"  {label}: n={n}  min={s[0]:.4f}  p50={s[n // 2]:.4f}  "
        f"p90={s[min(n - 1, int(n * 0.9))]:.4f}  max={s[-1]:.4f}  "
        f"mean={sum(s) / n:.4f}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure Stage 3b premise empirically.")
    parser.add_argument("--pre", default="/tmp/tt_pre_anki_only.db", help="Pre TT DB snapshot")
    parser.add_argument("--post", default="/tmp/tt_post_anki_only.db", help="Post TT DB snapshot")
    parser.add_argument(
        "--anki-pre",
        default="/tmp/anki_pre_anki_only.db",
        help="Pre Anki DB snapshot (currently unused — reserved for symmetry)",
    )
    parser.add_argument(
        "--anki-post", default="/tmp/anki_post_anki_only.db", help="Post Anki DB snapshot (cards.data source)"
    )
    args = parser.parse_args(argv)

    r = measure(Path(args.pre), Path(args.post), Path(args.anki_pre), Path(args.anki_post))

    total = r["total"]
    match = r["match"]
    practical = r["practical_match"]
    strict_pct = _pct(match, total)
    practical_pct = _pct(practical, total)

    print("=" * 70)
    print(f"TT  pre:  {args.pre}")
    print(f"TT  post: {args.post}")
    print(f"Anki post: {args.anki_post}  (cards.data source)")
    print("=" * 70)
    print(f"  Directions considered:     {total}")
    print(f"  Skipped (no Anki card):    {r['skipped_no_anki_card']}")
    print(f"  Skipped (SM-2 fallback):   {r['skipped_sm2']}")
    print()
    print(f"  STRICT MATCH  (±0.01 abs):    {match}/{total} ({strict_pct:.1f}%)")
    print(f"  PRACTICAL MATCH (±5% s, ±0.1 d): {practical}/{total} ({practical_pct:.1f}%)")
    print(f"    stability diverges (>0.01):  {r['diverge_stability']}")
    print()
    print("  Side stats (not part of MATCH classification — pass-through fields):")
    print(
        f"    due_at match within 1h:    {r['due_at_match_within_1h']}/{total} ({_pct(r['due_at_match_within_1h'], total):.1f}%)"
    )
    print(
        f"    due_at match within 1d:    {r['due_at_match_within_1d']}/{total} ({_pct(r['due_at_match_within_1d'], total):.1f}%)"
    )
    print()
    print(_summarize_deltas("stability rel-delta (%)", [d * 100 for d in r["stability_deltas_pct"]]))
    print(_summarize_deltas("difficulty abs-delta", r["difficulty_deltas_abs"]))
    print()
    if r["n_new_rows_histogram"]:
        hist = sorted(r["n_new_rows_histogram"].items())
        print("  N new rows per direction:")
        for n, c in hist:
            print(f"    {n} row(s): {c} directions")
    print()
    if r["examples_match"]:
        print("  STRICT MATCH examples:")
        for ex in r["examples_match"]:
            print(
                f"    cid={ex['cid']:>4} dir={ex['dir']:<11} n_new={ex['n_new_rows']} stab={ex['stab']:.4f} diff={ex['diff']:.4f}"
            )
    if r["examples_diverge"]:
        print("  STRICT DIVERGE examples:")
        for ex in r["examples_diverge"]:
            print(f"    cid={ex['cid']:>4} dir={ex['dir']:<11} n_new={ex['n_new_rows']}: {', '.join(ex['diffs'])}")
    if r["examples_practical_diverge"]:
        print("  PRACTICAL DIVERGE examples:")
        for ex in r["examples_practical_diverge"]:
            print(f"    cid={ex['cid']:>4} dir={ex['dir']:<11} n_new={ex['n_new_rows']}: {', '.join(ex['diffs'])}")
    print()
    print("=" * 70)
    print("Stage 3b decision gate (using PRACTICAL match rate):")
    print("=" * 70)
    if practical_pct >= 95:
        print("  ≥95% — Stage 3b simplification claim HOLDS. Proceed to staged cadence.")
    elif practical_pct >= 50:
        print(f"  {practical_pct:.0f}% — between thresholds. Real simplification possible but")
        print("  'take-Anki on divergence' is more than a rare diagnostic.")
        print("  Re-frame as 'merge with Anki precedence on FSRS state' (see doc § 50-95% re-frame).")
    else:
        print(f"  {practical_pct:.0f}% — below 50%. Incremental replay doesn't reproduce Anki's")
        print("  adjustments often enough. Abandon Stage 3b; keep tt_revlog as event log only.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
