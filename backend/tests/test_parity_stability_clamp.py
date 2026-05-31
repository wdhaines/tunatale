"""FSRS stability must be clamped to ``[S_MIN, S_MAX]`` like fsrs-rs's ``step``.

**Inspection finding (anki-mirror-audit, 2026-05-30).** fsrs-rs clamps *every*
post-grade stability to ``[S_MIN=0.001, S_MAX=36500.0]`` inside ``Model::step``
(``fsrs-rs/src/model.rs:178``: ``stability: new_s.clamp(S_MIN, S_MAX)``;
constants at ``fsrs-rs/src/simulation.rs:41-42``). TT clamped inconsistently —
``max(0.001, …)`` on some write sites, **no clamp at all** on the lapse
(``_schedule_review_again``) and learning-step (``_schedule_with_steps``) paths,
and **never** the ``S_MAX`` upper bound.

The lower-bound gap is reachable: the lapse formula's own floor
(``stability_after_failure``'s ``new_s_min = last_s / exp(w17·w18)``) sits *below*
0.001 once ``last_s`` is near the floor, so an Again on a minimum-stability card
lands ~0.0008 in TT but Anki clamps it to 0.001. On the live deck the lowest
card (``taliti``, s≈0.0048, 7 lapses) is only a handful of further lapses from
tripping it. The upper bound is effectively unreachable in practice (recall
growth stalls as ``s → S_MAX``) but is mirrored for faithfulness.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.srs_item import Direction, DirectionState, Rating, SRSState
from app.srs.fsrs import (
    DEFAULT_FSRS5_PARAMS,
    _clamp_stability,
    _next_stability_for_grade,
    _quantize_stability,
)

fsrs_rs_python = pytest.importorskip("fsrs_rs_python")

W = DEFAULT_FSRS5_PARAMS.weights


def test_clamp_stability_bounds() -> None:
    """``_clamp_stability`` mirrors fsrs-rs ``clamp(S_MIN, S_MAX)`` on both ends."""
    assert _clamp_stability(0.0005) == pytest.approx(0.001)  # below S_MIN → S_MIN
    assert _clamp_stability(40000.0) == pytest.approx(36500.0)  # above S_MAX → S_MAX
    assert _clamp_stability(42.0) == pytest.approx(42.0)  # in range → unchanged


def _review_prev(stability: float, difficulty: float, elapsed_days: int) -> DirectionState:
    """A REVIEW DirectionState last reviewed *elapsed_days* ago (interday)."""
    last_review = datetime(2026, 5, 30, 12, 0, tzinfo=UTC) - timedelta(days=elapsed_days)
    return DirectionState(
        direction=Direction.RECOGNITION,
        due_at=last_review + timedelta(days=elapsed_days),
        stability=stability,
        difficulty=difficulty,
        state=SRSState.REVIEW,
        last_review=last_review,
    )


def test_lapse_below_floor_clamps_to_s_min() -> None:
    """An Again on a minimum-stability card matches fsrs-rs's S_MIN-clamped value.

    fsrs-rs's lapse floor drops below 0.001 here, then ``step`` clamps it back up
    to S_MIN. TT's ``_next_stability_for_grade`` must produce the same clamped
    value rather than the raw sub-floor lapse stability.
    """
    elapsed = 5
    now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
    prev = _review_prev(stability=0.001, difficulty=9.0, elapsed_days=elapsed)

    tt_s = _next_stability_for_grade(prev, Rating.AGAIN, now, DEFAULT_FSRS5_PARAMS, None)

    f = fsrs_rs_python.FSRS(W)
    anki_s = f.next_states(fsrs_rs_python.MemoryState(0.001, 9.0), 0.9, elapsed).again.memory.stability

    assert anki_s == pytest.approx(0.001, abs=1e-6), "fsrs-rs should clamp the lapse to S_MIN here"
    assert _quantize_stability(tt_s) == _quantize_stability(anki_s), (
        f"lapse below floor: TT={tt_s} vs fsrs-rs (S_MIN-clamped)={anki_s}"
    )
