"""Pre-flight transition parity (Stage 3b gate).

Before Stage 3b collapses ``_pull_merge_direction`` to trust the forward-step
``schedule()``, we must pin the FSRS transitions the 2026-05-23 measurement day
did NOT exercise. That day was overwhelmingly REVIEW→REVIEW (plus a few
graduations); the collapse — and the TT-native live grade path — leans on
``schedule()`` reproducing Anki bit-exact across transitions that day never hit.

These tests pin the **replay-derived fields** (``stability`` and ``difficulty``)
against Anki's persisted post-grade ``cards.data`` for:

- NEW → REVIEW via EASY-skip (graduate immediately, FSRS init at EASY weights).
- LEARNING → REVIEW graduation (NEW → GOOD → GOOD on a 2-step ladder, no AGAINs).
- REVIEW → HARD on a high-stability card (recall-stability hard penalty).
- REVIEW → RELEARNING with no same-day recovery (lapse stability + Layer-42 ceiling).

``due_at`` is intentionally NOT compared: it's pass-through from Anki at sync
(Layer 53 — the FSRS load balancer relocates the interval within the fuzz range
using the collection-wide due histogram, which a per-card forward-step can't
reconstruct). Stage 3b replay-derives ``stability``/``difficulty`` only, and
those drive R-asc queue order; ``due_at`` is read from ``cards.due`` verbatim.

Comparison is at the quantization grid (4dp stability / 3dp difficulty), NOT
raw exact equality. Both sides quantize per-grade — TT at every ``schedule()``
write site, Anki in ``rslib/src/storage/card/data.rs:95-98`` — but Anki then
stores the result as f32, so the oracle reads back e.g. ``88.427803`` where TT's
f64 holds ``88.4278``. That sub-4dp gap is the f32/f64 difference the plan
explicitly says NOT to chase (TT computes FSRS in f64, fsrs-rs in f32; matching
the trailing digits means porting platform libm — a trap). Rounding both to the
4dp/3dp grid both apps quantize to is the correct, non-flaky pin and is far
tighter than the Stage-3b shadow-column gate (5% relative on stability).

If any of these diverge, it's a Layer-56+ candidate to fix BEFORE Stage 3b
step 1 — the collapse trusts these transitions.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from app.models.srs_item import Direction, DirectionState, Rating, SRSItem, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.fsrs import DEFAULT_FSRS5_PARAMS, schedule
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import (
    DEFAULT_DESIRED_RETENTION,
    SyntheticCollection,
)

# Drive both apps with TT's production FSRS-5 weights (sit inside every clip
# range, so neither side's parameter_clipper alters them — see the note in
# test_parity_fsrs_schedule.py).
FSRS_WEIGHTS = DEFAULT_FSRS5_PARAMS.weights

_ELAPSED_DAYS = 30


def _s_eq(tt: float, anki: float) -> bool:
    """Equal at the 4dp stability quantization grid (absorbs f32-vs-f64)."""
    return round(tt, 4) == round(anki, 4)


def _d_eq(tt: float, anki: float) -> bool:
    """Equal at the 3dp difficulty quantization grid (absorbs f32-vs-f64)."""
    return round(tt, 3) == round(anki, 3)


def _new_item(guid: str, note_id: int, anki_card_id: int) -> SRSItem:
    """A fresh NEW-state SRSItem for the RECOGNITION direction."""
    unit = SyntacticUnit(text="t", translation="t", word_count=1, difficulty=1, source="t")
    direction = DirectionState(
        direction=Direction.RECOGNITION,
        due_at=datetime.now(UTC),
        anki_card_id=anki_card_id,
    )
    return SRSItem(
        syntactic_unit=unit,
        directions={Direction.RECOGNITION: direction},
        guid=guid,
        anki_note_id=note_id,
    )


def _review_item(
    guid: str,
    note_id: int,
    anki_card_id: int,
    stability: float,
    difficulty: float,
    last_review_dt: datetime,
    reps: int,
    lapses: int,
) -> SRSItem:
    """A REVIEW-state SRSItem matching a seeded Anki review card."""
    unit = SyntacticUnit(text="t", translation="t", word_count=1, difficulty=1, source="t")
    direction = DirectionState(
        direction=Direction.RECOGNITION,
        due_at=last_review_dt + timedelta(days=_ELAPSED_DAYS),
        stability=stability,
        difficulty=difficulty,
        reps=reps,
        lapses=lapses,
        state=SRSState.REVIEW,
        last_review=last_review_dt,
        anki_card_id=anki_card_id,
        anki_due=int(last_review_dt.timestamp()) // 86400,
    )
    return SRSItem(
        syntactic_unit=unit,
        directions={Direction.RECOGNITION: direction},
        guid=guid,
        anki_note_id=note_id,
    )


def _anki_final_state(coll: SyntheticCollection, card_id: int, operations: list[dict]) -> dict:
    """Run *operations* against the oracle and return the trailing get_card dict."""
    coll.save()
    result = run_oracle(coll.path, operations)
    raw = result.raw()
    return raw[next(k for k in raw if k.startswith("get_card_"))]


@pytest.mark.oracle
def test_parity_new_to_review_easy_skip(synthetic_collection: SyntheticCollection) -> None:
    """NEW + EASY graduates straight to REVIEW; FSRS inits at EASY weights.

    The Stage-0 audit flagged NEW→REVIEW Easy-skip as a likely misclassification
    in ``_compute_review_kind``; this pins the *memory state* of that transition
    (stability/difficulty) against Anki regardless of revlog ``type`` labelling.
    """
    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)
    synthetic_collection.set_learning_steps(learn_steps=[1.0, 10.0], relearn_steps=[10.0])

    card_id, note_id = 10010, 1001
    synthetic_collection.add_note(id=note_id, guid="g-easy-skip", fields=["front", "back"])
    synthetic_collection.add_card(id=card_id, note_id=note_id, ord=0, type=0, queue=0, reps=0, left=0)

    anki = _anki_final_state(
        synthetic_collection,
        card_id,
        [
            {"op": "answer_card", "card_id": card_id, "rating": 4},
            {"op": "get_card", "card_id": card_id},
        ],
    )

    item = schedule(_new_item("g-easy-skip", note_id, card_id), Rating.EASY)
    new_dir = item.directions[Direction.RECOGNITION]

    assert new_dir.state == SRSState.REVIEW, "EASY on a NEW card must graduate to REVIEW"
    assert _s_eq(new_dir.stability, anki["stability"]), (
        f"NEW→REVIEW EASY-skip stability: TT={new_dir.stability} vs Anki={anki['stability']}"
    )
    assert _d_eq(new_dir.difficulty, anki["difficulty"]), (
        f"NEW→REVIEW EASY-skip difficulty: TT={new_dir.difficulty} vs Anki={anki['difficulty']}"
    )


@pytest.mark.oracle
def test_parity_learning_to_review_graduation_clean(synthetic_collection: SyntheticCollection) -> None:
    """NEW → GOOD (step 1) → GOOD (graduate) on a 2-step ladder, no AGAINs.

    The empirical day's graduations were AGAIN-heavy (covered by
    ``test_parity_graduation_after_many_agains``). This pins the clean
    graduation path: two same-day GOOD grades routing through
    ``_stability_short_term``.
    """
    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)
    synthetic_collection.set_learning_steps(learn_steps=[1.0, 10.0], relearn_steps=[10.0])

    card_id, note_id = 10020, 1002
    synthetic_collection.add_note(id=note_id, guid="g-grad-clean", fields=["front", "back"])
    synthetic_collection.add_card(id=card_id, note_id=note_id, ord=0, type=0, queue=0, reps=0, left=0)

    anki = _anki_final_state(
        synthetic_collection,
        card_id,
        [
            {"op": "answer_card", "card_id": card_id, "rating": 3},
            {"op": "answer_card", "card_id": card_id, "rating": 3},
            {"op": "get_card", "card_id": card_id},
        ],
    )

    item = _new_item("g-grad-clean", note_id, card_id)
    item = schedule(item, Rating.GOOD)
    item = schedule(item, Rating.GOOD)
    new_dir = item.directions[Direction.RECOGNITION]

    assert new_dir.state == SRSState.REVIEW, "second GOOD on a 2-step ladder must graduate"
    assert _s_eq(new_dir.stability, anki["stability"]), (
        f"LEARNING→REVIEW graduation stability: TT={new_dir.stability} vs Anki={anki['stability']}"
    )
    assert _d_eq(new_dir.difficulty, anki["difficulty"]), (
        f"LEARNING→REVIEW graduation difficulty: TT={new_dir.difficulty} vs Anki={anki['difficulty']}"
    )


@pytest.mark.oracle
def test_parity_review_hard_high_stability(synthetic_collection: SyntheticCollection) -> None:
    """REVIEW + HARD on a high-stability card stays REVIEW; pins the hard penalty.

    Seeds a deeply-known card (s=50, d=2) 30 days back so R is meaningfully <1,
    then grades HARD. Exercises ``_next_stability_recall`` with the HARD branch
    on the high-stability tail the empirical day under-sampled.
    """
    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)

    s, d = 50.0, 2.0
    reps, lapses = 8, 0
    now_secs = int(time.time())
    last_review_secs = now_secs - _ELAPSED_DAYS * 86400

    card_id, note_id = 10030, 1003
    synthetic_collection.add_note(id=note_id, guid="g-review-hard", fields=["front", "back"])
    synthetic_collection.add_card(
        id=card_id,
        note_id=note_id,
        ord=0,
        type=2,
        queue=2,
        due=0,
        ivl=_ELAPSED_DAYS,
        reps=reps,
        lapses=lapses,
        stability=s,
        difficulty=d,
        last_review_secs=last_review_secs,
    )

    anki = _anki_final_state(
        synthetic_collection,
        card_id,
        [
            {"op": "answer_card", "card_id": card_id, "rating": 2},
            {"op": "get_card", "card_id": card_id},
        ],
    )

    last_review_dt = datetime.fromtimestamp(last_review_secs, tz=UTC)
    grade_dt = datetime.fromtimestamp(now_secs, tz=UTC)
    item = _review_item("g-review-hard", note_id, card_id, s, d, last_review_dt, reps, lapses)
    item = schedule(item, Rating.HARD, review_date=grade_dt.date(), now=grade_dt)
    new_dir = item.directions[Direction.RECOGNITION]

    assert new_dir.state == SRSState.REVIEW, "HARD on a REVIEW card stays REVIEW"
    assert _s_eq(new_dir.stability, anki["stability"]), (
        f"REVIEW→HARD stability: TT={new_dir.stability} vs Anki={anki['stability']}"
    )
    assert _d_eq(new_dir.difficulty, anki["difficulty"]), (
        f"REVIEW→HARD difficulty: TT={new_dir.difficulty} vs Anki={anki['difficulty']}"
    )


@pytest.mark.oracle
def test_parity_review_to_relearning_no_recovery(synthetic_collection: SyntheticCollection) -> None:
    """REVIEW + AGAIN → RELEARNING, no same-day GOOD recovery.

    Seeds a low-stability high-difficulty card (s=1.5, d=7.5) — the regime where
    fsrs-rs's lapse-stability ceiling (Layer 42) bites — 30 days back, then grades
    AGAIN once. Pins ``_next_stability_lapse`` + the post-lapse difficulty in a
    real transition (not the isolated-formula test in test_parity_fsrs_schedule).
    """
    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)
    synthetic_collection.set_learning_steps(learn_steps=[1.0, 10.0], relearn_steps=[10.0])

    s, d = 1.5, 7.5
    reps, lapses = 6, 1
    now_secs = int(time.time())
    last_review_secs = now_secs - _ELAPSED_DAYS * 86400

    card_id, note_id = 10040, 1004
    synthetic_collection.add_note(id=note_id, guid="g-relearn", fields=["front", "back"])
    synthetic_collection.add_card(
        id=card_id,
        note_id=note_id,
        ord=0,
        type=2,
        queue=2,
        due=0,
        ivl=_ELAPSED_DAYS,
        reps=reps,
        lapses=lapses,
        stability=s,
        difficulty=d,
        last_review_secs=last_review_secs,
    )

    anki = _anki_final_state(
        synthetic_collection,
        card_id,
        [
            {"op": "answer_card", "card_id": card_id, "rating": 1},
            {"op": "get_card", "card_id": card_id},
        ],
    )

    last_review_dt = datetime.fromtimestamp(last_review_secs, tz=UTC)
    grade_dt = datetime.fromtimestamp(now_secs, tz=UTC)
    item = _review_item("g-relearn", note_id, card_id, s, d, last_review_dt, reps, lapses)
    item = schedule(item, Rating.AGAIN, review_date=grade_dt.date(), now=grade_dt)
    new_dir = item.directions[Direction.RECOGNITION]

    assert new_dir.state == SRSState.RELEARNING, "AGAIN on a REVIEW card lapses to RELEARNING"
    assert _s_eq(new_dir.stability, anki["stability"]), (
        f"REVIEW→RELEARNING lapse stability: TT={new_dir.stability} vs Anki={anki['stability']}"
    )
    assert _d_eq(new_dir.difficulty, anki["difficulty"]), (
        f"REVIEW→RELEARNING lapse difficulty: TT={new_dir.difficulty} vs Anki={anki['difficulty']}"
    )
