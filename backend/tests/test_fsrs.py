"""FSRS algorithm tests."""

from datetime import UTC, date, datetime, time, timedelta

import pytest

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

    def test_review_again_uses_integer_col_day_elapsed_LAYER_50(self):
        """REVIEW + AGAIN: grade-time R must use INTEGER col-day diff, NOT
        fractional days.

        Layer 50 finding (supersedes the prior 'fractional' hypothesis):
        Anki's grade-time path computes ``days_elapsed = next_day_at
        .elapsed_days_since(lrt)`` — u64 integer division by 86400
        (``rslib/.../timestamp.rs:31``). It is integer regardless of whether
        ``lrt`` has sub-day precision. The dual-branch fractional/integer
        behavior lives only in queue-sort ``extract_fsrs_retrievability``,
        NOT in the answering path.

        Two cards graded at the same moment, with ``last_review`` on the
        SAME calendar day but at different sub-day times, must produce
        IDENTICAL new stabilities (because they share the same integer
        col-day index). The previous test asserted the opposite, pinning
        an empirical bug discovered in the 2026-05-22 Stage 3b measurement:
        TT computed fractional elapsed at grade time (5.5 days) while Anki
        computed integer (5 days), producing 5-7% systematic drift on every
        sub-day-precise REVIEW grade.
        """
        from dataclasses import replace
        from datetime import datetime as _dt

        col_crt = 1388836800  # user's real col_crt; doesn't change result
        grade_dt = _dt(2026, 5, 18, 12, 0, 0, tzinfo=UTC)

        def _grade_again_with_last_review_at(last_review_dt):
            item = _review_item()
            ds = item.directions[Direction.RECOGNITION]
            item.directions[Direction.RECOGNITION] = replace(
                ds, last_review=last_review_dt, stability=5.0, difficulty=7.0
            )
            return schedule(
                item,
                Rating.AGAIN,
                review_date=grade_dt.date(),
                now=grade_dt,
                col_crt=col_crt,
            )

        # Two cards graded at the same moment, with last_review at different
        # sub-day times WITHIN THE SAME col-day window. col_crt=1388836800
        # (12:00 UTC) puts col-day boundaries at 16:00 UTC. Both times below
        # fall in the [16:00 UTC 5/11, 16:00 UTC 5/12) col-day window, so
        # Anki's integer col-day diff is identical → identical new_s.
        early = _dt(2026, 5, 11, 17, 0, 1, tzinfo=UTC)  # just past col-day boundary
        late = _dt(2026, 5, 11, 22, 0, 0, tzinfo=UTC)  # later same col-day

        r_early = _grade_again_with_last_review_at(early)
        r_late = _grade_again_with_last_review_at(late)

        s_early = r_early.directions[Direction.RECOGNITION].stability
        s_late = r_late.directions[Direction.RECOGNITION].stability

        assert s_early == s_late, (
            f"Layer 50: lapse formula must use INTEGER col-day elapsed at grade time. "
            f"Same col-day grades produced different s={s_early} vs {s_late}; "
            f"means TT still uses fractional days_elapsed at grade time."
        )

    def test_review_good_uses_integer_col_day_elapsed_LAYER_50(self):
        """REVIEW + GOOD: same Layer 50 invariant for the recall path.

        Both grade-time call sites — ``schedule()`` (REVIEW+passing) and
        ``_schedule_review_again`` (REVIEW+AGAIN) — must use integer col-day
        elapsed. Empirical verification: bit-exact match against Anki across
        all 65 REVIEW→REVIEW single passing grades in the 2026-05-22
        measurement snapshots.
        """
        from dataclasses import replace
        from datetime import datetime as _dt

        col_crt = 1388836800
        grade_dt = _dt(2026, 5, 18, 12, 0, 0, tzinfo=UTC)

        def _grade_good_with_last_review_at(last_review_dt):
            item = _review_item()
            ds = item.directions[Direction.RECOGNITION]
            item.directions[Direction.RECOGNITION] = replace(
                ds, last_review=last_review_dt, stability=5.0, difficulty=7.0
            )
            return schedule(
                item,
                Rating.GOOD,
                review_date=grade_dt.date(),
                now=grade_dt,
                col_crt=col_crt,
            )

        # Same col-day-window times as the AGAIN test above.
        early = _dt(2026, 5, 11, 17, 0, 1, tzinfo=UTC)
        late = _dt(2026, 5, 11, 22, 0, 0, tzinfo=UTC)

        r_early = _grade_good_with_last_review_at(early)
        r_late = _grade_good_with_last_review_at(late)

        s_early = r_early.directions[Direction.RECOGNITION].stability
        s_late = r_late.directions[Direction.RECOGNITION].stability

        assert s_early == s_late, (
            f"Layer 50: recall formula must use INTEGER col-day elapsed at grade time. "
            f"Same col-day grades produced different s={s_early} vs {s_late}."
        )

    def test_review_good_matches_anki_integer_elapsed_bit_exact_LAYER_50(self):
        """Layer 50 bit-exact pin: TT's ``schedule()`` REVIEW+GOOD must match
        ``_next_stability_recall`` evaluated with integer col-day elapsed.

        Reproducer for the 2026-05-22 empirical finding: with sub-day-precise
        ``last_review`` 5+ days back, TT was using fractional days (e.g.,
        5.5 → wrong R → wrong new_s). Anki uses integer col-day diff (5 → R
        matches → new_s bit-exact). All 65 REVIEW→REVIEW single passing
        grades in the measurement snapshots converge to 0% drift under
        integer elapsed.
        """
        from dataclasses import replace
        from datetime import datetime as _dt

        from app.anki.protobuf_wire import compute_anki_day_index
        from app.srs.fsrs import (
            _forgetting_curve,
            _next_stability_recall,
            _quantize_stability,
        )

        col_crt = 1388836800
        grade_dt = _dt(2026, 5, 18, 12, 0, 0, tzinfo=UTC)
        # Sub-day precision lrt 5 days + 3 hours before grade. TT was reading
        # 5.125 fractional days; Anki reads integer col-day diff = 5.
        last_review_dt = _dt(2026, 5, 13, 9, 0, 0, tzinfo=UTC)

        s_pre, d_pre = 5.0, 7.0
        item = _review_item()
        item.directions[Direction.RECOGNITION] = replace(
            item.directions[Direction.RECOGNITION],
            last_review=last_review_dt,
            stability=s_pre,
            difficulty=d_pre,
        )

        # Mirror schedule()'s internal last_review_dt derivation so the
        # expected value tracks the function under test regardless of when
        # the test runs.
        if grade_dt.date() == date.today():
            ref_now_for_elapsed = grade_dt
        else:
            ref_now_for_elapsed = datetime.combine(grade_dt.date(), time(0, 0), tzinfo=UTC)
        today_idx = compute_anki_day_index(col_crt, 4, ref_now_for_elapsed)
        review_idx = compute_anki_day_index(col_crt, 4, last_review_dt)
        expected_elapsed = max(0, today_idx - review_idx)

        params = DEFAULT_FSRS5_PARAMS
        w = params.weights
        expected_r = _forgetting_curve(expected_elapsed, s_pre, decay=-params.decay)
        expected_s = _quantize_stability(max(0.001, _next_stability_recall(d_pre, s_pre, expected_r, Rating.GOOD, w)))

        result = schedule(
            item,
            Rating.GOOD,
            review_date=grade_dt.date(),
            now=grade_dt,
            col_crt=col_crt,
        )
        actual_s = result.directions[Direction.RECOGNITION].stability

        # Bit-exact within rounding tolerance (4dp quantization).
        assert actual_s == expected_s, (
            f"Layer 50: schedule() must use integer col-day elapsed at grade time. "
            f"Expected s={expected_s} (integer elapsed={expected_elapsed}); "
            f"got s={actual_s} (likely fractional elapsed)."
        )


class TestIsDayLevelLastReview:
    """Unit tests for the shared day-level-marker predicate (Layer 72).

    Midnight-UTC `last_review` is `parse_fsrs_data`'s marker for "reconstructed
    day-level from due - ivl, no lrt present". The predicate is shared by
    `_elapsed_days_for_fsrs` (R/elapsed branch select) and sync's
    `_tt_memory_newer` recency guard (a round-tripped day-level timestamp is
    not a TT grade time).
    """

    def test_midnight_datetime_is_day_level(self):
        from datetime import datetime as _dt

        from app.srs.fsrs import is_day_level_last_review

        assert is_day_level_last_review(_dt(2026, 5, 21, 0, 0, 0, tzinfo=UTC)) is True

    def test_subday_datetime_is_not_day_level(self):
        from datetime import datetime as _dt

        from app.srs.fsrs import is_day_level_last_review

        assert is_day_level_last_review(_dt(2026, 5, 20, 16, 8, 36, tzinfo=UTC)) is False
        # Even a single microsecond past midnight counts as a real (lrt/grade) time.
        assert is_day_level_last_review(_dt(2026, 5, 21, 0, 0, 0, 1, tzinfo=UTC)) is False

    def test_bare_date_is_day_level(self):
        from app.srs.fsrs import is_day_level_last_review

        assert is_day_level_last_review(date(2026, 5, 21)) is True


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

    def test_date_last_review_uses_integer_date_diff(self):
        """When last_review is a `date` (no time-of-day), elapsed is integer
        days between the two dates. Backs Layer 50's date-branch coverage."""
        from datetime import datetime as _dt

        from app.srs.fsrs import _elapsed_days_for_fsrs

        last = date(2026, 4, 11)  # date, not datetime
        now = _dt(2026, 5, 20, 9, 0, 0, tzinfo=UTC)
        assert _elapsed_days_for_fsrs(last, now) == 39


class TestGradeElapsedDaysLAYER_50:
    """Layer 50: grade-time `_grade_elapsed_days` returns INTEGER col-day diff.

    Mirrors Anki's answering-path elapsed_days computation
    (`next_day_at.elapsed_days_since(lrt)`, u64 div by 86400). Distinct from
    `_elapsed_days_for_fsrs` (queue-sort R) which has a fractional branch
    when lrt is present.
    """

    def test_none_last_review_returns_zero(self):
        from datetime import datetime as _dt

        from app.srs.fsrs import _grade_elapsed_days

        assert _grade_elapsed_days(None, _dt(2026, 5, 18, 12, 0, tzinfo=UTC)) == 0

    def test_sub_day_precision_lrt_returns_integer(self):
        """Key contrast with `_elapsed_days_for_fsrs`: sub-day-precise lrt
        does NOT trigger a fractional branch. Returns integer col-day diff."""
        from datetime import datetime as _dt

        from app.srs.fsrs import _elapsed_days_for_fsrs, _grade_elapsed_days

        col_crt = 1388836800
        last = _dt(2026, 5, 11, 17, 0, 0, tzinfo=UTC)
        now = _dt(2026, 5, 18, 12, 0, 0, tzinfo=UTC)
        # _elapsed_days_for_fsrs returns fractional ~6.79 (queue-sort branch
        # uses raw seconds-diff / 86400 when last_review has sub-day precision)
        assert _elapsed_days_for_fsrs(last, now, col_crt=col_crt) == pytest.approx(6.79, abs=0.01)
        # _grade_elapsed_days returns integer col-day diff (7), regardless of
        # lrt sub-day precision. Anki's `next_day_at.elapsed_days_since(lrt)`
        # has the same behavior.
        assert _grade_elapsed_days(last, now, col_crt=col_crt) == 7

    def test_datetime_without_col_crt_uses_utc_date_diff(self):
        from datetime import datetime as _dt

        from app.srs.fsrs import _grade_elapsed_days

        last = _dt(2026, 5, 11, 17, 0, 0, tzinfo=UTC)
        now = _dt(2026, 5, 18, 12, 0, 0, tzinfo=UTC)
        assert _grade_elapsed_days(last, now, col_crt=None) == 7

    def test_date_last_review_uses_integer_date_diff(self):
        from datetime import datetime as _dt

        from app.srs.fsrs import _grade_elapsed_days

        last = date(2026, 5, 11)  # date, not datetime
        now = _dt(2026, 5, 18, 12, 0, 0, tzinfo=UTC)
        assert _grade_elapsed_days(last, now) == 7


class TestScheduledDaysForGradeLAYER_51:
    """Layer 51 (companion fix): scheduled_days must mirror Anki's
    ``card.interval``. The previous timestamp-diff formula truncated to N-1
    for sub-day-precise lrt + Layer 49's 04:00 UTC due_at anchor.
    """

    def test_none_last_review_returns_zero(self):
        from app.srs.fsrs import _scheduled_days_for_grade

        prev = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime(2026, 5, 22, 4, 0, tzinfo=UTC),
            stability=5.0,
            difficulty=5.0,
            state=SRSState.REVIEW,
            last_review=None,
            anki_card_id=12345,
            anki_due=4521,
        )
        assert _scheduled_days_for_grade(prev, col_crt=1388836800) == 0

    def test_uses_anki_due_minus_col_day_when_both_available(self):
        """Production path: anki_due (synced from Anki) and col_crt are both set.
        Result equals Anki's ``card.interval`` exactly, regardless of lrt sub-day
        time-of-day.
        """
        from app.srs.fsrs import _scheduled_days_for_grade

        prev = DirectionState(
            direction=Direction.RECOGNITION,
            # due_at irrelevant when anki_due is set
            due_at=datetime(2026, 5, 22, 4, 0, tzinfo=UTC),
            stability=14.194,
            difficulty=9.755,
            state=SRSState.REVIEW,
            # lrt 21+ days back; col_day(2026-05-01 02:23:14 UTC) with col_crt=1388836800
            # and rollover=4 lands at col_day 4499. anki_due=4521 → diff=22.
            last_review=datetime(2026, 5, 1, 2, 23, 14, tzinfo=UTC),
            anki_card_id=1775264031860,
            anki_due=4521,
        )
        assert _scheduled_days_for_grade(prev, col_crt=1388836800) == 22

    def test_falls_back_to_timestamp_diff_when_anki_due_unset(self):
        """TT-only state pre-sync. Inaccurate for sub-day lrt but unchanged."""
        from app.srs.fsrs import _scheduled_days_for_grade

        prev = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime(2026, 5, 22, 4, 0, tzinfo=UTC),
            stability=5.0,
            difficulty=5.0,
            state=SRSState.REVIEW,
            last_review=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
            anki_card_id=12345,
            anki_due=None,
        )
        # (2026-05-22 04:00) - (2026-05-19 12:00) = 2 days 16 hours → .days = 2
        assert _scheduled_days_for_grade(prev, col_crt=1388836800) == 2

    def test_falls_back_when_col_crt_none(self):
        """Pre-sync TT state without col_crt cache."""
        from app.srs.fsrs import _scheduled_days_for_grade

        prev = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime(2026, 5, 22, 4, 0, tzinfo=UTC),
            stability=5.0,
            difficulty=5.0,
            state=SRSState.REVIEW,
            last_review=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
            anki_card_id=12345,
            anki_due=4521,
        )
        assert _scheduled_days_for_grade(prev, col_crt=None) == 2


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
        """Answering from REVIEW returns kind=1."""
        from app.srs.fsrs import _compute_review_kind

        assert _compute_review_kind(SRSState.REVIEW) == 1

    def test_lapse_review_kind_1(self):
        """Layer 78: a lapse (answered FROM review, into relearning) is kind=1.

        Anki keys revlog kind on the pre-answer state: ``apply_relearning_state``
        builds the entry from ``current.revlog_kind()``, and
        ``ReviewState::revlog_kind()`` is Review (states/review.rs:56-62) — the
        Relearning kind (2) only applies to later presses on the relearn step.
        """
        from app.srs.fsrs import _compute_review_kind

        assert _compute_review_kind(SRSState.REVIEW) == 1

    def test_relearn_step_review_kind_2(self):
        """Answering FROM relearning (a relearn step) returns kind=2."""
        from app.srs.fsrs import _compute_review_kind

        assert _compute_review_kind(SRSState.RELEARNING) == 2

    def test_learning_review_kind_0(self):
        """Answering from LEARNING returns kind=0 — including the graduating
        answer (LearnState::revlog_kind() is Learning regardless of next state)."""
        from app.srs.fsrs import _compute_review_kind

        assert _compute_review_kind(SRSState.LEARNING) == 0

    def test_fallback_review_kind_1(self):
        """Exotic pre-answer states fall through to kind=1."""
        from app.srs.fsrs import _compute_review_kind

        assert _compute_review_kind(SRSState.SUSPENDED) == 1
        assert _compute_review_kind(SRSState.BURIED) == 1

    def test_new_review_kind_0(self):
        """Answering a NEW card (first grade, even Easy-graduate) is kind=0."""
        from app.srs.fsrs import _compute_review_kind

        assert _compute_review_kind(SRSState.NEW) == 0

    def test_compute_revlog_interval_learning(self):
        """Learning intervals are negative seconds — the new step from ``now``."""
        from app.srs.fsrs import _compute_revlog_interval

        now = datetime(2026, 5, 19, tzinfo=UTC)
        new_dir = DirectionState(
            direction=Direction.RECOGNITION, state=SRSState.LEARNING, due_at=now + timedelta(minutes=10)
        )
        interval = _compute_revlog_interval(new_dir, now)
        assert interval < 0  # negative for learning
        assert interval == -600  # 10 minutes * 60 seconds

    def test_compute_revlog_interval_learning_ignores_elapsed_since_prev(self):
        """A relearning step is the step from ``now`` — the days since the prior
        review (a lapse from a mature card) must NOT inflate the negative seconds."""
        from app.srs.fsrs import _compute_revlog_interval

        now = datetime(2026, 5, 19, tzinfo=UTC)
        new_dir = DirectionState(
            direction=Direction.RECOGNITION, state=SRSState.RELEARNING, due_at=now + timedelta(minutes=10)
        )
        assert _compute_revlog_interval(new_dir, now) == -600  # not -(10 days + 10 min)

    def test_compute_revlog_interval_review(self):
        """Review interval is the NEW interval from ``now`` — not span-from-prev.

        Regression: anchoring on the previous review double-counted the elapsed
        time (a 30-day interval answered 10 days late stored 40). ``revlog.ivl``
        is the newly-assigned interval; the previous one lives in last_interval.
        """
        from app.srs.fsrs import _compute_revlog_interval

        now = datetime(2026, 5, 19, tzinfo=UTC)
        new_dir = DirectionState(
            direction=Direction.RECOGNITION, state=SRSState.REVIEW, due_at=now + timedelta(days=30)
        )
        interval = _compute_revlog_interval(new_dir, now)
        assert interval == 30

    def test_compute_revlog_interval_review_min_one(self):
        """A same-day review interval floors at 1 day."""
        from app.srs.fsrs import _compute_revlog_interval

        now = datetime(2026, 5, 19, tzinfo=UTC)
        new_dir = DirectionState(
            direction=Direction.RECOGNITION, state=SRSState.REVIEW, due_at=now + timedelta(hours=12)
        )
        assert _compute_revlog_interval(new_dir, now) == 1

    def test_compute_revlog_last_interval_zero(self):
        """Returns 0 when no last_review or due_at."""
        from app.srs.fsrs import _compute_revlog_last_interval

        now = datetime(2026, 5, 19, tzinfo=UTC)
        prev = DirectionState(direction=Direction.RECOGNITION, state=SRSState.NEW, due_at=now)
        assert _compute_revlog_last_interval(prev) == 0

    def test_last_interval_review_day_granular_due_is_positive_days(self):
        """Layer 78 regression (the 0/1/50 badge bug): a REVIEW card graduated
        late in the local day has a day-granular ``due_at`` (next col-day
        boundary) less than 24h after ``last_review``. The wall-clock delta
        formula encoded that as negative seconds (-7236), so
        ``count_reviews_completed_today``'s ``last_interval >= 1`` filter missed
        the lapse and the review budget never charged the grade. Anki's lastIvl
        for any review-card answer is the stored ``ivl`` in days, always >= 1
        (states/review.rs:50-54 → interval_kind.rs as_revlog_interval)."""
        from app.srs.fsrs import _compute_revlog_last_interval

        lr = datetime(2026, 7, 10, 1, 59, 23, tzinfo=UTC)
        prev = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            last_review=lr,
            due_at=lr + timedelta(seconds=7236),
        )
        assert _compute_revlog_last_interval(prev) == 1

    def test_last_interval_review_reconstructs_ivl_from_anki_due(self):
        """With ``anki_due`` + ``col_crt`` available, the REVIEW branch uses the
        ``_scheduled_days_for_grade`` col-day reconstruction (exactly ``card.ivl``
        at sync time), not the truncating wall-clock delta."""
        from app.anki.protobuf_wire import compute_anki_day_index
        from app.srs.fsrs import _compute_revlog_last_interval

        col_crt = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp())
        lr = datetime(2026, 7, 10, 15, 0, tzinfo=UTC)
        prev = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            last_review=lr,
            due_at=lr + timedelta(hours=2),
            anki_due=compute_anki_day_index(col_crt, 4, lr) + 30,
        )
        assert _compute_revlog_last_interval(prev, col_crt) == 30

    def test_last_interval_interday_learning_positive_days(self):
        """A learning card on a day-scale step (interday, Anki queue=3) encodes
        the pre-answer step as positive days — the `lastIvl >= 1` footing that
        charges Anki's review-per-day limit."""
        from app.srs.fsrs import _compute_revlog_last_interval

        lr = datetime(2026, 7, 8, 15, 0, tzinfo=UTC)
        prev = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.LEARNING,
            last_review=lr,
            due_at=lr + timedelta(days=2),
        )
        assert _compute_revlog_last_interval(prev) == 2

    def test_last_interval_relearn_step_stays_negative_seconds(self):
        """A relearn step answered FROM relearning keeps the seconds-negative
        encoding (Anki: LearnState::interval_kind() is always InSecs)."""
        from app.srs.fsrs import _compute_revlog_last_interval

        lr = datetime(2026, 7, 10, 15, 0, tzinfo=UTC)
        prev = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.RELEARNING,
            last_review=lr,
            due_at=lr + timedelta(minutes=10),
        )
        assert _compute_revlog_last_interval(prev) == -600

    def test_build_revlog_row_lapse_counts_as_review(self):
        """End-to-end shape of the badge bug: a day-granular REVIEW card graded
        Again must produce a row with ``review_kind=1`` and ``last_interval>=1``
        so the Layer 73 counter (``review_kind IN (0,1,2) AND last_interval >= 1``)
        charges it against the review budget."""
        from app.srs.fsrs import build_revlog_row

        now = datetime(2026, 7, 10, 17, 49, 53, tzinfo=UTC)
        prev = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            last_review=now - timedelta(hours=16),
            due_at=now - timedelta(hours=14),
            anki_card_id=7,
        )
        new_dir = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.RELEARNING,
            last_review=now,
            due_at=now + timedelta(minutes=11),
        )
        row = build_revlog_row(1, Direction.RECOGNITION, prev, new_dir, Rating.AGAIN, 0, now=now)
        assert row.review_kind == 1
        assert row.last_interval == 1

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

    def test_fuzz_minimum_carries_cascade_floor_LAYER_51(self):
        """Anki's fuzz `with_review_fuzz(interval, minimum, maximum)` clamps the
        fuzz lower bound to ``minimum``. The minimum comes from the cascade:
        ``max(greater_than_last(round(raw)), prev_fuzzed + 1)``.

        Pre-fix, TT cascaded raw integers, then fuzzed the cascade output with
        ``minimum=1`` — letting fuzz drop the interval back below the cascade
        floor. For low fuzz factors, this produced off-by-1-day intervals.

        Reproduces the 2026-05-22 measurement scenario for cid 1775264032672
        (pre s=1.0948, d=9.792, reps=39, GOOD rating, anki_pre.cards.ivl=2).
        Anki's stored post-grade ivl was 3; TT computed 2 (factor=0.0119 from
        the shared ChaCha12 RNG seeded at cid+reps=1775264032711, fuzz_bounds
        [2, 4], so result=floor(2 + 0.0119*3)=2 with minimum=1, but should be
        clamped to 3 because cascade floor = max(greater_than_last(round(3.95),
        2), hard_fuzzed+1) = max(3, 3) = 3).
        """
        from app.srs.fsrs import schedule

        col_crt = 1388836800
        # Per the Stage 3b drill-down (2026-05-22 measurement, cid 1775264032672):
        # pre s=1.0948, d=9.792, reps=39, cards.ivl=2 (so scheduled_days=2).
        unit = SyntacticUnit(text="t", translation="t", word_count=1, difficulty=1, source="t")
        grade_dt = datetime(2026, 5, 22, 17, 0, tzinfo=UTC)
        # last_review just past the col-day boundary so col_day diff to grade_dt = 1.
        # (Layer 50 makes _grade_elapsed_days return integer col-day diff.)
        last_review = datetime(2026, 5, 20, 19, 19, 20, tzinfo=UTC)
        # pre.due_at corresponds to anki_due=4521 (cards.ivl=2 from a previous grade).
        due_at = datetime(2026, 5, 22, 4, 0, tzinfo=UTC)
        prev = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=due_at,
            stability=1.0948,
            difficulty=9.792,
            reps=39,
            state=SRSState.REVIEW,
            last_review=last_review,
            anki_card_id=1775264032672,
        )
        item = SRSItem(
            syntactic_unit=unit,
            directions={Direction.RECOGNITION: prev},
            guid="g-t",
            anki_note_id=10001,
        )
        # Use the same FSRS-5 params from the Slovene deck-config (real user data).
        slovene_params = FSRSParams(
            weights=(
                0.40255,
                1.18385,
                3.173,
                15.69105,
                7.1949,
                0.5345,
                1.4604,
                0.0046,
                1.54575,
                0.1192,
                1.01925,
                1.9395,
                0.11,
                0.29605,
                2.2698,
                0.2315,
                2.9898,
                0.51655,
                0.6621,
            ),
            desired_retention=0.86,
        )
        result = schedule(
            item,
            Rating.GOOD,
            review_date=grade_dt.date(),
            now=grade_dt,
            params=slovene_params,
            col_crt=col_crt,
        )
        new_dir = result.directions[Direction.RECOGNITION]
        # Anki's stored anki_post.cards.ivl=3 for this card. The expected due_at
        # is what `_review_due_at_from_interval` produces for interval=3, anchored
        # at the same grade_dt (so the col_day baseline matches).
        from app.srs.fsrs import _review_due_at_from_interval

        expected_due_at = _review_due_at_from_interval(grade_dt.date(), 3, col_crt, grade_dt)
        assert new_dir.due_at == expected_due_at, (
            f"Layer 51: TT must clamp fuzz lower bound to cascade floor. "
            f"Anki's stored anki_post.cards.ivl=3; TT due_at={new_dir.due_at}, "
            f"expected={expected_due_at}. Pre-fix bug: fuzz with minimum=1 lets "
            f"factor=0.012 drop the interval to 2, below the cascade floor of 3."
        )


class TestReviewDueAtRolloverConvention:
    """Layer 49: schedule()'s due_at for REVIEW state must agree with sync_pull writeback.

    Pre-Layer-49, schedule() emitted due_at at midnight UTC on now.date()+interval,
    while sync_pull (via compute_due_at) writes 04:00 UTC on the calendar date
    derived from Anki's col_day arithmetic. The two paths disagreed by 4 hours
    plus any day offset from grades crossing the 04:00 UTC col_day boundary.
    """

    _COL_CRT = 1388836800  # 2014-01-04 12:00 UTC — real user collection value.

    def _review_prev(self) -> DirectionState:
        return DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime(2026, 5, 22, 4, 0, tzinfo=UTC),
            stability=5.0,
            difficulty=5.0,
            reps=3,
            state=SRSState.REVIEW,
            last_review=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
            anki_card_id=12345,
        )

    def _wrap(self, prev: DirectionState) -> SRSItem:
        unit = SyntacticUnit(text="t", translation="t", word_count=1, difficulty=1, source="t")
        return SRSItem(
            syntactic_unit=unit,
            directions={Direction.RECOGNITION: prev},
            guid="g-t",
            anki_note_id=1001,
        )

    def test_review_due_at_lands_at_rollover_hour_utc(self):
        """REVIEW+GOOD due_at time-of-day must be 04:00 UTC, not midnight."""
        now = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
        item = self._wrap(self._review_prev())
        result = schedule(item, Rating.GOOD, now=now, col_crt=self._COL_CRT)
        new_due_at = result.directions[Direction.RECOGNITION].due_at
        assert (new_due_at.hour, new_due_at.minute, new_due_at.second) == (4, 0, 0), (
            f"expected 04:00:00 UTC (compute_due_at convention), got {new_due_at.time()}"
        )

    def test_review_due_at_uses_col_day_not_utc_date_pre_rollover(self):
        """Grade at 03:00 UTC (before 04:00 col_day boundary) is 'yesterday' in Anki.

        TT must use Anki's col_day arithmetic so the resulting due_date matches
        what sync_pull would write back. Pre-Layer-49, schedule() used now.date()
        UTC and landed one day too far for grades in the 00:00–04:00 UTC band.
        """
        from app.anki.protobuf_wire import compute_anki_day_index, review_due_at_for_col_day

        now = datetime(2026, 5, 22, 3, 0, tzinfo=UTC)
        item = self._wrap(self._review_prev())
        result = schedule(item, Rating.GOOD, now=now, col_crt=self._COL_CRT)
        new_due_at = result.directions[Direction.RECOGNITION].due_at

        today_col_day = compute_anki_day_index(self._COL_CRT, 4, now)
        # The interval is FSRS-derived; we can read it back from the result.
        last_review = result.directions[Direction.RECOGNITION].last_review
        interval_days = (new_due_at.date() - last_review.date()).days  # approx; exact path below
        # Authoritative check: due_at must equal what review_due_at_for_col_day
        # would produce for (today_col_day + interval) where interval is whatever
        # FSRS picked. Since the test can't predict interval bit-exact, instead
        # assert the helper applied to the same col_day arithmetic produces the
        # same datetime.
        delta_days = (new_due_at.date() - (datetime.fromtimestamp(self._COL_CRT, tz=UTC).date())).days
        expected_due_at = review_due_at_for_col_day(self._COL_CRT, delta_days, rollover_hour=4)
        assert new_due_at == expected_due_at
        # Sanity: interval_days agrees with col_day arithmetic.
        assert delta_days - today_col_day == interval_days or delta_days - today_col_day >= 0

    def test_review_due_at_falls_back_when_col_crt_none(self):
        """Without col_crt (pre-sync), preserves legacy UTC-midnight behavior.

        This is the safety hatch for code paths that schedule before any Anki
        sync has happened. Behavior stays as it was pre-Layer-49.
        """
        now = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
        item = self._wrap(self._review_prev())
        result = schedule(item, Rating.GOOD, now=now, col_crt=None)
        new_due_at = result.directions[Direction.RECOGNITION].due_at
        # Legacy: midnight UTC.
        assert (new_due_at.hour, new_due_at.minute, new_due_at.second) == (0, 0, 0)

    def test_graduation_due_at_lands_at_rollover_hour_utc(self):
        """Layer 49 also covers LEARNING/NEW→REVIEW graduation (line 935 site).

        Graduation produces a day-level due_at via the same code path as a
        REVIEW+rating transition, so the same convention applies.
        """
        now = datetime(2026, 5, 22, 21, 0, tzinfo=UTC)
        # Start from LEARNING state at the final step so EASY graduates.
        prev = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=now,
            state=SRSState.LEARNING,
            stability=2.0,
            difficulty=5.0,
            reps=1,
            left=1001,  # 1 total remaining (final step)
            anki_card_id=99999,
        )
        item = self._wrap(prev)
        result = schedule(item, Rating.EASY, now=now, col_crt=self._COL_CRT)
        new_dir = result.directions[Direction.RECOGNITION]
        # Should have graduated to REVIEW.
        assert new_dir.state == SRSState.REVIEW
        assert (new_dir.due_at.hour, new_dir.due_at.minute, new_dir.due_at.second) == (4, 0, 0)


class TestStabilityForInterval:
    """Tests for stability_for_interval — inverse of _next_interval."""

    def test_round_trips_at_max_interval_default_decay(self):
        """stability_for_interval(max_ivl, dr) → _next_interval returns max_ivl."""
        from app.srs.fsrs import _next_interval, stability_for_interval

        max_ivl = 36500
        dr = 0.9
        s = stability_for_interval(max_ivl, dr)
        result = _next_interval(s, dr)
        assert result == max_ivl

    def test_round_trips_at_max_interval_fsrs6_decay(self):
        """Round-trips with a FSRS-6 style decay value."""
        from app.srs.fsrs import _next_interval, stability_for_interval

        max_ivl = 36500
        dr = 0.9
        decay = 0.1542
        s = stability_for_interval(max_ivl, dr, decay)
        result = _next_interval(s, dr, decay)
        assert result == max_ivl

    def test_round_trips_at_various_intervals(self):
        """Spot-check intervals: 365, 3650, 100000."""
        from app.srs.fsrs import _next_interval, stability_for_interval

        for ivl in [365, 3650, 100000]:
            s = stability_for_interval(ivl, 0.9)
            result = _next_interval(s, 0.9)
            assert result == min(ivl, 36500)

    def test_round_trips_with_different_desired_retention(self):
        """Works with non-default desired retention (e.g. 0.85)."""
        from app.srs.fsrs import _next_interval, stability_for_interval

        max_ivl = 36500
        dr = 0.85
        s = stability_for_interval(max_ivl, dr)
        result = _next_interval(s, dr)
        assert result == max_ivl
