"""New-card display order: the two deck settings that decide whether a production
card is ever served (tunatale-uze6, tunatale-qf6.13).

Two things were hardcoded in TT until 2026-08-22 — TEMPLATE and HighestPosition —
and a third was in the wrong order.

1. `NewCardGatherPriority` was pinned to HighestPosition; the user's real preset is
   DECK, so TT walked the new pool from the opposite end to Anki.
2. `NewCardSortOrder` was pinned to TEMPLATE.
3. ⚠️ The daily limit was applied AFTER the template sort rather than before it.
   That is the one that mattered. Anki gathers up to the limit in position order and
   sorts only what it gathered, so TEMPLATE ranks the day's new cards; TT sorted the
   whole pool and truncated afterwards, which turns that ranking into a filter — no
   ord=1 card could survive while any new ord=0 card existed, at any position. 284
   production cards, 0 ever served.

All three are fixed. These tests mirror `test_parity_new_card_sort_order.py` case
for case, against the same fixture shape, so the two sides cannot drift; that module
pins the same claims against the real Anki binary, which is what falsified the
original diagnosis.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.models.srs_item import Direction, DirectionState, SRSItem, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.queue_engine import _compute_live_main
from app.srs.database import SRSDatabase

# NewCardSortOrder / NewCardGatherPriority wire values (deck_config fields 32 / 34).
SORT_TEMPLATE = "0"
SORT_NO_SORT = "1"
GATHER_DECK = "0"
GATHER_HIGHEST_POSITION = "2"


def _future(days: int) -> datetime:
    return datetime.combine(date.today() + timedelta(days=days), time(4, 0), tzinfo=UTC)


@pytest.fixture
def db() -> SRSDatabase:
    d = SRSDatabase(":memory:")
    d.set_anki_state_cache("daily_new_cap", "1")
    d.set_anki_state_cache("daily_review_cap", "200")
    d.set_anki_state_cache("new_card_sort_order", SORT_TEMPLATE)
    d.set_anki_state_cache("new_card_gather_priority", GATHER_DECK)
    return d


def _add(db: SRSDatabase, text: str) -> int:
    db.add_collocation(
        SyntacticUnit(text=text, translation=text, word_count=1, difficulty=1, source="corpus"),
        language_code="sl",
    )
    with db._get_conn() as conn:
        return conn.execute("SELECT id FROM collocations WHERE text = ?", (text,)).fetchone()[0]


def _set_direction(
    db: SRSDatabase,
    row_id: int,
    direction: Direction,
    state: SRSState,
    *,
    anki_due: int,
    due_at: datetime,
) -> None:
    db.update_direction_by_id(
        row_id,
        direction,
        DirectionState(
            direction=direction,
            state=state,
            due_at=due_at,
            anki_card_id=anki_due * 10 + (0 if direction == Direction.RECOGNITION else 1),
            anki_due=anki_due,
        ),
    )


def _seed_matured_word_with_production(db: SRSDatabase) -> int:
    """One word whose recognition card is a FUTURE-due review (so it neither enters
    today's due pool nor buries its sibling) carrying a NEW production card at the
    FRONT of the position range."""
    row_id = _add(db, "matured")
    _set_direction(db, row_id, Direction.RECOGNITION, SRSState.REVIEW, anki_due=10, due_at=_future(30))
    _set_direction(db, row_id, Direction.PRODUCTION, SRSState.NEW, anki_due=10, due_at=_future(0))
    return row_id


def _seed_new_vocabulary(db: SRSDatabase, count: int) -> list[int]:
    """Brand-new recognition words at positions well behind the production card."""
    ids = []
    for i in range(count):
        row_id = _add(db, f"newword{i}")
        _set_direction(db, row_id, Direction.RECOGNITION, SRSState.NEW, anki_due=1000 + i, due_at=_future(0))
        ids.append(row_id)
    return ids


def _new_slice(db: SRSDatabase) -> list[tuple[int, Direction]]:
    return [(row_id, direction) for row_id, _item, _lang, direction in _compute_live_main(db)]


class TestNewLimitIsAPositionWindow:
    def test_front_positioned_production_card_is_the_sole_new_card_at_cap_one(self, db):
        """cap=1 and the production card holds position 10, so it is the only card
        inside the window. TEMPLATE ranks what was gathered; it cannot reach the
        five recognition cards at 1000+, which were never gathered."""
        prod_id = _seed_matured_word_with_production(db)
        _seed_new_vocabulary(db, 5)

        assert _new_slice(db) == [(prod_id, Direction.PRODUCTION)]

    def test_template_sort_ranks_within_the_window_without_filtering(self, db):
        """cap=3 gathers positions 10, 1000, 1001. TEMPLATE puts the two ord=0 cards
        first — and the production card is still served, last of the three. This is
        the assertion that separates "TEMPLATE filters production out" (the original,
        false diagnosis) from "TEMPLATE ranks it last within the day's new cards"."""
        db.set_anki_state_cache("daily_new_cap", "3")
        prod_id = _seed_matured_word_with_production(db)
        new_ids = _seed_new_vocabulary(db, 5)

        assert _new_slice(db) == [
            (new_ids[0], Direction.RECOGNITION),
            (new_ids[1], Direction.RECOGNITION),
            (prod_id, Direction.PRODUCTION),
        ]

    def test_no_sort_leaves_the_window_in_position_order(self, db):
        """Same fixture, sort order changed: the production card leads the day's new
        cards instead of trailing them."""
        db.set_anki_state_cache("daily_new_cap", "3")
        db.set_anki_state_cache("new_card_sort_order", SORT_NO_SORT)
        prod_id = _seed_matured_word_with_production(db)
        new_ids = _seed_new_vocabulary(db, 5)

        assert _new_slice(db) == [
            (prod_id, Direction.PRODUCTION),
            (new_ids[0], Direction.RECOGNITION),
            (new_ids[1], Direction.RECOGNITION),
        ]

    def test_a_tail_positioned_production_card_is_still_never_served(self, db):
        """Position is the lever, and this is the fixture that says so. The same
        production card at the far end of the range falls outside the window and is
        not served — which is why the mint allocator, not the sort order, is the
        second half of the fix."""
        row_id = _add(db, "matured")
        _set_direction(db, row_id, Direction.RECOGNITION, SRSState.REVIEW, anki_due=10, due_at=_future(30))
        _set_direction(db, row_id, Direction.PRODUCTION, SRSState.NEW, anki_due=99_999, due_at=_future(0))
        new_ids = _seed_new_vocabulary(db, 5)

        served = _new_slice(db)

        assert served == [(new_ids[0], Direction.RECOGNITION)]
        assert (row_id, Direction.PRODUCTION) not in served


class TestGatherPriority:
    def test_deck_gather_serves_the_lowest_position(self, db):
        new_ids = _seed_new_vocabulary(db, 5)
        assert _new_slice(db) == [(new_ids[0], Direction.RECOGNITION)]

    def test_highest_position_gather_serves_the_highest_position(self, db):
        """The mirror image against one fixture — proof the setting is read rather
        than defaulted."""
        db.set_anki_state_cache("new_card_gather_priority", GATHER_HIGHEST_POSITION)
        new_ids = _seed_new_vocabulary(db, 5)
        assert _new_slice(db) == [(new_ids[-1], Direction.RECOGNITION)]


assert SRSItem  # imported for the tuple shape documented above


class TestTemplateRankIsTheCardsOwnOrd:
    """TEMPLATE ranks by the Anki card's ord, which a direction only implies (tunatale-h0q3.60).

    A lesson line's cards are all PRODUCTION directions of a note whose ords run
    0..N, so "recognition 0, production 1" misranked every one not at ord 1. The
    pull stores the real ord (``anki_ord``); the same decks against the Anki engine:
    ``test_parity_sentence_card_ord.py``.
    """

    def _new_production(self, db: SRSDatabase, text: str, *, anki_due: int, anki_ord: int | None) -> int:
        row_id = _add(db, text)
        _set_direction(db, row_id, Direction.RECOGNITION, SRSState.REVIEW, anki_due=anki_due, due_at=_future(30))
        db.update_direction_by_id(
            row_id,
            Direction.PRODUCTION,
            DirectionState(
                direction=Direction.PRODUCTION,
                state=SRSState.NEW,
                due_at=_future(0),
                anki_card_id=anki_due * 10 + 1,
                anki_due=anki_due,
                anki_ord=anki_ord,
            ),
        )
        return row_id

    def test_a_card_at_ord_two_ranks_after_a_production_card_gathered_later(self, db):
        db.set_anki_state_cache("daily_new_cap", "2")
        blank = self._new_production(db, "blank", anki_due=5, anki_ord=2)
        prod = self._new_production(db, "word", anki_due=10, anki_ord=1)

        assert _new_slice(db) == [(prod, Direction.PRODUCTION), (blank, Direction.PRODUCTION)]

    def test_a_production_direction_at_ord_zero_ranks_with_recognition_in_gather_order(self, db):
        db.set_anki_state_cache("daily_new_cap", "3")
        whole = self._new_production(db, "line", anki_due=5, anki_ord=0)
        prod = self._new_production(db, "word", anki_due=10, anki_ord=1)
        [rec] = _seed_new_vocabulary(db, 1)

        assert _new_slice(db) == [
            (whole, Direction.PRODUCTION),
            (rec, Direction.RECOGNITION),
            (prod, Direction.PRODUCTION),
        ]

    def test_a_card_whose_ord_is_not_known_yet_ranks_by_its_direction(self, db):
        """Not in Anki yet (or not pulled since the column arrived): nothing to mirror, the
        direction's usual ord stands."""
        db.set_anki_state_cache("daily_new_cap", "2")
        unsynced = self._new_production(db, "unsynced", anki_due=5, anki_ord=None)
        [rec] = _seed_new_vocabulary(db, 1)

        assert _new_slice(db) == [(rec, Direction.RECOGNITION), (unsynced, Direction.PRODUCTION)]
