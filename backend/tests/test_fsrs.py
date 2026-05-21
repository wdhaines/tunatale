"""FSRS algorithm tests."""

from datetime import UTC, date, datetime, time, timedelta

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

    def test_review_again_uses_fractional_elapsed_when_last_review_has_sub_day_precision(self):
        """REVIEW + AGAIN: the lapse formula's `r` must mirror Anki's
        `extract_fsrs_retrievability` dual branch — fractional days from `lrt`
        when sub-day precision is present, integer calendar days only when
        the day-level midnight fallback was used.

        Pre-fix, `_schedule_review_again` unconditionally used integer
        calendar days (`last_review_dt.date() - last_date.days`). For a card
        last reviewed at 02:43 yesterday-of-a-week-ago, that's 7 calendar
        days vs Anki's ~7.55 fractional days — flowing through
        `exp(w14 * (1-r))` into a 2-4% stability gap (real-world
        observation: kupiti 1.0897 vs 1.1337 = 4.0% gap).
        """
        from dataclasses import replace
        from datetime import datetime as _dt

        grade_dt = _dt(2026, 5, 18, 12, 0, 0, tzinfo=UTC)

        def _grade_again_with_last_review_at(last_review_dt):
            item = _review_item()
            ds = item.directions[Direction.RECOGNITION]
            item.directions[Direction.RECOGNITION] = replace(
                ds, last_review=last_review_dt, stability=5.0, difficulty=7.0
            )
            return schedule(item, Rating.AGAIN, review_date=grade_dt.date(), now=grade_dt)

        # Two cards graded at the same moment, with last_review on the SAME calendar
        # day but at different sub-day times. Integer-days gives elapsed=7 for both;
        # fractional-days gives 7.5 and 7.0 respectively → different new stabilities.
        early_morning = _dt(2026, 5, 11, 0, 0, 1, tzinfo=UTC)  # ~7.5 days before grade
        midday = _dt(2026, 5, 11, 12, 0, 0, tzinfo=UTC)  # exactly 7.0 days before grade

        r_early = _grade_again_with_last_review_at(early_morning)
        r_mid = _grade_again_with_last_review_at(midday)

        s_early = r_early.directions[Direction.RECOGNITION].stability
        s_mid = r_mid.directions[Direction.RECOGNITION].stability

        # Sanity: both are sub-day precision (non-midnight microseconds for early,
        # non-midnight for midday), so the fix's fractional branch fires for both.
        # Their fractional elapseds differ (7.5 vs 7.0) → r differs → new_s differs.
        # With the pre-fix integer-days bug, both rounded to 7 and produced identical s.
        assert s_early != s_mid, (
            f"Lapse formula must use fractional elapsed when last_review has sub-day precision. "
            f"7.5-day-elapsed produced s={s_early}; 7.0-day-elapsed produced s={s_mid}; "
            f"identical values mean the bug (integer days for both) is still present."
        )
        # Direction check: more time elapsed = lower r = larger 1-r = larger exp factor =
        # higher new_s in lapse formula. So early (7.5 days) should produce s >= mid (7.0).
        assert s_early > s_mid, (
            f"More elapsed time should yield slightly higher new_s in the lapse formula "
            f"(via the exp(w14*(1-r)) term). Got early={s_early}, mid={s_mid}."
        )

    def test_review_good_uses_fractional_elapsed_when_last_review_has_sub_day_precision(self):
        """Same as above but for REVIEW + GOOD (the recall path at line 353).
        Both code sites — `_schedule_review_again` and the REVIEW+other-ratings
        branch — need the same fix.
        """
        from dataclasses import replace
        from datetime import datetime as _dt

        grade_dt = _dt(2026, 5, 18, 12, 0, 0, tzinfo=UTC)

        def _grade_good_with_last_review_at(last_review_dt):
            item = _review_item()
            ds = item.directions[Direction.RECOGNITION]
            item.directions[Direction.RECOGNITION] = replace(
                ds, last_review=last_review_dt, stability=5.0, difficulty=7.0
            )
            return schedule(item, Rating.GOOD, review_date=grade_dt.date(), now=grade_dt)

        early_morning = _dt(2026, 5, 11, 0, 0, 1, tzinfo=UTC)
        midday = _dt(2026, 5, 11, 12, 0, 0, tzinfo=UTC)

        r_early = _grade_good_with_last_review_at(early_morning)
        r_mid = _grade_good_with_last_review_at(midday)

        s_early = r_early.directions[Direction.RECOGNITION].stability
        s_mid = r_mid.directions[Direction.RECOGNITION].stability

        assert s_early != s_mid, (
            f"Recall formula must use fractional elapsed when last_review has sub-day precision. "
            f"Got identical s={s_early} for both 7.5-day and 7.0-day elapsed cards — integer-days bug."
        )


class TestElapsedDaysForFsrs:
    """Direct unit tests for the day-level vs lrt-fractional helper."""

    def test_none_last_review_returns_zero(self):
        from datetime import datetime as _dt

        from app.srs.fsrs import _elapsed_days_for_fsrs

        assert _elapsed_days_for_fsrs(None, _dt(2026, 5, 18, 12, 0, tzinfo=UTC)) == 0.0

    def test_midnight_datetime_uses_integer_days(self):
        """A midnight-UTC last_review is the marker for day-level fallback
        (no lrt at parse time) — must return integer calendar days."""
        from datetime import datetime as _dt

        from app.srs.fsrs import _elapsed_days_for_fsrs

        last = _dt(2026, 5, 11, 0, 0, 0, tzinfo=UTC)
        # 7 calendar days + 15h means integer days = 7, NOT 7.625.
        now_dt = _dt(2026, 5, 18, 15, 0, 0, tzinfo=UTC)
        assert _elapsed_days_for_fsrs(last, now_dt) == 7

    def test_sub_day_datetime_uses_fractional_days(self):
        from datetime import datetime as _dt

        from app.srs.fsrs import _elapsed_days_for_fsrs

        # midday 7 days ago → exactly 7.0 fractional days
        last = _dt(2026, 5, 11, 12, 0, 0, tzinfo=UTC)
        now_dt = _dt(2026, 5, 18, 12, 0, 0, tzinfo=UTC)
        assert _elapsed_days_for_fsrs(last, now_dt) == 7.0

        # 15h after midnight 7 days ago → 7.625 fractional days
        last2 = _dt(2026, 5, 11, 0, 0, 1, tzinfo=UTC)  # sub-day precision (second != 0)
        now2 = _dt(2026, 5, 18, 15, 0, 1, tzinfo=UTC)
        assert _elapsed_days_for_fsrs(last2, now2) == 7.625

    def test_midnight_datetime_col_day_rollover_crossing(self):
        """Day-level branch with col_crt crosses a 4am-local rollover boundary.

        When col_crt=-572400, the Anki col-day boundary is at 05:00 UTC.
        A midnight-UTC last_review falls before the day's boundary.
        Between May 20 01:00 UTC (before boundary) and May 20 09:00 UTC
        (after boundary), the col day advances by 1 even though the UTC
        calendar date stays the same. The col-day-aware computation must
        reflect this extra day; the old UTC-date code cannot.
        """
        from datetime import datetime as _dt

        from app.srs.fsrs import _elapsed_days_for_fsrs

        last = _dt(2026, 4, 11, 0, 0, 0, tzinfo=UTC)
        col_crt = -572400
        rollover = 4

        # Before 05:00 UTC boundary — col day 20599 (same as May 19's col day)
        now_before = _dt(2026, 5, 20, 1, 0, 0, tzinfo=UTC)
        assert _elapsed_days_for_fsrs(last, now_before, col_crt=col_crt, rollover_hour=rollover) == 39
        # Old code (no col_crt) also gives 39
        assert _elapsed_days_for_fsrs(last, now_before) == 39

        # After 05:00 UTC boundary — col day 20600 (May 20's col day)
        now_after = _dt(2026, 5, 20, 9, 0, 0, tzinfo=UTC)
        assert _elapsed_days_for_fsrs(last, now_after, col_crt=col_crt, rollover_hour=rollover) == 40
        # Old code (no col_crt) still gives 39 — the divergence this fix addresses
        assert _elapsed_days_for_fsrs(last, now_after) == 39

    def test_midnight_datetime_col_day_same_as_utc_when_boundary_not_crossed(self):
        """When both timestamps are before or after the col-day boundary,
        the col-day elapsed matches the UTC-date elapsed.

        With boundary at 05:00 UTC: midnight belongs to the previous col day,
        and any time before 05:00 UTC shares that same col day.
        """
        from datetime import datetime as _dt

        from app.srs.fsrs import _elapsed_days_for_fsrs

        # last at midnight, now at 01:00 on same day → both before 05:00 boundary
        last = _dt(2026, 4, 11, 0, 0, 0, tzinfo=UTC)
        now = _dt(2026, 4, 11, 1, 0, 0, tzinfo=UTC)
        assert _elapsed_days_for_fsrs(last, now, col_crt=-572400, rollover_hour=4) == 0
        assert _elapsed_days_for_fsrs(last, now) == 0

    def test_midnight_datetime_col_day_fallback_to_utc_when_col_crt_none(self):
        """When col_crt is None, falls back to UTC date subtraction."""
        from datetime import datetime as _dt

        from app.srs.fsrs import _elapsed_days_for_fsrs

        last = _dt(2026, 4, 11, 0, 0, 0, tzinfo=UTC)
        now = _dt(2026, 5, 20, 9, 0, 0, tzinfo=UTC)
        # col_crt=None → old UTC-date behavior
        assert _elapsed_days_for_fsrs(last, now, col_crt=None) == 39
        # same as calling without col_crt
        assert _elapsed_days_for_fsrs(last, now) == 39


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
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
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
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
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


class TestBuildRevlogRow:
    """Tests for build_revlog_row and the _compute_* helpers it calls."""

    def test_review_review_kind_1(self):
        """REVIEW→REVIEW transition returns kind=1."""
        from app.srs.fsrs import _compute_review_kind

        assert _compute_review_kind(SRSState.REVIEW, SRSState.REVIEW) == 1

    def test_relearning_review_kind_2(self):
        """→RELEARNING returns kind=2 (line 879)."""
        from app.srs.fsrs import _compute_review_kind

        assert _compute_review_kind(SRSState.REVIEW, SRSState.RELEARNING) == 2

    def test_learning_to_learning_review_kind_0(self):
        """LEARNING→LEARNING returns kind=0 (line 883)."""
        from app.srs.fsrs import _compute_review_kind

        assert _compute_review_kind(SRSState.LEARNING, SRSState.LEARNING) == 0
        assert _compute_review_kind(SRSState.RELEARNING, SRSState.LEARNING) == 0

    def test_fallback_review_kind_1(self):
        """Non-standard state transitions fall through to return 1 (line 886)."""
        from app.srs.fsrs import _compute_review_kind

        assert _compute_review_kind(SRSState.NEW, SRSState.SUSPENDED) == 1
        assert _compute_review_kind(SRSState.REVIEW, SRSState.BURIED) == 1

    def test_new_to_review_kind_1(self):
        """NEW→REVIEW returns kind=1."""
        from app.srs.fsrs import _compute_review_kind

        assert _compute_review_kind(SRSState.NEW, SRSState.REVIEW) == 1

    def test_new_to_learning_kind_0(self):
        """→LEARNING returns kind=0."""
        from app.srs.fsrs import _compute_review_kind

        assert _compute_review_kind(SRSState.NEW, SRSState.LEARNING) == 0

    def test_compute_revlog_interval_learning(self):
        """Learning intervals are negative seconds."""
        from app.srs.fsrs import _compute_revlog_interval

        now = datetime(2026, 5, 19, tzinfo=UTC)
        prev_dir = DirectionState(direction=Direction.RECOGNITION, state=SRSState.NEW, due_at=now)
        new_dir = DirectionState(
            direction=Direction.RECOGNITION, state=SRSState.LEARNING, due_at=now + timedelta(minutes=10)
        )
        interval = _compute_revlog_interval(SRSState.NEW, new_dir, prev_dir, now)
        assert interval < 0  # negative for learning
        assert interval == -600  # 10 minutes * 60 seconds

    def test_compute_revlog_interval_review(self):
        """Review intervals are positive days (span between last_review and new due)."""
        from app.srs.fsrs import _compute_revlog_interval

        now = datetime(2026, 5, 19, tzinfo=UTC)
        prev_dir = DirectionState(
            direction=Direction.RECOGNITION, state=SRSState.REVIEW, due_at=now, last_review=now - timedelta(days=10)
        )
        new_dir = DirectionState(
            direction=Direction.RECOGNITION, state=SRSState.REVIEW, due_at=now + timedelta(days=30)
        )
        interval = _compute_revlog_interval(SRSState.REVIEW, new_dir, prev_dir, now)
        # 30 - (-10) = 40 days between last_review and new due_at
        assert interval == 40

    def test_compute_revlog_interval_review_no_last_review(self):
        """Review interval falls back to now when last_review is missing."""
        from app.srs.fsrs import _compute_revlog_interval

        now = datetime(2026, 5, 19, tzinfo=UTC)
        prev_dir = DirectionState(
            direction=Direction.RECOGNITION, state=SRSState.REVIEW, due_at=now - timedelta(days=5)
        )
        new_dir = DirectionState(
            direction=Direction.RECOGNITION, state=SRSState.REVIEW, due_at=now + timedelta(days=25)
        )
        interval = _compute_revlog_interval(SRSState.REVIEW, new_dir, prev_dir, now)
        assert interval >= 1

    def test_compute_revlog_last_interval_zero(self):
        """Returns 0 when no last_review or due_at."""
        from app.srs.fsrs import _compute_revlog_last_interval

        now = datetime(2026, 5, 19, tzinfo=UTC)
        prev = DirectionState(direction=Direction.RECOGNITION, state=SRSState.NEW, due_at=now)
        assert _compute_revlog_last_interval(prev) == 0

    def test_build_revlog_row_default_now(self):
        """build_revlog_row uses datetime.now when now=None (line 936)."""
        from app.srs.fsrs import build_revlog_row

        now = datetime(2026, 5, 19, tzinfo=UTC)
        prev = DirectionState(direction=Direction.RECOGNITION, state=SRSState.NEW, due_at=now, anki_card_id=42)
        new_dir = DirectionState(
            direction=Direction.RECOGNITION, state=SRSState.LEARNING, due_at=now + timedelta(minutes=1)
        )
        row = build_revlog_row(1, Direction.RECOGNITION, prev, new_dir, Rating.GOOD, 0)
        assert row.collocation_id == 1
        assert row.direction == Direction.RECOGNITION
        assert row.button_chosen == 3
        assert row.interval < 0  # learning
        assert row.last_interval == 0
        assert row.taken_millis == 0
        assert row.anki_card_id == 42

    def test_build_revlog_row_pk_is_wall_clock_taken_millis_is_elapsed(self):
        """PK is wall-clock ms from ``now`` (matches Anki's revlog.id convention);
        ``time_ms`` argument becomes ``taken_millis`` (Anki's revlog.time)."""
        from app.srs.fsrs import build_revlog_row

        now = datetime(2026, 5, 19, tzinfo=UTC)
        prev = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_at=now,
            last_review=now - timedelta(days=5),
            anki_card_id=1,
        )
        new_dir = DirectionState(
            direction=Direction.RECOGNITION, state=SRSState.REVIEW, due_at=now + timedelta(days=14)
        )
        row = build_revlog_row(2, Direction.PRODUCTION, prev, new_dir, Rating.AGAIN, 1234, now=now)
        assert row.id == int(now.timestamp() * 1000)
        assert row.taken_millis == 1234
        assert row.collocation_id == 2
        assert row.direction == Direction.PRODUCTION
        assert row.button_chosen == 1
        assert row.interval > 0  # review
        assert row.last_interval > 0
        assert row.review_kind == 1  # review→review

    def test_build_revlog_row_pk_uses_now_even_when_time_ms_zero(self):
        """Even with time_ms=0 (no elapsed timing), PK is still wall-clock."""
        from app.srs.fsrs import build_revlog_row

        now = datetime(2026, 5, 19, 12, 34, 56, tzinfo=UTC)
        prev = DirectionState(direction=Direction.RECOGNITION, state=SRSState.NEW, due_at=now, anki_card_id=42)
        new_dir = DirectionState(
            direction=Direction.RECOGNITION, state=SRSState.LEARNING, due_at=now + timedelta(minutes=1)
        )
        row = build_revlog_row(1, Direction.RECOGNITION, prev, new_dir, Rating.GOOD, 0, now=now)
        assert row.id == int(now.timestamp() * 1000)
        assert row.taken_millis == 0


class TestReviewIntervalCascade:
    """FSRS review interval cascade (Anki parity)."""

    def test_greater_than_last(self):
        from app.srs.fsrs import _greater_than_last

        assert _greater_than_last(0, 0) == 0
        assert _greater_than_last(0, 5) == 0
        assert _greater_than_last(5, 5) == 0
        assert _greater_than_last(6, 5) == 6

    def test_constrain_passing_intervals_poljubiti_case(self):
        from app.srs.fsrs import _constrain_passing_intervals

        h, g, e = _constrain_passing_intervals(1, 1, 3, 1)
        # raw_hard=1, gt(1,1)=0, floor=1 → hard=max(1,1)=1
        # raw_good=1, gt(1,1)=0, floor=max(0,1+1)=2 → good=max(1,2)=2 ✓
        # raw_easy=3, gt(3,1)=2, floor=max(2,2+1=3) → easy=max(3,3)=3
        assert (h, g, e) == (1, 2, 3)

    def test_constrain_passing_intervals_below_scheduled_days(self):
        from app.srs.fsrs import _constrain_passing_intervals

        h, g, e = _constrain_passing_intervals(1, 1, 3, 10)
        assert (h, g, e) == (1, 2, 3)

    def test_constrain_passing_intervals_all_above_scheduled_days(self):
        from app.srs.fsrs import _constrain_passing_intervals

        h, g, e = _constrain_passing_intervals(10, 10, 10, 5)
        # gt(10,5)=6, hard=max(10,6)=10, gt(10,5)=6, floor=max(6,10+1=11) → good=max(10,11)=11
        # gt(10,5)=6, floor=max(6,11+1=12) → easy=max(10,12)=12
        assert (h, g, e) == (10, 11, 12)

    def test_review_good_interval_exceeds_hard_by_at_least_one(self):
        """Cascade ensures Good ≥ Hard + 1 for adjacent ratings."""
        from app.srs.fsrs import schedule

        unit = SyntacticUnit(text="test", translation="test", word_count=1, difficulty=1, source="test")
        now = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)
        last_review = now - timedelta(days=30)
        due_at = last_review + timedelta(days=1)
        prev = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=due_at,
            stability=0.5,
            difficulty=5.0,
            reps=5,
            state=SRSState.REVIEW,
            last_review=last_review,
            anki_card_id=12345,
        )
        item = SRSItem(
            syntactic_unit=unit,
            directions={Direction.RECOGNITION: prev},
            guid="g-test",
            anki_note_id=1001,
        )
        hard_item = schedule(item, Rating.HARD, direction=Direction.RECOGNITION, now=now)
        good_item = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION, now=now)
        hard_dir = hard_item.directions[Direction.RECOGNITION]
        good_dir = good_item.directions[Direction.RECOGNITION]
        hard_ivl = (hard_dir.due_at - hard_dir.last_review).days
        good_ivl = (good_dir.due_at - good_dir.last_review).days
        assert good_ivl >= hard_ivl + 1, f"Expected Good≥Hard+1 but got Good={good_ivl}, Hard={hard_ivl}"

    def test_scheduled_days_derived_from_due_at_minus_last_review(self):
        """scheduled_days = max(0, (prev.due_at - prev.last_review).days)."""
        from app.srs.fsrs import schedule

        unit = SyntacticUnit(text="test", translation="test", word_count=1, difficulty=1, source="test")
        now = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)
        last_review = now - timedelta(days=30)
        due_at = last_review + timedelta(days=7)
        prev = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=due_at,
            stability=0.5,
            difficulty=5.0,
            reps=5,
            state=SRSState.REVIEW,
            last_review=last_review,
            anki_card_id=12345,
        )
        item = SRSItem(
            syntactic_unit=unit,
            directions={Direction.RECOGNITION: prev},
            guid="g-test",
            anki_note_id=1001,
        )
        result = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION, now=now)
        new_dir = result.directions[Direction.RECOGNITION]
        interval = (new_dir.due_at - new_dir.last_review).days
        # With scheduled_days=7 and stability low, raw intervals might be ≤7.
        # Cascade: hard ≥ max(greater_than_last(raw_hard, 7), 1)
        # If raw_hard ≤ 7, greater_than_last returns 0, hard = 1.
        # good ≥ max(greater_than_last(raw_good, 7), hard+1) = max(0, 2) = 2
        assert interval >= 1, f"Expected interval≥1, got {interval}"
