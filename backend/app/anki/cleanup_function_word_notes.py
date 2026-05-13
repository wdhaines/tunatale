"""Final cleanup pass for the 5 function-word notes left after delete_phonology_demos.

- **ja** (yes): keep as a normal vocab note; fix the Production card by
  setting Image=`<img src="img_yes.jpg">`. The asset is already in
  collection.media.
- **sem** (I am), **vsak** (every): convert from Slovene-Voc to a Cloze note
  using a curriculum source sentence.
- **že** (already), **njega** (him gen.): not in the curriculum; delete
  entirely (Anki graves + TT collocation). They'll regenerate as Cloze notes
  if/when they appear in a future lesson.

Prereq: the Cloze notetype must already exist in the collection. If you've
never used Cloze before, open Anki → Tools → Manage Note Types → Add →
"Add: Cloze", sync, then close Anki before running this.

No ``col.scm`` bump from this script — it's data-only.

Usage::

    uv run python -m app.anki.cleanup_function_word_notes --dry-run
    uv run python -m app.anki.cleanup_function_word_notes
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from app.anki.sync import OfflineWriter
from app.config import settings
from app.srs.function_words import make_cloze_text

_GRAVE_KIND_CARD = 0
_GRAVE_KIND_NOTE = 1


@dataclass(frozen=True)
class FixImageOp:
    """Update the Image field on a Slovene-Voc note in place."""

    anki_nid: int
    image_filename: str


@dataclass(frozen=True)
class ConvertToClozeOp:
    """Replace a Slovene-Voc note with a Cloze note using a source sentence."""

    anki_nid: int
    tt_collocation_id: int
    surface: str
    source_sentence: str
    translation: str
    note_text: str


@dataclass(frozen=True)
class DeleteOp:
    """Tombstone a Slovene-Voc note entirely (Anki graves + TT delete)."""

    anki_nid: int
    tt_collocation_id: int


FIX_IMAGE_OPS: tuple[FixImageOp, ...] = (
    FixImageOp(anki_nid=1774631982025, image_filename="img_yes.jpg"),  # ja
)

CONVERT_TO_CLOZE_OPS: tuple[ConvertToClozeOp, ...] = (
    ConvertToClozeOp(
        anki_nid=1774631982040,
        tt_collocation_id=672,
        surface="sem",
        source_sentence="Zdravo Ana, jaz sem Janez.",
        translation="I am",
        note_text=(
            "[səm]<br><br>"
            '<a href="https://forvo.com/word/sem/#header-pronunciation-sl">▶ Forvo</a><br><br>'
            "e = /ə/ — stressed schwa<br>⚠ In current Ljubljana speech: [sɛm]"
        ),
    ),
    ConvertToClozeOp(
        anki_nid=1774631982054,
        tt_collocation_id=701,
        surface="vsak",
        source_sentence="Odprto je vsak dan",
        translation="every",
        note_text=(
            "[ʍsak]<br><br>"
            '<a href="https://forvo.com/word/vsak/#header-pronunciation-sl">▶ Forvo</a><br><br>'
            "v word-initial before voiceless consonant → [ʍ]<br>Casual: [usak]"
        ),
    ),
)

DELETE_OPS: tuple[DeleteOp, ...] = (
    DeleteOp(anki_nid=1774631982029, tt_collocation_id=636),  # že
    DeleteOp(anki_nid=1774631982048, tt_collocation_id=657),  # njega
)


def _bump_col_dirty(anki_conn: sqlite3.Connection) -> None:
    now_ms = int(time.time() * 1000)
    anki_conn.execute("UPDATE col SET mod = ?, usn = -1", (now_ms,))


def _add_graves_and_delete_note(anki_conn: sqlite3.Connection, nid: int) -> int:
    """Tombstone a note + all its cards. Returns the card count tombstoned."""
    card_ids = [r[0] for r in anki_conn.execute("SELECT id FROM cards WHERE nid = ?", (nid,)).fetchall()]
    for cid in card_ids:
        anki_conn.execute(
            "INSERT OR REPLACE INTO graves (oid, type, usn) VALUES (?, ?, -1)",
            (cid, _GRAVE_KIND_CARD),
        )
        anki_conn.execute("DELETE FROM cards WHERE id = ?", (cid,))
    anki_conn.execute(
        "INSERT OR REPLACE INTO graves (oid, type, usn) VALUES (?, ?, -1)",
        (nid, _GRAVE_KIND_NOTE),
    )
    anki_conn.execute("DELETE FROM notes WHERE id = ?", (nid,))
    return len(card_ids)


def apply_fix_image(anki_conn: sqlite3.Connection, tt_conn: sqlite3.Connection, op: FixImageOp) -> bool:
    """Set the Image field on the target note and link a TT media row.

    Returns False if the Anki note is absent. The TT media row is what
    DrillCard.svelte uses to render the production prompt; without it, the UI
    falls back to the English translation text instead of showing the image.
    """
    row = anki_conn.execute("SELECT flds FROM notes WHERE id = ?", (op.anki_nid,)).fetchone()
    if row is None:
        return False
    fields = row[0].split("\x1f")
    fields[3] = f'<img src="{op.image_filename}">'
    new_flds = "\x1f".join(fields)
    now_secs = int(time.time())
    anki_conn.execute(
        "UPDATE notes SET flds = ?, mod = ?, usn = -1 WHERE id = ?",
        (new_flds, now_secs, op.anki_nid),
    )
    _bump_col_dirty(anki_conn)
    anki_conn.commit()

    tt_row = tt_conn.execute("SELECT id FROM collocations WHERE anki_note_id = ?", (op.anki_nid,)).fetchone()
    if tt_row is not None:
        coll_id = tt_row[0]
        existing = tt_conn.execute(
            "SELECT id FROM media WHERE collocation_id = ? AND kind = 'image' AND filename = ?",
            (coll_id, op.image_filename),
        ).fetchone()
        if existing is None:
            tt_conn.execute(
                "INSERT INTO media (collocation_id, kind, filename, anki_filename) VALUES (?, 'image', ?, ?)",
                (coll_id, op.image_filename, op.image_filename),
            )
            tt_conn.commit()
    return True


def apply_convert_to_cloze(
    anki_conn: sqlite3.Connection,
    tt_conn: sqlite3.Connection,
    op: ConvertToClozeOp,
    *,
    deck_name: str,
) -> int | None:
    """Delete the Slovene-Voc note, create a Cloze note in its place, update TT.

    Returns the new Cloze note's anki nid, or None if the source note was absent.
    Raises ValueError if the Cloze notetype isn't present in the collection.
    """
    row = anki_conn.execute("SELECT id FROM notes WHERE id = ?", (op.anki_nid,)).fetchone()
    if row is None:
        return None

    _add_graves_and_delete_note(anki_conn, op.anki_nid)

    # Build cloze text + back extra. Translation in italics on its own line.
    cloze_text = make_cloze_text(op.surface, op.source_sentence)
    back_parts = [f"<i>{op.translation}</i>"] if op.translation else []
    if op.note_text:
        back_parts.append(op.note_text)
    back_extra = "<br><br>".join(back_parts)

    writer = OfflineWriter(anki_conn)
    new_nid = writer.create_cloze_note(
        deck_name=deck_name,
        cloze_text=cloze_text,
        back_extra=back_extra,
        tags=["tunatale", "cloze"],
    )
    new_cid = anki_conn.execute("SELECT id FROM cards WHERE nid = ? ORDER BY ord LIMIT 1", (new_nid,)).fetchone()[0]

    # TT: collapse to cloze (recognition only), repoint to new note/card.
    tt_conn.execute(
        "UPDATE collocations SET card_type = 'cloze', source_sentence = ?, anki_note_id = ? WHERE id = ?",
        (op.source_sentence, new_nid, op.tt_collocation_id),
    )
    tt_conn.execute(
        "DELETE FROM collocation_directions WHERE collocation_id = ? AND direction = 'production'",
        (op.tt_collocation_id,),
    )
    tt_conn.execute(
        "UPDATE collocation_directions SET anki_card_id = ? WHERE collocation_id = ? AND direction = 'recognition'",
        (new_cid, op.tt_collocation_id),
    )

    _bump_col_dirty(anki_conn)
    anki_conn.commit()
    tt_conn.commit()
    return new_nid


def apply_delete(anki_conn: sqlite3.Connection, tt_conn: sqlite3.Connection, op: DeleteOp) -> bool:
    """Tombstone the Anki note + cards; delete the TT collocation + directions."""
    row = anki_conn.execute("SELECT id FROM notes WHERE id = ?", (op.anki_nid,)).fetchone()
    if row is None:
        return False
    _add_graves_and_delete_note(anki_conn, op.anki_nid)
    tt_conn.execute(
        "DELETE FROM collocation_directions WHERE collocation_id = ?",
        (op.tt_collocation_id,),
    )
    tt_conn.execute("DELETE FROM collocations WHERE id = ?", (op.tt_collocation_id,))
    _bump_col_dirty(anki_conn)
    anki_conn.commit()
    tt_conn.commit()
    return True


def _print_plan() -> None:
    print("Plan:")
    for op in FIX_IMAGE_OPS:
        print(f"  FIX_IMAGE  nid={op.anki_nid} image={op.image_filename}")
    for op in CONVERT_TO_CLOZE_OPS:
        print(f"  CLOZE      nid={op.anki_nid} surface={op.surface!r} src={op.source_sentence!r}")
    for op in DELETE_OPS:
        print(f"  DELETE     nid={op.anki_nid} tt_cid={op.tt_collocation_id}")


def _require_cloze_notetype(anki_conn: sqlite3.Connection) -> bool:
    row = anki_conn.execute("SELECT id FROM notetypes WHERE name = 'Cloze'").fetchone()
    if row is None:
        print(
            "Cloze notetype not found in collection.\n"
            "Open Anki → Tools → Manage Note Types → Add → 'Add: Cloze', sync, then re-run.",
            file=sys.stderr,
        )
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    parser.add_argument("--dry-run", action="store_true", help="show plan without writing")
    parser.add_argument("--anki-db", type=Path, default=None, help="override Anki collection path")
    parser.add_argument("--tt-db", type=Path, default=None, help="override TT database path")
    parser.add_argument("--deck-name", type=str, default=None, help="Anki deck name (default: settings.anki_deck_name)")
    args = parser.parse_args(argv)

    anki_path = args.anki_db or settings.anki_collection_path
    tt_path = args.tt_db or Path(settings.database_url.removeprefix("sqlite:///"))
    deck_name = args.deck_name or settings.anki_deck_name

    if not anki_path.exists():
        print(f"Anki collection not found: {anki_path}", file=sys.stderr)
        return 1
    if not tt_path.exists():
        print(f"TT database not found: {tt_path}", file=sys.stderr)
        return 1

    if args.dry_run:
        from app.anki.safety import _register_anki_collations

        anki_conn = sqlite3.connect(f"file:{anki_path}?mode=ro", uri=True)
        _register_anki_collations(anki_conn)
        tt_conn = sqlite3.connect(str(tt_path))
        try:
            if not _require_cloze_notetype(anki_conn):
                return 1
            _print_plan()
            print("--dry-run: no changes applied.")
            return 0
        finally:
            anki_conn.close()
            tt_conn.close()

    from app.anki.safety import safe_open

    tt_conn = sqlite3.connect(str(tt_path), isolation_level=None)
    try:
        with safe_open(anki_path, mode="rw") as ctx:
            anki_conn = ctx.conn
            if not _require_cloze_notetype(anki_conn):
                return 1
            _print_plan()
            counts = {"fixed_image": 0, "converted_to_cloze": 0, "deleted": 0}
            for op in FIX_IMAGE_OPS:
                if apply_fix_image(anki_conn, tt_conn, op):
                    counts["fixed_image"] += 1
            for op in CONVERT_TO_CLOZE_OPS:
                if apply_convert_to_cloze(anki_conn, tt_conn, op, deck_name=deck_name) is not None:
                    counts["converted_to_cloze"] += 1
            for op in DELETE_OPS:
                if apply_delete(anki_conn, tt_conn, op):
                    counts["deleted"] += 1
            print(f"Applied: {counts}")
    finally:
        tt_conn.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
