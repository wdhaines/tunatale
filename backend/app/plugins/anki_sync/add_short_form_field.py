"""Give a TT vocab notetype with dictionary layers the Short form field (tunatale-0rlg).

The live "Cebuano Vocabulary" notetype has the layers but not this field. This
appends it after them and shows it under the spelling, wherever the word is
written, so the notetype ends up exactly as ``create_vocab_notetype`` builds it
today. The block is conditional, so a note whose field is empty renders as before.

The field arrives empty; ``backfill_dictionary_fields`` fills it (data-only).

Adding a field bumps ``col.scm``, so AnkiWeb demands a one-time full upload.
Workflow (``.claude/rules/anki-sync.md``):

1. Sync every device first (phone, TT, then desktop Anki), so nothing is stranded
   on a device the full sync will overwrite.
2. Quit Anki (this tool needs exclusive write access via ``safe_open``).
3. ``uv run python -m app.plugins.anki_sync.add_short_form_field --language <code>``
4. Open Anki → File → Sync → **Upload to AnkiWeb**.
5. After Anki closes again: ``uv run python -m app.plugins.anki_sync.normalize_usns``.
6. Re-anchor TT's own sync mirror (download-only)::

       uv run python -m app.plugins.anki_sync.sync_orchestrator --bootstrap

   Without it the next peer-sync aborts with ``FULL_SYNC (required=2)``.
7. Every other device (the phone) must choose **Download** at its next sync.

Idempotent: once the field exists, a re-run changes nothing and bumps nothing.

Usage:
    uv run python -m app.plugins.anki_sync.add_short_form_field --language <code> [--dry-run]
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

from app.cards.vocab_notetype import (
    SHORT_FORM_BLOCK,
    SHORT_FORM_CSS,
    SHORT_FORM_FIELD,
    SPELLING_BLOCK,
    VocabNotetype,
    build_field_config,
)
from app.config import settings
from app.languages import get_vocab_notetype
from app.plugins.anki_sync.safety import safe_open
from app.srs.anki_mirror.protobuf_wire import find_len_field, pb_replace_or_insert_len

_FIELD_SEP = "\x1f"
# Protobuf field numbers: TemplateConfig q_format / a_format; NotetypeConfig css.
_QFMT, _AFMT, _CSS = 1, 2, 3


def _short_form_vocab(language_code: str) -> VocabNotetype:
    vocab = get_vocab_notetype(language_code)
    if vocab is None or not vocab.short_form:
        raise ValueError(f"Language {language_code!r} mints into a notetype with no Short form field")
    return vocab


def _with_block(vocab: VocabNotetype, side: str) -> str:
    under_the_word = vocab.word_div + SPELLING_BLOCK
    if side.count(under_the_word) != 1:
        raise ValueError(f"template does not write the spelling under the word exactly once: {side!r}")
    return side.replace(under_the_word, under_the_word + SHORT_FORM_BLOCK)


def add_short_form_field(conn: sqlite3.Connection, vocab: VocabNotetype, *, now_ms: int | None = None) -> str:
    """Append the Short form field to *vocab*'s notetype in *conn* and show it.

    Returns ``"created"`` or ``"exists"`` (idempotent). Refuses, changing nothing,
    a notetype whose fields are neither the roster before the field nor the one
    after, or whose templates do not write the spelling under the word as TT does.
    The caller owns the ``safe_open`` envelope; this commits its own transaction.
    """
    if not vocab.short_form:
        raise ValueError(f"{vocab.name!r} has no Short form field to add")
    row = conn.execute("SELECT id, config FROM notetypes WHERE name = ?", (vocab.name,)).fetchone()
    if row is None:
        raise ValueError(f"Notetype {vocab.name!r} not found in notetypes table")
    mid, nt_config = row[0], bytes(row[1] or b"")

    names = tuple(r[0] for r in conn.execute("SELECT name FROM fields WHERE ntid = ? ORDER BY ord", (mid,)))
    if names == vocab.field_names:
        return "exists"
    if names != vocab.field_names[:-1]:
        raise ValueError(f"{vocab.name!r} has the field roster {names!r}, not one this migration knows how to extend")

    # Build every new config before the first write, so a template this cannot
    # read aborts the whole migration with nothing changed. The word is on the
    # recognition front (ord 0) and the production back (ord 1).
    new_configs = []
    for ord_, cfg in conn.execute("SELECT ord, config FROM templates WHERE ntid = ? ORDER BY ord", (mid,)):
        cfg = bytes(cfg or b"")
        tag = _QFMT if ord_ == 0 else _AFMT
        side = find_len_field(cfg, tag)
        if side is None:
            raise ValueError(f"Template ord {ord_} of {vocab.name!r} has no template text to edit")
        new_configs.append((pb_replace_or_insert_len(cfg, tag, _with_block(vocab, side.decode()).encode()), ord_))

    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    now_ts = now_ms // 1000

    conn.execute(
        "INSERT INTO fields (ntid, ord, name, config) VALUES (?, ?, ?, ?)",
        (mid, len(names), SHORT_FORM_FIELD, build_field_config()),
    )
    # One more separator for the new trailing field. Content change → usn = -1 +
    # mod = now (anki-sync.md).
    conn.execute("UPDATE notes SET flds = flds || ?, usn = -1, mod = ? WHERE mid = ?", (_FIELD_SEP, now_ts, mid))
    for config, ord_ in new_configs:
        conn.execute(
            "UPDATE templates SET config = ?, mtime_secs = ?, usn = -1 WHERE ntid = ? AND ord = ?",
            (config, now_ts, mid, ord_),
        )
    css = (find_len_field(nt_config, _CSS) or b"") + SHORT_FORM_CSS.encode()
    conn.execute(
        "UPDATE notetypes SET config = ?, mtime_secs = ?, usn = -1 WHERE id = ?",
        (pb_replace_or_insert_len(nt_config, _CSS, css), now_ts, mid),
    )
    # A field insert is a schema change: bump col.scm (forces the one-time full
    # upload) and col.mod. Do NOT touch col.usn (Layer 61).
    conn.execute("UPDATE col SET scm = ?, mod = ?", (now_ms, now_ms))
    conn.commit()
    return "created"


def run(
    *,
    language_code: str,
    anki_collection_path: Path | None = None,
    anki_backup_dir: Path | None = None,
    dry_run: bool = False,
) -> str:
    """Add the field to *language_code*'s vocab notetype. Returns ``"created"``, ``"exists"`` or ``"dry-run"``."""
    vocab = _short_form_vocab(language_code)
    if anki_collection_path is None:
        anki_collection_path = settings.anki_collection_path
    if anki_backup_dir is None:
        anki_backup_dir = settings.anki_backup_dir

    with safe_open(anki_collection_path, backup_dir=anki_backup_dir, mode="rw") as ctx:
        if dry_run:
            names = {
                r[0]
                for r in ctx.conn.execute(
                    "SELECT f.name FROM fields f JOIN notetypes nt ON nt.id = f.ntid WHERE nt.name = ?", (vocab.name,)
                )
            }
            status = "exists" if SHORT_FORM_FIELD in names else "created"
            print(f"[DRY RUN] Short form field on {vocab.name!r} would be: {status}", flush=True)
            return "dry-run"
        result = add_short_form_field(ctx.conn, vocab)

    if result == "created":
        print(
            f"[DONE] Added the Short form field to {vocab.name!r} and put it on its cards "
            "(col.scm bumped). It is empty until backfill_dictionary_fields runs.\n"
            "  Next: open Anki → File → Sync → Upload to AnkiWeb, quit Anki, then run\n"
            "        uv run python -m app.plugins.anki_sync.normalize_usns\n"
            "        uv run python -m app.plugins.anki_sync.sync_orchestrator --bootstrap\n"
            "  Then choose Download on every other device (the phone) at its next sync.",
            flush=True,
        )
    else:
        print(f"[SKIP] {vocab.name!r} already has its Short form field — no change.", flush=True)
    return result


def _cli() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Add the Short form field to a TT vocab notetype (schema change)")
    parser.add_argument("--language", required=True, help="Language code whose vocab notetype to migrate (e.g. ceb)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(language_code=args.language, dry_run=args.dry_run)


if __name__ == "__main__":  # pragma: no cover
    _cli()
