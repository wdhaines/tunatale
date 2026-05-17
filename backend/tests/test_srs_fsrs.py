"""Tests for FSRS retrievability computation and short-term scheduler."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models.srs_item import Direction, DirectionState, Rating, SRSItem, SRSState
from app.srs.fsrs import schedule


class TestForgettingCurve:
    """Tests for _forgetting_curve with decay parameter."""

    def test_forgetting_curve_respects_decay(self):
        """Different decay values produce different retrievability."""
        from app.srs.fsrs import _forgetting_curve

        # FSRS-5 decay (-0.5)
        r_fsrs5 = _forgetting_curve(elapsed_days=10, stability=5, decay=-0.5)
        # FSRS-6 decay (-0.1542)
        r_fsrs6 = _forgetting_curve(elapsed_days=10, stability=5, decay=-0.1542)

        # Same inputs but different decay → different retrievability
        assert r_fsrs5 != r_fsrs6
        # FSRS-6 decay (less negative) → flatter curve → higher R
        assert r_fsrs6 > r_fsrs5
        # Pin exact FSRS-5 value: (1 + 19/81 * 10/5)^(-0.5) = (1 + 0.23457 * 2)^(-0.5)
        # = (1.46914)^(-0.5) = 0.825
        assert abs(r_fsrs5 - 0.825) < 0.01


class TestComputeRetrievability:
    """Tests for compute_retrievability."""

    def test_null_stability_returns_desired_retention(self):
        """Null stability → return desired_retention (mirrors Anki's R-asc placement)."""
        state = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
            stability=None,
            last_review=date.today(),
        )
        from app.srs.fsrs import compute_retrievability

        assert compute_retrievability(state, date.today()) == 0.9  # default
        assert compute_retrievability(state, date.today(), desired_retention=0.86) == 0.86

    def test_null_last_review_returns_desired_retention(self):
        """Null last_review → return desired_retention."""
        state = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
            stability=0.5,
            last_review=None,
        )
        from app.srs.fsrs import compute_retrievability

        assert compute_retrievability(state, date.today()) == 0.9
        assert compute_retrievability(state, date.today(), desired_retention=0.86) == 0.86

    def test_both_null_returns_desired_retention(self):
        state = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
            stability=None,
            last_review=None,
        )
        from app.srs.fsrs import compute_retrievability

        assert compute_retrievability(state, date.today(), desired_retention=0.86) == 0.86

    def test_low_stability_low_retrievability(self):
        """Lower stability → lower retrievability for same elapsed days."""
        today = date(2026, 5, 2)
        state_low = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date(2026, 5, 1),
            stability=0.086,
            last_review=date(2026, 5, 1),
        )
        state_high = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date(2026, 5, 1),
            stability=0.5,
            last_review=date(2026, 5, 1),
        )
        from app.srs.fsrs import compute_retrievability

        r_low = compute_retrievability(state_low, today)
        r_high = compute_retrievability(state_high, today)
        assert r_low < r_high

    def test_same_stability_same_retrievability(self):
        """Same stability and elapsed → same retrievability."""
        today = date(2026, 5, 2)
        state1 = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date(2026, 5, 1),
            stability=0.4,
            last_review=date(2026, 5, 1),
        )
        state2 = DirectionState(
            direction=Direction.PRODUCTION,
            due_date=date(2026, 5, 1),
            stability=0.4,
            last_review=date(2026, 5, 1),
        )
        from app.srs.fsrs import compute_retrievability

        assert compute_retrievability(state1, today) == compute_retrievability(state2, today)

    def test_elapsed_zero_returns_one(self):
        """Just reviewed today → retrievability ≈ 1."""
        today = date(2026, 5, 2)
        state = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=today,
            stability=0.086,
            last_review=today,
        )
        from app.srs.fsrs import compute_retrievability

        r = compute_retrievability(state, today)
        assert r == 1.0

    def test_subday_elapsed_uses_fractional_time(self):
        """Regression for Layer 10: when last_review is a precise datetime
        earlier today (not midnight), retrievability must reflect the
        fractional elapsed time, not snap to 1.0 because day-level
        `(today - last_review.date()).days` is 0.

        Concrete scenario: bolezen-production graded at 02:53 UTC with
        stability=0.0127. Computing R later the same day (say 10:00 UTC,
        ~7h elapsed) must produce R well below 1.0, otherwise R-asc sorts
        it to the END of the queue (where R=1.0 ties live) instead of the
        HEAD where its very-low-stability + recent-grade deserves it.
        """
        from datetime import UTC, datetime

        from app.srs.fsrs import compute_retrievability

        today = date(2026, 5, 11)
        # 02:53 UTC today
        last_review = datetime(2026, 5, 11, 2, 53, 20, tzinfo=UTC)
        # Reference "now" is later same day — 10:00 UTC.
        now = datetime(2026, 5, 11, 10, 0, 0, tzinfo=UTC)
        state = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=today,
            stability=0.0127,
            last_review=last_review,
        )
        r = compute_retrievability(state, today, now=now)
        assert r < 0.5, f"sub-day elapsed (~7h) on a very-low-stability card must produce R<0.5; got R={r:.4f}"

    def test_subday_elapsed_zero_at_now_returns_one(self):
        """If last_review == now, elapsed is 0 → R = 1.0 (boundary)."""
        from datetime import UTC, datetime

        from app.srs.fsrs import compute_retrievability

        today = date(2026, 5, 11)
        now = datetime(2026, 5, 11, 10, 0, 0, tzinfo=UTC)
        state = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=today,
            stability=0.5,
            last_review=now,
        )
        assert compute_retrievability(state, today, now=now) == 1.0

    def test_default_now_uses_current_time(self):
        """When `now` is omitted the function must still produce a well-defined
        value (uses `datetime.now(UTC)` internally) — callers should not need
        to thread `now` everywhere.
        """
        from datetime import UTC, datetime, timedelta

        from app.srs.fsrs import compute_retrievability

        today = date.today()
        last_review = datetime.now(UTC) - timedelta(hours=12)
        state = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=today,
            stability=0.1,
            last_review=last_review,
        )
        r = compute_retrievability(state, today)
        # 12h on stability=0.1 → elapsed≈0.5d, R ≈ (1 + 0.5/0.9)^-1 ≈ 0.643
        assert 0.3 < r < 0.9

    def test_midnight_utc_last_review_uses_integer_day_elapsed(self):
        """Cards without `lrt` in Anki's cards.data fall back to `_compute_last_review`
        which returns midnight UTC of the day-level review date. Anki's
        `extract_fsrs_retrievability` for such cards uses integer-day elapsed
        (`(today_col_day - review_day) * 86400`, no sub-day component). TT must
        do the same when `last_review.time() == 00:00:00` — otherwise fractional
        elapsed produces a slightly smaller R than Anki for the same card,
        flipping R-asc order against Anki's.
        """
        from datetime import UTC, datetime

        from app.srs.fsrs import compute_retrievability

        # 5 days ago at midnight UTC (typical day-level value from _compute_last_review)
        last_review = datetime(2026, 5, 6, 0, 0, 0, tzinfo=UTC)
        # "Now" is 5.5 days later — fractional elapsed would give different R.
        now = datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC)
        today = date(2026, 5, 11)
        state = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date(2026, 5, 11),
            stability=1.282,
            last_review=last_review,
        )
        r_tt = compute_retrievability(state, today, now=now)
        # Anki's R using integer 5-day elapsed:
        # R = (1 + 19/81 * 5 / 1.282) ^ -0.5 ≈ 0.7233
        # Fractional 5.5-day would give ~0.7055.
        assert abs(r_tt - 0.7233) < 0.01, (
            f"midnight-UTC last_review must use integer-day elapsed (5d → R≈0.7233); got R={r_tt:.4f}"
        )

    def test_subday_last_review_still_uses_fractional_elapsed(self):
        """When `last_review` has a non-zero time (i.e., a precise lrt timestamp),
        fractional elapsed is correct — Anki uses `now - lrt` in seconds for
        cards with lrt. Layer 11's fix must remain in place for the lrt case.
        """
        from datetime import UTC, datetime

        from app.srs.fsrs import compute_retrievability

        last_review = datetime(2026, 5, 11, 2, 53, 20, tzinfo=UTC)
        now = datetime(2026, 5, 11, 10, 0, 0, tzinfo=UTC)
        today = date(2026, 5, 11)
        state = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=today,
            stability=0.0127,
            last_review=last_review,
        )
        r = compute_retrievability(state, today, now=now)
        # 7h elapsed on stability=0.0127 → R≈0.28 (sub-day matters here).
        assert r < 0.5, f"non-midnight last_review must use fractional elapsed; got R={r:.4f}"

    def test_actual_values_from_plan(self):
        """Verify the exact values from the plan: prašič s=0.4 vs vlak s=0.086, today=2026-05-02, last_review=2026-05-01."""
        today = date(2026, 5, 2)
        prasic = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date(2026, 5, 1),
            stability=0.4,
            last_review=date(2026, 5, 1),
        )
        vlak = DirectionState(
            direction=Direction.PRODUCTION,
            due_date=date(2026, 5, 1),
            stability=0.086,
            last_review=date(2026, 5, 1),
        )
        from app.srs.fsrs import compute_retrievability

        r_prasic = compute_retrievability(prasic, today)
        r_vlak = compute_retrievability(vlak, today)
        # vlak has much lower stability → lower retrievability → should come first
        assert r_vlak < r_prasic


class TestRelearnGraduation:
    """Tests for RELEARNING → REVIEW graduation.

    When relearn_steps is non-empty, Anki graduates directly to REVIEW
    on the last step (relearning.rs:119-130). The 0.5-day short-term rule
    only applies when relearn_steps is empty.
    """

    def _make_relearning_item(self, stability=0.086, difficulty=5.0, reps=18, lapses=1):
        """Create an SRSItem with a RELEARNING direction."""
        utc = ZoneInfo("UTC")
        now = datetime(2026, 5, 2, 10, 0, 0, tzinfo=utc)
        last_review = datetime(2026, 5, 1, 10, 0, 0, tzinfo=utc)

        dir_state = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date(2026, 5, 1),
            stability=stability,
            difficulty=difficulty,
            reps=reps,
            lapses=lapses,
            state=SRSState.RELEARNING,
            last_review=last_review,
            last_review_time_ms=60000,
            left=1001,  # 1 step remaining, 1 total step
            due_at=now,
            dirty_fsrs=False,
        )
        return SRSItem(
            syntactic_unit=None,  # type: ignore[arg-type]
            directions={Direction.RECOGNITION: dir_state},
            guid="test-relearn-1",
            anki_note_id=1775264031901,
        )

    def test_relearn_last_step_good_long_interval_graduates(self):
        """RELEARNING + GOOD on last step with long FSRS interval → REVIEW.

        High stability → FSRS interval >= 0.5d → graduates to REVIEW.
        """
        # High stability → interval will be >= 0.5 days
        item = self._make_relearning_item(stability=10.0, difficulty=3.0, reps=18, lapses=1)

        result = schedule(
            item,
            Rating.GOOD,
            review_date=date(2026, 5, 2),
            direction=Direction.RECOGNITION,
            time_ms=60000,
        )

        recog = result.directions[Direction.RECOGNITION]
        # Should graduate to REVIEW
        assert recog.state == SRSState.REVIEW, f"Expected REVIEW, got {recog.state}"
        # due_date should be set (days-based, not sub-day)
        assert recog.due_date is not None
        # due_at should be None (no longer in learning)
        assert recog.due_at is None

    def test_review_again_then_good_graduates_to_review(self):
        """REVIEW + AGAIN + GOOD graduates to REVIEW (Anki behavior with non-empty relearn_steps).

        Anki's relearning.rs: when relearn_steps is non-empty, GOOD on the last
        relearn step graduates directly to REVIEW. The 0.5-day short-term rule
        only applies when relearn_steps is empty or fsrs_short_term_with_steps_enabled.

        Sequence (1-step relearn ladder: relearn_steps=[10.0]):
        1. REVIEW + AGAIN → RELEARNING (step 0)
        2. RELEARNING + GOOD on last (only) step → REVIEW (graduate)
        """
        utc = ZoneInfo("UTC")
        now = datetime(2026, 5, 1, 8, 0, 0, tzinfo=utc)

        dir_state = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date(2026, 4, 24),  # 7 days ago
            stability=0.086,  # Low stability
            difficulty=5.0,
            reps=18,
            lapses=1,
            state=SRSState.REVIEW,
            last_review=datetime(2026, 4, 24, 8, 0, 0, tzinfo=utc),
            last_review_time_ms=60000,
            dirty_fsrs=False,
        )
        item = SRSItem(
            syntactic_unit=None,  # type: ignore[arg-type]
            directions={Direction.RECOGNITION: dir_state},
            guid="test-roznat-1",
            anki_note_id=1775264031901,
        )

        # Step 1: REVIEW + AGAIN → RELEARNING
        result1 = schedule(
            item,
            Rating.AGAIN,
            review_date=date(2026, 5, 1),
            direction=Direction.RECOGNITION,
            time_ms=60000,
            now=now,
        )
        state1 = result1.directions[Direction.RECOGNITION]
        assert state1.state == SRSState.RELEARNING, f"Step 1: Expected RELEARNING, got {state1.state}"
        assert state1.lapses == 2

        # Step 2: RELEARNING + GOOD on last (only) step → REVIEW (graduate)
        result2 = schedule(
            result1,
            Rating.GOOD,
            review_date=date(2026, 5, 1),
            direction=Direction.RECOGNITION,
            time_ms=60000,
            now=now + timedelta(minutes=10),
        )
        state2 = result2.directions[Direction.RECOGNITION]
        assert state2.state == SRSState.REVIEW, f"Step 2: Expected REVIEW, got {state2.state}"
        # Should have graduated: no left/due_at
        assert state2.left is None
        assert state2.due_at is None
        assert state2.due_date is not None


class TestPriorStateStickyNew:
    """Fix: prior_state='new' must stick across same-state-class grades so
    `count_new_introduced_today` keeps counting a freshly-introduced card
    through every grade made on it today. Without this, grading a learning
    card whose sync set prior_state=NEW overwrites prior_state to LEARNING
    and the new-card badge "rebounds" upward by 1.
    """

    def _make_learning_item(self, *, prior_state: SRSState | None) -> SRSItem:
        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus")
        rec = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.LEARNING,
            prior_state=prior_state,
            due_date=date.today(),
            stability=0.5,
            difficulty=8.0,
            reps=1,
            lapses=0,
            left=1002,
            due_at=datetime.now() + timedelta(minutes=1),
            anki_card_id=100,
        )
        prod = DirectionState(
            direction=Direction.PRODUCTION,
            state=SRSState.NEW,
            due_date=date.today(),
            anki_card_id=101,
        )
        return SRSItem(
            syntactic_unit=unit,
            directions={Direction.RECOGNITION: rec, Direction.PRODUCTION: prod},
            guid="g",
        )

    def test_good_on_learning_step_preserves_prior_state_new(self):
        """Grade Good on a learning card whose prior_state=NEW (sync set it
        from a NEW→LEARNING transition today). The card advances to the next
        learning step — state stays LEARNING. prior_state must stay NEW so
        the introduced-today counter keeps it."""
        item = self._make_learning_item(prior_state=SRSState.NEW)
        result = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION)
        rec = result.directions[Direction.RECOGNITION]
        assert rec.state == SRSState.LEARNING, "still learning (advanced a step)"
        assert rec.prior_state == SRSState.NEW, (
            "prior_state='new' must persist across same-class grades for the introduced-today count"
        )

    def test_again_on_learning_step_preserves_prior_state_new(self):
        """Again on learning → resets to step 0 but stays LEARNING. NEW sticks."""
        item = self._make_learning_item(prior_state=SRSState.NEW)
        result = schedule(item, Rating.AGAIN, direction=Direction.RECOGNITION)
        rec = result.directions[Direction.RECOGNITION]
        assert rec.state == SRSState.LEARNING
        assert rec.prior_state == SRSState.NEW

    def test_hard_on_learning_step_preserves_prior_state_new(self):
        item = self._make_learning_item(prior_state=SRSState.NEW)
        result = schedule(item, Rating.HARD, direction=Direction.RECOGNITION)
        rec = result.directions[Direction.RECOGNITION]
        assert rec.state == SRSState.LEARNING
        assert rec.prior_state == SRSState.NEW

    def test_graduate_from_learning_with_prior_new_preserves_new(self):
        """LEARNING→REVIEW graduation must preserve `prior_state='new'` when
        the card was introduced today. Anki's `newToday` counter increments
        on first grade and never decrements during the day — TT must match
        that or the new-card badge undercounts. Anki's revlog `type` for
        a graduation grade is 0 (Learning) whether prior is 'new' or
        'learning', so keeping 'new' is safe for revlog and required for
        the introduced-today badge.
        """
        item = self._make_learning_item(prior_state=SRSState.NEW)
        # Force left to total_remaining=1 → Good graduates immediately.
        rec = item.directions[Direction.RECOGNITION]
        from dataclasses import replace as _replace

        item.directions[Direction.RECOGNITION] = _replace(rec, left=1001)
        result = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION)
        rec_after = result.directions[Direction.RECOGNITION]
        assert rec_after.state == SRSState.REVIEW, "graduated"
        assert rec_after.prior_state == SRSState.NEW, (
            "introduced-today marker must survive graduation so the new-card badge keeps the card"
        )

    def test_lapse_releases_sticky_new(self):
        """REVIEW→RELEARNING (lapse) must release sticky-NEW so the revlog
        records `prior_state='review'` → Anki revlog `type=1` (Review),
        which is what the lapse grade event actually was. Edge case: card
        introduced + graduated + lapsed all in the same day. The badge will
        lose this card after the lapse, but revlog correctness wins.
        """
        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus")
        rec = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            prior_state=SRSState.NEW,  # introduced + graduated today
            due_date=date.today(),
            stability=2.0,
            difficulty=8.0,
            reps=3,
            lapses=0,
            anki_card_id=100,
        )
        prod = DirectionState(
            direction=Direction.PRODUCTION,
            state=SRSState.NEW,
            due_date=date.today(),
            anki_card_id=101,
        )
        item = SRSItem(
            syntactic_unit=unit,
            directions={Direction.RECOGNITION: rec, Direction.PRODUCTION: prod},
            guid="g",
        )
        result = schedule(item, Rating.AGAIN, direction=Direction.RECOGNITION)
        rec_after = result.directions[Direction.RECOGNITION]
        assert rec_after.state == SRSState.RELEARNING, "lapsed"
        assert rec_after.prior_state == SRSState.REVIEW, (
            "lapse must capture the immediate-previous state for revlog type=1, overriding sticky-NEW"
        )

    def test_relearning_step_grade_does_not_become_sticky_new(self):
        """Relearning is NOT sticky-NEW — only prior_state=NEW is special.
        A relearning card graded again still records prior_state=RELEARNING
        so the revlog gets type=2.
        """
        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus")
        rec = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.RELEARNING,
            prior_state=SRSState.REVIEW,  # lapsed from review earlier today
            due_date=date.today(),
            stability=0.5,
            difficulty=8.0,
            reps=10,
            lapses=2,
            left=1001,
            due_at=datetime.now() + timedelta(minutes=10),
            anki_card_id=100,
        )
        prod = DirectionState(
            direction=Direction.PRODUCTION, state=SRSState.NEW, due_date=date.today(), anki_card_id=101
        )
        item = SRSItem(
            syntactic_unit=unit,
            directions={Direction.RECOGNITION: rec, Direction.PRODUCTION: prod},
            guid="g",
        )
        result = schedule(item, Rating.AGAIN, direction=Direction.RECOGNITION)
        rec_after = result.directions[Direction.RECOGNITION]
        assert rec_after.state == SRSState.RELEARNING
        assert rec_after.prior_state == SRSState.RELEARNING, (
            "non-NEW prior states must NOT be sticky — they capture the immediate-previous state for revlog type"
        )
