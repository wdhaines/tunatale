"""Give a TT vocab notetype minted before its dictionary layers the fields and blocks.

The live "Cebuano Vocabulary" notetype was created with the seven shared
fields. Wolff's dictionary on the card (tunatale-h0q3.2) needs five more —
``vocab_notetype.DICTIONARY_FIELDS`` — and the template blocks that show them:
the Handbook spelling under the word, and Meaning, Verb, Details and the
verbatim entry on the back. This appends the fields after ``DisambigKey`` and
edits the templates and CSS so the notetype ends up exactly as
``create_vocab_notetype`` would build it today. Every block is conditional, so
a note whose fields are still empty renders as before.

The fields arrive empty; ``backfill_dictionary_fields`` fills them (data-only).

Adding a field bumps ``col.scm`` → AnkiWeb demands a one-time full upload.
Workflow (``.claude/rules/anki-sync.md``):

1. Sync every device first (phone, TT, then desktop Anki), so nothing is
   stranded on a device the full sync will overwrite.
2. Quit Anki (this tool needs exclusive write access via ``safe_open``).
3. ``uv run python -m app.plugins.anki_sync.add_dictionary_fields --language <code>``
4. Open Anki → File → Sync → **Upload to AnkiWeb**.
5. After Anki closes again: ``uv run python -m app.plugins.anki_sync.normalize_usns``.
6. Re-anchor TT's own sync mirror (download-only)::

       uv run python -m app.plugins.anki_sync.sync_orchestrator --bootstrap

   Without it the next peer-sync aborts with ``FULL_SYNC (required=2)``.
7. Every other device (the phone) must choose **Download** at its next sync.

Idempotent: once the fields exist, a re-run changes nothing and bumps nothing.

Usage:
    uv run python -m app.plugins.anki_sync.add_dictionary_fields --language <code> [--dry-run]
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

from app.cards.vocab_notetype import DICTIONARY_CSS, VocabNotetype, build_field_config, with_dictionary
from app.config import settings
from app.languages import get_vocab_notetype
from app.plugins.anki_sync.safety import safe_open
from app.srs.anki_mirror.protobuf_wire import find_len_field, pb_replace_or_insert_len

_FIELD_SEP = "\x1f"
# Protobuf field numbers: TemplateConfig q_format / a_format; NotetypeConfig css.
_QFMT, _AFMT, _CSS = 1, 2, 3


def _dictionary_vocab(language_code: str) -> VocabNotetype:
    vocab = get_vocab_notetype(language_code)
    if vocab is None or not vocab.dictionary:
        raise ValueError(f"Language {language_code!r} mints into a notetype with no dictionary layers")
    return vocab


def add_dictionary_fields(conn: sqlite3.Connection, vocab: VocabNotetype, *, now_ms: int | None = None) -> str:
    """Append *vocab*'s dictionary fields to its notetype in *conn* and show them.

    Returns ``"created"`` or ``"exists"`` (idempotent). Refuses, changing
    nothing, a notetype whose fields are neither the roster before the layers
    nor the one after, or whose templates do not write the word as TT does.
    The caller owns the ``safe_open`` envelope; this commits its own transaction.
    """
    if not vocab.dictionary:
        raise ValueError(f"{vocab.name!r} has no dictionary layers to add")
    row = conn.execute("SELECT id, config FROM notetypes WHERE name = ?", (vocab.name,)).fetchone()
    if row is None:
        raise ValueError(f"Notetype {vocab.name!r} not found in notetypes table")
    mid, nt_config = row[0], bytes(row[1] or b"")

    names = tuple(r[0] for r in conn.execute("SELECT name FROM fields WHERE ntid = ? ORDER BY ord", (mid,)))
    if names == vocab.field_names:
        return "exists"
    new_fields = vocab.field_names[len(names) :]
    if vocab.field_names[: len(names)] != names or not new_fields:
        raise ValueError(f"{vocab.name!r} has the field roster {names!r}, not one this migration knows how to extend")

    # Build every new config before the first write, so a template this cannot
    # read aborts the whole migration with nothing changed.
    new_configs = []
    for ord_, cfg in conn.execute("SELECT ord, config FROM templates WHERE ntid = ? ORDER BY ord", (mid,)):
        cfg = bytes(cfg or b"")
        qfmt, afmt = find_len_field(cfg, _QFMT), find_len_field(cfg, _AFMT)
        if qfmt is None or afmt is None:
            raise ValueError(f"Template ord {ord_} of {vocab.name!r} has no template text to edit")
        new_q, new_a = with_dictionary(vocab, qfmt.decode(), afmt.decode(), recognition=ord_ == 0)
        cfg = pb_replace_or_insert_len(cfg, _QFMT, new_q.encode())
        new_configs.append((pb_replace_or_insert_len(cfg, _AFMT, new_a.encode()), ord_))

    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    now_ts = now_ms // 1000

    for i, name in enumerate(new_fields, start=len(names)):
        conn.execute(
            "INSERT INTO fields (ntid, ord, name, config) VALUES (?, ?, ?, ?)", (mid, i, name, build_field_config())
        )
    # One more separator per new trailing field. Content change → usn = -1 +
    # mod = now (anki-sync.md).
    conn.execute(
        "UPDATE notes SET flds = flds || ?, usn = -1, mod = ? WHERE mid = ?",
        (_FIELD_SEP * len(new_fields), now_ts, mid),
    )
    for config, ord_ in new_configs:
        conn.execute(
            "UPDATE templates SET config = ?, mtime_secs = ?, usn = -1 WHERE ntid = ? AND ord = ?",
            (config, now_ts, mid, ord_),
        )
    css = (find_len_field(nt_config, _CSS) or b"") + DICTIONARY_CSS.encode()
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
    """Add the dictionary fields to *language_code*'s vocab notetype. Returns ``"created"``, ``"exists"`` or ``"dry-run"``."""
    vocab = _dictionary_vocab(language_code)
    if anki_collection_path is None:
        anki_collection_path = settings.anki_collection_path
    if anki_backup_dir is None:
        anki_backup_dir = settings.anki_backup_dir

    with safe_open(anki_collection_path, backup_dir=anki_backup_dir, mode="rw") as ctx:
        if dry_run:
            have = {
                r[0]
                for r in ctx.conn.execute(
                    "SELECT f.name FROM fields f JOIN notetypes nt ON nt.id = f.ntid WHERE nt.name = ?", (vocab.name,)
                )
            }
            status = "exists" if set(vocab.field_names) <= have else "created"
            print(f"[DRY RUN] dictionary fields on {vocab.name!r} would be: {status}", flush=True)
            return "dry-run"
        result = add_dictionary_fields(ctx.conn, vocab)

    if result == "created":
        print(
            f"[DONE] Added the dictionary fields to {vocab.name!r} and put them on its cards "
            "(col.scm bumped). The fields are empty until backfill_dictionary_fields runs.\n"
            "  Next: open Anki → File → Sync → Upload to AnkiWeb, quit Anki, then run\n"
            "        uv run python -m app.plugins.anki_sync.normalize_usns\n"
            "        uv run python -m app.plugins.anki_sync.sync_orchestrator --bootstrap\n"
            "  Then choose Download on every other device (the phone) at its next sync.",
            flush=True,
        )
    else:
        print(f"[SKIP] {vocab.name!r} already has its dictionary fields — no change.", flush=True)
    return result


def _cli() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Add the dictionary fields to a TT vocab notetype (schema change)")
    parser.add_argument("--language", required=True, help="Language code whose vocab notetype to migrate (e.g. ceb)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(language_code=args.language, dry_run=args.dry_run)


if __name__ == "__main__":  # pragma: no cover
    _cli()
