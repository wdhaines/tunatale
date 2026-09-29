"""Give an imported two-card notetype an ``Image`` field, and show it on its cards.

Pimsleur Tagalog's genanki export (``Basic (and reversed card) (genanki)``) has
Front/Back/Audio and no ``Image`` field. TT still draws pictures for those words
and stores them, but the next sync drops them, because the Anki note has nowhere
to hold one (tunatale-ejwh). This migration adds the field and puts it on the
cards: above the English on the production card's front, and on the recognition
card's answer, matching where TT's own vocab notetypes show it. Both are wrapped
in ``{{#Image}}`` so a note with no picture renders exactly as before.

Unlike ``add_production_template`` this adds no template: the notetype already
makes both cards. A single-template notetype is refused and pointed there.

Adding a field bumps ``col.scm`` → AnkiWeb demands a one-time full upload.
Workflow (``.claude/rules/anki-sync.md``):

1. Sync every device first (phone, TT, then desktop Anki), so nothing is
   stranded on a device the full sync will overwrite.
2. Quit Anki (this tool needs exclusive write access via ``safe_open``).
3. ``uv run python -m app.plugins.anki_sync.add_image_field --notetype "<name>" --language <code>``
4. Open Anki → File → Sync → **Upload to AnkiWeb**.
5. After Anki closes again: ``uv run python -m app.plugins.anki_sync.normalize_usns``.
6. Re-anchor TT's own sync mirror (download-only)::

       uv run python -m app.plugins.anki_sync.sync_orchestrator --bootstrap

   Without it the next peer-sync aborts with ``FULL_SYNC (required=2)``.
7. Every other device (the phone) must choose **Download** at its next sync.

Idempotent: once the field exists, a re-run changes nothing and bumps nothing.

Usage:
    uv run python -m app.plugins.anki_sync.add_image_field \
        --notetype "<notetype name>" --language <code> [--dry-run]
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

from app.cards.field_map import NotetypeProfile, get_profile
from app.cards.vocab_notetype import build_field_config
from app.config import settings
from app.plugins.anki_sync.add_production_template import IMAGE_FIELD
from app.plugins.anki_sync.safety import safe_open
from app.srs.anki_mirror.protobuf_wire import find_len_field, pb_replace_or_insert_len

#: The picture, only when the note has one — a pictureless card is unchanged.
IMAGE_BLOCK = "{{#" + IMAGE_FIELD + '}}<div class="img">{{' + IMAGE_FIELD + "}}</div>{{/" + IMAGE_FIELD + "}}"

#: Same size cap TT's own vocab notetypes use (``vocab_notetype._css``).
IMAGE_CSS = "\n.img img { max-height: 240px; }\n"

_FIELD_SEP = "\x1f"
# Protobuf field numbers: TemplateConfig q_format / a_format; NotetypeConfig css.
_QFMT, _AFMT, _CSS = 1, 2, 3


def _with_image(config: bytes, *, recognition: bool, what: str) -> bytes:
    """Return a template config with the picture added to the side it belongs on."""
    qfmt, afmt = find_len_field(config, _QFMT), find_len_field(config, _AFMT)
    if qfmt is None or afmt is None:
        raise ValueError(f"{what} has no template text to edit — refusing to guess its layout")
    if recognition:
        return pb_replace_or_insert_len(config, _AFMT, afmt + IMAGE_BLOCK.encode())
    return pb_replace_or_insert_len(config, _QFMT, IMAGE_BLOCK.encode() + qfmt)


def add_image_field(
    conn: sqlite3.Connection,
    notetype_name: str,
    profile: NotetypeProfile,
    *,
    now_ms: int | None = None,
) -> str:
    """Add an ``Image`` field to *notetype_name* and show it on both its cards.

    Returns ``"created"`` or ``"exists"`` (idempotent). *profile* says which
    template is recognition; every other one is a production card. The caller
    owns the ``safe_open`` envelope; this commits its own transaction.
    """
    row = conn.execute("SELECT id, config FROM notetypes WHERE name = ?", (notetype_name,)).fetchone()
    if row is None:
        raise ValueError(f"Notetype {notetype_name!r} not found in notetypes table")
    mid, nt_config = row[0], bytes(row[1] or b"")

    field_names = [r[0] for r in conn.execute("SELECT name FROM fields WHERE ntid = ? ORDER BY ord", (mid,))]
    if IMAGE_FIELD in field_names:
        return "exists"

    templates = conn.execute("SELECT ord, config FROM templates WHERE ntid = ? ORDER BY ord", (mid,)).fetchall()
    if len(templates) < 2:
        raise ValueError(
            f"Notetype {notetype_name!r} has one template, so no production card to show a picture on — "
            "use add_production_template, which adds the field and that card together"
        )
    # Build every new config before the first write, so a template this cannot
    # read aborts the whole migration with nothing changed.
    new_configs = [
        (
            _with_image(
                bytes(cfg or b""),
                recognition=ord_ == profile.recognition_ord,
                what=f"Template ord {ord_} of {notetype_name!r}",
            ),
            ord_,
        )
        for ord_, cfg in templates
    ]

    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    now_ts = now_ms // 1000

    conn.execute(
        "INSERT INTO fields (ntid, ord, name, config) VALUES (?, ?, ?, ?)",
        (mid, len(field_names), IMAGE_FIELD, build_field_config()),
    )
    # A note's `flds` carries (num_fields - 1) separators: one more for the new
    # trailing field. Content change → usn = -1 + mod = now (anki-sync.md).
    conn.execute("UPDATE notes SET flds = flds || ?, usn = -1, mod = ? WHERE mid = ?", (_FIELD_SEP, now_ts, mid))
    for config, ord_ in new_configs:
        conn.execute(
            "UPDATE templates SET config = ?, mtime_secs = ?, usn = -1 WHERE ntid = ? AND ord = ?",
            (config, now_ts, mid, ord_),
        )
    css = (find_len_field(nt_config, _CSS) or b"") + IMAGE_CSS.encode()
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
    notetype_name: str,
    language_code: str,
    anki_collection_path: Path | None = None,
    anki_backup_dir: Path | None = None,
    dry_run: bool = False,
) -> str:
    """Add the ``Image`` field to *notetype_name*. Returns ``"created"``, ``"exists"`` or ``"dry-run"``."""
    # Language-scoped: the genanki notetype name is generic, and only the
    # language that owns the deck may claim it (tunatale-w4m7.8).
    profile = get_profile(notetype_name, language_code)
    if profile is None:
        raise ValueError(
            f"Notetype {notetype_name!r} has no field-role profile for language {language_code!r} — "
            "the profile says which card is recognition, so add one first"
        )

    if anki_collection_path is None:
        anki_collection_path = settings.anki_collection_path
    if anki_backup_dir is None:
        anki_backup_dir = settings.anki_backup_dir

    with safe_open(anki_collection_path, backup_dir=anki_backup_dir, mode="rw") as ctx:
        if dry_run:
            already = ctx.conn.execute(
                "SELECT 1 FROM fields f JOIN notetypes nt ON nt.id = f.ntid WHERE nt.name = ? AND f.name = ?",
                (notetype_name, IMAGE_FIELD),
            ).fetchone()
            status = "exists" if already else "created"
            print(f"[DRY RUN] {IMAGE_FIELD!r} field on {notetype_name!r} would be: {status}", flush=True)
            return "dry-run"
        result = add_image_field(ctx.conn, notetype_name, profile)

    if result == "created":
        print(
            f"[DONE] Added {IMAGE_FIELD!r} field to {notetype_name!r} and put it on its cards "
            "(col.scm bumped). No cards were created.\n"
            "  Next: open Anki → File → Sync → Upload to AnkiWeb, quit Anki, then run\n"
            "        uv run python -m app.plugins.anki_sync.normalize_usns\n"
            "        uv run python -m app.plugins.anki_sync.sync_orchestrator --bootstrap\n"
            "  Then choose Download on every other device (the phone) at its next sync.",
            flush=True,
        )
    else:
        print(f"[SKIP] {notetype_name!r} already has an {IMAGE_FIELD!r} field — no change.", flush=True)
    return result


def _cli() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Add an Image field to an imported two-card notetype (schema change)")
    parser.add_argument("--notetype", required=True, help="Notetype name to migrate")
    parser.add_argument("--language", required=True, help="Language code that owns the deck (e.g. tl)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(notetype_name=args.notetype, language_code=args.language, dry_run=args.dry_run)


if __name__ == "__main__":  # pragma: no cover
    _cli()
