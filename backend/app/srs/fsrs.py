"""FSRS-5 spaced repetition scheduling algorithm.

Reference: https://github.com/open-spaced-repetition/fsrs5
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from app.models.srs_item import Direction, Rating, SRSItem, SRSState

# FSRS-5 default parameters (w vector, 19 values)
W = [
    0.4072,  # w0: initial stability for Again
    1.1829,  # w1: initial stability for Hard
    3.1262,  # w2: initial stability for Good
    15.4722,  # w3: initial stability for Easy
    7.2102,  # w4: initial difficulty
    0.5316,  # w5: initial difficulty decay
    1.0651,  # w6: difficulty mean-reversion weight
    0.0589,  # w7: difficulty update weight
    1.5330,  # w8: stability increase factor
    0.1544,  # w9: stability increase decay
    1.0050,  # w10: stability increase R-factor
    1.9767,  # w11: lapse stability factor
    0.0967,  # w12: lapse stability difficulty decay
    0.2573,  # w13: lapse stability S-factor
    2.2930,  # w14: lapse stability R-factor
    0.5100,  # w15: hard penalty
    2.9898,  # w16: easy bonus
    0.5100,  # w17: (unused in v5)
    0.4350,  # w18: (unused in v5)
]

REQUESTED_RETENTION = 0.9
DECAY = -0.5
FACTOR = 19 / 81  # = 0.234...


def _forgetting_curve(elapsed_days: float, stability: float) -> float:
    """Retrievability at elapsed_days given stability."""
    return (1 + FACTOR * elapsed_days / stability) ** DECAY


def _next_interval(stability: float) -> int:
    """Days until next review at REQUESTED_RETENTION."""
    interval = stability / FACTOR * (REQUESTED_RETENTION ** (1 / DECAY) - 1)
    return max(1, min(round(interval), 36500))


def _init_stability(rating: Rating) -> float:
    return W[rating.value - 1]


def _init_difficulty(rating: Rating) -> float:
    d = W[4] - math.exp(W[5] * (rating.value - 1)) + 1
    return max(1.0, min(10.0, d))


def _next_difficulty(d: float, rating: Rating) -> float:
    # W[6]=1.0651 is the delta multiplier; W[7]=0.0589 is the mean-reversion weight
    next_d = d - W[6] * (rating.value - 3)
    # Mean-reversion toward W[4]=7.21 (the initial difficulty for a "normal" item)
    next_d = W[7] * W[4] + (1 - W[7]) * next_d
    return max(1.0, min(10.0, next_d))


def _next_stability_recall(d: float, s: float, r: float, rating: Rating) -> float:
    hard_penalty = W[15] if rating == Rating.HARD else 1.0
    easy_bonus = W[16] if rating == Rating.EASY else 1.0
    return s * (
        math.exp(W[8]) * (11 - d) * s ** (-W[9]) * (math.exp((1 - r) * W[10]) - 1) * hard_penalty * easy_bonus + 1
    )


def _next_stability_lapse(d: float, s: float, r: float) -> float:
    return W[11] * d ** (-W[12]) * ((s + 1) ** W[13] - 1) * math.exp((1 - r) * W[14])


def schedule(
    item: SRSItem,
    rating: Rating,
    review_date: date | None = None,
    direction: Direction = Direction.RECOGNITION,
) -> SRSItem:
    """Apply a review rating to the given direction of an SRSItem.

    Updates only the specified direction; the other is left untouched.
    Marks `dirty_fsrs=True` on the updated direction so the Anki-sync layer
    can later push the change.

    Args:
        item: The SRSItem to schedule.
        rating: Learner's rating for this review.
        review_date: The date of the review (defaults to today).
        direction: Which direction was reviewed (default: recognition).

    Returns:
        A new SRSItem with the updated direction state.
    """
    if review_date is None:
        review_date = date.today()

    from dataclasses import replace

    prev = item.directions[direction]

    if prev.state == SRSState.NEW:
        new_stability = _init_stability(rating)
        new_difficulty = _init_difficulty(rating)
        new_reps = 1
        new_lapses = prev.lapses
        new_state = SRSState.LEARNING if rating == Rating.AGAIN else SRSState.REVIEW
    else:
        last = prev.last_review or review_date
        elapsed = max(0, (review_date - last).days)
        r = _forgetting_curve(elapsed, prev.stability)

        if rating == Rating.AGAIN:
            new_stability = _next_stability_lapse(prev.difficulty, prev.stability, r)
            new_difficulty = _next_difficulty(prev.difficulty, rating)
            new_reps = prev.reps + 1
            new_lapses = prev.lapses + 1
            new_state = SRSState.RELEARNING
        else:
            new_stability = _next_stability_recall(prev.difficulty, prev.stability, r, rating)
            new_difficulty = _next_difficulty(prev.difficulty, rating)
            new_reps = prev.reps + 1
            new_lapses = prev.lapses
            new_state = SRSState.REVIEW

    new_stability = max(0.1, new_stability)
    new_difficulty = max(1.0, min(10.0, new_difficulty))
    interval = _next_interval(new_stability)
    new_due = review_date + timedelta(days=interval)

    new_dir = replace(
        prev,
        stability=new_stability,
        difficulty=new_difficulty,
        due_date=new_due,
        reps=new_reps,
        lapses=new_lapses,
        state=new_state,
        last_review=review_date,
        dirty_fsrs=True,
    )
    new_directions = dict(item.directions)
    new_directions[direction] = new_dir
    return SRSItem(
        syntactic_unit=item.syntactic_unit,
        directions=new_directions,
        guid=item.guid,
        anki_note_id=item.anki_note_id,
    )
