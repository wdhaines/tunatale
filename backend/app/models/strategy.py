"""Content generation strategy enum."""

from __future__ import annotations

from enum import Enum


class ContentStrategy(Enum):
    """Content generation strategy.

    WIDER: Generate new scenarios using familiar vocabulary (breadth).
    DEEPER: Enhance existing scenarios with more advanced L2 expressions (depth).
    REVIEW: No scenario at all — the content IS the learner's decaying
        vocabulary. A strategy rather than a ReviewPressure setting because
        WIDER and DEEPER both take Theme/Focus/Story Guidance and REVIEW has
        none to trade against.
    """

    WIDER = "wider"
    DEEPER = "deeper"
    REVIEW = "review"


class ReviewPressure(Enum):
    """How hard a story prompt should push to use the learner's review words.

    The range is the user's (2026-09-02, bd tunatale-ow7t): from "today's state
    of thematic unity" up to "aggressively incorporating the words at the cost
    of theme (but not coherence)".

    NATURAL is the default and reproduces the pre-feature behaviour: the words
    are offered, and declining one is a correct answer. The user's phrasing was
    "how/IF to incorporate" — a low setting that cannot decline is not the low
    end of anything.

    ⚠️ Coherence is NOT the top of this scale, it is the floor under all of it.
    Theme is the thing that gives way; a scene that stops making sense is a
    failure at every setting, including INSISTENT.
    """

    NATURAL = "natural"
    BALANCED = "balanced"
    INSISTENT = "insistent"
