"""Where Anki's new-card limit is applied, and what the sort order does after it
(tunatale-uze6, tunatale-qf6.13).

TunaTale had never served a production card: 284 minted, 1439 new recognition cards
ahead of them, 0 introduced. Two different stories explain that, and only the real
binary distinguishes them — which is the point of this module.

**Anki gathers up to the new limit in POSITION order first, and applies
NewCardSortOrder only to what it gathered.** So the limit is a position window: a
card outside it is never sorted at all, and TunaTale's production cards sit at
positions 1,000,287+ behind a window that starts at 1518. Within the window, the
default TEMPLATE order ranks ord=0 ahead of ord=1 — but that is a reordering of
cards that are all going to be served today, not a filter.

The consequence, measured here rather than assumed: a production card at the FRONT
of the position range IS served under Anki's default settings. No deck-option change
is needed; the position allocator is the whole fix.

⚠️ This falsified the first written diagnosis, which had TEMPLATE filtering the
whole pool and concluded that repositioning could not help. TunaTale's own queue
engine works that way — it template-sorts the full pool and truncates afterwards —
and reading TT's mirror is what produced the wrong story about Anki. See
``test_new_card_display_order.py`` for the TT-side pin of the corrected behaviour.

What this test covers:
- The limit is applied in gather order, BEFORE the sort: at new/day=1 the sole
  served card is the front-positioned production card, under TEMPLATE.
- The sort reorders only the gathered slice: at new/day=3 the production card is
  served, ranked last of the three by TEMPLATE.
- NO_SORT leaves the gathered slice in position order, putting it first.

What it does NOT cover (owned elsewhere):
- Gather priority (DECK vs HighestPosition) — ``test_new_card_display_order.py``.
- Where a minted production card's position comes from — that is the mint
  allocator, still on MAX(due)+1 (tunatale-uze6, second half).
"""

from __future__ import annotations

import time

import pytest

from app.srs.fsrs import DEFAULT_FSRS5_PARAMS
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import (
    DEFAULT_DESIRED_RETENTION,
    SyntheticCollection,
)

FSRS_WEIGHTS = DEFAULT_FSRS5_PARAMS.weights

DUAL_MID = 1600000000002
SORT_ORDER_TEMPLATE = 0
SORT_ORDER_NO_SORT = 1

# Below Anki's 365_000 day sentinel; far enough out that the recognition sibling is
# neither gathered as due nor able to bury its new production sibling.
FUTURE_DUE_DAY = 50_000

MATURED_NOTE_ID = 300
PRODUCTION_POSITION = 10
NEW_WORD_BASE_POSITION = 1000
NEW_WORD_COUNT = 5


def _build(coll: SyntheticCollection, *, sort_order: int, new_per_day: int) -> tuple[int, list[int]]:
    """A matured word carrying a front-positioned NEW production card, plus five
    brand-new recognition words far behind it. Returns (production_card_id,
    new_recognition_card_ids)."""
    coll.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)
    coll.set_bury(bury_new=True, bury_reviews=True)
    coll.set_daily_limits(new=new_per_day, reviews=200)
    coll.set_new_card_display_order(sort_order=sort_order, gather_priority=0)
    coll.add_notetype(DUAL_MID, "Dual", ("Front", "Back"), template_count=2)

    past = int(time.time()) - 5 * 86400
    coll.add_note(id=MATURED_NOTE_ID, guid="g-matured", fields=["matured", "back"], mid=DUAL_MID)
    # Recognition (ord 0): a mature review, due far in the future.
    coll.add_card(
        id=MATURED_NOTE_ID * 10,
        note_id=MATURED_NOTE_ID,
        ord=0,
        type=2,
        queue=2,
        due=FUTURE_DUE_DAY,
        ivl=30,
        reps=5,
        stability=30.0,
        difficulty=5.0,
        last_review_secs=past,
        desired_retention=DEFAULT_DESIRED_RETENTION,
    )
    # Production (ord 1): NEW, at the front of the position range.
    production_card_id = MATURED_NOTE_ID * 10 + 1
    coll.add_card(
        id=production_card_id,
        note_id=MATURED_NOTE_ID,
        ord=1,
        type=0,
        queue=0,
        due=PRODUCTION_POSITION,
    )

    new_card_ids = []
    for i in range(NEW_WORD_COUNT):
        note_id = 400 + i
        coll.add_note(id=note_id, guid=f"g-new-{i}", fields=[f"newword{i}", "back"], mid=DUAL_MID)
        card_id = note_id * 10
        coll.add_card(
            id=card_id,
            note_id=note_id,
            ord=0,
            type=0,
            queue=0,
            due=NEW_WORD_BASE_POSITION + i,
        )
        new_card_ids.append(card_id)
    coll.save()
    return production_card_id, new_card_ids


def _served_new_card_ids(coll: SyntheticCollection) -> list[int]:
    result = run_oracle(coll.path, [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}])
    return [c["card_id"] for c in result.raw()["get_queue_0"]["cards"]]


@pytest.mark.oracle
def test_new_limit_is_a_position_window_applied_before_the_sort(
    synthetic_collection: SyntheticCollection,
) -> None:
    """new/day=1 and the production card holds the lowest position, so it is the
    one card gathered — and TEMPLATE, which ranks ord=0 first, never gets to
    demote it, because the five recognition cards were never gathered at all."""
    production_card_id, new_card_ids = _build(synthetic_collection, sort_order=SORT_ORDER_TEMPLATE, new_per_day=1)

    served = _served_new_card_ids(synthetic_collection)

    assert served == [production_card_id], (
        f"expected the front-positioned production card {production_card_id} as the sole "
        f"gathered new card; served={served}, recognition cards={new_card_ids}"
    )


@pytest.mark.oracle
def test_template_sort_reorders_the_gathered_slice_without_filtering_it(
    synthetic_collection: SyntheticCollection,
) -> None:
    """new/day=3 gathers positions 10, 1000 and 1001. TEMPLATE then ranks the two
    ord=0 cards ahead of the ord=1 card — all three are still served. This is the
    assertion that separates "TEMPLATE filters production out" (false) from
    "TEMPLATE ranks it last within the day's new cards" (true)."""
    production_card_id, new_card_ids = _build(synthetic_collection, sort_order=SORT_ORDER_TEMPLATE, new_per_day=3)

    served = _served_new_card_ids(synthetic_collection)

    assert served == [new_card_ids[0], new_card_ids[1], production_card_id], (
        f"expected the two lowest-positioned recognition cards then the production card; served={served}"
    )


@pytest.mark.oracle
def test_no_sort_leaves_the_gathered_slice_in_position_order(
    synthetic_collection: SyntheticCollection,
) -> None:
    """Same collection, sort order changed: the gathered slice keeps position order,
    so the production card leads it instead of trailing it."""
    production_card_id, new_card_ids = _build(synthetic_collection, sort_order=SORT_ORDER_NO_SORT, new_per_day=3)

    served = _served_new_card_ids(synthetic_collection)

    assert served == [production_card_id, new_card_ids[0], new_card_ids[1]], (
        f"expected position order under NO_SORT; served={served}"
    )
