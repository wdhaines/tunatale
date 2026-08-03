"""Re-anchor a collocation whose review history crossed onto another Anki card.

**The problem.** A TT guid is ``(text, language, disambig_key)`` with the part of
speech as disambig, so two Anki notes sharing the same ``(text, POS)`` collapse
to ONE collocation holding TWO candidate cards. ``foran`` alternated between its
twins from 2026-07-14: ``tt_revlog`` accumulated the twin's rows, and the
direction's FSRS fields drifted onto a grade belonging to the card TT wasn't
tracking. (POS homonyms are NOT affected — different POS ⇒ different guid ⇒
separate pinned collocations.)

**Why it needs a manual fix.** Neither sync direction heals it:

* ``sync_pull`` — ``_tt_memory_newer`` sees TT's ``last_review`` as newer than
  Anki's ``lrt`` and keeps TT's (wrong) memory state.
* ``sync_push`` — ``dirty_fsrs=0``, so the row is never pushed.

The same two-way deadlock documented for the 2026-06-29 forced-download
incident, and the same remedy: take Anki verbatim.

**What it does** (TT-side only — never touches ``collection.anki2``):

1. Deletes ``tt_revlog`` rows Anki attributes to a *different* card.
2. Re-anchors ``stability`` / ``fsrs_difficulty`` / ``last_review`` from the
   survivor's ``cards.data``, clearing ``last_review_time_ms`` / ``last_rating``.
3. Drops the frozen ``session_main_queue`` so queue order rebuilds — the cache is
   DB-backed and survives restarts, so without this the stale order replays.

Usage::

    uv run python -m scripts.anki_archive.reanchor_crossed_collocation --dry-run
    uv run python -m scripts.anki_archive.reanchor_crossed_collocation --apply
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True)
class CrossedRepair:
    """One direction to re-anchor, and the card it must end up on."""

    collocation_id: int
    direction: str
    survivor_card_id: int


@dataclass
class RepairPlan:
    op: CrossedRepair
    stray_revlog_ids: list[int] = field(default_factory=list)
    stability: float = 0.0
    difficulty: float = 0.0
    last_review: str = ""


# foran: collocation 1478 kept 49 tt_revlog rows belonging to the graved twin
# (card ...305379) alongside its own 44, and its FSRS fields sat on the twin's
# 2026-08-01 grade instead of the survivor's 2026-07-31 one.
REPAIRS: tuple[CrossedRepair, ...] = (
    CrossedRepair(collocation_id=1478, direction="recognition", survivor_card_id=1696398300233),
)


def plan_repair(
    tt_conn: sqlite3.Connection,
    anki_conn: sqlite3.Connection,
    ops: tuple[CrossedRepair, ...] | list[CrossedRepair] = REPAIRS,
) -> list[RepairPlan]:
    """Resolve *ops* into plans, refusing anything unsafe.

    Raises ValueError if the direction does not already point at the survivor
    (re-pointing is a different repair and must be deliberate) or if the survivor
    card is absent from Anki (nothing to anchor to).
    """
    plans: list[RepairPlan] = []
    for op in ops:
        row = tt_conn.execute(
            "SELECT anki_card_id FROM collocation_directions WHERE collocation_id = ? AND direction = ?",
            (op.collocation_id, op.direction),
        ).fetchone()
        if row is None:
            raise ValueError(f"collocation {op.collocation_id}/{op.direction} not found in TT")
        if row["anki_card_id"] != op.survivor_card_id:
            raise ValueError(
                f"collocation {op.collocation_id}/{op.direction} points at card"
                f" {row['anki_card_id']}, not survivor {op.survivor_card_id}"
            )

        card = anki_conn.execute("SELECT data FROM cards WHERE id = ?", (op.survivor_card_id,)).fetchone()
        if card is None:
            raise ValueError(f"survivor card {op.survivor_card_id} not found in Anki")
        data = json.loads(card["data"]) if card["data"] else {}

        owner = dict(anki_conn.execute("SELECT id, cid FROM revlog"))
        stray = [
            r["id"]
            for r in tt_conn.execute(
                "SELECT id FROM tt_revlog WHERE collocation_id = ? AND direction = ?",
                (op.collocation_id, op.direction),
            )
            if owner.get(r["id"]) is not None and owner[r["id"]] != op.survivor_card_id
        ]
        plans.append(
            RepairPlan(
                op=op,
                stray_revlog_ids=stray,
                stability=data.get("s", 0.0),
                difficulty=data.get("d", 0.0),
                last_review=datetime.fromtimestamp(data["lrt"], UTC).isoformat(),
            )
        )
    return plans


def apply_repair(tt_conn: sqlite3.Connection, plans: list[RepairPlan]) -> dict[str, int]:
    """Prune stray rows and re-anchor each direction. Returns counts of rows touched."""
    counts = {"revlog_rows_pruned": 0, "directions_reanchored": 0}
    for plan in plans:
        for rid in plan.stray_revlog_ids:
            tt_conn.execute("DELETE FROM tt_revlog WHERE id = ?", (rid,))
            counts["revlog_rows_pruned"] += 1
        tt_conn.execute(
            "UPDATE collocation_directions SET stability = ?, fsrs_difficulty = ?, last_review = ?,"
            " last_review_time_ms = 0, last_rating = NULL"
            " WHERE collocation_id = ? AND direction = ?",
            (plan.stability, plan.difficulty, plan.last_review, plan.op.collocation_id, plan.op.direction),
        )
        counts["directions_reanchored"] += 1
    if plans:
        # DB-backed and restart-surviving: the stale order replays until sync
        # unless it is dropped here (anki-queue-parity.md rule 2).
        tt_conn.execute("DELETE FROM anki_state_cache WHERE key = 'session_main_queue'")
    tt_conn.commit()
    return counts


def main() -> None:  # pragma: no cover - CLI glue
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the repair (default is a dry run)")
    parser.add_argument("--language", default=None, help="language code (default: settings.target_language)")
    args = parser.parse_args()

    from app.config import settings
    from app.languages import resolve_language_context
    from app.plugins.anki_sync.safety import safe_open
    from app.srs.database import SRSDatabase

    lang = resolve_language_context(args.language or settings.target_language, settings)
    print(f"language={lang.code}  db={lang.db_url}")
    db = SRSDatabase(lang.db_url)
    with safe_open(settings.anki_collection_path, mode="ro") as ctx, db._get_conn() as tt_conn:
        tt_conn.row_factory = sqlite3.Row
        plans = plan_repair(tt_conn, ctx.conn)
        for plan in plans:
            print(
                f"  coll {plan.op.collocation_id}/{plan.op.direction}:"
                f" prune {len(plan.stray_revlog_ids)} revlog rows;"
                f" anchor s={plan.stability} d={plan.difficulty} last_review={plan.last_review}"
            )
        if args.apply:
            print(f"Applied: {apply_repair(tt_conn, plans)}")
        else:
            print("Re-run with --apply to write.")


if __name__ == "__main__":  # pragma: no cover - CLI guard
    main()
