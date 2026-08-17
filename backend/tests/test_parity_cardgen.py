"""Card-generation parity for the just-in-time production mint (tunatale-qf6.2).

A community deck imported as recognition-only gets its production *capability*
from ``add_production_template``: an ``Image`` field plus a ``Production``
template fronted on ``{{Image}}``. The migration deliberately mints no cards —
each ord=1 card is created one at a time when its recognition sibling graduates
(``.beads-tasks/briefs/design-no-production-cards-2026-08.md``).

That design has a load-bearing assumption about Anki: TT writes the image into
``notes.flds`` with raw SQL, so the ``Production`` front stops rendering empty,
and **Anki's own card generator could mint the ord=1 card behind TT's back** —
producing a duplicate, or a card whose id TT never recorded (which strands the
production direction the way the rejected "TT-only rows" option did).

This pins what Anki actually does, measured 2026-08-17 against the real binary:

- a **plain open** (and a queue build) does **not** generate the card. The only
  triggers in 26.05 are note-update-through-the-backend, a notetype schema
  change, text import, and **Check Database** (``rslib/src/dbcheck.rs``, which
  walks every note through ``update_note_inner_generating_cards``).
- **Check Database does** generate it, once the ``Image`` field is non-empty.
  So the hazard is real, but it is an *ordering* hazard, not a race: it only
  bites a note whose image TT wrote and whose card TT never minted.
- Anki **never duplicates** an ord that already exists
  (``cardgen.rs::new_cards_required_normal`` filters on ``existing_ords``), and
  it leaves a TT-minted card's **id** untouched. This is what lets the mint be
  adopt-or-create and therefore idempotent.

The image-less note is the **control**: it must stay at ord=0 through both
phases, or the test is only observing that Check Database generates cards
indiscriminately and proves nothing about the ``{{Image}}`` front.

What this test does NOT cover:
- TT's side of the mint (adopt-or-create + the same-transaction image write) —
  that is ``sync_writer`` unit work, not a question about Anki's behavior.
- The ``Empty cards`` tool, which reports an ord=1 card whose ``Image`` is empty
  and is why the mint must never run ahead of a successful image fetch. Anki's
  report is advisory and user-confirmed, so it is a design constraint rather
  than a behavior TT must match.
"""

from __future__ import annotations

import pytest

from app.cards.field_map import get_profile
from app.plugins.anki_sync.add_production_template import (
    IMAGE_FIELD,
    PRODUCTION_TEMPLATE,
    build_production_template,
)
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import SyntheticCollection

#: The real imported notetype this migration ran against, so the templates under
#: test render from the same field-role profile production code uses.
NOTETYPE_NAME = "6000 Most Frequent Norwegian Words"
MID = 1694414741634

#: TT mints ord=1 with an id it chooses and records. The literal is what the
#: assertions read back — if Anki rewrote or duplicated the row, this changes.
TT_MINTED_CARD_ID = 1700000000001

_NOTES = {
    # image written by raw SQL, ord=1 NOT minted -> the stranding window
    "hus": (1700000000101, "house", '<img src="hus.jpg">', False),
    # image written AND ord=1 minted by TT -> must survive untouched
    "bil": (1700000000102, "car", '<img src="bil.jpg">', True),
    # CONTROL: no image, no ord=1 -> must stay at ord=0 in both phases
    "tid": (1700000000103, "time", "", False),
}


def _field_names(profile) -> tuple[str, ...]:
    """The post-migration field list: the profile's own fields, ``Image`` last.

    ``add_production_template`` appends ``Image`` at ``len(field_names)``, so its
    position here is not arbitrary — the template's front resolves by name, but a
    note's ``flds`` resolves by ord.
    """
    names = [profile.l2, profile.translation]
    names += [n for n in (profile.disambig, profile.article) if n]
    names += [spec.field_name for spec in profile.back_fields]
    names.append(IMAGE_FIELD)
    return tuple(names)


def _build(collection: SyntheticCollection) -> None:
    profile = get_profile(NOTETYPE_NAME)
    assert profile is not None, "the production template renders from a field-role profile"
    qfmt, afmt = build_production_template(profile)
    assert qfmt == "{{" + IMAGE_FIELD + "}}", "the production front must be the image alone"

    field_names = _field_names(profile)
    image_ord = field_names.index(IMAGE_FIELD)

    collection.add_notetype(
        MID,
        NOTETYPE_NAME,
        field_names,
        templates=[
            # The single template the imported deck shipped with...
            ("Recognition", "{{" + profile.l2 + "}}", "{{FrontSide}}<hr id=answer>{{" + profile.translation + "}}"),
            # ...and the one the migration added.
            (PRODUCTION_TEMPLATE, qfmt, afmt),
        ],
    )

    card_id = 1700000000201
    for word, (note_id, gloss, image, tt_minted) in _NOTES.items():
        fields = [""] * len(field_names)
        fields[0] = word
        fields[1] = gloss
        fields[image_ord] = image
        collection.add_note(note_id, f"guid-{word}", fields, mid=MID)
        collection.add_card(card_id, note_id, ord=0)
        card_id += 1
        if tt_minted:
            collection.add_card(TT_MINTED_CARD_ID, note_id, ord=1)
    collection.save()


@pytest.mark.oracle
def test_anki_generates_production_card_only_on_check_database(
    synthetic_collection: SyntheticCollection,
) -> None:
    """Opening the collection must not mint ord=1; Check Database must."""
    _build(synthetic_collection)

    result = run_oracle(
        synthetic_collection.path,
        [
            {"op": "note_ords"},
            {"op": "check_database"},
            {"op": "note_ords"},
        ],
    ).raw()

    # dbcheck must actually have RUN. It reports failure by return value rather
    # than raising, and an aborted dbcheck generates nothing — which looks exactly
    # like Anki declining to generate. Without this the test passes vacuously.
    assert result["check_database_1"]["ok"], result["check_database_1"]["report"]

    on_open = result["note_ords_0"]["ords"]
    after_check = result["note_ords_2"]["ords"]

    # A plain open + queue build generates nothing: the note whose image TT
    # wrote is still recognition-only.
    assert on_open == {"hus": [0], "bil": [0, 1], "tid": [0]}, on_open

    # Check Database is the trigger. `hus` gains the production card Anki
    # generated for itself; the CONTROL `tid` does not, which is what makes the
    # `hus` row evidence about the `{{Image}}` front.
    assert after_check == {"hus": [0, 1], "bil": [0, 1], "tid": [0]}, after_check


@pytest.mark.oracle
def test_check_database_leaves_a_tt_minted_production_card_alone(
    synthetic_collection: SyntheticCollection,
) -> None:
    """No duplicate, and TT's recorded card id survives — the basis for adopt-or-create."""
    _build(synthetic_collection)

    result = run_oracle(
        synthetic_collection.path,
        [
            {"op": "check_database"},
            {"op": "note_ords"},
        ],
    ).raw()

    assert result["check_database_0"]["ok"], result["check_database_0"]["report"]

    ords = result["note_ords_1"]["ords"]
    card_ids = result["note_ords_1"]["card_ids"]

    # Exactly one ord=1 card on the note TT minted for — Anki skips an ord that
    # already exists rather than adding a sibling.
    assert ords["bil"] == [0, 1], ords["bil"]
    assert card_ids["bil"]["1"] == TT_MINTED_CARD_ID, card_ids["bil"]

    # The card Anki generated for `hus` carries an id TT never chose. This is
    # the stranding hazard stated as a measurement: TT cannot assume it knows
    # the id of an ord=1 card it did not mint, so the mint must read it back.
    assert card_ids["hus"]["1"] != TT_MINTED_CARD_ID, card_ids["hus"]
