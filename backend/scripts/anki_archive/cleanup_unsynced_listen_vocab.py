"""One-shot cleanup of unsynced vocab rows created by /listen.

Before the Phase F cloze gating (May 2026), clicking "Mark as Listened"
created one vocab row per unique lemma — including proper nouns, conjugated
forms, and content words the user does not want as Anki cards. Each row
got graded "good" by default and moved to state='learning'.

This script finds and deletes those rows: TT-only write, no Anki tables
touched, no safe_open envelope needed.

Usage::

    uv run python -m app.anki.cleanup_unsynced_listen_vocab --dry-run
    uv run python -m app.anki.cleanup_unsynced_listen_vocab
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from app.config import settings


def find_unsynced_listen_vocab(conn: sqlite3.Connection) -> list[tuple[int, str, str]]:
    """Return (id, text, lemma) for unsynced /listen-created vocab rows.

    Matches rows where:
      - source = 'llm' (created by /listen, not Anki import)
      - card_type = 'vocab' (not cloze)
      - anki_note_id IS NULL (not yet synced to Anki)
      - word_count = 1 (single-word, not multi-word key phrases)
    """
    rows = conn.execute(
        """
        SELECT id, text, lemma
        FROM collocations
        WHERE source = 'llm'
          AND card_type = 'vocab'
          AND anki_note_id IS NULL
          AND word_count = 1
        """,
    ).fetchall()
    return [(r[0], r[1], r[2] if r[2] is not None else "") for r in rows]


def apply_cleanup(conn: sqlite3.Connection) -> int:
    """Delete every matching row and its child rows. Returns count."""
    rows = find_unsynced_listen_vocab(conn)
    ids = [r[0] for r in rows]
    if not ids:
        return 0

    placeholders = ",".join("?" for _ in ids)
    conn.execute(f"DELETE FROM collocation_directions WHERE collocation_id IN ({placeholders})", ids)
    conn.execute(f"DELETE FROM media WHERE collocation_id IN ({placeholders})", ids)
    conn.execute(f"DELETE FROM collocation_tags WHERE collocation_id IN ({placeholders})", ids)
    conn.execute(f"DELETE FROM collocations WHERE id IN ({placeholders})", ids)
    conn.commit()
    return len(ids)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    parser.add_argument("--dry-run", action="store_true", help="show plan without writing")
    parser.add_argument("--tt-db", type=Path, default=None, help="override TT database path")
    args = parser.parse_args(argv)

    tt_path = args.tt_db or Path(settings.database_url.removeprefix("sqlite:///"))
    if not tt_path.exists():
        print(f"TT database not found: {tt_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    try:
        rows = find_unsynced_listen_vocab(conn)
        if not rows:
            print("No unsynced /listen vocab rows found.")
            return 0
        print(f"Plan: delete {len(rows)} unsynced /listen vocab row(s)")
        for cid, text, lemma in rows:
            print(f"  cid={cid} text={text!r} lemma={lemma!r}")
        if args.dry_run:
            print("--dry-run: no changes applied.")
            return 0
        count = apply_cleanup(conn)
        print(f"Deleted: {count}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
