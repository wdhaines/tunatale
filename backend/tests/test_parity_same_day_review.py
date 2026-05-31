"""Same-day (delta_t == 0) REVIEW grade must use FSRS short-term stability.

**Inspection finding (anki-mirror-audit, 2026-05-30).** fsrs-rs ``Model::step``
overrides the success/failure stability with ``stability_short_term`` whenever
``delta_t == 0`` — for *every* rating, not just Again
(``fsrs-rs/src/model.rs:163``:
``new_stability = new_stability.mask_where(delta_t.equal_elem(0), stability_short_term)``).

TT's ``schedule()`` honoured this on the REVIEW + AGAIN branch
(``_schedule_review_again``) but the REVIEW + HARD/GOOD/EASY branch called
``_next_stability_recall`` directly, ignoring the same-day override. That made a
same-day re-review of a REVIEW-state card compute the recall (interday) stability
instead of the short-term one.

**Why the compare-shadow soak never caught it.** The replay *is* DRY with the
live path (``rebuild_from_revlog`` → ``schedule``), but the soak runs it
*incrementally*: it anchors each card to its last-synced ``DirectionState`` (=
Anki's authoritative ``cards.data.s``) and forward-steps only the revlog rows
added since that sync (``_write_compare_shadow`` → ``starting_state`` + ``since_id``).
Already-synced same-day double-grades are baked into the anchor, so they are
never re-derived through ``schedule``. A *from-scratch* replay does diverge —
reproduced against the live collection: card "How is Slovene..." graded EASY
twice on 05-08 (01:50 and 02:31, both before the 4am col-day rollover ⇒
``delta_t == 0``) stored s=132.667 in Anki, while a full ``schedule`` replay
produced 175.05. The bug is live *between* syncs (TT's R-asc queue uses the wrong
stability until the next sync re-anchors it).

This pins ``schedule`` (the live grade path) against ``fsrs_rs_python.next_states``
at ``days_elapsed = 0`` so the divergence can never silently return.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.models.srs_item import Direction, DirectionState, Rating, SRSItem, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.fsrs import DEFAULT_FSRS5_PARAMS, _quantize_stability, schedule

fsrs_rs_python = pytest.importorskip("fsrs_rs_python")

W = DEFAULT_FSRS5_PARAMS.weights

# delta_t == 0: last review earlier the *same* col-day (both before the 4am
# rollover, matching the reproduced collection case). With col_crt unset,
# ``_grade_elapsed_days`` uses a UTC-date diff which is 0 for two same-date stamps.
_LAST_REVIEW = datetime(2026, 5, 30, 1, 50, tzinfo=UTC)
_NOW = datetime(2026, 5, 30, 2, 31, tzinfo=UTC)


def _review_item(stability: float, difficulty: float) -> SRSItem:
    """A REVIEW-state SRSItem last reviewed earlier the same col-day."""
    unit = SyntacticUnit(text="t", translation="t", word_count=1, difficulty=1, source="t")
    direction = DirectionState(
        direction=Direction.RECOGNITION,
        due_at=_LAST_REVIEW + timedelta(days=10),
        stability=stability,
        difficulty=difficulty,
        reps=5,
        lapses=0,
        state=SRSState.REVIEW,
        last_review=_LAST_REVIEW,
        anki_card_id=12345,
        anki_due=int(_LAST_REVIEW.timestamp()) // 86400,
    )
    return SRSItem(
        syntactic_unit=unit,
        directions={Direction.RECOGNITION: direction},
        guid="g-same-day",
        anki_note_id=999,
    )


def _anki_same_day_stability(stability: float, difficulty: float, attr: str) -> float:
    """fsrs-rs short-term stability for a same-day (days_elapsed=0) grade."""
    f = fsrs_rs_python.FSRS(W)
    states = f.next_states(fsrs_rs_python.MemoryState(stability, difficulty), 0.9, 0)
    return getattr(states, attr).memory.stability


# (stability, difficulty) spanning low / medium / high-stability review cards.
_REVIEW_STATES = [
    (5.0, 3.0),
    (40.0, 6.84),
    (132.667, 5.5),  # the reproduced "How is Slovene..." card
]

# Only HARD/GOOD/EASY: REVIEW + AGAIN already routes through the short-term
# branch (``_schedule_review_again``) and entering RELEARNING needs deck-config
# step resolution, which the passing path deliberately avoids.
_PASSING = [(Rating.HARD, "hard"), (Rating.GOOD, "good"), (Rating.EASY, "easy")]


@pytest.mark.parametrize("stability, difficulty", _REVIEW_STATES)
@pytest.mark.parametrize("rating, attr", _PASSING)
def test_same_day_review_passing_uses_short_term(
    stability: float, difficulty: float, rating: Rating, attr: str
) -> None:
    """A same-day passing REVIEW grade must match fsrs-rs's short-term stability."""
    item = schedule(
        _review_item(stability, difficulty),
        rating,
        review_date=date(2026, 5, 30),
        now=_NOW,
        params=DEFAULT_FSRS5_PARAMS,
    )
    tt_s = item.directions[Direction.RECOGNITION].stability
    anki_s = _anki_same_day_stability(stability, difficulty, attr)
    assert _quantize_stability(tt_s) == _quantize_stability(anki_s), (
        f"same-day REVIEW+{rating.name} s={stability} d={difficulty}: TT={tt_s} (stored) vs fsrs-rs short-term={anki_s}"
    )
