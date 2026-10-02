"""The revlog row for a schedule change nobody graded — Anki oracle parity (Layer 88).

When TunaTale moves a reviewed card's due date or interval by hand (mark known,
restore from known, a state change that reschedules), the push records it the way
Anki records its own Set Due Date: a ``type = 4`` (Manual) row. These tests read
that row from the Anki binary and compare it with ``manual_revlog_row``, the
production shape the push writes.

The trap this pins from both sides: ``type = 4`` with ``factor = 0`` is not a
neutral manual row. It is the marker Anki writes for Forget, and its FSRS code
discards the card's review history before such a row. So the factor has to be
non-zero, and it is truncated, not rounded as it is on the grade path
(``test_parity_revlog_factor.py``).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from app.srs.anki_mirror.protobuf_wire import anki_today_col_day
from app.srs.fsrs import DEFAULT_FSRS5_PARAMS, manual_revlog_row
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import COL_CRT, SyntheticCollection

FSRS_WEIGHTS = DEFAULT_FSRS5_PARAMS.weights

# Difficulties chosen to separate the three candidate formulas. Rounding and
# truncation differ at 7.0 and 8.7; f32 and f64 arithmetic differ at 1.9, 2.8,
# 4.6 and 9.1 (f64 truncation gives 200 / 300 / 500 / 999 there). 1.0 and 10.0
# are the ends of FSRS's range.
_DIFFICULTIES = (1.0, 1.9, 2.8, 4.6, 5.5, 7.0, 8.7, 9.1, 10.0)
_IVL_BEFORE = 10
_REPS, _LAPSES = 5, 1


def _seed_review_cards(coll: SyntheticCollection, *, due_offset: int) -> list[int]:
    coll.enable_fsrs(weights=FSRS_WEIGHTS, retention=0.9)
    today = anki_today_col_day(COL_CRT, datetime.now(UTC))
    now = int(time.time())
    card_ids = []
    for i, difficulty in enumerate(_DIFFICULTIES):
        card_id = 10010 + i * 10
        coll.add_note(id=1001 + i, guid=f"g-manual-{i}", fields=[f"f-{i}", "back"])
        coll.add_card(
            id=card_id,
            note_id=1001 + i,
            ord=0,
            type=2,
            queue=2,
            due=today + due_offset,
            ivl=_IVL_BEFORE,
            reps=_REPS,
            lapses=_LAPSES,
            stability=10.0,
            difficulty=difficulty,
            last_review_secs=now - 7 * 86400,
            desired_retention=0.9,
        )
        card_ids.append(card_id)
    coll.save()
    return card_ids


@pytest.mark.oracle
@pytest.mark.parametrize("due_offset", [3, -4], ids=["not-yet-due", "overdue"])
def test_anki_set_due_date_row_matches_tt_manual_row(
    synthetic_collection: SyntheticCollection, due_offset: int
) -> None:
    """Anki's Set Due Date row == ``manual_revlog_row`` for the same change.

    What this covers:
    - every column of the row (ease, ivl, lastIvl, factor, time, type)
    - the factor's arithmetic, at difficulties where the candidates disagree
    - that the change leaves ``reps`` and ``lapses`` alone
    - that the row is stamped when the change is made, not at the last review

    What this does NOT cover:
    - which interval a TunaTale push leaves on the card (the round trips in
      test_anki_sync_round_trip.py); here the interval is whatever Anki chose
    - a card with no FSRS memory state, where Anki logs ``cards.factor`` instead
    """
    card_ids = _seed_review_cards(synthetic_collection, due_offset=due_offset)

    ops: list[dict] = []
    for card_id in card_ids:
        ops.append({"op": "set_due_date", "card_ids": [card_id], "days": "5"})
        ops.append({"op": "get_card", "card_id": card_id})
        ops.append({"op": "get_revlog", "card_id": card_id})
    started_ms = int(time.time() * 1000)
    raw = run_oracle(synthetic_collection.path, ops).raw()
    finished_ms = int(time.time() * 1000)

    for i, difficulty in enumerate(_DIFFICULTIES):
        assert raw[f"set_due_date_{i * 3}"] == {"ok": True}  # the control: the op ran
        card = raw[f"get_card_{i * 3 + 1}"]
        revlog = raw[f"get_revlog_{i * 3 + 2}"]
        assert len(revlog) == 1, f"d={difficulty}: one change, one row; got {revlog}"
        row = revlog[0]
        assert card["ivl"] != _IVL_BEFORE  # the control: the interval moved, so ivl and lastIvl are told apart

        expected = manual_revlog_row(ivl_before=_IVL_BEFORE, ivl_after=card["ivl"], difficulty=difficulty)
        anki_row = {k: row[k] for k in ("ease", "ivl", "lastIvl", "factor", "time", "type")}
        assert anki_row == expected, f"d={difficulty}: Anki={anki_row} TT={expected}"
        assert (card["reps"], card["lapses"]) == (_REPS, _LAPSES)
        assert started_ms <= row["id"] <= finished_ms


@pytest.mark.oracle
def test_anki_forget_row_is_the_factor_zero_manual_row(synthetic_collection: SyntheticCollection) -> None:
    """Forget writes ``type = 4, factor = 0``: the shape a manual row must never take.

    The other half of the trap. If this ever stops holding, the non-zero factor in
    ``manual_revlog_row`` stops being what separates a reschedule from a reset.
    """
    card_ids = _seed_review_cards(synthetic_collection, due_offset=3)

    raw = run_oracle(
        synthetic_collection.path,
        [
            {"op": "forget_cards", "card_ids": card_ids[:1]},
            {"op": "get_card", "card_id": card_ids[0]},
            {"op": "get_revlog", "card_id": card_ids[0]},
        ],
    ).raw()

    assert raw["get_card_1"]["type"] == 0  # the control: the card really was forgotten
    (row,) = raw["get_revlog_2"]
    assert (row["type"], row["ease"], row["factor"], row["ivl"], row["lastIvl"]) == (4, 0, 0, 0, _IVL_BEFORE)
    for difficulty in _DIFFICULTIES:
        assert manual_revlog_row(ivl_before=_IVL_BEFORE, ivl_after=12, difficulty=difficulty)["factor"] > 0


def test_manual_revlog_factor_is_never_the_reset_marker() -> None:
    """Whatever the input, the factor stays inside [100, 1100]. No oracle needed."""
    from app.srs.fsrs import manual_revlog_factor

    assert [manual_revlog_factor(d) for d in (-3.0, 0.0, 1.0, 5.0, 10.0, 42.0)] == [100, 100, 100, 544, 1100, 1100]
