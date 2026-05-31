"""Re-lemmatize single-word collocations using the configured lemmatizer (e.g. classla).

For each single-word collation whose recomputed lemma differs from its stored lemma:

- If the target lemma already has a collocation: **MERGE** the inflection's direction
  state into the base, union directions, preserve ``anki_note_id``/``anki_card_id``
  linkage, then delete the inflection row.
- Otherwise: **re-key** (UPDATE ``lemma``) the row.

Invariant: never leave two collocations sharing one ``anki_note_id``.

Usage::

    uv run python -m app.srs.relemmatize_collocations --dry-run
    uv run python -m app.srs.relemmatize_collocations --apply
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from app.config import settings

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
    w_lr = winner.get("last_review") or ""
    l_lr = loser.get("last_review") or ""
    return loser if l_lr > w_lr else winner


def _merge_direction_rows(winner: dict, loser: dict) -> dict:
    later = _later_last_review(winner, loser)
    merged = dict(later)
    merged["reps"] = max(winner["reps"], loser["reps"])
    merged["stability"] = max(winner["stability"], loser["stability"])
    merged["lapses"] = max(winner["lapses"], loser["lapses"])
    other = winner if later is loser else loser
    if merged["anki_card_id"] is None and other["anki_card_id"] is not None:
        merged["anki_card_id"] = other["anki_card_id"]
    if merged["anki_due"] is None and other["anki_due"] is not None:
        merged["anki_due"] = other["anki_due"]
    return merged


def _merge_inflection_into_base(
    conn: sqlite3.Connection,
    inflection_id: int,
    base_id: int,
    base_lemma: str,
) -> None:
    """Merge direction state from inflection row into base; delete inflection."""
    inflection_dirs = conn.execute(
        "SELECT direction FROM collocation_directions WHERE collocation_id = ?",
        (inflection_id,),
    ).fetchall()
    for dir_row in inflection_dirs:
        direction = dir_row[0]
        base_state = _direction_state_for_cid(conn, base_id, direction)
        inflection_state = _direction_state_for_cid(conn, inflection_id, direction)
        assert inflection_state is not None
        if base_state is None:
            conn.execute(
                "INSERT INTO collocation_directions "
                "(collocation_id, direction, state, reps, lapses, stability, fsrs_difficulty, "
                "due_date, last_review, anki_card_id, anki_due) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    base_id,
                    direction,
                    inflection_state["state"],
                    inflection_state["reps"],
                    inflection_state["lapses"],
                    inflection_state["stability"],
                    inflection_state["fsrs_difficulty"],
                    inflection_state["due_date"],
                    inflection_state["last_review"],
                    inflection_state["anki_card_id"],
                    inflection_state["anki_due"],
                ),
            )
        else:
            merged = _merge_direction_rows(base_state, inflection_state)
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
                    base_id,
                    direction,
                ),
            )

    conn.execute("DELETE FROM collocation_directions WHERE collocation_id = ?", (inflection_id,))
    conn.execute("DELETE FROM media WHERE collocation_id = ?", (inflection_id,))
    conn.execute("DELETE FROM collocation_tags WHERE collocation_id = ?", (inflection_id,))
    conn.execute("DELETE FROM collocations WHERE id = ?", (inflection_id,))


def relemmatize(conn: sqlite3.Connection, *, dry_run: bool) -> dict:
    """Re-lemmatize all single-word collocations.

    Returns an audit dict with counts of merges, re-keys, and errors.
    """
    from app.srs.lemmatizer import get_lemmatizer

    lemmatizer = get_lemmatizer()

    rows = conn.execute(
        "SELECT id, text, lemma, anki_note_id FROM collocations WHERE word_count = 1 AND lemma IS NOT NULL"
    ).fetchall()

    audit: dict = {"scanned": 0, "rekeyed": 0, "merged": 0, "errors": []}

    for row in rows:
        cid, text, stored_lemma, anki_note_id = row
        audit["scanned"] += 1
        computed_lemma = lemmatizer.lemmatize(text, "sl")
        if computed_lemma.casefold() == (stored_lemma or text).casefold():
            continue

        if dry_run:
            continue

        # Check if target lemma already has a collocation
        base = conn.execute(
            "SELECT id, anki_note_id FROM collocations WHERE lemma = ? AND id != ? AND word_count = 1",
            (computed_lemma, cid),
        ).fetchone()

        if base is not None:
            base_id, base_anki_note_id = base
            if base_anki_note_id is not None and anki_note_id is not None and base_anki_note_id != anki_note_id:
                audit["errors"].append(
                    f"cid={cid} ('{text}'): merge would create duplicate anki_note_id "
                    f"(base={base_anki_note_id}, inflection={anki_note_id}) — skipping"
                )
                continue
            try:
                _merge_inflection_into_base(conn, cid, base_id, computed_lemma)
                conn.commit()
                audit["merged"] += 1
            except Exception as e:
                conn.rollback()
                audit["errors"].append(f"cid={cid} ('{text}'): merge failed: {e}")
        else:
            conn.execute("UPDATE collocations SET lemma = ? WHERE id = ?", (computed_lemma, cid))
            conn.commit()
            audit["rekeyed"] += 1

    # Verify invariant: no duplicate anki_note_id
    dups = conn.execute(
        "SELECT anki_note_id, COUNT(*) FROM collocations "
        "WHERE anki_note_id IS NOT NULL "
        "GROUP BY anki_note_id HAVING COUNT(*) > 1"
    ).fetchall()
    if dups:
        audit["errors"].append(f"Duplicate anki_note_id found after merge: {dict(dups)}")

    return audit


def _print_audit(audit: dict) -> None:
    print(f"Scanned: {audit['scanned']}")
    print(f"Re-keyed: {audit['rekeyed']}")
    print(f"Merged: {audit['merged']}")
    if audit["errors"]:
        print(f"Errors ({len(audit['errors'])}):")
        for err in audit["errors"]:
            print(f"  {err}")
    else:
        print("Errors: 0")
    print("Invariant check: ", "PASS" if not audit["errors"] else "FAIL")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    parser.add_argument("--apply", action="store_true", help="actually apply changes (default: dry-run)")
    parser.add_argument("--tt-db", type=Path, default=None, help="override TT database path")
    args = parser.parse_args(argv)

    tt_path = args.tt_db or Path(settings.database_url.removeprefix("sqlite:///"))
    if not tt_path.exists():
        print(f"TT database not found: {tt_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    try:
        audit = relemmatize(conn, dry_run=not args.apply)
        _print_audit(audit)
        if not args.apply:
            print("\n--dry-run: no changes applied. Use --apply to write.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
