"""Delete the 13 phonology-demo notes the LingQ fix-up script created.

Each of these 13 notes was originally a Basic note carrying a phonology rule
(IPA, Forvo link, sound-change explanation). `fix_lingq_import_mess.py` converted
them in place to Slovene-Vocabulary and added an ord=1 Production card. The
Production card's front is `{{Image}}` (empty), which renders as a blank card
in Anki, and a question-prompted "How is X pronounced?" Basic sibling already
covers 11 of these words. The other 2 (iskra, ovca) the user opted to delete
too.

This is an Anki delete via the `graves` table, NOT a schema change. It bumps
``col.mod`` but leaves ``col.scm`` untouched — incremental sync, no forced
full upload. After running, a normal AnkiWeb sync propagates the deletions.

Mirrors Anki's own delete flow (``rslib/src/notes/mod.rs:502-515``): one
type=0 grave per card + one type=1 grave for the note, all with ``usn=-1``.

Usage::

    uv run python -m app.anki.delete_phonology_demos --dry-run
    uv run python -m app.anki.delete_phonology_demos     # apply
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from app.config import settings

# Hardcoded NIDs of the 13 phonology-demo notes in deck "0. Slovene".
# Order: iskra, beseda, vesel, pot, trg, Ljubljana, ovca, grob, grad, mraz,
#        prišel, rekel, videl.
PHONOLOGY_DEMO_NIDS: tuple[int, ...] = (
    1774631982032,  # iskra (spark) — "i = /i/"
    1774631982037,  # beseda (word) — vowel demo
    1774631982039,  # vesel (happy) — schwa
    1774631982041,  # pot (path) — long stressed close-mid o
    1774631982043,  # trg (town square) — syllabic r
    1774631982047,  # Ljubljana — j before vowels
    1774631982052,  # ovca (sheep) — v after vowel → [u̯]
    1774631982055,  # grob (grave) — final b → [p]
    1774631982056,  # grad (castle) — final d → [t]
    1774631982058,  # mraz (frost) — final z → [s]
    1774631982060,  # prišel (he arrived) — final l → [u̯]
    1774631982061,  # rekel (he said) — final l → [u̯]
    1774631982062,  # videl (he saw) — final l → [u̯]
)

_GRAVE_KIND_CARD = 0
_GRAVE_KIND_NOTE = 1


@dataclass(frozen=True)
class DeleteRecord:
    """One note + its cards + linked TT collocation to remove."""

    anki_nid: int
    anki_cids: tuple[int, ...]
    tt_collocation_id: int | None


def plan_deletes(
    anki_conn: sqlite3.Connection,
    tt_conn: sqlite3.Connection,
) -> list[DeleteRecord]:
    """Return DeleteRecord for each PHONOLOGY_DEMO_NIDS row that actually exists in Anki.

    Pure: read-only on both connections.
    """
    items: list[DeleteRecord] = []
    for nid in PHONOLOGY_DEMO_NIDS:
        row = anki_conn.execute("SELECT id FROM notes WHERE id = ?", (nid,)).fetchone()
        if row is None:
            continue
        cids = tuple(
            r[0] for r in anki_conn.execute("SELECT id FROM cards WHERE nid = ? ORDER BY ord", (nid,)).fetchall()
        )
        tt_row = tt_conn.execute("SELECT id FROM collocations WHERE anki_note_id = ?", (nid,)).fetchone()
        items.append(
            DeleteRecord(
                anki_nid=nid,
                anki_cids=cids,
                tt_collocation_id=tt_row[0] if tt_row else None,
            )
        )
    return items


def apply_deletes(
    anki_conn: sqlite3.Connection,
    tt_conn: sqlite3.Connection,
    items: list[DeleteRecord],
) -> dict[str, int]:
    """Apply the delete plan. Returns counts of rows touched.

    Anki: one card-grave (type=0) per card + one note-grave (type=1) per note;
    all graves carry usn=-1. Then DELETE the underlying rows. col.mod is bumped
    and col.usn=-1; col.scm is NOT touched (data-only mutation).

    TT: cascade delete the collocation row + its directions.
    """
    counts = {"notes_deleted": 0, "cards_deleted": 0, "tt_collocations_deleted": 0}
    if not items:
        return counts

    now_ms = int(time.time() * 1000)

    for item in items:
        for cid in item.anki_cids:
            anki_conn.execute(
                "INSERT OR REPLACE INTO graves (oid, type, usn) VALUES (?, ?, -1)",
                (cid, _GRAVE_KIND_CARD),
            )
            anki_conn.execute("DELETE FROM cards WHERE id = ?", (cid,))
            counts["cards_deleted"] += 1
        anki_conn.execute(
            "INSERT OR REPLACE INTO graves (oid, type, usn) VALUES (?, ?, -1)",
            (item.anki_nid, _GRAVE_KIND_NOTE),
        )
        anki_conn.execute("DELETE FROM notes WHERE id = ?", (item.anki_nid,))
        counts["notes_deleted"] += 1

        if item.tt_collocation_id is not None:
            tt_conn.execute(
                "DELETE FROM collocation_directions WHERE collocation_id = ?",
                (item.tt_collocation_id,),
            )
            tt_conn.execute("DELETE FROM collocations WHERE id = ?", (item.tt_collocation_id,))
            counts["tt_collocations_deleted"] += 1

    anki_conn.execute("UPDATE col SET mod = ?, usn = -1", (now_ms,))
    anki_conn.commit()
    tt_conn.commit()
    return counts


def _print_plan(items: list[DeleteRecord]) -> None:
    print(f"Plan: delete {len(items)} note(s)")
    for it in items:
        cid_str = ", ".join(str(c) for c in it.anki_cids)
        tt_str = f"tt_cid={it.tt_collocation_id}" if it.tt_collocation_id is not None else "tt=(none)"
        print(f"  nid={it.anki_nid} cards=[{cid_str}] {tt_str}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    parser.add_argument("--dry-run", action="store_true", help="show the plan without writing")
    parser.add_argument("--anki-db", type=Path, default=None, help="override Anki collection path")
    parser.add_argument("--tt-db", type=Path, default=None, help="override TT database path")
    args = parser.parse_args(argv)

    anki_path = args.anki_db or settings.anki_collection_path
    tt_path = args.tt_db or Path(settings.database_url.removeprefix("sqlite:///"))

    if not anki_path.exists():
        print(f"Anki collection not found: {anki_path}", file=sys.stderr)
        return 1
    if not tt_path.exists():
        print(f"TT database not found: {tt_path}", file=sys.stderr)
        return 1

    if args.dry_run:
        from app.anki.safety import _register_anki_collations

        anki_conn = sqlite3.connect(f"file:{anki_path}?mode=ro", uri=True)
        _register_anki_collations(anki_conn)
        tt_conn = sqlite3.connect(str(tt_path))
        try:
            items = plan_deletes(anki_conn, tt_conn)
            if not items:
                print("Nothing to delete.")
            else:
                _print_plan(items)
                print("--dry-run: no changes applied.")
            return 0
        finally:
            anki_conn.close()
            tt_conn.close()

    from app.anki.safety import safe_open

    tt_conn = sqlite3.connect(str(tt_path), isolation_level=None)
    try:
        with safe_open(anki_path, mode="rw") as ctx:
            anki_conn = ctx.conn
            items = plan_deletes(anki_conn, tt_conn)
            if not items:
                print("Nothing to delete.")
                return 0
            _print_plan(items)
            counts = apply_deletes(anki_conn, tt_conn, items)
            print(f"Applied: {counts}")
    finally:
        tt_conn.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
