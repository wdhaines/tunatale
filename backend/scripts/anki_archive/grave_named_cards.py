"""Remove specific cards by their text, the Anki-safe way (tunatale-u8nz.20).

Written for the Cebuano stage-2 conversion: affixed vocab cards (``matulog``,
``magkita``, ``nagdala``, ``mangape``) whose roots are already cards go, so the
decks match how new cards work — root card, affixed form as an on-demand cloze.

The Anki half is ``grave_ignored_lemma_cards.apply_graves`` unchanged: one
``type=0`` grave per card and one ``type=1`` per note, all ``usn=-1``, then the
rows go; ``col.mod`` bumped, ``col.usn`` and ``col.scm`` untouched
(`.claude/rules/anki-sync.md` §Deletes). The next peer-sync pushes the graves.

``--no-anki`` is for a learner deck with no Anki behind it: TT rows only. It
REFUSES a row that was pushed (``anki_note_id`` set), because removing that
from TT alone would let the next sync resurrect it from the collection.

The collection defaults to ``settings.tt_collection_path`` — the one TunaTale
syncs through — not the desktop collection.

Usage (on the LIVE side, under its env; dry-run first)::

    python -m scripts.anki_archive.grave_named_cards --language ceb --texts matulog,magkita --dry-run
    python -m scripts.anki_archive.grave_named_cards --language ceb --texts matulog,magkita
    python -m scripts.anki_archive.grave_named_cards --language ceb --tt-db <learner db> --no-anki --texts ...
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from app.config import settings
from scripts.anki_archive.grave_ignored_lemma_cards import (
    GraveRecord,
    _open_tt,
    _print_plan,
    _resolve_tt_db_path,
    apply_graves,
)


def plan_by_texts(
    anki_conn: sqlite3.Connection | None,
    tt_conn: sqlite3.Connection,
    language_code: str,
    texts: list[str],
) -> tuple[list[GraveRecord], list[str]]:
    """``(plan, names with no row)`` for *texts* in *language_code*, matched case-insensitively.

    With no *anki_conn*, a pushed row raises ``ValueError`` (see the module docstring).
    A pushed row whose note is already gone from the collection plans TT-only.
    """
    plan: list[GraveRecord] = []
    missing: list[str] = []
    for text in texts:
        rows = tt_conn.execute(
            "SELECT id, text, anki_note_id FROM collocations WHERE lower(text) = lower(?) AND language_code = ?"
            " ORDER BY id",
            (text, language_code),
        ).fetchall()
        if not rows:
            missing.append(text)
        for row_id, row_text, nid in rows:
            if nid is not None and anki_conn is None:
                raise ValueError(f"{row_text!r} was pushed to Anki (note {nid}); it needs a collection, not --no-anki")
            anki_nid: int | None = None
            cids: tuple[int, ...] = ()
            if nid is not None and anki_conn.execute("SELECT 1 FROM notes WHERE id = ?", (nid,)).fetchone():
                anki_nid = nid
                cids = tuple(r[0] for r in anki_conn.execute("SELECT id FROM cards WHERE nid = ? ORDER BY ord", (nid,)))
            plan.append(GraveRecord(text=row_text, anki_nid=anki_nid, anki_cids=cids, tt_collocation_id=row_id))
    return plan, missing


def _run(anki_conn: sqlite3.Connection | None, tt_conn: sqlite3.Connection, args) -> int:
    texts = [t.strip() for t in args.texts.split(",") if t.strip()]
    plan, missing = plan_by_texts(anki_conn, tt_conn, args.language, texts)
    if missing:
        print(f"No {args.language} card for: {', '.join(missing)} — nothing written.", file=sys.stderr)
        return 1
    _print_plan(plan, "named card(s)")
    if args.dry_run:
        print("--dry-run: no changes applied.")
        return 0
    if anki_conn is None:
        for item in plan:
            tt_conn.execute("DELETE FROM collocation_directions WHERE collocation_id = ?", (item.tt_collocation_id,))
            tt_conn.execute("DELETE FROM collocations WHERE id = ?", (item.tt_collocation_id,))
        tt_conn.commit()
        print(f"Applied: {{'tt_collocations_deleted': {len(plan)}}}")
        return 0
    print(f"Applied: {apply_graves(anki_conn, tt_conn, plan)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grave named cards (TT + Anki).")
    parser.add_argument("--language", required=True)
    parser.add_argument("--texts", required=True, help="comma-separated card texts")
    parser.add_argument("--tt-db", type=Path, default=None, help="TT database (default: the language's)")
    parser.add_argument("--anki-db", type=Path, default=None, help="collection (default: settings.tt_collection_path)")
    parser.add_argument("--no-anki", action="store_true", help="a deck with no Anki: TT rows only")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    tt_path = _resolve_tt_db_path(args.tt_db, args.language)
    tt_conn = _open_tt(tt_path)
    try:
        if args.no_anki:
            return _run(None, tt_conn, args)
        anki_path = args.anki_db or settings.tt_collection_path
        if not Path(anki_path).exists():
            print(f"Collection not found: {anki_path}", file=sys.stderr)
            return 1
        from app.plugins.anki_sync.safety import safe_open

        with safe_open(Path(anki_path), mode="ro" if args.dry_run else "rw") as ctx:
            return _run(ctx.conn, tt_conn, args)
    finally:
        tt_conn.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
