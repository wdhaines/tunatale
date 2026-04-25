"""SRS feedback utilities.

PostGenerationFeedback: identifies which collocations appear in a generated story.
rating_from_input: maps explicit rating strings or implicit signal strings to FSRS ratings.
"""

from __future__ import annotations

from app.models.srs_item import Rating

_SIGNAL_MAP: dict[str, Rating] = {
    "no_help": Rating.GOOD,
    "slowdown": Rating.HARD,
    "translation_request": Rating.AGAIN,
    "fast_forward": Rating.EASY,
}

_RATING_MAP: dict[str, Rating] = {
    "again": Rating.AGAIN,
    "hard": Rating.HARD,
    "good": Rating.GOOD,
    "easy": Rating.EASY,
}


def rating_from_input(rating: str | None = None, signal: str | None = None) -> Rating:
    """Convert explicit rating string or implicit signal string to a Rating enum.

    Exactly one of rating/signal must be provided; raises ValueError otherwise.
    rating accepts 'again'|'hard'|'good'|'easy' (case-insensitive).
    signal delegates to the existing _SIGNAL_MAP.
    """
    if (rating is None) == (signal is None):
        raise ValueError("Provide exactly one of rating or signal, not both (or neither).")
    if rating is not None:
        key = rating.lower()
        if key not in _RATING_MAP:
            raise ValueError(f"Unknown rating {rating!r}. Valid: {list(_RATING_MAP)}")
        return _RATING_MAP[key]
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
