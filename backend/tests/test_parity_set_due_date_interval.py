"""The interval a pushed due date leaves on the card — Anki oracle parity (Layer 89).

When a TunaTale push moves a review card's due date, it also has to write
``cards.ivl``. Anki's own Set Due Date writes the days since the last review plus
the days from today, so that the interval stays "last review to due date" however
late the change is made. These tests read that number from the Anki binary and
compare it with ``review_interval_for_due``.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from app.srs.anki_mirror.protobuf_wire import anki_today_col_day
from app.srs.fsrs import DEFAULT_FSRS5_PARAMS, review_interval_for_due
from tests._helpers.localtz import local_timezone, timezone_with_local_hour
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


@pytest.mark.oracle
@pytest.mark.parametrize("local_hour", [12, 1])
def test_set_due_date_on_a_card_without_lrt_matches_at_any_hour(
    synthetic_collection: SyntheticCollection, local_hour: int
) -> None:
    """A no-``lrt`` card: Anki adds the due-date shift to the old interval, and so must TT.

    TunaTale reads such a card's last review as the day-level marker ``due - ivl``
    and counts the days since it from today. That reduces to Anki's sum only if
    "today" is Anki's today. Hour 1 is inside ``[local midnight, 04:00)``, where the
    index domain is a day ahead; main's CI went red there on 2026-10-03
    (tunatale-46js). The collection is created at 04:00 local, as a real one is.

    What this does NOT cover: a card with ``lrt`` (the test above).
    """
    from app.plugins.anki_sync.sqlite_reader import _compute_last_review
    from app.srs.anki_mirror.protobuf_wire import compute_anki_day_index

    old_ivl, old_ahead, days = 40, 2, 5
    with local_timezone(timezone_with_local_hour(local_hour)):
        now = datetime.now(UTC)
        col_crt = int(
            (now.astimezone() - timedelta(days=800)).replace(hour=4, minute=0, second=0, microsecond=0).timestamp()
        )
        synthetic_collection.col_crt = col_crt
        synthetic_collection.enable_fsrs(weights=DEFAULT_FSRS5_PARAMS.weights, retention=0.9)
        today = anki_today_col_day(col_crt, now)
        # The control: inside the band the index domain is a day ahead of Anki's today.
        assert (compute_anki_day_index(col_crt, 4, now) != today) is (local_hour < 4)
        synthetic_collection.add_note(id=2001, guid="g-ivl-nolrt", fields=["f", "back"])
        # No last_review_secs: no `lrt` in cards.data, which is the whole point.
        synthetic_collection.add_card(
            id=20010, note_id=2001, ord=0, type=2, queue=2, due=today + old_ahead, ivl=old_ivl, reps=5,
            stability=10.0, difficulty=5.5,
        )  # fmt: skip
        synthetic_collection.save()
        raw = run_oracle(
            synthetic_collection.path,
            [{"op": "set_due_date", "card_ids": [20010], "days": str(days)}, {"op": "get_card_row", "card_id": 20010}],
        ).raw()
        marker = _compute_last_review(2, today + old_ahead, old_ivl, col_crt)
        tt_ivl = review_interval_for_due(marker, days, col_crt=col_crt, now=now)

    card = raw["get_card_row_1"]
    assert card["due"] == today + days  # the control: the op ran and moved the due date
    assert card["ivl"] == old_ivl + days - old_ahead  # Anki's rule for a card with no lrt
    assert tt_ivl == card["ivl"], f"local hour {local_hour}: Anki={card['ivl']} TT={tt_ivl}"


def test_review_interval_for_due_without_a_last_review_is_unknown() -> None:
    """No last review, no interval to compute: the caller keeps its own fallback."""
    assert review_interval_for_due(None, 5, col_crt=COL_CRT) is None


def test_review_interval_for_due_is_at_least_one_day() -> None:
    """Reviewed today and due today would be 0; a review card's interval is never 0 here."""
    now = datetime.now(UTC)
    assert review_interval_for_due(now, 0, col_crt=COL_CRT, now=now) == 1
