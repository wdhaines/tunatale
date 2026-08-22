"""Negative new-card positions, and what "the front of the new queue" means at each
gather setting (Layer 83, tunatale-uze6).

TunaTale writes cards Anki did not create — production cards minted onto an imported
note, and its own `/listen` auto-adds — and both must land at the FRONT of the new
queue to be seen. Under DECK gather the front is the LOWEST position, and the
imported Norwegian deck already occupies 1518 upward, so "ahead of the deck" means
BELOW it: a reserved band at negative positions.

That band only works if Anki's scheduler orders negative `cards.due` values the way
the arithmetic says it does. `cards.due` is an i32 and nothing in the Anki UI offers
a negative starting position, so this is assumed rather than documented — and an
assumption underneath a position allocator is exactly the kind that produced Layer
83's wrong diagnosis. Pinned here against the binary instead.

What this test covers:
- Anki gathers negative positions in ascending order, interleaved with zero and
  positive ones, and serves them first.
- A card written into the reserved band is served ahead of a 1518-based imported
  deck, at the default `daily_new_cap`.

What it does NOT cover (owned elsewhere):
- The sort applied after gather — `test_parity_new_card_sort_order.py`.
- TT's mirror of the same ordering — `test_new_card_display_order.py`.
"""

from __future__ import annotations

import pytest

from app.plugins.anki_sync.sync_writer import _PRODUCTION_BAND_FLOOR
from app.srs.fsrs import DEFAULT_FSRS5_PARAMS
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import DEFAULT_DESIRED_RETENTION, SyntheticCollection

MID = 1600000000004


def _seed(coll: SyntheticCollection, positions: list[int], *, new_per_day: int) -> list[int]:
    coll.enable_fsrs(weights=DEFAULT_FSRS5_PARAMS.weights, retention=DEFAULT_DESIRED_RETENTION)
    coll.set_daily_limits(new=new_per_day, reviews=200)
    coll.set_new_card_display_order(sort_order=0, gather_priority=0)
    coll.add_notetype(MID, "Single", ("Front", "Back"), template_count=1)

    card_ids = []
    for i, due in enumerate(positions):
        note_id = 600 + i
        coll.add_note(id=note_id, guid=f"g-pos-{i}", fields=[f"w{i}", "b"], mid=MID)
        coll.add_card(id=note_id * 10, note_id=note_id, ord=0, type=0, queue=0, due=due)
        card_ids.append(note_id * 10)
    coll.save()
    return card_ids


def _served(coll: SyntheticCollection) -> list[int]:
    result = run_oracle(coll.path, [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}])
    return [c["card_id"] for c in result.raw()["get_queue_0"]["cards"]]


@pytest.mark.oracle
def test_negative_positions_gather_in_ascending_order(synthetic_collection: SyntheticCollection) -> None:
    """Seeded in position order, so the assertion is that Anki agrees rather than
    that it happens to preserve insertion order — the two are separated by the
    descending-gather case in `test_parity_new_card_sort_order.py`."""
    positions = [_PRODUCTION_BAND_FLOOR, _PRODUCTION_BAND_FLOOR + 1, -5, 0, 7, 1518]
    card_ids = _seed(synthetic_collection, positions, new_per_day=len(positions))

    assert _served(synthetic_collection) == card_ids


@pytest.mark.oracle
def test_a_banded_card_beats_an_imported_deck_at_the_real_daily_cap(
    synthetic_collection: SyntheticCollection,
) -> None:
    """The shape that matters on real data: one card in the reserved band against a
    deck starting at 1518, at the Norwegian deck's actual `new/day = 3`."""
    positions = [_PRODUCTION_BAND_FLOOR, 1518, 1519, 1520, 1521]
    card_ids = _seed(synthetic_collection, positions, new_per_day=3)

    served = _served(synthetic_collection)

    assert served[0] == card_ids[0], f"the banded card must be introduced first; served={served}"
    assert len(served) == 3
