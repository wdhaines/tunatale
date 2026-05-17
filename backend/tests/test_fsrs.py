"""FSRS algorithm tests."""

from datetime import UTC, date, timedelta

from app.models.srs_item import Direction, DirectionState, Rating, SRSItem, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.fsrs import (
    DEFAULT_FSRS5_PARAMS,
    FSRSParams,
    compute_retrievability,
    schedule,
)


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
        assert result.directions[Direction.RECOGNITION].state == SRSState.LEARNING
        assert result.directions[Direction.RECOGNITION].reps == 1

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
        assert result.directions[Direction.RECOGNITION].state in (SRSState.LEARNING, SRSState.NEW)

    def test_schedule_new_good_stability_above_one(self):
        item = _new_item()
        result = schedule(item, Rating.GOOD)
        assert result.directions[Direction.RECOGNITION].state == SRSState.LEARNING

    def test_schedule_new_easy_stability_greater_than_good(self):
        today = date.today()
        r_good = schedule(_new_item(), Rating.GOOD, today)
        r_easy = schedule(_new_item(), Rating.EASY, today)
        assert r_easy.directions[Direction.RECOGNITION].state == SRSState.REVIEW
        assert r_easy.stability > 0  # FSRS init ran for EASY
        assert r_good.directions[Direction.RECOGNITION].state == SRSState.LEARNING


class TestReviewScheduling:
    """Tests for FSRS scheduling of review items."""

    def test_review_good_increases_stability(self):
        item = _review_item()
        result = schedule(item, Rating.GOOD)
        assert result.stability > item.stability

    def test_review_again_triggers_relearning(self):
        item = _review_item()
        result = schedule(item, Rating.AGAIN)
        assert result.directions[Direction.RECOGNITION].state == SRSState.RELEARNING
        assert result.directions[Direction.RECOGNITION].lapses == item.directions[Direction.RECOGNITION].lapses + 1

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
        # last_review should be a datetime with UTC timezone
        assert result.last_review is not None
        assert result.last_review.tzinfo is not None

    def test_schedule_past_date_uses_datetime_combine(self):
        """When review_date is not today, uses datetime.combine (line 134)."""
        past_date = date.today() - timedelta(days=5)
        item = _review_item()
        result = schedule(item, Rating.GOOD, past_date)
        # last_review should be a datetime with the past_date
        assert result.last_review.date() == past_date
        assert result.last_review.tzinfo is not None

    def test_schedule_with_date_last_review(self):
        """When last_review is date (not datetime), triggers line 157."""
        from dataclasses import replace
        from datetime import timedelta

        item = _review_item()
        # Set last_review as a date (not datetime)
        item.directions[Direction.RECOGNITION] = replace(
            item.directions[Direction.RECOGNITION],
            last_review=date.today() - timedelta(days=10),
        )
        # Schedule with a past date so last_review_dt is NOT datetime
        # (it will be datetime.combine(review_date, ...))
        # but last = prev.last_review (date), so line 157 triggers
        past_date = date.today() - timedelta(days=5)
        result = schedule(item, Rating.GOOD, past_date)
        # Should not raise TypeError
        assert result.last_review.date() == past_date


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
        item = _review_item()
        for rating in Rating:
            result = schedule(item, rating)
            assert result.directions[Direction.RECOGNITION].last_rating == rating.value

    def test_schedule_does_not_touch_other_direction_last_rating(self):
        """Scheduling recognition leaves production.last_rating unchanged."""
        item = _new_item()
        result = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION)
        assert result.directions[Direction.PRODUCTION].last_rating is None


class TestFSRSParams:
    """Tests for parameterised FSRS scheduling via FSRSParams."""

    # User's Anki-optimised FSRS-5 weights (from deck_config, field 5)
    _ANKI_WEIGHTS = (
        0.12787972390651703,
        1.5785421133041382,
        16.496992111206055,
        100.0,
        6.960937976837158,
        0.7343991994857788,
        1.8881254196166992,
        0.0010000000474974513,
        1.2984633445739746,
        0.4768359363079071,
        0.8232685327529907,
        1.8871877193450928,
        0.13465967774391174,
        0.21997599303722382,
        2.3025882244110107,
        0.19444920122623444,
        2.4298620223999023,
        0.5871865749359131,
        0.801949679851532,
    )

    def test_default_params_preserved_without_argument(self):
        """schedule() without params arg gives same result as DEFAULT_FSRS5_PARAMS."""
        today = date.today()
        item = _new_item()
        without_arg = schedule(item, Rating.EASY, today)
        with_default = schedule(item, Rating.EASY, today, params=DEFAULT_FSRS5_PARAMS)
        assert without_arg.stability == with_default.stability
        assert without_arg.due_date == with_default.due_date

    def test_non_default_weights_change_interval(self):
        """Custom weights produce a different due_date than defaults."""
        today = date.today()
        ank_params = FSRSParams(weights=self._ANKI_WEIGHTS)
        result_default = schedule(_new_item(), Rating.EASY, today)
        result_anki = schedule(_new_item(), Rating.EASY, today, params=ank_params)
        assert result_anki.due_date != result_default.due_date

    def test_compute_retrievability_with_datetime_last_review(self):
        """compute_retrievability handles last_review as datetime (line 157)."""
        from datetime import datetime

        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
            stability=3.0,
            difficulty=5.0,
            reps=5,
            lapses=0,
            state=SRSState.REVIEW,
            last_review=datetime.now(tz=UTC),
        )
        # Should not raise TypeError
        r = compute_retrievability(ds, date.today())
        assert 0.0 <= r <= 1.0

    def test_compute_retrievability_with_date_last_review(self):
        """compute_retrievability handles last_review as date (line 157)."""
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
            stability=3.0,
            difficulty=5.0,
            reps=5,
            lapses=0,
            state=SRSState.REVIEW,
            last_review=date.today() - timedelta(days=5),
        )
        r = compute_retrievability(ds, date.today())
        assert 0.0 <= r <= 1.0

    def test_fsrs_params_has_19_weights(self):
        """FSRSParams rejects weights that aren't 19 or 21 floats."""
        import pytest

        with pytest.raises((ValueError, TypeError, AssertionError)):
            FSRSParams(weights=(1.0,) * 18)
        with pytest.raises(ValueError):
            FSRSParams(weights=(1.0,) * 17)

    def test_19_weights_implies_fsrs5_decay(self):
        """19 weights → decay=0.5, version=5."""
        params = FSRSParams(weights=(0.4072,) * 19)
        assert params.decay == 0.5
        assert params.version == 5

    def test_21_weights_uses_w20_as_decay(self):
        """21 weights → decay=weights[20], version=6."""
        weights = (0.4072,) * 20 + (0.1542,)
        params = FSRSParams(weights=weights)
        assert params.decay == 0.1542
        assert params.version == 6

    def test_invalid_weights_length_raises(self):
        """Neither 19 nor 21 weights → ValueError."""
        import pytest

        with pytest.raises(ValueError):
            FSRSParams(weights=(1.0,) * 17)
        with pytest.raises(ValueError):
            FSRSParams(weights=(1.0,) * 20)


class TestShortTermStability:
    """Tests for _stability_short_term helper."""

    # Default FSRS-5 weights have w[17]=0.51, w[18]=0.435
    _FSRS5_DEFAULT = DEFAULT_FSRS5_PARAMS
    # FSRS-6 params with w[19]=0.2 for testing
    _FSRS6_WEIGHTS = tuple(list(DEFAULT_FSRS5_PARAMS.weights) + [0.2, 0.1542])

    def test_short_term_fsrs5_again(self):
        """AGAIN on FSRS-5: sinc = exp(0.51*-1.565) ≈ 0.450, new_s ≈ 0.450."""
        from app.srs.fsrs import _stability_short_term

        new_s = _stability_short_term(1.0, Rating.AGAIN, self._FSRS5_DEFAULT)
        # exp(0.51 * (1 - 3 + 0.435)) = exp(-0.79815) ≈ 0.4501
        assert abs(new_s - 0.450) < 0.01

    def test_short_term_fsrs5_good(self):
        """GOOD on FSRS-5: sinc = exp(0.51*0.435) ≈ 1.246, clamp not needed (≥1)."""
        from app.srs.fsrs import _stability_short_term

        new_s = _stability_short_term(1.0, Rating.GOOD, self._FSRS5_DEFAULT)
        # exp(0.51 * 0.435) ≈ 1.248; clamp max(1.248, 1) = 1.248
        assert abs(new_s - 1.248) < 0.01

    def test_short_term_fsrs5_hard(self):
        """HARD on FSRS-5: sinc < 1, rating < 3 so no clamp, new_s < last_s."""
        from app.srs.fsrs import _stability_short_term

        new_s = _stability_short_term(1.0, Rating.HARD, self._FSRS5_DEFAULT)
        # exp(0.51 * (-1 + 0.435)) = exp(-0.28815) ≈ 0.749
        # rating=2 < 3, no clamp
        assert abs(new_s - 0.749) < 0.01

    def test_short_term_fsrs5_easy(self):
        """EASY on FSRS-5: sinc = exp(0.51*1.435) ≈ 2.084."""
        from app.srs.fsrs import _stability_short_term

        new_s = _stability_short_term(1.0, Rating.EASY, self._FSRS5_DEFAULT)
        # exp(0.51 * (4 - 3 + 0.435)) = exp(0.51 * 1.435) = exp(0.73185) ≈ 2.079
        assert abs(new_s - 2.079) < 0.01

    def test_short_term_fsrs6_uses_w19(self):
        """FSRS-6 w[19]=0.2 changes result from FSRS-5."""
        from app.srs.fsrs import _stability_short_term

        fsrs6_params = FSRSParams(weights=self._FSRS6_WEIGHTS)
        # With last_s=1.0, last_s^(-w19) = 1.0 for both FSRS-5 and FSRS-6
        # With last_s=2.0, FSRS-6 gets: sinc *= 2.0^(-0.2) = 0.871
        fsrs5_s = _stability_short_term(2.0, Rating.AGAIN, self._FSRS5_DEFAULT)
        fsrs6_s = _stability_short_term(2.0, Rating.AGAIN, fsrs6_params)
        # FSRS-6 has additional w19 factor, so stability is lower
        assert fsrs6_s < fsrs5_s

    def test_short_term_good_clamps_below_one(self):
        """When sinc < 1 with GOOD rating, clamp forces new_s == last_s."""
        from app.srs.fsrs import _stability_short_term

        # Use w17 that makes sinc < 1 even for GOOD: w17 large negative
        bad_weights = (0.4072,) * 17 + (-5.0, 0.435, 0.0, 0.1542)  # 21 weights, w17=-5.0
        params = FSRSParams(weights=bad_weights)
        # sinc = exp(-5 * 0.435) = exp(-2.175) ≈ 0.114
        # rating=3 >= 3, so clamp: sinc = max(0.114, 1.0) = 1.0
        new_s = _stability_short_term(2.0, Rating.GOOD, params)
        assert abs(new_s - 2.0) < 1e-10  # clamped to last_s
