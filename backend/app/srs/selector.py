"""Collocation selector using PedagogicalScoringConfig weights."""

from __future__ import annotations

from datetime import date

from app.models.srs_item import SRSItem
from app.models.strategy import ContentStrategy, PedagogicalScoringConfig


class CollocationSelector:
    """Scores and selects collocations for inclusion in a lesson."""

    def __init__(self, scoring_config: PedagogicalScoringConfig) -> None:
        self._config = scoring_config

    def score(self, item: SRSItem) -> float:
        """Score a single SRSItem for selection priority (higher = more urgent)."""
        today = date.today()
        cfg = self._config
        total = 0.0

        # SRS readiness (40%): penalize very stable items, reward low stability
        srs_score = 0.0
        if item.stability < 2.0:
            srs_score += cfg.low_stability_bonus
        days_overdue = max(0, (today - item.due_date).days)
        if days_overdue > 0:
            srs_score += cfg.review_overdue_bonus * min(days_overdue / 7, 1.0)
        total += cfg.srs_readiness_weight * srs_score

        # Language quality (30%): favor items without digits
        lq_score = 0.0
        text = item.syntactic_unit.text
        if any(c.isdigit() for c in text):
            lq_score += cfg.digit_penalty
        lq_score = max(0.0, lq_score + 0.5)
        total += cfg.language_quality_weight * lq_score

        # Pedagogical value (20%): frequency bonus
        pv_score = 0.0
        if item.syntactic_unit.frequency >= cfg.min_frequency_threshold:
            pv_score += (item.syntactic_unit.frequency - cfg.min_frequency_threshold) * cfg.frequency_bonus_multiplier
        pv_score = max(0.0, pv_score)
        total += cfg.pedagogical_value_weight * pv_score

        # Diversity (10%): word count variety
        wc = item.syntactic_unit.word_count
        diversity_score = 0.1 if 2 <= wc <= 5 else 0.0
        total += cfg.diversity_weight * diversity_score

        return total

    def select(
        self,
        new_items: list[SRSItem],
        review_items: list[SRSItem],
        strategy: ContentStrategy,
        max_new: int,
        min_review: int,
    ) -> tuple[list[SRSItem], list[SRSItem]]:
        """Select new and review collocations for a lesson.

        Args:
            new_items: Unseen collocations (state=NEW).
            review_items: Due collocations (state=REVIEW/RELEARNING).
            strategy: WIDER or DEEPER.
            max_new: Maximum new collocations to include.
            min_review: Minimum review collocations to aim for.

        Returns:
            (selected_new, selected_review)
        """
        # Score and sort review items (highest score first)
        scored_review = sorted(review_items, key=lambda i: -self.score(i))
        # Return at most min_review + 2 items as a hard cap
        max_review = min_review + 2
        selected_review = scored_review[:max_review]

        # Score and sort new items
        scored_new = sorted(new_items, key=lambda i: -self.score(i))
        selected_new = scored_new[:max_new]

        return selected_new, selected_review
