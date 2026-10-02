"""The interval a pushed due date leaves on the card — Anki oracle parity (Layer 89).

When a TunaTale push moves a review card's due date, it also has to write
``cards.ivl``. Anki's own Set Due Date writes the days since the last review plus
the days from today, so that the interval stays "last review to due date" however
late the change is made. These tests read that number from the Anki binary and
compare it with ``review_interval_for_due``.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from app.srs.anki_mirror.protobuf_wire import anki_today_col_day
from app.srs.fsrs import DEFAULT_FSRS5_PARAMS, review_interval_for_due
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import COL_CRT, SyntheticCollection

_HOUR, _DAY = 3600, 86400
# (seconds since the last review, days from today). The last two put the review
# a little under and a little over a whole number of days ago, where a formula
# that counts calendar dates and one that divides a duration come apart.
_CASES = (
    (7 * _DAY, 5),
    (14 * _DAY, 0),
    (1 * _HOUR, 3),
    (3 * _DAY - 2 * _HOUR, 10),
    (3 * _DAY + 2 * _HOUR, 10),
)


@pytest.mark.oracle
def test_anki_set_due_date_interval_matches_review_interval_for_due(synthetic_collection: SyntheticCollection) -> None:
    """Anki's ``cards.ivl`` after Set Due Date == ``review_interval_for_due``.

    What this covers:
    - a review card with a real last-review time (``lrt``), reviewed days ago,
      an hour ago, and either side of a whole number of days ago
    - a due date of today (0 days) and of days ahead

    What this does NOT cover:
    - a card with no ``lrt``, where Anki adds the due-date shift to the old
      interval; TunaTale's day-level branch reduces to the same sum
    - a new card or one with interval 0, which Anki leaves at interval 0
    """
    synthetic_collection.enable_fsrs(weights=DEFAULT_FSRS5_PARAMS.weights, retention=0.9)
    now = datetime.now(UTC)
    today = anki_today_col_day(COL_CRT, now)
    now_secs = int(time.time())
    for i, (since_review, _days) in enumerate(_CASES):
        synthetic_collection.add_note(id=1001 + i, guid=f"g-ivl-{i}", fields=[f"f-{i}", "back"])
        synthetic_collection.add_card(
            id=10010 + i * 10,
            note_id=1001 + i,
            ord=0,
            type=2,
            queue=2,
            due=today + 2,
            ivl=40,
            reps=5,
            stability=10.0,
            difficulty=5.5,
            last_review_secs=now_secs - since_review,
            desired_retention=0.9,
        )
    synthetic_collection.save()

    ops: list[dict] = []
    for i, (_since, days) in enumerate(_CASES):
        ops.append({"op": "set_due_date", "card_ids": [10010 + i * 10], "days": str(days)})
        ops.append({"op": "get_card_row", "card_id": 10010 + i * 10})
    raw = run_oracle(synthetic_collection.path, ops).raw()

    for i, (since_review, days) in enumerate(_CASES):
        card = raw[f"get_card_row_{i * 2 + 1}"]
        assert card["due"] == today + days  # the control: the op ran and moved the due date
        assert card["ivl"] != 40  # the control: the interval was rewritten, not left alone
        last_review = datetime.fromtimestamp(now_secs - since_review, tz=UTC)
        tt_ivl = review_interval_for_due(last_review, days, col_crt=COL_CRT, now=now)
        assert card["ivl"] == tt_ivl, f"reviewed {since_review}s ago, due in {days}d: Anki={card['ivl']} TT={tt_ivl}"


def test_review_interval_for_due_without_a_last_review_is_unknown() -> None:
    """No last review, no interval to compute: the caller keeps its own fallback."""
    assert review_interval_for_due(None, 5, col_crt=COL_CRT) is None


def test_review_interval_for_due_is_at_least_one_day() -> None:
    """Reviewed today and due today would be 0; a review card's interval is never 0 here."""
    now = datetime.now(UTC)
    assert review_interval_for_due(now, 0, col_crt=COL_CRT, now=now) == 1
