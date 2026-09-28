"""The one-off repair: existing number cards get the picture new ones now get.

Real ``SRSDatabase``; the canonical media dir is pinned to tmp by the autouse
fixture in conftest, so the files written here never reach backend/media.
"""

from __future__ import annotations

from app.cards.media import vocab_media
from app.cards.number_picture import number_picture
from app.cards.number_redraw import apply_number_redraws, plan_number_redraws
from app.models.srs_item import Direction
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase


def _card(db: SRSDatabase, text: str, *, image: str | None, note_id: int | None = 7001, card_type="vocab") -> int:
    db.add_collocation(
        SyntacticUnit(text=text, translation="t", word_count=1, difficulty=1, source="corpus", card_type=card_type),
        "ceb",
    )
    item = db.get_collocation(text)
    coll_id = db.get_collocation_id_by_guid(item.guid)
    if note_id is not None:
        db.set_anki_ids(item.guid, note_id, {Direction.RECOGNITION: note_id * 10})
    if image is not None:
        vocab_media.store_tt_media(db, coll_id, "image", image, b"PHOTO-" + image.encode())
    return coll_id


def test_a_photographed_number_is_planned_and_everything_else_is_not() -> None:
    db = SRSDatabase(":memory:")
    duha = _card(db, "duha", image="img_two_1234abcd.jpg")
    singko = _card(db, "singko", image="img_five_5678abcd.jpg")
    _card(db, "tulo", image=number_picture("tulo", "ceb").filename)  # already drawn
    _card(db, "upat", image=None)  # no picture at all: not this module's call
    _card(db, "iring", image="img_cat_00000000.jpg")  # not a number
    _card(db, "usa", image="img_one_11111111.jpg")  # excluded: 'one' AND 'a/an'
    _card(db, "lima", image="img_x.jpg", card_type="cloze")  # a cloze has no Image field

    plan = plan_number_redraws(db, "ceb")

    assert [(r.collocation_id, r.text, r.old_filename) for r in plan] == [
        (duha, "duha", "img_two_1234abcd.jpg"),
        (singko, "singko", "img_five_5678abcd.jpg"),
    ]
    assert plan[1].new_filename.startswith("clock_05_")


def test_apply_swaps_the_picture_flags_the_push_and_retires_the_photo() -> None:
    db = SRSDatabase(":memory:")
    duha = _card(db, "duha", image="img_two_1234abcd.jpg")
    old_file = vocab_media._MEDIA_DIR / "img_two_1234abcd.jpg"
    assert old_file.exists()

    apply_number_redraws(db, plan_number_redraws(db, "ceb"), "ceb")

    picture = number_picture("duha", "ceb")
    assert db.get_image_filename(duha) == picture.filename
    assert (vocab_media._MEDIA_DIR / picture.filename).read_bytes() == picture.svg
    assert "image" in db.get_dirty_fields(db.get_collocation("duha").guid).split(",")
    assert not old_file.exists()


def test_an_unlinked_card_is_redrawn_but_not_flagged_for_a_push() -> None:
    """No Anki note yet: the mint will carry the new picture; a dirty flag would dangle."""
    db = SRSDatabase(":memory:")
    duha = _card(db, "duha", image="img_two_1234abcd.jpg", note_id=None)

    plan = plan_number_redraws(db, "ceb")
    apply_number_redraws(db, plan, "ceb")

    assert plan[0].linked is False
    assert db.get_image_filename(duha) == number_picture("duha", "ceb").filename
    assert db.get_dirty_fields(db.get_collocation("duha").guid) == ""


def test_a_second_run_plans_nothing() -> None:
    db = SRSDatabase(":memory:")
    _card(db, "duha", image="img_two_1234abcd.jpg")
    apply_number_redraws(db, plan_number_redraws(db, "ceb"), "ceb")
    assert plan_number_redraws(db, "ceb") == []
