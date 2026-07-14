"""Create a TT-managed vocabulary notetype in the Anki collection.

This is the schema-changing migration that must run **before** TT can mint its
own cards for a language whose imported deck uses a different notetype (e.g.
Norwegian, whose deck is the recognition-only 17-field "6000 Most Frequent
Norwegian Words" — TT mints into a dedicated "Norwegian Vocabulary" notetype
instead, so production cards + an Image field fit cleanly).

Inserting a notetype bumps ``col.scm`` → AnkiWeb demands a one-time full upload.
Workflow (``.claude/rules/anki-sync.md``):

1. Quit Anki (this tool needs exclusive write access via ``safe_open``).
2. ``uv run python -m app.anki.add_vocab_notetype --language no``
3. Open Anki → File → Sync → **Upload to AnkiWeb**.
4. After Anki closes again: ``uv run python -m app.anki.normalize_usns``.

Idempotent: re-running when the notetype already exists is a no-op (no col.scm
bump), so it's safe to run twice.

Usage:
    uv run python -m app.anki.add_vocab_notetype [--language no] [--dry-run]
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

from app.anki.vocab_notetype import VocabNotetype, create_vocab_notetype
from app.config import settings
from app.languages import get_vocab_notetype
from app.plugins.anki_sync.safety import safe_open


def add_vocab_notetype(conn: sqlite3.Connection, vocab: VocabNotetype, *, now_ms: int | None = None) -> str:
    """Create *vocab*'s notetype in *conn* if absent; bump ``col.scm``.

    Returns ``"created"`` or ``"exists"`` (idempotent). The caller owns the
    ``safe_open`` envelope; this commits its own transaction.
    """
    existing = conn.execute("SELECT id FROM notetypes WHERE name = ?", (vocab.name,)).fetchone()
    if existing is not None:
        return "exists"

    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    now_ts = now_ms // 1000
    max_mid = conn.execute("SELECT MAX(id) FROM notetypes").fetchone()[0] or 0
    mid = max(now_ms, max_mid + 1)

    create_vocab_notetype(conn, vocab, mid, now_ts)
    # Notetype insert is a schema change: bump col.scm (forces the one-time full
    # upload) and col.mod. Do NOT touch col.usn (Layer 61). The inserted
    # notetype/field/template rows already carry usn = -1.
    conn.execute("UPDATE col SET scm = ?, mod = ?", (now_ms, now_ms))
    conn.commit()
    return "created"


def run(
    language_code: str | None = None,
    anki_collection_path: Path | None = None,
    anki_backup_dir: Path | None = None,
    dry_run: bool = False,
) -> str:
    """Resolve the language's vocab notetype and create it in the collection.

    Returns ``"created"``, ``"exists"``, or ``"dry-run"``.
    """
    code = language_code if language_code is not None else settings.target_language
    vocab = get_vocab_notetype(code)
    if vocab is None:
        raise ValueError(f"Language {code!r} has no TT-managed vocab notetype configured")

    if anki_collection_path is None:
        anki_collection_path = settings.anki_collection_path
    if anki_backup_dir is None:
        anki_backup_dir = settings.anki_backup_dir

    with safe_open(anki_collection_path, backup_dir=anki_backup_dir, mode="rw") as ctx:
        already = ctx.conn.execute("SELECT 1 FROM notetypes WHERE name = ?", (vocab.name,)).fetchone()
        if dry_run:
            status = "exists" if already else "created"
            print(f"[DRY RUN] notetype {vocab.name!r} would be: {status}", flush=True)
            return "dry-run"
        result = add_vocab_notetype(ctx.conn, vocab)

    if result == "created":
        print(
            f"[DONE] Created notetype {vocab.name!r} (col.scm bumped).\n"
            "  Next: open Anki → File → Sync → Upload to AnkiWeb, then run\n"
            "        uv run python -m app.anki.normalize_usns",
            flush=True,
        )
    else:
        print(f"[SKIP] Notetype {vocab.name!r} already exists — no change.", flush=True)
    return result


def _cli() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Create a TT-managed vocab notetype (schema change)")
    parser.add_argument("--language", default=None, help="Language code (default: settings.target_language)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(language_code=args.language, dry_run=args.dry_run)


if __name__ == "__main__":  # pragma: no cover
    _cli()
