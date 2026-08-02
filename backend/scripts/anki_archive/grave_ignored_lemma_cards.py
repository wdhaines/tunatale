"""Grave the Anki notes for lemmas that are on TT's card-less ignore list.

TT's ``ignored_lemmas`` list only ever suppressed card *creation* — the check
lives inside the untracked branch of ``mark_lesson_listened`` /
``get_listen_preview``, deliberately (hoisting it above
``_resolve_card_for_lemma`` hides a carded-ignored lemma from the preview while
the commit still stages it: the 6a5c718 preview↔commit bug class). The
consequence is that a lemma which acquires a card by any route keeps it, and its
ignore row goes inert: the transcript stops rendering the word as ignored, the
mastery line counts it as ordinary vocabulary, and it becomes gradeable again.
``untrack_collocation`` does not close this — for a pushed row it *suspends*,
never deletes.

This script closes it after the fact: for every ignored lemma that nonetheless
has a card, remove the Anki note the Anki-safe way and drop the TT collocation.

Anki deletes go through the ``graves`` table, never a bare DELETE — see
`.claude/rules/anki-sync.md` §Deletes. Mirrors Anki's own flow
(``rslib/src/notes/mod.rs:502-515``): one ``type=0`` grave per card plus one
``type=1`` grave for the note, all ``usn=-1``, then the rows go. Data-only:
bumps ``col.mod``, leaves ``col.scm`` alone, so a normal incremental AnkiWeb
sync propagates the deletions with no forced full upload.

Usage::

    uv run python -m scripts.anki_archive.grave_ignored_lemma_cards --language no --dry-run
    uv run python -m scripts.anki_archive.grave_ignored_lemma_cards --language no
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from app.config import settings

_GRAVE_KIND_CARD = 0
_GRAVE_KIND_NOTE = 1


@dataclass(frozen=True)
class GraveRecord:
    """One ignored-but-carded lemma: the Anki note to grave + the TT row to drop.

    ``anki_nid`` is None when there is nothing to grave — either the row was
    never pushed (``anki_note_id IS NULL``) or the note is already gone from the
    collection. Both cases still clear the TT side.
    """

    text: str
    anki_nid: int | None
    anki_cids: tuple[int, ...]
    tt_collocation_id: int


def plan_graves(
    anki_conn: sqlite3.Connection,
    tt_conn: sqlite3.Connection,
    language_code: str,
) -> list[GraveRecord]:
    """Return one GraveRecord per ignored lemma that still has a TT collocation.

    Pure: read-only on both connections. Lemma matching is case-insensitive on
    both sides — the ignore list is written lowercased but the stored
    collocation text preserves the lemmatizer's casing (proper nouns like
    "Hansen" are exactly the population this script exists for).
    """
    rows = tt_conn.execute(
        """
        SELECT c.id AS cid, c.text AS text, c.anki_note_id AS nid
        FROM collocations c
        JOIN ignored_lemmas i
          ON lower(i.lemma) = lower(c.text)
         AND i.language_code = c.language_code
        WHERE c.language_code = ?
        ORDER BY c.id
        """,
        (language_code,),
    ).fetchall()

    items: list[GraveRecord] = []
    for row in rows:
        nid = row["nid"] if isinstance(row, sqlite3.Row) else row[2]
        cid = row["cid"] if isinstance(row, sqlite3.Row) else row[0]
        text = row["text"] if isinstance(row, sqlite3.Row) else row[1]

        anki_nid: int | None = None
        anki_cids: tuple[int, ...] = ()
        if nid is not None:
            # A nid that no longer resolves must NOT be graved: an oid with no
            # row behind it is noise the server has to reconcile, and the note
            # may already carry a grave from an earlier Anki-side delete.
            exists = anki_conn.execute("SELECT 1 FROM notes WHERE id = ?", (nid,)).fetchone()
            if exists is not None:
                anki_nid = nid
                anki_cids = tuple(
                    r[0] for r in anki_conn.execute("SELECT id FROM cards WHERE nid = ? ORDER BY ord", (nid,))
                )
        items.append(GraveRecord(text=text, anki_nid=anki_nid, anki_cids=anki_cids, tt_collocation_id=cid))
    return items


def apply_graves(
    anki_conn: sqlite3.Connection,
    tt_conn: sqlite3.Connection,
    items: list[GraveRecord],
) -> dict[str, int]:
    """Apply the plan. Returns counts of rows touched.

    ``col.mod`` is bumped only when something was actually graved, so a no-op
    run leaves the collection byte-identical.
    """
    counts = {"notes_graved": 0, "cards_graved": 0, "tt_collocations_deleted": 0}
    if not items:
        return counts

    for item in items:
        for cid in item.anki_cids:
            anki_conn.execute(
                "INSERT OR REPLACE INTO graves (oid, type, usn) VALUES (?, ?, -1)",
                (cid, _GRAVE_KIND_CARD),
            )
            anki_conn.execute("DELETE FROM cards WHERE id = ?", (cid,))
            counts["cards_graved"] += 1
        if item.anki_nid is not None:
            anki_conn.execute(
                "INSERT OR REPLACE INTO graves (oid, type, usn) VALUES (?, ?, -1)",
                (item.anki_nid, _GRAVE_KIND_NOTE),
            )
            anki_conn.execute("DELETE FROM notes WHERE id = ?", (item.anki_nid,))
            counts["notes_graved"] += 1

        tt_conn.execute(
            "DELETE FROM collocation_directions WHERE collocation_id = ?",
            (item.tt_collocation_id,),
        )
        tt_conn.execute("DELETE FROM collocations WHERE id = ?", (item.tt_collocation_id,))
        counts["tt_collocations_deleted"] += 1

    if counts["notes_graved"] or counts["cards_graved"]:
        # Data-only: col.mod tells Anki the collection changed; col.scm stays
        # put so this remains an incremental sync (anki-sync.md §Deletes).
        # col.usn is the sync ANCHOR (server's last USN), never a dirty flag —
        # the grave rows carry their own usn=-1, which is what pushes. Layer 61.
        anki_conn.execute("UPDATE col SET mod = ?", (int(time.time() * 1000),))
    anki_conn.commit()
    tt_conn.commit()
    return counts


def _print_plan(items: list[GraveRecord]) -> None:
    print(f"Plan: grave {len(items)} ignored-but-carded lemma(s)")
    for it in items:
        if it.anki_nid is None:
            print(f"  {it.text!r}: tt_cid={it.tt_collocation_id} (no Anki note — TT row only)")
        else:
            cids = ", ".join(str(c) for c in it.anki_cids)
            print(f"  {it.text!r}: nid={it.anki_nid} cards=[{cids}] tt_cid={it.tt_collocation_id}")


def _open_tt(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grave Anki notes for ignored lemmas.")
    parser.add_argument("--dry-run", action="store_true", help="show the plan without writing")
    parser.add_argument("--language", default=None, help="language code (default: settings.target_language)")
    parser.add_argument("--anki-db", type=Path, default=None, help="override Anki collection path")
    parser.add_argument("--tt-db", type=Path, default=None, help="override TT database path")
    args = parser.parse_args(argv)

    anki_path = args.anki_db or settings.anki_collection_path
    tt_path = args.tt_db or Path(settings.database_url.removeprefix("sqlite:///"))
    language_code = args.language or settings.target_language

    if not Path(anki_path).exists() or not Path(tt_path).exists():
        missing = anki_path if not Path(anki_path).exists() else tt_path
        print(f"Database not found: {missing}", file=sys.stderr)
        return 1

    if args.dry_run:
        from app.plugins.anki_sync.safety import _register_anki_collations

        anki_conn = sqlite3.connect(f"file:{anki_path}?mode=ro", uri=True)
        anki_conn.row_factory = sqlite3.Row
        _register_anki_collations(anki_conn)
        tt_conn = _open_tt(tt_path)
        try:
            items = plan_graves(anki_conn, tt_conn, language_code)
            if not items:
                print("Nothing to grave.")
            else:
                _print_plan(items)
                print("--dry-run: no changes applied.")
            return 0
        finally:
            anki_conn.close()
            tt_conn.close()

    from app.plugins.anki_sync.safety import safe_open

    tt_conn = _open_tt(tt_path)
    try:
        with safe_open(Path(anki_path), mode="rw") as ctx:
            items = plan_graves(ctx.conn, tt_conn, language_code)
            if not items:
                print("Nothing to grave.")
                return 0
            _print_plan(items)
            counts = apply_graves(ctx.conn, tt_conn, items)
            print(f"Applied: {counts}")
    finally:
        tt_conn.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
