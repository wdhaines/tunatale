"""Diagnose why replay diverges from stored state.

Picks representative directions and prints stored vs replayed field-by-field.
Stage 2.5 deliverable.
"""

import argparse
import contextlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.anki.replay_fsrs_from_revlog import _states_match
from app.anki.safety import safe_open
from app.config import settings

_TOLERANCE_T = 0.01  # stability & difficulty
_TOLERANCE_DT = 86400  # ±1 day

UTC = UTC


def _parse_dt(s: str | None) -> datetime | None:
    if s is None:
        return None
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def fmt_dt(s: str | None) -> str:
    dt = _parse_dt(s)
    return dt.isoformat() if dt else "NULL"


def diagnose() -> dict:
    tt_db = Path(settings.database_url.removeprefix("sqlite:///"))
    anki_path = Path(settings.anki_collection_path)

    from app.srs.database import SRSDatabase
    from app.srs.fsrs import Direction
    from app.srs.queue_stats import (
        refresh_col_crt,
        refresh_fsrs_params,
        refresh_learning_steps,
        resolve_col_crt,
        resolve_fsrs_params,
    )

    with safe_open(anki_path, mode="ro") as ctx:
        anki = ctx.conn
        srs = SRSDatabase(str(tt_db))
        refresh_col_crt(srs, anki)
        refresh_fsrs_params(srs, anki, settings.anki_deck_name)
        with contextlib.suppress(sqlite3.Error):
            refresh_learning_steps(srs, anki, settings.anki_deck_name)
        params, params_source = resolve_fsrs_params(srs)
        col_crt = resolve_col_crt(srs)

    tt = sqlite3.connect(str(tt_db))
    tt.row_factory = sqlite3.Row
    results = []

    # ── Select 10 directions from different strata ──
    # Stratum A: small revlog (1-5 rows)
    # Stratum B: medium (6-15 rows)
    # Stratum C: large (16+ rows)
    # Pick a mix including both HAS_TT (mixed) and PURE_BOOTSTRAP sets
    dirs = tt.execute("""
        SELECT cd.collocation_id, cd.direction
        FROM collocation_directions cd
        WHERE cd.reps > 0
        ORDER BY (SELECT COUNT(*) FROM tt_revlog r
                  WHERE r.collocation_id = cd.collocation_id
                    AND r.direction = cd.direction) ASC
        LIMIT 3
    """).fetchall()
    dirs += tt.execute("""
        SELECT cd.collocation_id, cd.direction
        FROM collocation_directions cd
        WHERE cd.reps > 0
        ORDER BY (SELECT COUNT(*) FROM tt_revlog r
                  WHERE r.collocation_id = cd.collocation_id
                    AND r.direction = cd.direction) DESC
        LIMIT 3
    """).fetchall()
    dirs += tt.execute("""
        SELECT cd.collocation_id, cd.direction
        FROM collocation_directions cd
        WHERE cd.reps > 0
          AND cd.anki_card_id IN (
              SELECT anki_card_id FROM tt_revlog WHERE factor = 0 LIMIT 20
          )
        ORDER BY (SELECT COUNT(*) FROM tt_revlog r
                  WHERE r.collocation_id = cd.collocation_id
                    AND r.direction = cd.direction) DESC
        LIMIT 4
    """).fetchall()

    # Deduplicate, take 10
    seen = set()
    chosen = []
    for cid, d in dirs:
        key = (cid, d)
        if key not in seen:
            seen.add(key)
            chosen.append(key)
            if len(chosen) >= 10:
                break

    all_match_count = 0
    for cid, d_str in chosen:
        d_obj = Direction(d_str)
        row = tt.execute(
            "SELECT * FROM collocation_directions WHERE collocation_id = ? AND direction = ?",
            (cid, d_str),
        ).fetchone()
        stored = dict(row)

        # Get revlog statistics
        revlog_rows = tt.execute(
            "SELECT id, button_chosen, interval, factor, review_kind FROM tt_revlog WHERE collocation_id = ? AND direction = ? ORDER BY id",
            (cid, d_str),
        ).fetchall()
        factor0 = sum(1 for r in revlog_rows if r["factor"] == 0)
        factor_gt0 = sum(1 for r in revlog_rows if r["factor"] > 0)
        has_tt_grade = any(r["factor"] == 0 for r in revlog_rows)

        replayed = srs.rebuild_from_revlog(
            cid,
            d_obj,
            params=params,
            col_crt=col_crt,
            anki_card_id=stored.get("anki_card_id"),
        )
        match = _states_match(stored, replayed)
        if match:
            all_match_count += 1

        # Field-by-field differences
        diffs = []
        for attr, s_val, r_val, is_str in [
            ("stability", float(stored["stability"]), replayed.stability, False),
            ("difficulty", float(stored["fsrs_difficulty"]), replayed.difficulty, False),
            ("reps", int(stored["reps"]), replayed.reps, False),
            ("lapses", int(stored["lapses"]), replayed.lapses, False),
            ("state", str(stored["state"]), replayed.state.value, True),
        ]:
            if (is_str and s_val != r_val) or (
                not is_str and abs(s_val - r_val) > (0.01 if attr in ("stability", "difficulty") else 0)
            ):
                diffs.append(f"{attr}={s_val}→{r_val}")

        s_due = _parse_dt(stored["due_at"])
        r_due = replayed.due_at
        if s_due and abs((s_due - r_due).total_seconds()) >= _TOLERANCE_DT:
            diffs.append(f"due_at={fmt_dt(stored['due_at'])}→{r_due.isoformat()}")
        elif s_due is None and r_due:
            diffs.append(f"due_at=NULL→{r_due.isoformat()}")

        s_lr = _parse_dt(stored["last_review"])
        r_lr = replayed.last_review
        if (s_lr is None) != (r_lr is None):
            diffs.append(f"last_review={stored['last_review']}→{r_lr}")
        elif s_lr and r_lr and abs((s_lr - r_lr).total_seconds()) >= _TOLERANCE_DT:
            diffs.append(f"last_review={fmt_dt(stored['last_review'])}→{r_lr.isoformat()}")

        results.append(
            {
                "collocation_id": cid,
                "direction": d_str,
                "anki_card_id": stored.get("anki_card_id"),
                "revlog_count": len(revlog_rows),
                "revlog_factor0": factor0,
                "revlog_factor_gt0": factor_gt0,
                "has_tt_grade": has_tt_grade,
                "match": match,
                "stored_reps": stored["reps"],
                "replayed_reps": replayed.reps,
                "stored_stability": stored["stability"],
                "replayed_stability": round(replayed.stability, 4),
                "stored_difficulty": stored["fsrs_difficulty"],
                "replayed_difficulty": round(replayed.difficulty, 4),
                "stored_state": stored["state"],
                "replayed_state": replayed.state.value,
                "stored_due_at": stored["due_at"],
                "replayed_due_at": replayed.due_at.isoformat(),
                "stored_last_review": stored["last_review"],
                "replayed_last_review": replayed.last_review.isoformat() if replayed.last_review else None,
                "stored_lapses": stored["lapses"],
                "replayed_lapses": replayed.lapses,
                "stored_last_rating": stored["last_rating"],
                "replayed_last_rating": replayed.last_rating,
                "stored_left": stored["left"],
                "replayed_left": replayed.left,
                "diffs": diffs,
                "dominant_diff": diffs[0] if diffs else "MATCH",
            }
        )

    return {
        "params_source": params_source,
        "results": results,
        "match_count": all_match_count,
        "sample_count": len(chosen),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose replay divergences.")
    parser.parse_args(argv)

    report = diagnose()

    print(f"FSRS params source: {report['params_source']}")
    print(f"Sample: {report['match_count']}/{report['sample_count']} MATCH\n")

    for r in report["results"]:
        tag = "MATCH" if r["match"] else "DIVERGE"
        print(f"─── {tag} collocation_id={r['collocation_id']} direction={r['direction']} ───")
        print(f"    anki_card_id={r['anki_card_id']}")
        print(
            f"    tt_revlog: {r['revlog_count']} rows (factor=0: {r['revlog_factor0']}, factor>0: {r['revlog_factor_gt0']})"
        )
        print(f"    stability:   stored={r['stored_stability']} replayed={r['replayed_stability']}")
        print(f"    difficulty:  stored={r['stored_difficulty']} replayed={r['replayed_difficulty']}")
        print(f"    reps:        stored={r['stored_reps']} replayed={r['replayed_reps']}")
        print(f"    lapses:      stored={r['stored_lapses']} replayed={r['replayed_lapses']}")
        print(f"    state:       stored={r['stored_state']} replayed={r['replayed_state']}")
        print(f"    due_at:      stored={r['stored_due_at']} replayed={r['replayed_due_at']}")
        print(f"    last_review: stored={r['stored_last_review']} replayed={r['replayed_last_review']}")
        print(f"    last_rating: stored={r['stored_last_rating']} replayed={r['replayed_last_rating']}")
        print(f"    left:        stored={r['stored_left']} replayed={r['replayed_left']}")
        print(f"    diffs: {', '.join(r['diffs']) if r['diffs'] else 'none'}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
