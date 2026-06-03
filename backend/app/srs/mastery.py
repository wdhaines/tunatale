"""Per-lemma mastery = aggregated FSRS stability over the learn-set (Phase 5).

Mastery uses *stability*, not retrievability. The scheduler actively regulates
retrievability toward desired_retention (~0.9), so a review card's R lives in a
narrow band and can't distinguish a freshly graduated card from a long-mastered
one — every reviewed word renders the same green. Stability instead grows
monotonically as a word is learned (the user's deck spans ~3–116 days), so it is
what the transcript color ramp should track.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from app.models.srs_item import DirectionState, SRSState

# A REVIEW card's mastery is its stability mapped onto [0,1] by a log curve: a
# card stable for >= this many days reads as fully mastered (green). Log scale
# because the early stability gains (1→10 days) are the meaningful learning
# signal while the 100→120 day difference is not; the ceiling is chosen so the
# observed stability range spreads across the full red→green ramp.
MASTERY_STABILITY_CEILING_DAYS = 120.0

# In-steps (learning/relearning) cards sit at a fixed low floor: they are being
# acquired, not yet on the stability ramp.
_LEARNING_FLOOR = 0.15


def component_mastery(ds: DirectionState) -> float:
    """Mastery of one component (a direction/card) ∈ [0,1].

    NEW/never-reviewed → 0.0 (unlearned). LEARNING/RELEARNING → 0.15 fixed floor
    (in-steps, not graduated). KNOWN → 1.0. REVIEW → log-normalized stability,
    which is time-independent: a word keeps the same color between reviews.
    """
    if ds.state == SRSState.NEW or ds.last_review is None:
        return 0.0
    if ds.state in (SRSState.LEARNING, SRSState.RELEARNING):
        return _LEARNING_FLOOR
    if ds.state == SRSState.KNOWN:
        return 1.0
    mastery = math.log10(max(ds.stability, 1.0)) / math.log10(MASTERY_STABILITY_CEILING_DAYS)
    return max(0.0, min(1.0, mastery))


def compute_mastery_progress(directions: Iterable[DirectionState]) -> float | None:
    """Mean component_mastery over the learn-set. SUSPENDED components excluded.
    None if the set is empty (→ caller renders as not-on-the-ramp).
    """
    ms = [component_mastery(d) for d in directions if d.state != SRSState.SUSPENDED]
    return sum(ms) / len(ms) if ms else None
