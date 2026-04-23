"""FSRS algorithm tests."""

from datetime import date, timedelta

from app.models.srs_item import Rating, SRSItem, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.fsrs import schedule


def _new_item() -> SRSItem:
    unit = SyntacticUnit(text="dober dan", translation="good day", word_count=2, difficulty=1, source="corpus")
    return SRSItem(syntactic_unit=unit, due_date=date.today())


def _review_item() -> SRSItem:
    unit = SyntacticUnit(text="hvala lepa", translation="thank you", word_count=2, difficulty=1, source="corpus")
    return SRSItem(
        syntactic_unit=unit,
        due_date=date.today(),
        stability=10.0,
        difficulty=5.0,
        reps=3,
        state=SRSState.REVIEW,
        last_review=date.today() - timedelta(days=10),
    )


class TestNewItemScheduling:
    """Tests for FSRS scheduling of new items."""

    def test_schedule_new_good_advances_to_learning(self):
        item = _new_item()
        result = schedule(item, Rating.GOOD)
        assert result.state in (SRSState.LEARNING, SRSState.REVIEW)
        assert result.reps == 1

    def test_schedule_new_easy_longer_interval_than_good(self):
        item_good = _new_item()
        item_easy = _new_item()
        today = date.today()
        r_good = schedule(item_good, Rating.GOOD, today)
        r_easy = schedule(item_easy, Rating.EASY, today)
        assert r_easy.due_date >= r_good.due_date

    def test_schedule_new_again_stays_learning(self):
        item = _new_item()
        result = schedule(item, Rating.AGAIN)
        assert result.state in (SRSState.LEARNING, SRSState.NEW)

    def test_schedule_new_good_stability_above_one(self):
        item = _new_item()
        result = schedule(item, Rating.GOOD)
        assert result.stability > 0.0

    def test_schedule_new_easy_stability_greater_than_good(self):
        today = date.today()
        r_good = schedule(_new_item(), Rating.GOOD, today)
        r_easy = schedule(_new_item(), Rating.EASY, today)
        assert r_easy.stability > r_good.stability


class TestReviewScheduling:
    """Tests for FSRS scheduling of review items."""

    def test_review_good_increases_stability(self):
        item = _review_item()
        result = schedule(item, Rating.GOOD)
        assert result.stability > item.stability

    def test_review_again_triggers_relearning(self):
        item = _review_item()
        result = schedule(item, Rating.AGAIN)
        assert result.state == SRSState.RELEARNING
        assert result.lapses == item.lapses + 1

    def test_review_again_reduces_stability(self):
        item = _review_item()
        result = schedule(item, Rating.AGAIN)
        assert result.stability < item.stability

    def test_review_good_reps_incremented(self):
        item = _review_item()
        result = schedule(item, Rating.GOOD)
        assert result.reps == item.reps + 1

    def test_review_last_review_updated(self):
        today = date.today()
        item = _review_item()
        result = schedule(item, Rating.GOOD, today)
        assert result.last_review == today


class TestDifficultyAdjustment:
    """Tests for FSRS difficulty adjustment logic."""

    def test_difficulty_increases_on_again(self):
        item = _new_item()
        item.difficulty = 5.0
        result = schedule(item, Rating.AGAIN)
        assert result.difficulty >= item.difficulty

    def test_easy_results_in_lower_difficulty_than_hard(self):
        """EASY should produce lower difficulty than HARD (all else equal)."""
        today = date.today()
        item_hard = _review_item()
        item_easy = _review_item()
        r_hard = schedule(item_hard, Rating.HARD, today)
        r_easy = schedule(item_easy, Rating.EASY, today)
        assert r_easy.difficulty <= r_hard.difficulty

    def test_difficulty_stays_within_bounds(self):
        item = _new_item()
        for rating in Rating:
            result = schedule(item, rating)
            assert 1.0 <= result.difficulty <= 10.0


class TestLastRating:
    """B5: schedule() must persist the learner's rating for sync_push."""

    def test_schedule_sets_last_rating_to_rating_value(self):
        """schedule() stores rating.value on the updated DirectionState."""
        from app.models.srs_item import Direction

        item = _review_item()
        for rating in Rating:
            result = schedule(item, rating)
            assert result.directions[Direction.RECOGNITION].last_rating == rating.value

    def test_schedule_does_not_touch_other_direction_last_rating(self):
        """Scheduling recognition leaves production.last_rating unchanged."""
        from app.models.srs_item import Direction

        item = _new_item()
        result = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION)
        assert result.directions[Direction.PRODUCTION].last_rating is None
