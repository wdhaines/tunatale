"""Content strategy, difficulty level, and pedagogical scoring configuration.

Ported from micro-demo-0.1/content_strategy.py — exact scoring weights preserved.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ContentStrategy(Enum):
    """Content generation strategy.

    WIDER: Generate new scenarios using familiar vocabulary (breadth).
    DEEPER: Enhance existing scenarios with more advanced L2 expressions (depth).
    """

    WIDER = "wider"
    DEEPER = "deeper"


class DifficultyLevel(Enum):
    """L2 language complexity level."""

    BASIC = "basic"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


@dataclass
class PedagogicalScoringConfig:
    """Scoring weights for collocation selection.

    The four primary weights must sum to 1.0.
    """

    # Primary weights (must sum to 1.0)
    srs_readiness_weight: float = 0.4
    language_quality_weight: float = 0.3
    pedagogical_value_weight: float = 0.2
    diversity_weight: float = 0.1

    # Language quality scoring
    english_word_penalty: float = -0.5
    digit_penalty: float = -0.3
    target_word_bonus: float = 0.1
    pure_target_bonus: float = 0.3

    # Pedagogical value
    min_frequency_threshold: int = 2
    frequency_bonus_multiplier: float = 0.1
    completeness_bonus: float = 0.2

    # Diversity
    similarity_penalty: float = -0.15
    category_diversity_bonus: float = 0.1

    # SRS readiness
    low_stability_bonus: float = 0.3
    review_overdue_bonus: float = 0.2

    def weights_sum_to_one(self) -> bool:
        total = (
            self.srs_readiness_weight
            + self.language_quality_weight
            + self.pedagogical_value_weight
            + self.diversity_weight
        )
        return abs(total - 1.0) < 0.01


@dataclass
class StrategyConfig:
    """Parameters controlling SRS behavior and content generation for a strategy."""

    strategy: ContentStrategy
    difficulty_level: DifficultyLevel
    max_new_collocations: int
    min_review_collocations: int
    review_interval_multiplier: float


DEFAULT_STRATEGY_CONFIGS: dict[ContentStrategy, StrategyConfig] = {
    ContentStrategy.WIDER: StrategyConfig(
        strategy=ContentStrategy.WIDER,
        difficulty_level=DifficultyLevel.BASIC,
        max_new_collocations=8,
        min_review_collocations=2,
        review_interval_multiplier=1.5,
    ),
    ContentStrategy.DEEPER: StrategyConfig(
        strategy=ContentStrategy.DEEPER,
        difficulty_level=DifficultyLevel.INTERMEDIATE,
        max_new_collocations=3,
        min_review_collocations=7,
        review_interval_multiplier=0.8,
    ),
}
