"""From-scratch sequence parity: TT ``schedule()`` vs fsrs-rs over realistic grade trajectories.

**Why this exists (the gap Layer 62 exposed).** The compare-shadow soak shares the
live grade path (``rebuild_from_revlog`` → ``schedule``) but runs it *incrementally*:
it anchors each card to its last-synced ``DirectionState`` (Anki's authoritative
``cards.data``) and forward-steps only the few revlog rows since ``since_id``. So it
validates ``replay(anchor) ∘ recent_grades`` — never the live ``schedule()`` path
re-derived across a multi-grade sequence. A ``schedule()``-only bug that bites
*between* syncs and is overwritten by the next anchor (exactly Layer 62: same-day
re-review used the recall formula instead of FSRS short-term) is invisible to it.

A *from-scratch* full-history replay over the live deck can't cleanly fill the gap
either — every same-day-re-reviewed card on the deck also carries pre-FSRS
(``factor>0``) / restore-stamped revlog rows, so it mixes the bug with historical
noise (measured 2026-05-30: 0 FSRS-clean same-day cards to validate against).

So this gate is synthetic and data-noise-free: it walks a realistic trajectory using
fsrs-rs's ``next_states`` as the ground truth (the exact crate Anki embeds), and at
**every step** checks TT's single ``schedule()`` transition against it. The memory-state
update is a pure function of ``(prev_memory, delta_t, rating)`` — independent of the
card-state machine (Anki applies ``Model::step`` the same whether the card is
review/learning/relearning) — so this covers same-day bursts (``delta_t==0``, the
Layer-62 trigger), interday reviews, and lapse→relearn transitions in one sweep.

Each step is evaluated against the *ground-truth* previous state (not TT's own output),
so errors don't compound — keeping the per-step comparison at the same storage precision
as ``test_parity_fsrs_f32`` rather than accumulating ULP noise across the chain.
"""

from __future__ import annotations

import platform
from datetime import UTC, date, datetime, timedelta

import pytest

from app.models.srs_item import Direction, DirectionState, Rating, SRSItem, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.fsrs import (
    DEFAULT_FSRS5_PARAMS,
    _quantize_difficulty,
    _quantize_stability,
    schedule,
)

fsrs_rs_python = pytest.importorskip("fsrs_rs_python")

W = DEFAULT_FSRS5_PARAMS.weights
DR = 0.86  # the live deck's desired_retention (stability is dr-independent; matches reality)

# Same arch-aware precision stance as test_parity_fsrs_f32: numpy f32 transcendentals
# are bit-reproducible with fsrs-rs's Rust libm only on the deploy arch (arm64); on x86
# CI they differ ~1 ULP, which can tip a 4dp/3dp boundary. Strict storage-equality on
# arm64 (enforced by local ./test.sh pre-commit), ±1-quantum tolerance elsewhere.
_STRICT = platform.machine().lower() in ("arm64", "aarch64")

_RATING_ATTR = {Rating.AGAIN: "again", Rating.HARD: "hard", Rating.GOOD: "good", Rating.EASY: "easy"}

_BASE = date(2026, 1, 1)


def _review_item(stability: float, difficulty: float, last_review: datetime) -> SRSItem:
    unit = SyntacticUnit(text="t", translation="t", word_count=1, difficulty=1, source="t")
    direction = DirectionState(
        direction=Direction.RECOGNITION,
        due_at=last_review + timedelta(days=10),
        stability=stability,
        difficulty=difficulty,
        reps=5,
        lapses=0,
        state=SRSState.REVIEW,
        last_review=last_review,
        anki_card_id=987654,
        anki_due=int(last_review.timestamp()) // 86400,
    )
    return SRSItem(syntactic_unit=unit, directions={Direction.RECOGNITION: direction}, guid="g", anki_note_id=7)


def _assert_close(tt_stored: float, anki_raw: float, quantize, quantum: float, msg: str) -> None:
    """Compare TT's stored (already-quantized) value against Anki's stored value.

    Anki's stored value is ``quantize(anki_raw)`` (Anki's ``round_to_places``, which
    ``_quantize_*`` mirrors). On the deploy arch (arm64) the two are bit-identical, so we
    assert exact storage equality. On x86 CI, fsrs-rs's Rust libm and numpy's f32 differ
    by ~1 ULP, which can tip a single quantization boundary — so we allow ±1 quantum
    (1e-4 stability / 1e-3 difficulty), far below any real-formula-bug delta. NOTE the
    raw value must be quantized first: comparing the stored value against the *raw* value
    spuriously fails at small magnitudes (a 5e-5 rounding gap exceeds 1e-4 relative)."""
    anki_stored = quantize(anki_raw)
    if _STRICT:
        assert tt_stored == anki_stored, msg
    else:
        assert abs(tt_stored - anki_stored) <= quantum * 1.5, msg


# Realistic trajectories: (label, (s0, d0), [(rating, gap_days), ...]).
# gap_days=0 ⇒ same col-day re-review (delta_t==0, the Layer-62 trigger).
_SEQUENCES = [
    ("same_day_passing_burst", (40.0, 6.84), [(Rating.GOOD, 0), (Rating.GOOD, 0), (Rating.EASY, 0)]),
    ("interday_reviews", (5.0, 5.5), [(Rating.GOOD, 3), (Rating.HARD, 7), (Rating.GOOD, 14)]),
    ("interday_then_same_day_burst", (20.0, 4.0), [(Rating.GOOD, 5), (Rating.GOOD, 0), (Rating.EASY, 0)]),
    ("lapse_then_same_day_relearn", (60.0, 7.0), [(Rating.AGAIN, 10), (Rating.GOOD, 0)]),
    ("lapse_then_interday_relearn", (60.0, 7.0), [(Rating.AGAIN, 10), (Rating.GOOD, 1)]),
    ("high_stability_mixed", (200.0, 5.0), [(Rating.HARD, 30), (Rating.GOOD, 0), (Rating.AGAIN, 0), (Rating.GOOD, 0)]),
    ("floor_regime_lapses", (0.5, 9.0), [(Rating.AGAIN, 2), (Rating.AGAIN, 1), (Rating.GOOD, 0)]),
]


@pytest.mark.parametrize("label, start, steps", _SEQUENCES, ids=[s[0] for s in _SEQUENCES])
def test_replay_sequence_matches_fsrs_rs(
    label: str, start: tuple[float, float], steps: list[tuple[Rating, int]]
) -> None:
    """TT ``schedule()`` must match fsrs-rs's memory state at every step of the trajectory.

    Walks the ground-truth trajectory with ``fsrs_rs_python.next_states`` and, at each
    grade, evaluates TT's single ``schedule()`` transition from the same (ground-truth,
    quantized) previous state. Catches any (state, delta_t, rating) combination where
    ``schedule()`` selects the wrong FSRS formula (the Layer-62 class) anywhere a real
    multi-grade session would reach it.
    """
    f = fsrs_rs_python.FSRS(W)
    cur_s, cur_d = start
    prev_date = _BASE

    for i, (rating, gap) in enumerate(steps):
        now_date = prev_date + timedelta(days=gap)
        now_dt = datetime.combine(now_date, datetime.min.time(), tzinfo=UTC).replace(hour=12)
        prev_dt = datetime.combine(prev_date, datetime.min.time(), tzinfo=UTC).replace(hour=12)

        # fsrs-rs ground truth for this transition.
        anki = getattr(f.next_states(fsrs_rs_python.MemoryState(cur_s, cur_d), DR, gap), _RATING_ATTR[rating])
        anki_s, anki_d = anki.memory.stability, anki.memory.difficulty

        # TT's single schedule() transition from the same previous state.
        item = schedule(
            _review_item(cur_s, cur_d, prev_dt),
            rating,
            review_date=now_date,
            now=now_dt,
            params=DEFAULT_FSRS5_PARAMS,
        )
        nd = item.directions[Direction.RECOGNITION]

        ctx = f"{label} step {i} ({rating.name}, gap={gap}d): TT s={nd.stability} d={nd.difficulty} vs fsrs-rs s={anki_s} d={anki_d}"
        _assert_close(nd.stability, anki_s, _quantize_stability, 1e-4, f"stability {ctx}")
        _assert_close(nd.difficulty, anki_d, _quantize_difficulty, 1e-3, f"difficulty {ctx}")

        # Advance along the ground-truth (quantized) path so the next step starts from a
        # realistic state without compounding any TT-side rounding.
        cur_s, cur_d = _quantize_stability(anki_s), _quantize_difficulty(anki_d)
        prev_date = now_date
