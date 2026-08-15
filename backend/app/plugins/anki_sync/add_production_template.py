"""Give a recognition-only imported notetype the capability to carry production cards.

A deck imported from the community (the shape ``app.cards.field_map`` profiles
describe) typically ships a **single** card template, so every note yields one
recognition card and TT's production track is inert for the whole seed
vocabulary. This migration adds the two things such a notetype lacks: an
``Image`` field to front a production card with, and a ``Production`` template
(ord = last + 1) that renders from the notetype's *own* fields via its
:class:`~app.cards.field_map.NotetypeProfile`.

**It adds capability, not cards.** No ``cards`` row is written here. The ord=1
cards are minted one at a time, with their image fetched at that moment, when a
word's recognition card graduates — see
``.beads-tasks/briefs/design-no-production-cards-2026-08.md``. Minting all of
them up front would front-load thousands of media fetches to service a handful
of introductions a day, which is why the bulk backfill was rejected.

Adding a field and a template bumps ``col.scm`` → AnkiWeb demands a one-time
full upload. Workflow (``.claude/rules/anki-sync.md``):

1. Quit Anki (this tool needs exclusive write access via ``safe_open``).
2. ``uv run python -m app.plugins.anki_sync.add_production_template --notetype "<name>"``
3. Open Anki → File → Sync → **Upload to AnkiWeb**.
4. After Anki closes again: ``uv run python -m app.plugins.anki_sync.normalize_usns``.

Idempotent: re-running once the ``Production`` template exists is a no-op (no
``col.scm`` bump, no second separator appended to ``notes.flds``), so it is safe
to run twice.

Usage:
    uv run python -m app.plugins.anki_sync.add_production_template \
        --notetype "<notetype name>" [--dry-run]
    uv run python -m app.plugins.anki_sync.add_production_template --list [--deck "<deck>"]
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

from app.cards.field_map import NotetypeProfile, get_profile
from app.cards.vocab_notetype import build_field_config, build_template_config
from app.config import settings
from app.plugins.anki_sync.safety import safe_open

#: Field this migration adds to front the production card with. Matches the
#: field name TT's own vocab notetypes use, so the media pipeline writes to the
#: same place regardless of which notetype a card ended up on.
IMAGE_FIELD = "Image"

#: Template name that marks a notetype as already migrated.
PRODUCTION_TEMPLATE = "Production"

_FIELD_SEP = "\x1f"


def build_production_template(profile: NotetypeProfile) -> tuple[str, str]:
    """Return ``(qfmt, afmt)`` for a production card rendered from *profile*.

    The front is the image alone — the learner produces the L2 word from a
    picture, the same prompt shape TT's own vocab notetypes use, which avoids
    the L1-interference and gloss-ambiguity of an English-fronted card.

    The back reveals the L2 word (with its article, when the profile declares
    one, so the headword reads as real language), the English gloss, and any
    ``summary``-tier back fields. Lower tiers stay off the card: ``details`` and
    ``deep`` exist for the reader's disclosure UI, not for a recall prompt.

    No CSS is emitted — this template joins an existing notetype and inherits
    the styling already in its ``notetypes.config``.
    """
    qfmt = "{{" + IMAGE_FIELD + "}}"

    l2 = f"{{{{{profile.l2}}}}}"
    if profile.article:
        # Mirrors the imported deck's own conditional-article rendering.
        l2 = f"{{{{#{profile.article}}}}}{{{{{profile.article}}}}} {{{{/{profile.article}}}}}{l2}"

    parts = [
        '{{FrontSide}}<hr id="answer">',
        f"<div><b>{l2}</b></div>",
        f"<div>{{{{{profile.translation}}}}}</div>",
    ]
    parts.extend(f"<div>{{{{{spec.field_name}}}}}</div>" for spec in profile.back_fields if spec.tier == "summary")
    return qfmt, "".join(parts)


def find_recognition_only_notetypes(conn: sqlite3.Connection, deck_name: str) -> list[tuple[int, str, int]]:
    """Return ``(ntid, name, note_count)`` for single-template notetypes in *deck_name*.

    This is the root-cause signal behind the whole recognition-only problem: a
    notetype with one template can only ever yield recognition cards, so every
    note on it is missing a production direction *structurally* rather than by
    accident. Reported, never acted on automatically — a ``col.scm`` bump
    against the user's collection is an explicit choice.

    Anki's built-in ``Cloze`` notetype also has a single template but generates
    one card per cloze deletion; it is not a candidate for this migration.
    Returns ``[]`` when the deck does not exist.
    """
    from app.plugins.anki_sync.sqlite_reader import find_deck_id

    deck_id = find_deck_id(conn, deck_name)
    if deck_id is None:
        return []

    rows = conn.execute(
        "SELECT nt.id, nt.name, COUNT(DISTINCT n.id) AS notes "
        "FROM notetypes nt "
        "JOIN notes n ON n.mid = nt.id "
        "JOIN cards c ON c.nid = n.id "
        "WHERE c.did = ? "
        "  AND (SELECT COUNT(*) FROM templates t WHERE t.ntid = nt.id) = 1 "
        "GROUP BY nt.id, nt.name "
        "ORDER BY notes DESC, nt.id ASC",
        (deck_id,),
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def add_production_template(
    conn: sqlite3.Connection,
    notetype_name: str,
    profile: NotetypeProfile,
    *,
    now_ms: int | None = None,
) -> str:
    """Add an ``Image`` field + ``Production`` template to *notetype_name*.

    Returns ``"created"`` or ``"exists"`` (idempotent). The caller owns the
    ``safe_open`` envelope; this commits its own transaction.

    Writes no ``cards`` rows — see the module docstring.
    """
    row = conn.execute("SELECT id FROM notetypes WHERE name = ?", (notetype_name,)).fetchone()
    if row is None:
        raise ValueError(f"Notetype {notetype_name!r} not found in notetypes table")
    mid = row[0]

    templates = conn.execute("SELECT ord, name FROM templates WHERE ntid = ? ORDER BY ord", (mid,)).fetchall()
    if any(t[1] == PRODUCTION_TEMPLATE for t in templates):
        return "exists"

    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    now_ts = now_ms // 1000

    field_names = [r[0] for r in conn.execute("SELECT name FROM fields WHERE ntid = ? ORDER BY ord", (mid,))]
    if IMAGE_FIELD not in field_names:
        conn.execute(
            "INSERT INTO fields (ntid, ord, name, config) VALUES (?, ?, ?, ?)",
            (mid, len(field_names), IMAGE_FIELD, build_field_config()),
        )
        # A note's `flds` carries exactly (num_fields - 1) separators, so every
        # note on this notetype needs one more for the new trailing field. The
        # rows are content changes: usn = -1 + mod = now (see anki-sync.md).
        conn.execute(
            "UPDATE notes SET flds = flds || ?, usn = -1, mod = ? WHERE mid = ?",
            (_FIELD_SEP, now_ts, mid),
        )

    qfmt, afmt = build_production_template(profile)
    conn.execute(
        "INSERT INTO templates (ntid, ord, name, mtime_secs, usn, config) VALUES (?, ?, ?, ?, -1, ?)",
        (mid, len(templates), PRODUCTION_TEMPLATE, now_ts, build_template_config(qfmt, afmt)),
    )
    conn.execute("UPDATE notetypes SET mtime_secs = ?, usn = -1 WHERE id = ?", (now_ts, mid))
    # Field + template inserts are schema changes: bump col.scm (forces the
    # one-time full upload) and col.mod. Do NOT touch col.usn (Layer 61).
    conn.execute("UPDATE col SET scm = ?, mod = ?", (now_ms, now_ms))
    conn.commit()
    return "created"


def run(
    *,
    notetype_name: str,
    anki_collection_path: Path | None = None,
    anki_backup_dir: Path | None = None,
    dry_run: bool = False,
) -> str:
    """Add the production capability to *notetype_name* in the collection.

    Returns ``"created"``, ``"exists"``, or ``"dry-run"``.
    """
    profile = get_profile(notetype_name)
    if profile is None:
        raise ValueError(
            f"Notetype {notetype_name!r} has no field-role profile in app.cards.field_map — "
            "the production template renders from the profile's field names, so add one first"
        )

    if anki_collection_path is None:
        anki_collection_path = settings.anki_collection_path
    if anki_backup_dir is None:
        anki_backup_dir = settings.anki_backup_dir

    with safe_open(anki_collection_path, backup_dir=anki_backup_dir, mode="rw") as ctx:
        if dry_run:
            already = ctx.conn.execute(
                "SELECT 1 FROM templates t JOIN notetypes nt ON nt.id = t.ntid WHERE nt.name = ? AND t.name = ?",
                (notetype_name, PRODUCTION_TEMPLATE),
            ).fetchone()
            status = "exists" if already else "created"
            print(f"[DRY RUN] production capability on {notetype_name!r} would be: {status}", flush=True)
            return "dry-run"
        result = add_production_template(ctx.conn, notetype_name, profile)

    if result == "created":
        print(
            f"[DONE] Added {IMAGE_FIELD!r} field + {PRODUCTION_TEMPLATE!r} template to "
            f"{notetype_name!r} (col.scm bumped). No cards were created.\n"
            "  Next: open Anki → File → Sync → Upload to AnkiWeb, then run\n"
            "        uv run python -m app.plugins.anki_sync.normalize_usns",
            flush=True,
        )
    else:
        print(f"[SKIP] {notetype_name!r} already has a {PRODUCTION_TEMPLATE!r} template — no change.", flush=True)
    return result


def _cli() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Add an Image field + Production template to an imported notetype (schema change)"
    )
    parser.add_argument("--notetype", default=None, help="Notetype name to migrate")
    parser.add_argument("--deck", default=None, help="Deck to scan with --list")
    parser.add_argument("--list", action="store_true", help="Report single-template notetypes and exit")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.list:
        deck = args.deck if args.deck is not None else settings.anki_deck_name
        with safe_open(settings.anki_collection_path, backup_dir=settings.anki_backup_dir, mode="ro") as ctx:
            for ntid, name, count in find_recognition_only_notetypes(ctx.conn, deck):
                print(f"  {ntid}  {name!r}  notes={count}")
        return

    if not args.notetype:
        parser.error("--notetype is required (or pass --list to see candidates)")
    run(notetype_name=args.notetype, dry_run=args.dry_run)


if __name__ == "__main__":  # pragma: no cover
    _cli()
