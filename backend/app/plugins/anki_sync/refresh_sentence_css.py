"""Bring the "TT Sentence" notetype's CSS up to date (tunatale-h0q3.55).

TT owns the whole notetype (``app/cards/sentence_notetype.py``), so its CSS is
replaced outright with ``sentence_notetype.CSS``: the notetype ends up exactly as
``add_sentence_notetype`` would build it today.

Data-only: a notetype's CSS syncs incrementally, so ``col.scm`` does not move and
no full upload is needed. The notetype row is marked dirty (``usn = -1``,
``mtime_secs = now``) and ``col.mod`` is bumped; ``col.usn`` is never touched
(Layer 61, ``.claude/rules/anki-sync.md``). Idempotent.

⚠️ Defaults to ``settings.tt_collection_path``: the collection live peer-sync
pushes from. Run it there, then sync; the phone and the desktop receive it at
their next sync.

    uv run python -m app.plugins.anki_sync.refresh_sentence_css --dry-run
    uv run python -m app.plugins.anki_sync.refresh_sentence_css
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

from app.cards.sentence_notetype import CSS, NAME
from app.config import settings
from app.plugins.anki_sync.safety import safe_open
from app.srs.anki_mirror.protobuf_wire import find_len_field, pb_replace_or_insert_len

_CSS_FIELD = 3  # NotetypeConfig css


def _notetype(conn: sqlite3.Connection) -> tuple[int, bytes, str]:
    row = conn.execute("SELECT id, config FROM notetypes WHERE name = ?", (NAME,)).fetchone()
    if row is None:
        raise ValueError(f"Notetype {NAME!r} not found: run add_sentence_notetype first")
    config = bytes(row[1] or b"")
    return row[0], config, (find_len_field(config, _CSS_FIELD) or b"").decode()


def refresh_sentence_css(conn: sqlite3.Connection, *, now_ms: int | None = None) -> str:
    """Set the notetype's CSS in *conn* to the current one. Returns ``"updated"`` or ``"current"``.

    The caller owns the ``safe_open`` envelope; this commits its own transaction.
    """
    mid, config, css = _notetype(conn)
    if css == CSS:
        return "current"
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    conn.execute(
        "UPDATE notetypes SET config = ?, mtime_secs = ?, usn = -1 WHERE id = ?",
        (pb_replace_or_insert_len(config, _CSS_FIELD, CSS.encode()), now_ms // 1000, mid),
    )
    conn.execute("UPDATE col SET mod = ?", (now_ms,))  # NOT scm (data-only), NOT usn (Layer 61)
    conn.commit()
    return "updated"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--collection", type=Path, default=None, help="default: settings.tt_collection_path")
    parser.add_argument("--backup-dir", type=Path, default=None, help="default: settings.anki_backup_dir")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    path = args.collection or settings.tt_collection_path
    print(f"collection: {path}", flush=True)

    with safe_open(path, backup_dir=args.backup_dir or settings.anki_backup_dir, mode="rw") as ctx:
        if args.dry_run:
            status = "current" if _notetype(ctx.conn)[2] == CSS else "updated"
            print(f"[DRY RUN] the CSS of {NAME!r} would be: {status}", flush=True)
            return 0
        result = refresh_sentence_css(ctx.conn)
    if result == "updated":
        print(f"[DONE] {NAME!r} has the current CSS (data-only, no full upload). Sync to send it.", flush=True)
    else:
        print(f"[SKIP] {NAME!r} already has the current CSS — no change.", flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
