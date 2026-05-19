"""SRS item domain model (FSRS-based).

Each collocation tracks two directions independently:
- recognition (L2 → L1): the historical default; powers lesson transcripts
- production (L1 → L2): new in v2; powers the production drill route.

Flat FSRS fields on `SRSItem` (`state`, `due_date`, `stability`, ...) are
compatibility shims that read/write the recognition direction. They exist so
callers predating the two-direction schema keep working during Stage 1 and
are scheduled for removal in Stage 3.5 of the Anki sync plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from enum import Enum

from .syntactic_unit import SyntacticUnit


class SRSState(Enum):
    """Learning state of an SRS item."""

    NEW = "new"
    LEARNING = "learning"
    REVIEW = "review"
    RELEARNING = "relearning"
    SUSPENDED = "suspended"
    BURIED = "buried"
    KNOWN = "known"


class Rating(Enum):
    """Learner rating for an SRS review."""

    AGAIN = 1  # Complete blackout / forgot
    HARD = 2  # Significant difficulty
    GOOD = 3  # Correct with some effort
    EASY = 4  # Perfect recall


class Direction(Enum):
    """Review direction for an SRS item."""

    RECOGNITION = "recognition"  # L2 → L1 (Anki ord=0)
    PRODUCTION = "production"  # L1 → L2 (Anki ord=1)


@dataclass
class DirectionState:
    """FSRS scheduling state for one direction of a collocation.

    Single source of truth for due-time: ``due_at`` (TEXT iso datetime, UTC).
    Extended to all states (review/new included), NOT NULL.
    """

    direction: Direction
    due_at: datetime
    stability: float = 1.0
    difficulty: float = 5.0
    reps: int = 0
    lapses: int = 0
    state: SRSState = field(default=SRSState.NEW)
    last_review: datetime | None = None
    last_review_time_ms: int = 0
    anki_card_id: int | None = None
    anki_due: int | None = None
    # Anki's `cards.mod` (modification timestamp). Used as the secondary sort
    # key under RetrievabilityAscending — Anki tiebreaks via `fnvhash(id, mod)`.
    anki_card_mod: int | None = None
    # Source of a buried state: 'user' (manual bury, persists across rollover)
    # or 'sched' (sibling/auto bury, released at next rollover via Layer 27's
    # unbury_if_needed sweep). NULL on non-buried rows.
    bury_kind: str | None = None
    dirty_fsrs: bool = False
    last_synced_at: str | None = None
    last_rating: int | None = None
    left: int | None = None
    # Prior-grade snapshot used to construct a correct Anki revlog row at
    # push time. Set by `app.srs.fsrs.schedule` before each `replace`,
    # cleared by `mark_direction_clean` once the row has been pushed.
    prior_state: SRSState | None = None
    prior_left: int | None = None
    prior_stability: float | None = None
    # First-grade timestamp — set once on the initial NEW→non-NEW transition
    # (by `app.srs.fsrs.schedule` for TT-side grades, by `sync_pull` for Anki
    # grades). Used by `count_new_introduced_today` to mirror Anki's `newToday`
    # counter, which increments only on the actual first-grade event. Layer 26.
    introduced_at: datetime | None = None


class SRSItem:
    """An SRS-tracked syntactic unit with per-direction FSRS scheduling.

    Accepts two construction styles:

    1. Two-direction (new): `SRSItem(syntactic_unit=..., directions={...}, guid=..., anki_note_id=...)`.
    2. Flat legacy:         `SRSItem(syntactic_unit=..., due_date=..., stability=..., state=..., ...)`.

    The legacy kwargs populate the recognition direction and seed production
    with defaults. They will be removed in Stage 3.5 once all call sites move
    to `directions[Direction.RECOGNITION]` access.
    """

    __slots__ = ("syntactic_unit", "directions", "guid", "anki_note_id")

    def __init__(
        self,
        syntactic_unit: SyntacticUnit,
        directions: dict[Direction, DirectionState] | None = None,
        guid: str | None = None,
        anki_note_id: int | None = None,
        *,
        due_date: date | None = None,
        stability: float = 1.0,
        difficulty: float = 5.0,
        reps: int = 0,
        lapses: int = 0,
        state: SRSState = SRSState.NEW,
        last_review: date | None = None,
    ) -> None:
        self.syntactic_unit = syntactic_unit
        self.guid = guid
        self.anki_note_id = anki_note_id

        if directions is not None:
            self.directions = directions
        else:
            rec_due = due_date if due_date is not None else date.today()
            recognition_due_at = datetime.combine(rec_due, time(4, 0), tzinfo=UTC)
            self.directions = {
                Direction.RECOGNITION: DirectionState(
                    direction=Direction.RECOGNITION,
                    due_at=recognition_due_at,
                    stability=stability,
                    difficulty=difficulty,
                    reps=reps,
                    lapses=lapses,
                    state=state,
                    last_review=last_review,
                ),
                Direction.PRODUCTION: DirectionState(
                    direction=Direction.PRODUCTION,
                    due_at=recognition_due_at,
                ),
            }

    # ── Backward-compat flat shims (mirror recognition direction) ───────
    #
    # These let `item.state`, `item.reps`, etc. keep working for callers
    # predating the two-direction schema. Readers return recognition's value;
    # writers mutate recognition's DirectionState in place.

    @property
    def _rec(self) -> DirectionState:
        # Cloze items only carry a PRODUCTION direction (single-template Anki
        # Cloze notetype). Flat shims fall through to whichever direction the
        # card_type implies.
        if self.syntactic_unit.card_type == "cloze":
            return self.directions[Direction.PRODUCTION]
        return self.directions[Direction.RECOGNITION]

    @property
    def due_date(self) -> date:
        return self._rec.due_at.date()

    @due_date.setter
    def due_date(self, value: date) -> None:
        self._rec.due_at = datetime.combine(value, time.min).replace(tzinfo=UTC)

    @property
    def stability(self) -> float:
        return self._rec.stability

    @stability.setter
    def stability(self, value: float) -> None:
        self._rec.stability = value

    @property
    def difficulty(self) -> float:
        return self._rec.difficulty

    @difficulty.setter
    def difficulty(self, value: float) -> None:
        self._rec.difficulty = value

    @property
    def reps(self) -> int:
        return self._rec.reps

    @reps.setter
    def reps(self, value: int) -> None:
        self._rec.reps = value

    @property
    def lapses(self) -> int:
        return self._rec.lapses

    @lapses.setter
    def lapses(self, value: int) -> None:
        self._rec.lapses = value

    @property
    def state(self) -> SRSState:
        return self._rec.state

    @state.setter
    def state(self, value: SRSState) -> None:
        self._rec.state = value

    @property
    def last_review(self) -> date | None:
        return self._rec.last_review

    @last_review.setter
    def last_review(self, value: date | None) -> None:
        self._rec.last_review = value
