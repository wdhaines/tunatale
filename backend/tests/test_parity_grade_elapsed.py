"""Oracle parity for the GRADE path's ``days_elapsed`` (Layer 50, re-measured).

``_grade_elapsed_days`` feeds ``stability_after_success`` / ``_after_failure``.
Getting it wrong by one day does not fail loudly — it shifts every REVIEW
grade's stability by several percent, which is exactly the silent drift Layer 50
was created to remove.

Anki's answering path measures a DURATION from the next rollover:
``next_day_at.elapsed_days_since(lrt)``, integer-divided by 86400 (rslib
``scheduler/answering/mod.rs``). That is neither of TT's day-index domains. TT
used to difference two ``compute_anki_day_index`` values, which cancels the
crt-offset skew only while both endpoints sit on the same side of
``[local midnight, 04:00)``; with exactly one inside, it was off by a full day.

Layer 50 measured this path bit-exact across 65 real REVIEW→REVIEW grades and
was not wrong — none of those grades had an endpoint in the band. A sample that
misses a narrow window looks exactly like proof the window does not exist, which
is the same way the day-index bug survived 598 CI runs.

**The oracle here is the post-grade stability Anki actually writes**, not a
restatement of the formula above: the card is really graded through
``answer_card_raw`` and its resulting ``memory_state.stability`` read back. A
wrong elapsed cannot survive that.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.srs_item import Rating
from app.srs.fsrs import DEFAULT_FSRS5_PARAMS, _forgetting_curve, _next_stability_recall
from tests._helpers.localtz import local_timezone, timezone_with_local_hour
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import DEFAULT_DESIRED_RETENTION, SyntheticCollection

_S = 12.0
_D = 5.5
_DAYS_BACK = 5

# (local hour NOW, local hour of the review). The first two straddle the
# rollover band with exactly one endpoint inside — the configurations the old
# index-difference got wrong. The third has neither inside and is the control:
# it passed before this fix and must keep passing, or the change broke the
# ordinary case to fix the boundary one.
_CASES = (
    pytest.param(12, 2, id="review-inside-band"),
    pytest.param(2, 14, id="now-inside-band"),
    pytest.param(12, 14, id="control-neither-inside"),
)


@pytest.mark.oracle
@pytest.mark.parametrize("now_local_hour,review_local_hour", _CASES)
def test_grade_elapsed_reproduces_anki_stability(
    synthetic_collection: SyntheticCollection, now_local_hour: int, review_local_hour: int
) -> None:
    from app.srs.anki_mirror.rollover import local_next_rollover
    from app.srs.fsrs import _grade_elapsed_days

    weights = DEFAULT_FSRS5_PARAMS.weights
    zone = timezone_with_local_hour(now_local_hour)

    with local_timezone(zone):
        now = datetime.now(tz=UTC)
        # A real col.crt: 4 AM LOCAL on the creation day.
        col_crt = int(
            (now.astimezone() - timedelta(days=800)).replace(hour=4, minute=0, second=0, microsecond=0).timestamp()
        )
        # Sub-day-precise lrt, so Anki takes the recall path rather than
        # short-term (a card with no lrt is a different branch entirely).
        lrt = (
            (now.astimezone() - timedelta(days=_DAYS_BACK))
            .replace(hour=review_local_hour, minute=17, second=3, microsecond=0)
            .astimezone(UTC)
        )

        synthetic_collection.col_crt = col_crt
        synthetic_collection.enable_fsrs(weights=weights, retention=DEFAULT_DESIRED_RETENTION)
        synthetic_collection.add_note(id=701, guid="g-grade", fields=["f", "b"])
        synthetic_collection.add_card(
            id=7010,
            note_id=701,
            ord=0,
            type=2,
            queue=2,
            due=0,
            ivl=10,
            reps=5,
            stability=_S,
            difficulty=_D,
            last_review_secs=int(lrt.timestamp()),
        )
        synthetic_collection.save()

        raw = run_oracle(
            synthetic_collection.path,
            [{"op": "get_today"}, {"op": "answer_card", "card_id": 7010, "rating": 3}],
        ).raw()
        anki_cutoff = raw[next(k for k in raw if k.startswith("get_today"))]["day_cutoff"]
        anki_stability = raw[next(k for k in raw if k.startswith("answer_card"))]["stability"]

        tt_elapsed = _grade_elapsed_days(lrt, now, col_crt=col_crt)
        tt_cutoff = int(local_next_rollover(now).timestamp())

    # TT's notion of "the next rollover" must be Anki's, or the duration below is
    # measured from the wrong instant even when the arithmetic is right.
    assert tt_cutoff == anki_cutoff, f"TT next_day_at {tt_cutoff} != Anki day_cutoff {anki_cutoff}"

    predicted = _next_stability_recall(_D, _S, _forgetting_curve(tt_elapsed, _S, decay=-0.5), Rating.GOOD, weights)
    # f32-vs-f64 only; a one-day elapsed error moves stability by ~8-9%, which is
    # three orders of magnitude outside this bound.
    assert abs(predicted - anki_stability) / anki_stability < 1e-4, (
        f"elapsed={tt_elapsed} gives stability {predicted:.6f}, Anki wrote {anki_stability:.6f} "
        f"(now_local={now_local_hour:02d}, review_local={review_local_hour:02d}, zone={zone})"
    )
