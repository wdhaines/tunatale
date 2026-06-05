"""One-shot dedupe of TT collocations that both link to the same Anki note.

Three known cases left over from the LingQ-import era (April 2026):

| anki_note_id  | winner cid | winner text | loser cid | loser text       |
|---------------|------------|-------------|-----------|------------------|
| 1775264031808 | 626        | 'ulica'     | 263       | '[street/road]'  |
| 1775264032080 | 363        | 'Bog'       | 707       | 'bog'            |
| 1775264032238 | 409        | 'ura'       | 802       | 'ura'            |

`sync_pull` lookups by `anki_note_id` always return the first matching cid
(SQLite default ordering), so the duplicate's direction state — including
``anki_due`` — never gets refreshed. Layer 33's phantom-direction logic in
``_merge_directions`` (``app/api/srs.py``) then mis-classifies the duplicate
as a phantom and sinks it to the bottom of TT's new-card queue. Symptom:
ulica disappears from TT's new-queue head even though Anki shows it next.

Merge rule (per direction conflict):

- ``reps``, ``stability``, ``lapses`` → ``max(winner, loser)``
- ``state``, ``due_date``, ``last_review``, ``fsrs_difficulty`` → from the row
  with the later ``last_review`` (winner wins on tie / when both NULL)
- ``anki_card_id``, ``anki_due`` → prefer the non-NULL value; if both set, use
  the row with the later ``last_review``

Loser's media + tags get dropped (winner already has the matching files, per
audit). Run ``import_seed`` afterwards to refresh any remaining ``anki_due``
NULLs from Anki's current state.

Usage::

    uv run python -m app.anki.dedupe_tt_collocations --dry-run
    uv run python -m app.anki.dedupe_tt_collocations
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from app.config import settings


@dataclass(frozen=True)
class DedupePair:
    winner_cid: int
    loser_cid: int


DEDUPE_PAIRS: tuple[DedupePair, ...] = (
    DedupePair(winner_cid=626, loser_cid=263),  # ulica vs [street/road]
    DedupePair(winner_cid=363, loser_cid=707),  # Bog vs bog
    DedupePair(winner_cid=409, loser_cid=802),  # ura vs ura
)


_DIR_COLS = (
    "state",
    "reps",
    "lapses",
    "stability",
    "fsrs_difficulty",
    "due_date",
    "last_review",
    "anki_card_id",
    "anki_due",
)


def _direction_state_for_cid(conn: sqlite3.Connection, cid: int, direction: str) -> dict | None:
    row = conn.execute(
        f"SELECT {', '.join(_DIR_COLS)} FROM collocation_directions WHERE collocation_id = ? AND direction = ?",
        (cid, direction),
    ).fetchone()
    if row is None:
        return None
    return dict(zip(_DIR_COLS, row, strict=True))


def _later_last_review(winner: dict, loser: dict) -> dict:
    """Return whichever row has the later ``last_review`` (winner wins on tie / NULL)."""
    w_lr = winner.get("last_review") or ""
    l_lr = loser.get("last_review") or ""
    return loser if l_lr > w_lr else winner


def _merge_direction_rows(winner: dict, loser: dict) -> dict:
    """Combine the two per-direction rows using the documented merge rule."""
    later = _later_last_review(winner, loser)
    merged = dict(later)
    # max metrics regardless of which row is "later"
    merged["reps"] = max(winner["reps"], loser["reps"])
    merged["stability"] = max(winner["stability"], loser["stability"])
    merged["lapses"] = max(winner["lapses"], loser["lapses"])
    # prefer non-NULL anki_card_id / anki_due (using the later row first, then the other)
    other = winner if later is loser else loser
    if merged["anki_card_id"] is None and other["anki_card_id"] is not None:
        merged["anki_card_id"] = other["anki_card_id"]
    if merged["anki_due"] is None and other["anki_due"] is not None:
        merged["anki_due"] = other["anki_due"]
    return merged


def apply_dedupe(conn: sqlite3.Connection, pair: DedupePair) -> bool:
    """Merge the loser's direction state into the winner; drop the loser.

    Returns True if both collocations existed and were merged; False otherwise.
    """
    winner = conn.execute("SELECT id FROM collocations WHERE id = ?", (pair.winner_cid,)).fetchone()
    loser = conn.execute("SELECT id FROM collocations WHERE id = ?", (pair.loser_cid,)).fetchone()
    if winner is None or loser is None:
        return False

    loser_dirs = conn.execute(
        "SELECT direction FROM collocation_directions WHERE collocation_id = ?",
        (pair.loser_cid,),
    ).fetchall()
    for loser_dir_row in loser_dirs:
        direction = loser_dir_row[0]
        winner_state = _direction_state_for_cid(conn, pair.winner_cid, direction)
        loser_state = _direction_state_for_cid(conn, pair.loser_cid, direction)
        assert loser_state is not None  # we just selected it  # noqa: S101
        if winner_state is None:
            conn.execute(
                "INSERT INTO collocation_directions "
                "(collocation_id, direction, state, reps, lapses, stability, fsrs_difficulty, "
                "due_date, last_review, anki_card_id, anki_due) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    pair.winner_cid,
                    direction,
                    loser_state["state"],
                    loser_state["reps"],
                    loser_state["lapses"],
                    loser_state["stability"],
                    loser_state["fsrs_difficulty"],
                    loser_state["due_date"],
                    loser_state["last_review"],
                    loser_state["anki_card_id"],
                    loser_state["anki_due"],
                ),
            )
        else:
            merged = _merge_direction_rows(winner_state, loser_state)
            conn.execute(
                "UPDATE collocation_directions SET state = ?, reps = ?, lapses = ?, "
                "stability = ?, fsrs_difficulty = ?, due_date = ?, last_review = ?, "
                "anki_card_id = ?, anki_due = ? "
                "WHERE collocation_id = ? AND direction = ?",
                (
                    merged["state"],
                    merged["reps"],
                    merged["lapses"],
                    merged["stability"],
                    merged["fsrs_difficulty"],
                    merged["due_date"],
                    merged["last_review"],
                    merged["anki_card_id"],
                    merged["anki_due"],
                    pair.winner_cid,
                    direction,
                ),
            )

    conn.execute("DELETE FROM collocation_directions WHERE collocation_id = ?", (pair.loser_cid,))
    conn.execute("DELETE FROM media WHERE collocation_id = ?", (pair.loser_cid,))
    conn.execute("DELETE FROM collocation_tags WHERE collocation_id = ?", (pair.loser_cid,))
    conn.execute("DELETE FROM collocations WHERE id = ?", (pair.loser_cid,))
    conn.commit()
    return True


def _print_plan() -> None:
    print(f"Plan: merge {len(DEDUPE_PAIRS)} TT collocation pair(s)")
    for p in DEDUPE_PAIRS:
        print(f"  winner_cid={p.winner_cid}  loser_cid={p.loser_cid}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    parser.add_argument("--dry-run", action="store_true", help="show plan without writing")
    parser.add_argument("--tt-db", type=Path, default=None, help="override TT database path")
    args = parser.parse_args(argv)

    tt_path = args.tt_db or Path(settings.database_url.removeprefix("sqlite:///"))
    if not tt_path.exists():
        print(f"TT database not found: {tt_path}", file=sys.stderr)
        return 1

    _print_plan()
    if args.dry_run:
        print("--dry-run: no changes applied.")
        return 0

    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    try:
        counts = {"merged": 0}
        for pair in DEDUPE_PAIRS:
            if apply_dedupe(conn, pair):
                counts["merged"] += 1
        print(f"Applied: {counts}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
