"""SRS feedback adapters.

ImplicitFeedbackAdapter: maps learner signals → FSRS ratings.
PostGenerationFeedback: identifies which collocations appear in a generated story.
"""

from __future__ import annotations

from app.models.srs_item import Rating

_SIGNAL_MAP: dict[str, Rating] = {
    "no_help": Rating.GOOD,
    "slowdown": Rating.HARD,
    "translation_request": Rating.AGAIN,
    "fast_forward": Rating.EASY,
}


class ImplicitFeedbackAdapter:
    """Maps implicit learner signals to FSRS ratings."""

    def signal_to_rating(self, signal: str) -> Rating:
        """Convert a learner signal string to an FSRS Rating.

        Signals:
            no_help: Learner did not request help → Good
            slowdown: Learner slowed playback → Hard
            translation_request: Learner requested translation → Again
            fast_forward: Learner fast-forwarded → Easy
        """
        if signal not in _SIGNAL_MAP:
            raise ValueError(f"Unknown signal {signal!r}. Valid: {list(_SIGNAL_MAP)}")
        return _SIGNAL_MAP[signal]


class PostGenerationFeedback:
    """Identifies which provided collocations were actually used in a story."""

    def find_used_collocations(self, provided: list[str], story_text: str) -> list[str]:
        """Return the subset of provided collocations that appear in story_text.

        Matching is case-insensitive. Only collocations that appear as
        substrings in the story are marked as used.
        """
        story_lower = story_text.lower()
        return [c for c in provided if c.lower() in story_lower]
