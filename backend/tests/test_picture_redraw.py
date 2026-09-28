"""The one-off repair: existing drawn-word cards get the picture new ones now get.

Real ``SRSDatabase``; the canonical media dir is pinned to tmp by the autouse
fixture in conftest, so the files written here never reach backend/media.
"""

from __future__ import annotations

from app.cards.media import vocab_media
from app.cards.number_picture import number_picture
from app.cards.picture_redraw import apply_redraws, plan_redraws
from app.cards.spatial_picture import spatial_picture
from app.models.srs_item import Direction
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase


def _card(
    db: SRSDatabase,
    text: str,
    *,
    image: str | None,
    note_id: int | None = 7001,
    card_type="vocab",
    translation="t",
    lang="ceb",
) -> int:
    db.add_collocation(
        SyntacticUnit(
            text=text, translation=translation, word_count=1, difficulty=1, source="corpus", card_type=card_type
        ),
        lang,
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

    plan = plan_redraws(db, "ceb")

    assert [(r.collocation_id, r.text, r.old_filename) for r in plan] == [
        (duha, "duha", "img_two_1234abcd.jpg"),
        (singko, "singko", "img_five_5678abcd.jpg"),
    ]
    assert plan[1].new_filename.startswith("clock_05_")


def test_apply_swaps_the_picture_and_flags_the_push() -> None:
    db = SRSDatabase(":memory:")
    duha = _card(db, "duha", image="img_two_1234abcd.jpg")
    old_file = vocab_media._MEDIA_DIR / "img_two_1234abcd.jpg"
    assert old_file.exists()

    apply_redraws(db, plan_redraws(db, "ceb"), "ceb")

    picture = number_picture("duha", "ceb")
    assert db.get_image_filename(duha) == picture.filename
    assert (vocab_media._MEDIA_DIR / picture.filename).read_bytes() == picture.svg
    assert "image" in db.get_dirty_fields(db.get_collocation("duha").guid).split(",")
    assert old_file.exists(), "the photo is kept: another DB sharing the media dir may still show it"


def test_an_unlinked_card_is_redrawn_but_not_flagged_for_a_push() -> None:
    """No Anki note yet: the mint will carry the new picture; a dirty flag would dangle."""
    db = SRSDatabase(":memory:")
    duha = _card(db, "duha", image="img_two_1234abcd.jpg", note_id=None)

    plan = plan_redraws(db, "ceb")
    apply_redraws(db, plan, "ceb")

    assert plan[0].linked is False
    assert db.get_image_filename(duha) == number_picture("duha", "ceb").filename
    assert db.get_dirty_fields(db.get_collocation("duha").guid) == ""


def test_a_second_run_plans_nothing() -> None:
    db = SRSDatabase(":memory:")
    _card(db, "duha", image="img_two_1234abcd.jpg")
    apply_redraws(db, plan_redraws(db, "ceb"), "ceb")
    assert plan_redraws(db, "ceb") == []


def test_a_spatial_word_is_redrawn_filled_or_left_by_its_gloss() -> None:
    """Photo -> drawing; NO image -> filled (the user decided these are picture
    cards, unlike a number with no image, which stays out of scope); a homograph
    glossed with its other sense -> left alone."""
    db = SRSDatabase(":memory:")
    sulod = _card(db, "sulod", image="img_inside.jpg", translation="inside")
    likod = _card(db, "likod", image=None, translation="back (behind)")
    _card(db, "wala", image="img_none_366299d1.png", translation="none")  # 'left' homograph
    _card(db, "tunga", image=None, translation="half")  # 'middle' homograph, no image
    _card(db, "upat", image=None)  # a number with no image: still not this module's call
    _card(db, "gawas", image=None, translation="outside", card_type="cloze")  # a cloze has no Image field

    plan = plan_redraws(db, "ceb")

    by_id = {r.collocation_id: r for r in plan}
    assert {i: r.old_filename for i, r in by_id.items()} == {sulod: "img_inside.jpg", likod: None}
    assert by_id[likod].new_filename == spatial_picture("likod", "ceb", "back (behind)").filename
    assert by_id[likod].new_filename.startswith("spatial_behind_")

    apply_redraws(db, plan, "ceb")

    assert db.get_image_filename(sulod) == by_id[sulod].new_filename
    assert db.get_image_filename(likod) == by_id[likod].new_filename
    assert "image" in db.get_dirty_fields(db.get_collocation("likod").guid).split(",")
    assert plan_redraws(db, "ceb") == []


def test_a_repair_never_deletes_a_photo_another_db_still_shows(tmp_path) -> None:
    """The live failure (tunatale-ja9q, 2026-09-28).

    Every language DB and every learner's DB share one media dir. The Cebuano
    repair swapped kilid's img_side.jpg for a drawing, found nothing else in the
    CEBUANO DB using it, and deleted the file — which a Slovene card still showed.
    """
    ceb = SRSDatabase(f"sqlite:///{tmp_path / 'ceb.db'}")
    sl = SRSDatabase(f"sqlite:///{tmp_path / 'sl.db'}")
    _card(ceb, "kilid", image="img_side.jpg", translation="side")
    _card(sl, "stran", image="img_side.jpg", translation="side", lang="sl")
    shared = vocab_media._MEDIA_DIR / "img_side.jpg"

    apply_redraws(ceb, plan_redraws(ceb, "ceb"), "ceb")

    assert ceb.get_image_filename(ceb.get_collocation_id_by_guid(ceb.get_collocation("kilid").guid)) != "img_side.jpg"
    assert shared.exists()
