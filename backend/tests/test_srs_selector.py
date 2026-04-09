"""Collocation selector tests."""

from datetime import date, timedelta

import pytest

from app.models.srs_item import SRSItem, SRSState
from app.models.strategy import DEFAULT_STRATEGY_CONFIGS, ContentStrategy, PedagogicalScoringConfig
from app.models.syntactic_unit import SyntacticUnit
from app.srs.selector import CollocationSelector


def _make_item(text: str, state: SRSState = SRSState.NEW, days_overdue: int = 0, stability: float = 1.0) -> SRSItem:
    unit = SyntacticUnit(text=text, translation=text + "_tr", word_count=2, difficulty=1, source="corpus")
    return SRSItem(
        syntactic_unit=unit,
        due_date=date.today() - timedelta(days=days_overdue),
        stability=stability,
        state=state,
    )


@pytest.fixture
def selector():
    return CollocationSelector(PedagogicalScoringConfig())


class TestCollocationSelector:
    """Tests for CollocationSelector strategy limits, review prioritization, and scoring."""

    def test_wider_selects_up_to_8_new(self, selector):
        new_items = [_make_item(f"word{i}", SRSState.NEW) for i in range(15)]
        review_items = []
        config = DEFAULT_STRATEGY_CONFIGS[ContentStrategy.WIDER]
        new, review = selector.select(
            new_items, review_items, ContentStrategy.WIDER, config.max_new_collocations, config.min_review_collocations
        )
        assert len(new) <= 8

    def test_deeper_selects_up_to_3_new(self, selector):
        new_items = [_make_item(f"word{i}", SRSState.NEW) for i in range(15)]
        review_items = []
        config = DEFAULT_STRATEGY_CONFIGS[ContentStrategy.DEEPER]
        new, review = selector.select(
            new_items, review_items, ContentStrategy.DEEPER, config.max_new_collocations, config.min_review_collocations
        )
        assert len(new) <= 3

    def test_overdue_items_included_in_review(self, selector):
        review_items = [
            _make_item("fresh", SRSState.REVIEW, days_overdue=0),
            _make_item("overdue1", SRSState.REVIEW, days_overdue=5),
            _make_item("overdue2", SRSState.REVIEW, days_overdue=10),
        ]
        config = DEFAULT_STRATEGY_CONFIGS[ContentStrategy.WIDER]
        _, review = selector.select(
            [], review_items, ContentStrategy.WIDER, config.max_new_collocations, config.min_review_collocations
        )
        texts = [i.syntactic_unit.text for i in review]
        assert "overdue2" in texts or "overdue1" in texts

    def test_select_respects_max_review(self, selector):
        review_items = [_make_item(f"rev{i}", SRSState.REVIEW, days_overdue=i) for i in range(20)]
        new_result, review_result = selector.select([], review_items, ContentStrategy.WIDER, 8, 5)
        assert len(review_result) <= 5 + 2  # hard cap at min_review + 2

    def test_score_item_returns_float(self, selector):
        item = _make_item("test", SRSState.REVIEW, days_overdue=3)
        score = selector.score(item)
        assert isinstance(score, float)

    def test_low_stability_item_scores_higher_than_high_stability(self, selector):
        low = _make_item("low", SRSState.REVIEW, days_overdue=1, stability=0.5)
        high = _make_item("high", SRSState.REVIEW, days_overdue=1, stability=50.0)
        assert selector.score(low) >= selector.score(high)

    def test_score_includes_frequency_bonus_for_frequent_item(self, selector):
        """Item with frequency >= min_frequency_threshold gets a positive pv_score contribution."""
        unit = SyntacticUnit(
            text="frequent phrase", translation="x", word_count=2, difficulty=1, source="corpus", frequency=5
        )
        item = SRSItem(syntactic_unit=unit, due_date=date.today(), stability=1.0, state=SRSState.NEW)
        score_frequent = selector.score(item)

        unit_rare = SyntacticUnit(
            text="rare phrase", translation="x", word_count=2, difficulty=1, source="corpus", frequency=0
        )
        item_rare = SRSItem(syntactic_unit=unit_rare, due_date=date.today(), stability=1.0, state=SRSState.NEW)
        score_rare = selector.score(item_rare)

        assert score_frequent > score_rare
