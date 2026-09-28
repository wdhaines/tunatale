"""Give existing cards the drawn picture new ones now get (tunatale-w4m7.10, hvj0).

``app.cards.drawn_picture`` fixed the ROUTING: a number or spatial word minted
from now on is drawn. Cards already minted keep what they were given — for every number that
entered as a new card (the Cebuano base list, an add from the reader) that is a
Pixabay photo of "five", which is the failure the drawing exists to prevent. A
card's image is fixed once stored, so nothing heals them on its own.

This swaps each such card's image for its drawing and flags the ``image`` field
dirty, so the next sync writes it into the Anki note through the ordinary push
(``sync_engine.py::sync_push``, which copies the file into collection.media). No
Anki file is opened here.

Scope, deliberately narrow:

- **cloze rows are skipped** — a cloze note has no Image field;
- **a card whose image already IS the drawing is left alone**, so a second run is
  a no-op and a language whose numbers are already drawn (Norwegian, via the
  pre-stage) plans nothing;
- **a NUMBER with no image is left alone**. It is either not minted yet — the
  mint now draws it — or it carries no picture for a reason recorded elsewhere
  (a notetype without an Image field). Replacing a wrong picture is this
  module's job; deciding a number should have one is not.
- **a SPATIAL word with no image is filled.** That decision was made: the user
  said these are picture cards (2026-09-28), and the Norwegian deck's
  ``under``/``foran``/``inni`` came from Anki with no picture at all. A note
  whose notetype has no Image field is safe to flag: the push logs
  ``PUSH_FIELD_DROPPED`` and clears the flag (``sync_engine.py::sync_push``).

⚠️ It rewrites the Image field of whatever note the row points at, which for an
Anki-originated note is a card the user made. Run it per language, dry first,
and read the plan.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from app.cards.drawn_picture import drawn_picture
from app.cards.media import vocab_media
from app.cards.spatial_picture import spatial_picture


class Redraw(NamedTuple):
    collocation_id: int
    text: str
    old_filename: str | None  # None: the card had no picture, and is being filled
    new_filename: str
    linked: bool  # has an Anki note, so the next sync must push the new picture
    gloss: str  # the card's English, which a spatial word's picture is checked against


def plan_redraws(db: Any, language_code: str) -> list[Redraw]:
    """Every non-cloze card in *db* whose drawn word shows the wrong picture, or none."""
    rows, _ = db.list_collocations(limit=1_000_000)
    plan = []
    for coll_id, item, _guid in rows:
        unit = item.syntactic_unit
        if unit.card_type == "cloze":
            continue
        picture = drawn_picture(unit.text, language_code, unit.translation)
        if picture is None:
            continue
        current = db.get_image_filename(coll_id)
        if current == picture.filename:
            continue
        if current is None and spatial_picture(unit.text, language_code, unit.translation) is None:
            continue
        plan.append(
            Redraw(coll_id, unit.text, current, picture.filename, item.anki_note_id is not None, unit.translation)
        )
    return plan


def apply_redraws(db: Any, plan: list[Redraw], language_code: str) -> None:
    """Store each planned drawing, retire the old file if orphaned, flag the push."""
    for redraw in plan:
        picture = drawn_picture(redraw.text, language_code, redraw.gloss)
        vocab_media._unlink_orphaned_images(
            db, redraw.collocation_id, "image", vocab_media._MEDIA_DIR, skip_filename=picture.filename
        )
        vocab_media.store_tt_media(db, redraw.collocation_id, "image", picture.filename, picture.svg)
        if redraw.linked:
            db.add_dirty_field_by_id(redraw.collocation_id, "image")
