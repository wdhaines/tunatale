"""Content generation strategy enum."""

from __future__ import annotations

from enum import Enum


class ContentStrategy(Enum):
    """Content generation strategy.

    WIDER: Generate new scenarios using familiar vocabulary (breadth).
    DEEPER: Enhance existing scenarios with more advanced L2 expressions (depth).
    """

    WIDER = "wider"
    DEEPER = "deeper"
