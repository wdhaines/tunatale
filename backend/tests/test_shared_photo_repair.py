"""Shared photographs: the more common word keeps the picture, every other card is re-pictured.

Real ``SRSDatabase``; the canonical media dir is pinned to tmp by the autouse
fixture in conftest, so the files written here never reach backend/media. The
media generator is a fake that returns scripted results, so no network call runs.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from app.cards.media import vocab_media
from app.cards.shared_photo_repair import Repicture, apply_repictures, plan_shared_photos
from app.common.guid import compute_guid
from app.models.srs_item import Direction
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase

SHARED = b"PHOTO-shared-mother"


def _card(
    db: SRSDatabase,
    text: str,
    *,
    image: str | None,
    data: bytes = SHARED,
    translation: str = "mother",
    note_id: int | None = 7001,
    card_type: str = "vocab",
    lang: str = "ceb",
) -> int:
    db.add_collocation(
        SyntacticUnit(
            text=text,
            translation=translation,
            word_count=1,
            difficulty=1,
            source="corpus",
            card_type=card_type,
        ),
        lang,
    )
    guid = compute_guid(text, lang, "")
    coll_id = db.get_collocation_id_by_guid(guid)
    if note_id is not None:
        db.set_anki_ids(guid, note_id, {Direction.RECOGNITION: note_id * 10})
    if image is not None:
        vocab_media.store_tt_media(db, coll_id, "image", image, data)
    return coll_id


def _zipf(table: dict[str, float]):
    return lambda word: table.get(word, 0.0)


def _dirty(db: SRSDatabase, text: str) -> list[str]:
    return [f for f in db.get_dirty_fields(compute_guid(text, "ceb", "")).split(",") if f]


# ── The plan ───────────────────────────────────────────────────────────────


def test_the_more_common_word_keeps_the_photo() -> None:
    db = SRSDatabase(":memory:")
    nanay = _card(db, "nanay", image="img_mother.jpg")
    inahan = _card(db, "inahan", image="img_mother_1234abcd.jpg")
    _card(db, "iring", image="img_cat.jpg", data=b"PHOTO-cat", translation="cat")

    plan = plan_shared_photos(db, "ceb", zipf=_zipf({"nanay": 4.39, "inahan": 5.45}))

    assert len(plan) == 1
    group = plan[0]
    assert group.sha256 == hashlib.sha256(SHARED).hexdigest()
    assert (group.keeper.collocation_id, group.keeper.text) == (inahan, "inahan")
    assert [(c.collocation_id, c.text, c.filename) for c in group.others] == [(nanay, "nanay", "img_mother.jpg")]
    assert group.tied is False


def test_a_tie_keeps_the_oldest_card_and_says_so() -> None:
    db = SRSDatabase(":memory:")
    tiil = _card(db, "tiil", image="img_foot.jpg", translation="foot")
    dako = _card(db, "dako", image="img_big.jpg", translation="big")

    (group,) = plan_shared_photos(db, "ceb", zipf=_zipf({}))

    assert group.keeper.collocation_id == tiil
    assert [c.collocation_id for c in group.others] == [dako]
    assert group.tied is True


def test_a_group_of_three_re_pictures_both_others_in_card_order() -> None:
    db = SRSDatabase(":memory:")
    bitte = _card(db, "bitte", image="a.jpg", translation="very")
    veldig = _card(db, "veldig", image="b.jpg", translation="very")
    meget = _card(db, "meget", image="c.jpg", translation="very")

    (group,) = plan_shared_photos(db, "ceb", zipf=_zipf({"veldig": 5.91, "meget": 5.09, "bitte": 3.28}))

    assert group.keeper.collocation_id == veldig
    assert [c.collocation_id for c in group.others] == [bitte, meget]
    assert group.tied is False


def test_only_the_top_two_decide_a_tie() -> None:
    db = SRSDatabase(":memory:")
    _card(db, "a", image="a.jpg")
    b = _card(db, "b", image="b.jpg")
    _card(db, "c", image="c.jpg")

    (group,) = plan_shared_photos(db, "ceb", zipf=_zipf({"a": 1.0, "b": 3.0, "c": 1.0}))

    assert group.keeper.collocation_id == b
    assert group.tied is False


def test_drawn_pictures_are_not_photographs() -> None:
    # Two cards on one drawing is the drawn families' own business (a recorded
    # synonym, ania / nia), not a photo collision.
    db = SRSDatabase(":memory:")
    _card(db, "nia", image="spatial_is_here_both_1234abcd.svg", data=b"<svg/>", translation="is here")
    _card(db, "ania", image="spatial_is_here_both_1234abcd.svg", data=b"<svg/>", translation="is here")

    assert plan_shared_photos(db, "ceb", zipf=_zipf({})) == []


def test_a_cloze_row_does_not_count_as_a_holder() -> None:
    # A cloze note has no Image field, so a leftover image row shows nowhere.
    db = SRSDatabase(":memory:")
    _card(db, "nanay", image="img_mother.jpg")
    _card(db, "inahan", image="img_mother_1234abcd.jpg", card_type="cloze")

    assert plan_shared_photos(db, "ceb", zipf=_zipf({})) == []


def test_the_default_ranking_is_the_languages_own_frequency_source() -> None:
    # Measured 2026-10-27 on the shipped Cebuano table: inahan 5.45, nanay 4.39.
    db = SRSDatabase(":memory:")
    _card(db, "nanay", image="img_mother.jpg")
    inahan = _card(db, "inahan", image="img_mother_1234abcd.jpg")

    (group,) = plan_shared_photos(db, "ceb")

    assert group.keeper.collocation_id == inahan
    assert group.keeper.zipf == pytest.approx(5.45, abs=0.01)


def test_the_plan_says_whether_the_image_query_is_cached() -> None:
    # A cached query costs no LLM call: the dry run prices the apply with this.
    from app.cards.media.query_llm import IMAGE_QUERY_MODEL_VERSION

    db = SRSDatabase(":memory:")
    _card(db, "nanay", image="img_mother.jpg")
    _card(db, "inahan", image="img_mother_1234abcd.jpg")
    _card(db, "iloy", image="img_mother_5678abcd.jpg")
    db.set_image_query("nanay", "mother", IMAGE_QUERY_MODEL_VERSION, "mother with child")

    (group,) = plan_shared_photos(db, "ceb", zipf=_zipf({"inahan": 5.0}))

    assert {c.text: c.query_cached for c in group.others} == {"nanay": True, "iloy": False}


# ── Applying it ────────────────────────────────────────────────────────────


def _media(data: bytes | None, url: str | None = None, ext: str = "jpg"):
    return SimpleNamespace(image_bytes=data, image_url=url, image_ext=ext)


class _FakeMediaFn:
    """Returns scripted results in order, and records what each call barred."""

    def __init__(self, *results) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, str, frozenset[str], str]] = []

    async def __call__(self, word, english, *, used_image_urls, source_sentence="", grammar="", audio="full"):
        self.calls.append((word, english, frozenset(used_image_urls), audio))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


async def test_the_other_card_gets_the_next_photo_and_the_push_is_flagged() -> None:
    db = SRSDatabase(":memory:")
    nanay = _card(db, "nanay", image="img_mother.jpg")
    inahan = _card(db, "inahan", image="img_mother_1234abcd.jpg")
    plan = plan_shared_photos(db, "ceb", zipf=_zipf({"inahan": 5.0}))
    fresh = b"PHOTO-another-mother"
    # The first search hands back the shared photo again (its URL is the top
    # hit); barring that URL and asking once more yields the next one.
    media_fn = _FakeMediaFn(_media(SHARED, "https://px/1"), _media(fresh, "https://px/2"))

    results = await apply_repictures(db, plan, media_fn)

    new_name = f"img_mother_{hashlib.sha256(fresh).hexdigest()[:8]}.jpg"
    assert [(r.collocation_id, r.old_filename, r.new_filename, r.reason) for r in results] == [
        (nanay, "img_mother.jpg", new_name, None)
    ]
    assert db.get_image_filename(nanay) == new_name
    assert db.get_image_filename(inahan) == "img_mother_1234abcd.jpg"
    assert _dirty(db, "nanay") == ["image"]
    assert _dirty(db, "inahan") == []
    assert media_fn.calls[0][:2] == ("nanay", "mother")
    assert media_fn.calls[0][3] == "none"
    assert "https://px/1" in media_fn.calls[1][2]
    assert plan_shared_photos(db, "ceb", zipf=_zipf({"inahan": 5.0})) == []


async def test_a_card_with_no_anki_note_is_re_pictured_without_a_dangling_flag() -> None:
    db = SRSDatabase(":memory:")
    nanay = _card(db, "nanay", image="img_mother.jpg", note_id=None)
    _card(db, "inahan", image="img_mother_1234abcd.jpg")
    plan = plan_shared_photos(db, "ceb", zipf=_zipf({"inahan": 5.0}))

    (result,) = await apply_repictures(db, plan, _FakeMediaFn(_media(b"PHOTO-new", "https://px/9")))

    assert result.reason is None
    assert db.get_image_filename(nanay) == result.new_filename
    assert _dirty(db, "nanay") == []


@pytest.mark.parametrize(
    ("results", "reason"),
    [
        ((RuntimeError("pixabay 503"),), "fetch failed: pixabay 503"),
        ((None,), "no picture found"),
        ((_media(None),), "no picture found"),
        ((_media(SHARED, "https://px/1"), _media(SHARED, "https://px/2")), "still a duplicate of card {keeper}"),
        ((_media(SHARED, None),), "still a duplicate of card {keeper}"),
    ],
)
async def test_a_card_that_cannot_get_a_new_photo_keeps_the_old_one(results, reason) -> None:
    # Never imageless: a card that had a picture keeps it until a better one is in hand.
    db = SRSDatabase(":memory:")
    nanay = _card(db, "nanay", image="img_mother.jpg")
    inahan = _card(db, "inahan", image="img_mother_1234abcd.jpg")
    plan = plan_shared_photos(db, "ceb", zipf=_zipf({"inahan": 5.0}))

    (result,) = await apply_repictures(db, plan, _FakeMediaFn(*results))

    assert result.new_filename is None
    assert result.reason == reason.format(keeper=inahan)
    assert db.get_image_filename(nanay) == "img_mother.jpg"
    assert _dirty(db, "nanay") == []


async def test_two_others_in_one_run_cannot_land_on_one_new_photo() -> None:
    db = SRSDatabase(":memory:")
    _card(db, "inahan", image="a.jpg")
    nanay = _card(db, "nanay", image="b.jpg")
    iloy = _card(db, "iloy", image="c.jpg")
    plan = plan_shared_photos(db, "ceb", zipf=_zipf({"inahan": 5.0}))
    second = b"PHOTO-second-mother"
    third = b"PHOTO-third-mother"
    media_fn = _FakeMediaFn(
        _media(second, "https://px/2"),  # nanay: accepted
        _media(second, "https://px/2"),  # iloy: now nanay's, so barred ...
        _media(third, "https://px/3"),  # ... and the retry is free
    )

    results = await apply_repictures(db, plan, media_fn)

    assert [(r.collocation_id, r.reason) for r in results] == [(nanay, None), (iloy, None)]
    assert db.get_image_filename(nanay) != db.get_image_filename(iloy)
    assert plan_shared_photos(db, "ceb", zipf=_zipf({"inahan": 5.0})) == []


# ── Re-fetching named cards for a LanGeek picture (tunatale-xsbh) ──────────────


def _sourced(data: bytes, url: str, source: str):
    return SimpleNamespace(image_bytes=data, image_url=url, image_ext="jpg", image_source=source)


async def test_only_a_langeek_picture_replaces_a_card_when_asked_for_one() -> None:
    from app.cards.shared_photo_repair import load_cards, repicture_cards

    db = SRSDatabase(":memory:")
    nanay = _card(db, "nanay", image="img_mother_aaaa.jpg", data=b"PHOTO-a")
    iloy = _card(db, "iloy", image="img_mother_bbbb.jpg", data=b"PHOTO-b")
    cards = load_cards(db, [nanay, iloy])
    media_fn = _FakeMediaFn(
        _sourced(b"LGK-mother", "https://lgk/1", "langeek"),
        _sourced(b"PX-other", "https://px/7", "pixabay"),
    )

    results = await repicture_cards(db, cards, media_fn, only_source="langeek")

    assert [(r.collocation_id, r.reason) for r in results] == [(nanay, None), (iloy, "langeek had none")]
    assert db.get_image_filename(nanay).startswith("lgk_mother_")
    assert db.get_image_filename(iloy) == "img_mother_bbbb.jpg"
    assert _dirty(db, "iloy") == []


def test_load_cards_reads_each_card_as_the_repair_does() -> None:
    from app.cards.shared_photo_repair import load_cards

    db = SRSDatabase(":memory:")
    nanay = _card(db, "nanay", image="img_mother.jpg", note_id=None)

    (card,) = load_cards(db, [nanay])

    assert (card.collocation_id, card.text, card.gloss, card.filename, card.zipf, card.linked) == (
        nanay,
        "nanay",
        "mother",
        "img_mother.jpg",
        0.0,
        False,
    )


# ── Copying another learner's LanGeek pictures ─────────────────────────────────
# A learner's deck copied from the owner's shares its GUIDs, and one media dir
# serves every learner, so the owner's LanGeek pictures can be handed across with
# no LanGeek search and no sense-choice LLM call.


def _owner_with_lgk(text: str = "nanay", *, translation: str = "mother", data: bytes = b"LGK-mother") -> SRSDatabase:
    owner = SRSDatabase(":memory:")
    _card(owner, text, image=f"lgk_{translation}_0000aaaa.jpg", data=data, translation=translation)
    return owner


def test_a_card_takes_the_other_decks_langeek_picture_and_the_push_is_flagged() -> None:
    from app.cards.shared_photo_repair import copy_pictures, load_cards

    owner = _owner_with_lgk()
    learner = SRSDatabase(":memory:")
    nanay = _card(learner, "nanay", image="img_mother.jpg", data=b"PX-mother")

    results = copy_pictures(learner, owner, load_cards(learner, [nanay]))

    assert results == [Repicture(nanay, "nanay", "img_mother.jpg", "lgk_mother_0000aaaa.jpg", None)]
    assert learner.get_image_filename(nanay) == "lgk_mother_0000aaaa.jpg"
    assert _dirty(learner, "nanay") == ["image"]
    # The file is the owner's, byte for byte: nothing was fetched.
    assert (vocab_media._MEDIA_DIR / "lgk_mother_0000aaaa.jpg").read_bytes() == b"LGK-mother"


def test_a_card_with_no_anki_note_takes_it_without_a_dangling_flag() -> None:
    from app.cards.shared_photo_repair import copy_pictures, load_cards

    owner = _owner_with_lgk()
    learner = SRSDatabase(":memory:")
    nanay = _card(learner, "nanay", image="img_mother.jpg", data=b"PX-mother", note_id=None)

    (result,) = copy_pictures(learner, owner, load_cards(learner, [nanay]))

    assert result.new_filename == "lgk_mother_0000aaaa.jpg"
    assert _dirty(learner, "nanay") == []


@pytest.mark.parametrize(
    ("owner_text", "owner_gloss", "owner_image", "reason"),
    [
        ("iring", "cat", "lgk_cat_0000aaaa.jpg", "not in the other deck"),
        ("nanay", "mom", "lgk_mom_0000aaaa.jpg", "the other deck's card means 'mom'"),
        ("nanay", "mother", "img_mother_0000aaaa.jpg", "the other deck's picture is not LanGeek's"),
        ("nanay", "mother", None, "the other deck's picture is not LanGeek's"),
    ],
    ids=["absent", "other-meaning", "pixabay", "no-picture"],
)
def test_a_card_keeps_its_photo_unless_the_same_word_and_meaning_has_a_langeek_one(
    owner_text, owner_gloss, owner_image, reason
) -> None:
    from app.cards.shared_photo_repair import copy_pictures, load_cards

    owner = SRSDatabase(":memory:")
    _card(owner, owner_text, image=owner_image, data=b"OWNER", translation=owner_gloss)
    learner = SRSDatabase(":memory:")
    nanay = _card(learner, "nanay", image="img_mother.jpg", data=b"PX-mother")

    (result,) = copy_pictures(learner, owner, load_cards(learner, [nanay]))

    assert (result.new_filename, result.reason) == (None, reason)
    assert learner.get_image_filename(nanay) == "img_mother.jpg"
    assert _dirty(learner, "nanay") == []


def test_a_picture_another_card_in_this_deck_shows_is_not_copied() -> None:
    # The owner's deck may give two words one illustration that this deck already
    # shows on a third card; the one-photo-one-card rule holds per deck.
    from app.cards.shared_photo_repair import copy_pictures, load_cards

    owner = _owner_with_lgk()
    learner = SRSDatabase(":memory:")
    iloy = _card(learner, "iloy", image="lgk_mother_0000aaaa.jpg", data=b"LGK-mother")
    nanay = _card(learner, "nanay", image="img_mother.jpg", data=b"PX-mother")

    (result,) = copy_pictures(learner, owner, load_cards(learner, [nanay]))

    assert result.reason == f"card {iloy} already shows it"
    assert learner.get_image_filename(nanay) == "img_mother.jpg"


# ── Cards changed by hand since their list was made ──────────────────────────────────
# The owner, 2026-10-27, fixing LanGeek pictures by hand: "Will the script not update
# these?" A batch's ids are fixed when the list is made; without a guard, a card the
# owner re-pictured afterwards is re-fetched and replaced. Every swap writes a new
# image row, so the row's created_at says whether it changed after the list.


def _written_at(db: SRSDatabase, coll_id: int, when: str) -> None:
    with db._get_conn() as conn:
        conn.execute("UPDATE media SET created_at = ? WHERE collocation_id = ? AND kind = 'image'", (when, coll_id))
        conn.commit()


def test_a_card_re_pictured_after_the_list_is_left_alone() -> None:
    from app.cards.shared_photo_repair import load_cards, unchanged_since

    db = SRSDatabase(":memory:")
    old = _card(db, "nanay", image="img_mother.jpg", data=b"PX-a")
    fixed = _card(db, "iloy", image="lgk_mother_bbbb.jpg", data=b"HAND", translation="mom")
    _written_at(db, old, "2026-10-27 12:00:00")
    _written_at(db, fixed, "2026-10-27 15:30:00")  # the owner's hand fix, after the list

    keep, skipped = unchanged_since(db, load_cards(db, [old, fixed]), "2026-10-27 14:15:00")

    assert [c.collocation_id for c in keep] == [old]
    assert [c.collocation_id for c in skipped] == [fixed]


def test_a_card_written_exactly_at_the_list_time_is_kept() -> None:
    from app.cards.shared_photo_repair import load_cards, unchanged_since

    db = SRSDatabase(":memory:")
    card = _card(db, "nanay", image="img_mother.jpg", data=b"PX-a")
    _written_at(db, card, "2026-10-27 14:15:00")

    keep, skipped = unchanged_since(db, load_cards(db, [card]), "2026-10-27 14:15:00")

    assert ([c.collocation_id for c in keep], skipped) == ([card], [])
