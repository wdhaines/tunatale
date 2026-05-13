"""Backfill missing TT image media rows for Anki notes that already have an image.

TT's DrillCard reads ``image_url`` from the TT ``media`` table (keyed by
``collocation_id``), not from Anki's note fields. The media table is only
populated by ``/listen`` at card-creation time — Anki Image-field edits
made later never propagate back to TT. After an Image edit in Anki, the
TT Production card silently falls back to the translation text.

This one-shot script backfills the 7 known cases where the user edited or
added an image in Anki (some from the LingQ-import bug, some from manual
clipboard paste) and TT was left without any image row at all.

Also backfills ``ulica``'s empty translation (was lost in the LingQ
import; "street" matches the original L1→L2 prompt).

No ``col.scm`` bump — data-only mutation. Only writes Anki when filling
an empty English field.

Usage::

    uv run python -m app.anki.link_tt_images --dry-run
    uv run python -m app.anki.link_tt_images
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from app.config import settings


@dataclass(frozen=True)
class LinkImageOp:
    anki_nid: int
    image_filename: str
    translation_override: str | None = None


LINK_OPS: tuple[LinkImageOp, ...] = (
    LinkImageOp(1774631982063, "paste-77ee270504ca8c3d82c3661bcc707fd2da998620.png"),  # nič / nothing
    LinkImageOp(1775264031772, "img_wing.jpg"),  # krilo (cid 719, wing)
    LinkImageOp(
        1775264031808,
        "paste-532408af9dd4fbb6b3ef2be7433214251871a125.jpg",
        translation_override="street",
    ),  # ulica (cid 263; TT translation was empty)
    LinkImageOp(1775264032856, "img_wear.jpg"),  # nositi (cid 717, wear)
    LinkImageOp(1775264032872, "img_tall.jpg"),  # visok (cid 718, tall)
    LinkImageOp(1778267269399, "img_time.jpg"),  # ime / name
    LinkImageOp(1778267277826, "img_time.jpg"),  # časa / time
)


def apply_link_image(
    anki_conn: sqlite3.Connection,
    tt_conn: sqlite3.Connection,
    op: LinkImageOp,
    *,
    anki_media_dir: Path | None = None,
    tt_media_dir: Path | None = None,
) -> bool:
    """Link the Anki image to the TT collocation if not already linked.

    When ``anki_media_dir`` and ``tt_media_dir`` are provided the actual
    image file is also copied from Anki's collection.media into TT's media
    directory (instead of only inserting a DB row that ``import_seed`` would
    have to materialise on the next sync).

    Returns True if a media row was inserted (or already present and a
    translation was backfilled); False if no TT collocation references the
    Anki note. Never overwrites an existing TT image row (DIVERGENT-75
    handling is out of scope here).
    """
    tt_row = tt_conn.execute(
        "SELECT id, translation FROM collocations WHERE anki_note_id = ?", (op.anki_nid,)
    ).fetchone()
    if tt_row is None:
        return False
    coll_id, current_translation = tt_row[0], tt_row[1] or ""

    existing = tt_conn.execute(
        "SELECT id FROM media WHERE collocation_id = ? AND kind = 'image' LIMIT 1",
        (coll_id,),
    ).fetchone()
    if existing is None:
        tt_conn.execute(
            "INSERT INTO media (collocation_id, kind, filename, anki_filename) VALUES (?, 'image', ?, ?)",
            (coll_id, op.image_filename, op.image_filename),
        )
        # Copy the actual file from Anki media to TT media so the image
        # is immediately available (not waiting for the next import_seed).
        if anki_media_dir is not None and tt_media_dir is not None:
            src = anki_media_dir / op.image_filename
            if src.exists():
                from app.media.importer import copy_media_file

                copy_result = copy_media_file(src, tt_media_dir)
                tt_conn.execute(
                    "UPDATE media SET path = ?, sha256 = ?, bytes = ? "
                    "WHERE collocation_id = ? AND kind = 'image' AND filename = ?",
                    (
                        str(copy_result.dest_path),
                        copy_result.sha256,
                        copy_result.size_bytes,
                        coll_id,
                        op.image_filename,
                    ),
                )

    if op.translation_override and not current_translation.strip():
        tt_conn.execute(
            "UPDATE collocations SET translation = ? WHERE id = ?",
            (op.translation_override, coll_id),
        )
        # Also fill Anki's English field if it's empty.
        anki_row = anki_conn.execute("SELECT flds FROM notes WHERE id = ?", (op.anki_nid,)).fetchone()
        if anki_row is not None:
            fields = anki_row[0].split("\x1f")
            if len(fields) > 1 and not fields[1].strip():
                fields[1] = op.translation_override
                new_flds = "\x1f".join(fields)
                anki_conn.execute(
                    "UPDATE notes SET flds = ?, mod = ?, usn = -1 WHERE id = ?",
                    (new_flds, int(time.time()), op.anki_nid),
                )
                anki_conn.execute("UPDATE col SET mod = ?, usn = -1", (int(time.time() * 1000),))
                anki_conn.commit()

    tt_conn.commit()
    return True


def _print_plan() -> None:
    print(f"Plan: link {len(LINK_OPS)} image(s)")
    for op in LINK_OPS:
        trans = f" [+translation={op.translation_override!r}]" if op.translation_override else ""
        print(f"  nid={op.anki_nid} img={op.image_filename}{trans}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    parser.add_argument("--dry-run", action="store_true", help="show plan without writing")
    parser.add_argument("--anki-db", type=Path, default=None)
    parser.add_argument("--tt-db", type=Path, default=None)
    args = parser.parse_args(argv)

    anki_path = args.anki_db or settings.anki_collection_path
    tt_path = args.tt_db or Path(settings.database_url.removeprefix("sqlite:///"))

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
            _print_plan()
            print("--dry-run: no changes applied.")
            return 0
        finally:
            anki_conn.close()
            tt_conn.close()

    from app.anki.safety import safe_open

    anki_media_dir = anki_path.parent / "collection.media"
    tt_media_dir = settings.media_dir

    tt_conn = sqlite3.connect(str(tt_path), isolation_level=None)
    try:
        with safe_open(anki_path, mode="rw") as ctx:
            anki_conn = ctx.conn
            _print_plan()
            counts = {"media_linked": 0, "translations_backfilled": 0}
            for op in LINK_OPS:
                # Count translation backfills separately by detecting empty-before/non-empty-after.
                tt_row = tt_conn.execute(
                    "SELECT id, translation FROM collocations WHERE anki_note_id = ?", (op.anki_nid,)
                ).fetchone()
                pre_empty_translation = bool(tt_row) and not (tt_row[1] or "").strip()
                if apply_link_image(
                    anki_conn,
                    tt_conn,
                    op,
                    anki_media_dir=anki_media_dir,
                    tt_media_dir=tt_media_dir,
                ):
                    counts["media_linked"] += 1
                    if op.translation_override and pre_empty_translation:
                        counts["translations_backfilled"] += 1
            print(f"Applied: {counts}")
    finally:
        tt_conn.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
