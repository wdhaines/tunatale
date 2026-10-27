"""Bring a TT vocab notetype's dictionary CSS up to date (tunatale-iiwm.7).

A notetype keeps the CSS it was given when ``add_dictionary_fields`` added its
dictionary layers. When the look of a layer changes (the Verb field became a
table, the owner's layout B3 of 2026-10-28), the live notetype needs the new
block. This replaces the delimited verb-table block in its CSS
(``vocab_notetype.VERB_TABLE_CSS``), or appends it when the notetype predates
the block, so the notetype ends up exactly as ``create_vocab_notetype`` would
build it today.

Data-only: a notetype's CSS syncs incrementally, so ``col.scm`` does not move
and no full upload is needed. The notetype row is marked dirty (``usn = -1``,
``mtime_secs = now``) and ``col.mod`` is bumped; ``col.usn`` is never touched
(Layer 61, ``.claude/rules/anki-sync.md``). Run it on the collection the live
sync uses (``ANKI_COLLECTION_PATH`` = the live ``tt_collection.anki2``), then
sync, and the other devices receive it at their next sync.

Idempotent: a notetype whose block is current is left alone.

Usage:
    uv run python -m app.plugins.anki_sync.refresh_dictionary_css --language <code> [--dry-run]
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

from app.cards.vocab_notetype import VERB_TABLE_CSS, VocabNotetype
from app.config import settings
from app.languages import get_vocab_notetype
from app.plugins.anki_sync.safety import safe_open
from app.srs.anki_mirror.protobuf_wire import find_len_field, pb_replace_or_insert_len

_CSS = 3  # NotetypeConfig css
_START, _END = VERB_TABLE_CSS.splitlines()[0], VERB_TABLE_CSS.splitlines()[-1]


def _dictionary_vocab(language_code: str) -> VocabNotetype:
    vocab = get_vocab_notetype(language_code)
    if vocab is None or not vocab.dictionary:
        raise ValueError(f"Language {language_code!r} mints into a notetype with no dictionary layers")
    return vocab


def _refreshed(css: str) -> str:
    """*css* with its verb-table block replaced by the current one, or the block appended."""
    start = css.find(_START)
    if start < 0:
        return css + VERB_TABLE_CSS
    end = css.find(_END + "\n", start)
    if end < 0:
        raise ValueError("The verb-table block in this notetype's CSS has no end marker: fix it by hand")
    return css[:start] + VERB_TABLE_CSS + css[end + len(_END) + 1 :]


def _notetype(conn: sqlite3.Connection, vocab: VocabNotetype) -> tuple[int, bytes, str]:
    if not vocab.dictionary:
        raise ValueError(f"{vocab.name!r} has no dictionary layers")
    row = conn.execute("SELECT id, config FROM notetypes WHERE name = ?", (vocab.name,)).fetchone()
    if row is None:
        raise ValueError(f"Notetype {vocab.name!r} not found in notetypes table")
    mid, config = row[0], bytes(row[1] or b"")
    if conn.execute("SELECT 1 FROM fields WHERE ntid = ? AND name = 'Verb'", (mid,)).fetchone() is None:
        raise ValueError(f"{vocab.name!r} has no Verb field: run add_dictionary_fields first")
    return mid, config, (find_len_field(config, _CSS) or b"").decode()


def refresh_dictionary_css(conn: sqlite3.Connection, vocab: VocabNotetype, *, now_ms: int | None = None) -> str:
    """Bring *vocab*'s notetype CSS in *conn* up to date. Returns ``"updated"`` or ``"current"``.

    The caller owns the ``safe_open`` envelope; this commits its own transaction.
    """
    mid, config, css = _notetype(conn, vocab)
    new_css = _refreshed(css)
    if new_css == css:
        return "current"
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    conn.execute(
        "UPDATE notetypes SET config = ?, mtime_secs = ?, usn = -1 WHERE id = ?",
        (pb_replace_or_insert_len(config, _CSS, new_css.encode()), now_ms // 1000, mid),
    )
    conn.execute("UPDATE col SET mod = ?", (now_ms,))  # NOT scm (data-only), NOT usn (Layer 61)
    conn.commit()
    return "updated"


def run(
    *,
    language_code: str,
    anki_collection_path: Path | None = None,
    anki_backup_dir: Path | None = None,
    dry_run: bool = False,
) -> str:
    """Refresh *language_code*'s notetype CSS. Returns ``"updated"``, ``"current"`` or ``"dry-run"``."""
    vocab = _dictionary_vocab(language_code)
    if anki_collection_path is None:
        anki_collection_path = settings.anki_collection_path
    if anki_backup_dir is None:
        anki_backup_dir = settings.anki_backup_dir

    with safe_open(anki_collection_path, backup_dir=anki_backup_dir, mode="rw") as ctx:
        if dry_run:
            _, _, css = _notetype(ctx.conn, vocab)
            status = "current" if _refreshed(css) == css else "updated"
            print(f"[DRY RUN] the dictionary CSS of {vocab.name!r} would be: {status}", flush=True)
            return "dry-run"
        result = refresh_dictionary_css(ctx.conn, vocab)

    if result == "updated":
        print(
            f"[DONE] Brought the dictionary CSS of {vocab.name!r} up to date (data-only, no full upload). "
            "It reaches the other devices at their next sync.",
            flush=True,
        )
    else:
        print(f"[SKIP] {vocab.name!r} already has the current dictionary CSS — no change.", flush=True)
    return result


def _cli() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Bring a TT vocab notetype's dictionary CSS up to date (data-only)")
    parser.add_argument("--language", required=True, help="Language code whose vocab notetype to refresh (e.g. ceb)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(language_code=args.language, dry_run=args.dry_run)


if __name__ == "__main__":  # pragma: no cover
    _cli()
