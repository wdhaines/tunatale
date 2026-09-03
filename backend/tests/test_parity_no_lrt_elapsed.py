"""What elapsed Anki uses when a card has no ``lrt`` — and where TT differs.

Anki persists an FSRS-effective last-review timestamp in ``cards.data.lrt``, but
only for cards it has reviewed under FSRS. For anything older, TT reconstructs a
day-level ``last_review`` marker from ``due - ivl``
(``_compute_last_review``), and ``_grade_elapsed_days`` differences that against
today.

Measured here, because the rule is not obvious and two plausible readings are
both wrong:

* with a revlog, Anki takes elapsed from the **last revlog entry**, NOT from
  ``due - ivl`` and NOT from the short-term formula;
* with no revlog at all, it falls back to ``stability_short_term`` (elapsed 0).

⚠️ The second branch is a fixture-only shape and reading it as the general rule
is a trap this suite fell into once: a review card in a real collection has
review history by definition. A probe that seeded a review card with no revlog
concluded Anki "always uses short_term for no-lrt cards" and made the divergence
look about 2x. It is not — see ``test_untouched_card_agrees``.

So TT's ``due - ivl`` is exactly right while a card is untouched (its interval
IS the gap since its last review) and drifts only after something moves ``due``
without a review: ``set_due_date``, a manual reschedule, an interval edit.
``test_rescheduled_card_diverges`` pins that gap rather than lamenting it — the
shape ``test_colday_helper_consistency.py`` uses for the same reason. Following
the revlog instead would add a per-card query to the sync read path and rewrite
``last_review`` for every such card, which ``_direction_differs`` turns into a
push; measured against the real databases, 2 of 3030 review rows are in this
branch at all.
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
_IVL = 10
# f32-vs-f64 only. A one-day elapsed difference moves stability by several
# percent, three orders of magnitude outside this.
_TOL = 1e-4


def _anki_states(coll: SyntheticCollection, card_id: int) -> dict:
    raw = run_oracle(coll.path, [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}]).raw()
    cards = {c["card_id"]: c for c in raw[next(k for k in raw if k.startswith("get_queue"))]["cards"]}
    assert card_id in cards, f"card not in Anki's queue: {list(cards)}"
    return cards[card_id]


def _tt_stability(elapsed: float) -> float:
    return _next_stability_recall(
        _D, _S, _forgetting_curve(elapsed, _S, decay=-0.5), Rating.GOOD, DEFAULT_FSRS5_PARAMS.weights
    )


def _build(coll: SyntheticCollection, now: datetime, revlog_days_back: int | None) -> tuple[int, int, int]:
    """Seed one no-lrt review card due today. Returns (card_id, col_crt, due)."""
    from app.srs.anki_mirror.protobuf_wire import anki_today_col_day

    col_crt = int(
        (now.astimezone() - timedelta(days=800)).replace(hour=4, minute=0, second=0, microsecond=0).timestamp()
    )
    coll.col_crt = col_crt
    coll.enable_fsrs(weights=DEFAULT_FSRS5_PARAMS.weights, retention=DEFAULT_DESIRED_RETENTION)
    due = anki_today_col_day(col_crt, now)

    coll.add_note(id=6001, guid="g-nolrt", fields=["f", "b"])
    # No last_review_secs → no `lrt` in cards.data, which is the whole point.
    coll.add_card(
        id=60010, note_id=6001, ord=0, type=2, queue=2, due=due, ivl=_IVL, reps=5, stability=_S, difficulty=_D
    )
    if revlog_days_back is not None:
        coll.add_revlog(
            id=int((now - timedelta(days=revlog_days_back)).timestamp() * 1000),
            card_id=60010,
            ease=3,
            ivl=_IVL,
            last_ivl=5,
            time=2000,
            type=1,
        )
    coll.save()
    return 60010, col_crt, due


def _tt_elapsed(col_crt: int, due: int, now: datetime) -> int:
    """What TT computes at grade time for this card, through the real pipeline."""
    from app.plugins.anki_sync.sqlite_reader import _compute_last_review
    from app.srs.fsrs import _grade_elapsed_days

    marker = _compute_last_review(2, due, _IVL, col_crt)
    return _grade_elapsed_days(marker, now, col_crt=col_crt)


@pytest.mark.oracle
def test_untouched_card_agrees(synthetic_collection: SyntheticCollection) -> None:
    """Interval == gap since the last review: TT's `due - ivl` IS Anki's answer.

    This is the shape every real no-lrt card has, and the reason the divergence
    below was not worth chasing.
    """
    with local_timezone(timezone_with_local_hour(12)):
        now = datetime.now(tz=UTC)
        card_id, col_crt, due = _build(synthetic_collection, now, revlog_days_back=_IVL)
        anki = _anki_states(synthetic_collection, card_id)
        tt_elapsed = _tt_elapsed(col_crt, due, now)

    assert tt_elapsed == _IVL, f"expected TT to read the interval back, got {tt_elapsed}"
    anki_s = anki["states"]["good"]["stability"]
    assert abs(_tt_stability(tt_elapsed) - anki_s) / anki_s < _TOL, (
        f"TT elapsed={tt_elapsed} gives {_tt_stability(tt_elapsed):.6f}, Anki wrote {anki_s:.6f}"
    )


@pytest.mark.oracle
def test_rescheduled_card_diverges(synthetic_collection: SyntheticCollection) -> None:
    """Anki follows the REVLOG; TT follows `due - ivl`. Pinned, not fixed.

    Reached only when something moved `due` without a review. If this test ever
    fails because the two now AGREE, someone taught `_compute_last_review` to
    read the revlog — re-read this module's docstring before celebrating, because
    that rewrites `last_review` for every such card and `_direction_differs`
    turns it into a push.
    """
    revlog_days_back = 3
    with local_timezone(timezone_with_local_hour(12)):
        now = datetime.now(tz=UTC)
        card_id, col_crt, due = _build(synthetic_collection, now, revlog_days_back=revlog_days_back)
        anki = _anki_states(synthetic_collection, card_id)
        tt_elapsed = _tt_elapsed(col_crt, due, now)

    anki_s = anki["states"]["good"]["stability"]
    # Anki's number is the revlog gap...
    assert abs(_tt_stability(revlog_days_back) - anki_s) / anki_s < _TOL, (
        f"Anki no longer follows the revlog: elapsed={revlog_days_back} predicts "
        f"{_tt_stability(revlog_days_back):.6f}, Anki wrote {anki_s:.6f}"
    )
    # ...and TT's is the interval, which is a different number here.
    assert tt_elapsed == _IVL != revlog_days_back
    assert abs(_tt_stability(tt_elapsed) - anki_s) / anki_s > 0.05, (
        "TT and Anki agree here, so this test no longer documents anything — see the docstring"
    )
