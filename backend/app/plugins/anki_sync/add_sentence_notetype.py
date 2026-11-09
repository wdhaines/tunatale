"""Create the "TT Sentence" notetype in the Anki collection (tunatale-h0q3.55).

A lesson line's cards share one note of this cloze notetype, so Anki buries them
as siblings (``app/cards/sentence_notetype.py``). Inserting a notetype bumps
``col.scm``, so AnkiWeb then demands a one-time full upload. Workflow
(``.claude/rules/anki-sync.md``):

1. Quit Anki (this tool needs exclusive write access via ``safe_open``).
2. ``uv run python -m app.plugins.anki_sync.add_sentence_notetype``
3. Open Anki → File → Sync → **Upload to AnkiWeb**.
4. After Anki closes again: ``uv run python -m app.plugins.anki_sync.normalize_usns``.
5. Re-anchor TT's own sync mirror (download-only)::

       uv run python -m app.plugins.anki_sync.sync_orchestrator --bootstrap

Idempotent: when the notetype exists it is a no-op, with no ``col.scm`` bump.

Usage:
    uv run python -m app.plugins.anki_sync.add_sentence_notetype [--dry-run]
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

from app.cards.sentence_notetype import NAME, create_sentence_notetype
from app.config import settings
from app.plugins.anki_sync.safety import safe_open


def add_sentence_notetype(conn: sqlite3.Connection, *, now_ms: int | None = None) -> str:
    """Create the notetype in *conn* if absent and bump ``col.scm``: ``"created"`` or ``"exists"``.

    The caller owns the ``safe_open`` envelope; this commits its own transaction.
    """
    if conn.execute("SELECT 1 FROM notetypes WHERE name = ?", (NAME,)).fetchone() is not None:
        return "exists"
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    max_mid = conn.execute("SELECT MAX(id) FROM notetypes").fetchone()[0] or 0
    create_sentence_notetype(conn, max(now_ms, max_mid + 1), now_ms // 1000)
    # A schema change: col.scm forces the one-time full upload. Never col.usn (Layer 61).
    conn.execute("UPDATE col SET scm = ?, mod = ?", (now_ms, now_ms))
    conn.commit()
    return "created"


def run(anki_collection_path: Path | None = None, anki_backup_dir: Path | None = None, dry_run: bool = False) -> str:
    """Create the notetype in the collection: ``"created"``, ``"exists"`` or ``"dry-run"``."""
    path = anki_collection_path if anki_collection_path is not None else settings.anki_collection_path
    backup = anki_backup_dir if anki_backup_dir is not None else settings.anki_backup_dir
    with safe_open(path, backup_dir=backup, mode="rw") as ctx:
        if dry_run:
            exists = ctx.conn.execute("SELECT 1 FROM notetypes WHERE name = ?", (NAME,)).fetchone()
            print(f"[DRY RUN] notetype {NAME!r} would be: {'exists' if exists else 'created'}", flush=True)
            return "dry-run"
        result = add_sentence_notetype(ctx.conn)
    if result == "created":
        print(
            f"[DONE] Created notetype {NAME!r} (col.scm bumped).\n"
            "  Next: open Anki → File → Sync → Upload to AnkiWeb, then run\n"
            "        uv run python -m app.plugins.anki_sync.normalize_usns",
            flush=True,
        )
    else:
        print(f"[SKIP] Notetype {NAME!r} already exists — no change.", flush=True)
    return result


def _cli() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Create the TT Sentence notetype (schema change)")
    parser.add_argument("--dry-run", action="store_true")
    run(dry_run=parser.parse_args().dry_run)


if __name__ == "__main__":  # pragma: no cover
    _cli()
