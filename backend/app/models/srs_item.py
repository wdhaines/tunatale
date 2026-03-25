"""SRS item domain model (FSRS-based)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from .syntactic_unit import SyntacticUnit


class SRSState(Enum):
    """Learning state of an SRS item."""

    NEW = "new"
    LEARNING = "learning"
    REVIEW = "review"
    RELEARNING = "relearning"


class Rating(Enum):
    """Learner rating for an SRS review."""

    AGAIN = 1  # Complete blackout / forgot
    HARD = 2  # Significant difficulty
    GOOD = 3  # Correct with some effort
    EASY = 4  # Perfect recall


@dataclass
class SRSItem:
    """An SRS-tracked syntactic unit with FSRS scheduling fields."""

    syntactic_unit: SyntacticUnit
    due_date: date
    stability: float = 1.0  # FSRS stability (days before 90% retention)
    difficulty: float = 5.0  # FSRS difficulty (1–10)
    reps: int = 0
    lapses: int = 0
    state: SRSState = field(default=SRSState.NEW)
    last_review: date | None = None
