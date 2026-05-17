"""Tests for FSRS scheduler with learning step semantics."""

from datetime import UTC, datetime

import pytest

from app.models.srs_item import Direction, DirectionState, Rating, SRSItem, SRSState
from app.srs.fsrs import DEFAULT_FSRS5_PARAMS, schedule


def _make_item(state: SRSState = SRSState.NEW, left: int = None, due_at: datetime = None) -> SRSItem:
    """Create a minimal SRSItem for testing."""
    from app.models.syntactic_unit import SyntacticUnit

    unit = SyntacticUnit(text="test", translation="test", word_count=2, difficulty=1, source="corpus")
    rec_dir = DirectionState(
        direction=Direction.RECOGNITION,
        due_date=datetime.now().date(),
        state=state,
        left=left,
        due_at=due_at,
    )
    return SRSItem(syntactic_unit=unit, directions={Direction.RECOGNITION: rec_dir}, guid="test-guid-123")


class TestLearningStepSemantics:
    """Tests for scheduler with learning steps."""

    @pytest.fixture(autouse=True)
    def _defaults(self, monkeypatch):
        monkeypatch.setattr("app.srs.queue_stats.resolve_learning_steps", lambda db=None: ([1.0, 10.0], "default"))
        monkeypatch.setattr("app.srs.queue_stats.resolve_relearning_steps", lambda db=None: ([10.0], "default"))
        monkeypatch.setattr("app.srs.queue_stats.resolve_fsrs_short_term_flag", lambda db=None: True)

    # ── Anki cards.left encoding parity ──────────────────────────
    # Anki encodes cards.left as `today_left * 1000 + total_remaining`. The low
    # 3 digits are what drive the state machine (Anki's Card.remaining_steps()
    # returns `self.remaining_steps % 1000`). TunaTale must read and write the
    # same format so a card mid-learning lands on the right step in both apps.

    def test_parse_left_returns_anki_total_remaining(self):
        from app.srs.fsrs import _parse_left

        # left=2: today_left=0, total_remaining=2 (just entered learning)
        assert _parse_left(2) == 2
        # left=1002: today_left=1, total_remaining=2 — same step as left=2
        assert _parse_left(1002) == 2
        # left=1: total_remaining=1 (one step left, next Good graduates)
        assert _parse_left(1) == 1
        # legacy / synced-from-Anki nulls
        assert _parse_left(None) == 0
        assert _parse_left(0) == 0

    def test_pack_left_uses_anki_count_form(self):
        from app.srs.fsrs import _pack_left

        # Just write total_remaining (today_left=0 ≡ Anki's modern format).
        assert _pack_left(2) == 2
        assert _pack_left(1) == 1
        assert _pack_left(0) == 0

    def test_learning_good_with_legacy_left_1002_advances_not_graduates(self):
        """The piščanec bug: TT used to misinterpret left=1002 as 'last step',
        graduating on Good. Per Anki, left=1002 → idx=0 (first step), so Good
        must advance to the second step and stay LEARNING.
        """
        item = _make_item(state=SRSState.LEARNING, left=1002)
        result = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        assert new_dir.state == SRSState.LEARNING, "must NOT graduate from first step"
        # After Good: total_remaining was 2, decrements to 1 → idx=1 (last step)
        assert new_dir.left == 1

    def test_new_again_goes_to_learning(self):
        """NEW + AGAIN → LEARNING state."""
        item = _make_item(state=SRSState.NEW)
        result = schedule(item, Rating.AGAIN, direction=Direction.RECOGNITION)
        assert result.directions[Direction.RECOGNITION].state == SRSState.LEARNING

    def test_learning_again_resets_to_step_0(self):
        """LEARNING + AGAIN → step 0, left resets."""
        # left=1002 means 2 steps total, 2 remaining (just started)
        item = _make_item(state=SRSState.LEARNING, left=1002)
        result = schedule(item, Rating.AGAIN, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        assert new_dir.state == SRSState.LEARNING
        # After AGAIN, should reset to step 0
        assert new_dir.left is not None
        # The step should be 0 (total_steps_left * 1000 + steps_remaining)
        # With default [1.0, 10.0] steps: step 0 = 2 * 1000 + 2 = 2002

    def test_learning_hard_stays_on_same_step(self):
        """LEARNING + HARD → same step, due_at updated."""
        item = _make_item(state=SRSState.LEARNING, left=2002)  # step 0, 2 total
        result = schedule(item, Rating.HARD, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        assert new_dir.state == SRSState.LEARNING
        # Should still be on step 0
        # Anki encoding: total_remaining unchanged on HARD; modern Anki writes
        # just the count (no today_left * 1000 prefix). Re-pack normalizes 2002 → 2.
        assert new_dir.left == 2

    def test_learning_good_advances_step(self):
        """LEARNING + GOOD → next step or graduates if last step."""
        # On step 0 of 2-step deck
        item = _make_item(state=SRSState.LEARNING, left=2002)
        result = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        # Anki encoding: GOOD decrements total_remaining (2 → 1), advancing to step 1.
        # idx = total_steps - total_remaining = 2 - 1 = 1.
        assert new_dir.left == 1

    def test_learning_good_last_step_graduates(self):
        """LEARNING + GOOD on last step → graduates to REVIEW."""
        # On last step (step 1 of 2-step deck, left=1001)
        item = _make_item(state=SRSState.LEARNING, left=1001)
        result = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        # Should graduate
        assert new_dir.state == SRSState.REVIEW
        assert new_dir.left is None  # No longer in learning

    def test_learning_easy_graduates_immediately(self):
        """LEARNING + EASY → graduates immediately to REVIEW."""
        item = _make_item(state=SRSState.LEARNING, left=2002)
        result = schedule(item, Rating.EASY, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        assert new_dir.state == SRSState.REVIEW
        assert new_dir.left is None

    def test_learning_due_at_set_for_future(self):
        """LEARNING steps set due_at to future time."""
        item = _make_item(state=SRSState.LEARNING, left=2002)
        now = datetime.now(UTC)
        result = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        # due_at should be in the future (step 1 = 10 minutes)
        assert new_dir.due_at is not None
        assert new_dir.due_at > now

    def test_relearning_after_again(self):
        """REVIEW + AGAIN → RELEARNING state."""
        item = _make_item(state=SRSState.REVIEW)
        result = schedule(item, Rating.AGAIN, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        assert new_dir.state == SRSState.RELEARNING
        assert new_dir.left is not None  # Should have relearning steps

    def test_empty_steps_graduate_immediately(self):
        """With empty learn_steps, LEARNING + GOOD → graduates immediately."""
        # Use the autouse fixture which provides [1.0, 10.0] steps
        # But override to make steps empty
        item = _make_item(state=SRSState.LEARNING, left=0)  # 0 steps total from parse
        # The fixture gives [1.0, 10.0], not empty - but left=0 triggers normalization
        # which sets steps_remaining = total_steps = 2, so it stays in LEARNING
        result = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        # After normalization: total_remaining=2 (full); GOOD advances to step 1
        # (decrements to 1), still LEARNING.
        assert new_dir.state == SRSState.LEARNING
        assert new_dir.left == 1  # total_remaining=1 → idx=1 (last step)

    def test_new_again_empty_steps_graduates(self, monkeypatch):
        """NEW + AGAIN with empty learn_steps → graduates via _graduate_to_review (line 254)."""
        # Patch the source functions in queue_stats since fsrs imports them locally
        monkeypatch.setattr("app.srs.queue_stats.resolve_learning_steps", lambda db=None: ([], "default"))
        item = _make_item(state=SRSState.NEW)
        result = schedule(item, Rating.AGAIN, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        assert new_dir.state == SRSState.REVIEW
        assert new_dir.stability > 0  # Hits lines 463-464 (_init_stability + _init_difficulty)

    def test_review_again_empty_relearn_steps_graduates(self, monkeypatch):
        """REVIEW + AGAIN with empty relearn_steps → graduates immediately (line 311)."""
        monkeypatch.setattr("app.srs.queue_stats.resolve_relearning_steps", lambda db=None: ([], "default"))
        item = _make_item(state=SRSState.REVIEW)
        result = schedule(item, Rating.AGAIN, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        assert new_dir.state == SRSState.REVIEW

    def test_schedule_with_steps_empty_steps_graduates(self, monkeypatch):
        """LEARNING with empty steps and left=0 → graduates via _graduate_to_review (line 362)."""
        monkeypatch.setattr("app.srs.queue_stats.resolve_learning_steps", lambda db=None: ([], "default"))
        item = _make_item(state=SRSState.LEARNING, left=0)
        result = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        assert new_dir.state == SRSState.REVIEW

    def test_learning_easy_relearning_graduates(self, monkeypatch):
        """RELEARNING + EASY → graduates (hits line 433→437 fallthrough)."""
        # Use real steps for RELEARNING to get into _schedule_with_steps, then EASY
        monkeypatch.setattr("app.srs.queue_stats.resolve_relearning_steps", lambda db=None: ([10.0], "default"))
        monkeypatch.setattr("app.srs.queue_stats.resolve_learning_steps", lambda db=None: ([1.0, 10.0], "default"))
        item = _make_item(state=SRSState.RELEARNING, left=1001)  # 1 step remaining
        result = schedule(item, Rating.EASY, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        assert new_dir.state == SRSState.REVIEW

    def test_graduate_from_relearning_uses_next_stability_lapse(self, monkeypatch):
        """RELEARNING + GOOD (last step) → REVIEW, FSRS stability_lapse applied."""
        monkeypatch.setattr("app.srs.queue_stats.resolve_relearning_steps", lambda db=None: ([1.0], "default"))
        # Start in RELEARNING with 1 step, rate GOOD to graduate
        item = _make_item(state=SRSState.RELEARNING, left=1001)
        result = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        assert new_dir.state == SRSState.REVIEW
        assert new_dir.stability > 0  # Confirms FSRS next_stability_lapse was applied

    def test_new_hard_goes_to_learning_step_0(self):
        """NEW + HARD → LEARNING state at step 0."""
        item = _make_item(state=SRSState.NEW)
        result = schedule(item, Rating.HARD, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        assert new_dir.state == SRSState.LEARNING
        # NEW + HARD: stay at step 0 → total_remaining = full count (2).
        assert new_dir.left == 2

    def test_new_good_advances_to_learning_step_1(self):
        """NEW + GOOD → LEARNING state at step 1."""
        item = _make_item(state=SRSState.NEW)
        result = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        assert new_dir.state == SRSState.LEARNING
        # NEW + GOOD on 2-step deck: advance to step 1 → total_remaining = 1.
        assert new_dir.left == 1

    def test_new_easy_graduates_immediately(self):
        """NEW + EASY → graduates immediately to REVIEW."""
        item = _make_item(state=SRSState.NEW)
        result = schedule(item, Rating.EASY, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        assert new_dir.state == SRSState.REVIEW
        assert new_dir.left is None
        assert new_dir.stability > 0  # FSRS init ran

    def test_new_good_with_single_step_graduates(self, monkeypatch):
        """NEW + GOOD with single step deck → graduates immediately."""
        # Override the autouse fixture: 1-step deck means GOOD = graduate
        monkeypatch.setattr(
            "app.srs.queue_stats.resolve_learning_steps",
            lambda db=None: ([1.0], "default"),
        )
        item = _make_item(state=SRSState.NEW)
        result = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION)
        assert result.directions[Direction.RECOGNITION].state == SRSState.REVIEW

    def test_learning_hard_with_left_zero_normalizes_to_full_steps(self):
        """LEARNING + HARD with left=0 (from sync-imported card) → normalizes to full steps, no IndexError."""
        # left=0 means _parse_left returns (0, 0), which caused IndexError on HARD
        item = _make_item(state=SRSState.LEARNING, left=0)
        result = schedule(item, Rating.HARD, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        # Should stay in LEARNING, not crash
        assert new_dir.state == SRSState.LEARNING
        # Should have valid left value (full steps remaining)
        assert new_dir.left is not None
        assert new_dir.left > 0

    def test_learning_hard_with_left_none_normalizes_to_full_steps(self):
        """LEARNING + HARD with left=None (from sync-imported card) → normalizes to full steps."""
        item = _make_item(state=SRSState.LEARNING, left=None)
        result = schedule(item, Rating.HARD, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        assert new_dir.state == SRSState.LEARNING
        assert new_dir.left is not None
        assert new_dir.left > 0

    def test_relearning_hard_with_left_zero_normalizes_to_full_steps(self, monkeypatch):
        """RELEARNING + HARD with left=0 → normalizes to full steps, no IndexError."""
        monkeypatch.setattr("app.srs.queue_stats.resolve_relearning_steps", lambda db=None: ([10.0], "default"))
        item = _make_item(state=SRSState.RELEARNING, left=0)
        result = schedule(item, Rating.HARD, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        assert new_dir.state == SRSState.RELEARNING
        assert new_dir.left is not None
        assert new_dir.left > 0

    # ── Hard-on-first-step parity with Anki ──────────────────────────────────
    # Anki's rslib (scheduler/states/learning.rs) special-cases Hard on the
    # first step when the deck has ≥2 steps: the delay is the average of the
    # first two steps, not the current step. With learn_steps=[1, 10] this
    # yields 5.5 min, matching the empirically-observed revlog `ivl=-330` on
    # cards graded Hard on a fresh learning step.

    def test_new_hard_first_step_uses_avg_of_first_two_steps(self):
        """NEW + HARD with [1, 10] → due_at = now + 5.5 min (plus Anki-parity fuzz)."""
        item = _make_item(state=SRSState.NEW)
        now = datetime.now(UTC)
        result = schedule(item, Rating.HARD, direction=Direction.RECOGNITION, now=now)
        new_dir = result.directions[Direction.RECOGNITION]
        delay_sec = (new_dir.due_at - now).total_seconds()
        # 5.5min = 330s. Anki fuzz upper = min(int(330*0.25), 300) = 82.
        assert 330 <= delay_sec < 330 + 82, f"Hard avg-of-first-two with fuzz must land in [330, 412); got {delay_sec}"

    def test_learning_hard_first_step_uses_avg_of_first_two_steps(self):
        """LEARNING + HARD on first step (left=2) with [1, 10] → due_at = now + 5.5 min (+ fuzz).

        Direct regression for the kuhinja/koruza divergence: TT was scheduling
        Hard at +60 s while Anki scheduled at +330 s, putting the two queues
        out of agreement after the user pressed Hard on a fresh learn card.
        """
        item = _make_item(state=SRSState.LEARNING, left=2)
        now = datetime.now(UTC)
        result = schedule(item, Rating.HARD, direction=Direction.RECOGNITION, now=now)
        new_dir = result.directions[Direction.RECOGNITION]
        delay_sec = (new_dir.due_at - now).total_seconds()
        assert 330 <= delay_sec < 330 + 82, f"Hard avg-of-first-two with fuzz must land in [330, 412); got {delay_sec}"

    def test_learning_hard_later_step_uses_current_step(self):
        """LEARNING + HARD on second step (left=1) with [1, 10] → due_at = now + 10 min (+ fuzz).

        The avg-of-first-two rule only applies to the first step. On any later
        step, Hard keeps the current step's delay.
        """
        item = _make_item(state=SRSState.LEARNING, left=1)
        now = datetime.now(UTC)
        result = schedule(item, Rating.HARD, direction=Direction.RECOGNITION, now=now)
        new_dir = result.directions[Direction.RECOGNITION]
        delay_sec = (new_dir.due_at - now).total_seconds()
        # 10min = 600s. Anki fuzz upper = min(int(600*0.25), 300) = 150.
        assert 600 <= delay_sec < 600 + 150, f"Hard later-step with fuzz must land in [600, 750); got {delay_sec}"

    def test_new_hard_single_step_uses_step_zero(self, monkeypatch):
        """NEW + HARD with single-step deck → due_at = now + step[0] (+ fuzz), no averaging."""
        monkeypatch.setattr("app.srs.queue_stats.resolve_learning_steps", lambda db=None: ([10.0], "default"))
        item = _make_item(state=SRSState.NEW)
        now = datetime.now(UTC)
        result = schedule(item, Rating.HARD, direction=Direction.RECOGNITION, now=now)
        new_dir = result.directions[Direction.RECOGNITION]
        delay_sec = (new_dir.due_at - now).total_seconds()
        assert 600 <= delay_sec < 600 + 150, f"single-step Hard with fuzz must land in [600, 750); got {delay_sec}"


class TestLearningStepFuzz:
    """Anki parity: in-seconds learning steps get a positive uniform fuzz of
    `[0, min(0.25 * step_secs, 300))` so two cards graded "Again" at the same
    instant don't bunch up at exactly step+0s. Without fuzz, TT's `due_at`
    falls a fraction of a second before Anki's, and the next grade's cutoff
    can land between them — TT surfaces the card, Anki doesn't. Mirrors
    rslib/.../answering/learning.rs:learning_ivl_with_fuzz.
    """

    @pytest.fixture(autouse=True)
    def _defaults(self, monkeypatch):
        monkeypatch.setattr("app.srs.queue_stats.resolve_learning_steps", lambda db=None: ([1.0, 10.0], "default"))
        monkeypatch.setattr("app.srs.queue_stats.resolve_relearning_steps", lambda db=None: ([10.0], "default"))
        monkeypatch.setattr("app.srs.queue_stats.resolve_fsrs_short_term_flag", lambda db=None: True)

    def test_again_60s_step_due_at_falls_in_anki_fuzz_range(self):
        """For a 60s step, Anki schedules due in [60, 75) — TT must too."""
        now = datetime.now(UTC)
        item = _make_item(state=SRSState.NEW)
        # Override anki_card_id so the seed is deterministic-but-nontrivial.
        item.directions[Direction.RECOGNITION] = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=now.date(),
            state=SRSState.NEW,
            anki_card_id=12345,
            reps=3,
        )
        result = schedule(item, Rating.AGAIN, direction=Direction.RECOGNITION, now=now)
        delay_sec = (result.directions[Direction.RECOGNITION].due_at - now).total_seconds()
        # 60s base + uniform [0, min(0.25*60, 300)) = 60s + [0, 15) = [60, 75)
        assert 60 <= delay_sec < 75, f"60s step + fuzz must land in [60, 75); got {delay_sec}s"

    def test_fuzz_is_deterministic_per_card_and_reps(self):
        """Same (anki_card_id, reps) → same due_at. Mirrors Anki's deterministic
        seed so re-running a sync doesn't shift schedules."""
        now = datetime.now(UTC)

        def grade_once() -> float:
            item = _make_item(state=SRSState.NEW)
            item.directions[Direction.RECOGNITION] = DirectionState(
                direction=Direction.RECOGNITION,
                due_date=now.date(),
                state=SRSState.NEW,
                anki_card_id=98765,
                reps=7,
            )
            r = schedule(item, Rating.AGAIN, direction=Direction.RECOGNITION, now=now)
            return (r.directions[Direction.RECOGNITION].due_at - now).total_seconds()

        assert grade_once() == grade_once(), "fuzz must be deterministic per (card_id, reps)"

    def test_fuzz_differs_when_reps_changes(self):
        """Different reps → likely different fuzz (not strictly required but helps
        spread successive lapses on the same card)."""
        now = datetime.now(UTC)

        def grade_with_reps(reps: int) -> float:
            item = _make_item(state=SRSState.NEW)
            item.directions[Direction.RECOGNITION] = DirectionState(
                direction=Direction.RECOGNITION,
                due_date=now.date(),
                state=SRSState.NEW,
                anki_card_id=42,
                reps=reps,
            )
            r = schedule(item, Rating.AGAIN, direction=Direction.RECOGNITION, now=now)
            return (r.directions[Direction.RECOGNITION].due_at - now).total_seconds()

        # With a 15-value range and different seeds, at least one of these pairs differs.
        results = {grade_with_reps(r) for r in range(20)}
        assert len(results) > 1, f"fuzz should produce variety across reps; got {results}"

    def test_long_step_caps_fuzz_at_300s(self):
        """For a 1200s+ step, Anki caps fuzz at 300s. TT must match."""
        now = datetime.now(UTC)
        # Override learning steps to a single 30-minute (1800s) step.
        # 0.25 * 1800 = 450 → capped at 300. Range: [1800, 2100).
        item = _make_item(state=SRSState.NEW)
        item.directions[Direction.RECOGNITION] = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=now.date(),
            state=SRSState.NEW,
            anki_card_id=999,
            reps=1,
        )
        # Use the existing 1m/10m default; check the 10m step via a Good on first step
        # (transitions to step 1 = 10m). 0.25 * 600 = 150 → range [600, 750).
        item.directions[Direction.RECOGNITION] = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=now.date(),
            state=SRSState.LEARNING,
            left=2,  # 2 steps total, currently at step 0
            anki_card_id=999,
            reps=1,
        )
        result = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION, now=now)
        delay_sec = (result.directions[Direction.RECOGNITION].due_at - now).total_seconds()
        assert 600 <= delay_sec < 750, f"10m step + capped fuzz must land in [600, 750); got {delay_sec}s"


class TestShortTermAppliesInSteps:
    """Short-term stability is applied during learning/relearning steps.

    Regression test for the potisniti bug: TT was not updating stability on
    learning-step grades, causing a 2× divergence from Anki after 6 grades.
    """

    # User's actual "0. Slovene" deck FSRS-5 weights (deck_config 1774631358546 /
    # name "Slovene1774631349"). Pulled from collection.anki2 on 2026-05-16.
    _SLOVENE_WEIGHTS = (
        0.40255001187324524,
        1.1838500499725342,
        3.1730000972747803,
        15.691049575805664,
        7.194900035858154,
        0.534500002861023,
        1.4603999853134155,
        0.004600000102072954,
        1.5457500219345093,
        0.11919999867677689,
        1.0192500352859497,
        1.9394999742507935,
        0.10999999940395355,
        0.2960500121116638,
        2.2697999477386475,
        0.23149999976158142,
        2.989799976348877,
        0.5165500044822693,
        0.6621000170707703,
    )

    @pytest.fixture(autouse=True)
    def _defaults(self, monkeypatch):
        monkeypatch.setattr("app.srs.queue_stats.resolve_learning_steps", lambda db=None: ([1.0, 10.0], "default"))
        monkeypatch.setattr("app.srs.queue_stats.resolve_relearning_steps", lambda db=None: ([10.0], "default"))

    @pytest.mark.parametrize(
        ("name", "ratings", "expected_stabilities"),
        [
            # Ground truth captured by running grades in Anki 25.09.4
            # (fsrs-rs 5.1.0) with the Slovene weights above. The capture
            # script lives in PR notes; reproduce via:
            #   uv run --with anki python -c "<see SCRIPT in docstring>"
            # Anki produces identical stability evolution regardless of the
            # fsrsShortTermWithStepsEnabled deck option — the flag only
            # governs card-state transitions, not memory_state updates.
            (
                "again_hard_good_again",
                [Rating.AGAIN, Rating.HARD, Rating.GOOD, Rating.AGAIN],
                [0.4026, 0.3381, 0.4760, 0.2385],
            ),
            (
                "four_lapses",
                [Rating.AGAIN, Rating.AGAIN, Rating.AGAIN, Rating.AGAIN],
                [0.4026, 0.2017, 0.1011, 0.0507],
            ),
        ],
    )
    def test_stability_lockstep_with_anki(self, name, ratings, expected_stabilities):
        """End-to-end lockstep: TT's schedule() produces Anki's exact stability values.

        Routes through schedule() (not the helper directly) so any future
        refactor of _schedule_new / _schedule_with_steps that breaks parity
        will fail this test. Note: difficulty also diverges in TT today
        (TT uses old mean-reversion, Anki uses linear-damping) — that's a
        separate pre-existing bug; not asserted here.
        """
        from app.srs.fsrs import FSRSParams

        params = FSRSParams(weights=self._SLOVENE_WEIGHTS)
        item = _make_item(state=SRSState.NEW)
        for grade_num, (rating, expected) in enumerate(zip(ratings, expected_stabilities, strict=True), 1):
            item = schedule(item, rating, direction=Direction.RECOGNITION, params=params)
            actual = item.directions[Direction.RECOGNITION].stability
            assert abs(actual - expected) < 1e-3, (
                f"{name} grade {grade_num} ({rating.name}): TT stability {actual:.4f} ≠ Anki ground truth {expected}"
            )

    def test_new_first_grade_no_short_term(self):
        """NEW + AGAIN with no prior stability (None) → uses _init_stability, not short-term."""
        from app.srs.fsrs import _init_stability

        # Create item with stability=None (as would come from sync before any TT grade)
        item = _make_item(state=SRSState.NEW)
        from dataclasses import replace

        item.directions[Direction.RECOGNITION] = replace(
            item.directions[Direction.RECOGNITION],
            stability=None,
        )
        result = schedule(item, Rating.AGAIN, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        expected = _init_stability(Rating.AGAIN, DEFAULT_FSRS5_PARAMS.weights)
        assert new_dir.stability == expected

    def test_learning_grade_with_none_stability_inherits_none(self):
        """LEARNING + Again on a card with stability=None (promoted from listen-first
        UI without a prior grade): stability and difficulty stay None — TT can't
        run short-term without a prior value. Next sync_pull will populate it
        from Anki."""
        from dataclasses import replace

        item = _make_item(state=SRSState.LEARNING, left=2002)
        item.directions[Direction.RECOGNITION] = replace(
            item.directions[Direction.RECOGNITION],
            stability=None,
            difficulty=None,
        )
        result = schedule(item, Rating.AGAIN, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        assert new_dir.stability is None
        assert new_dir.difficulty is None

    def test_new_second_grade_applies_short_term(self):
        """After a first grade set stability, second grade applies short-term."""
        from app.srs.fsrs import _stability_short_term

        # First grade: NEW + AGAIN
        item = _make_item(state=SRSState.NEW)
        result = schedule(item, Rating.AGAIN, direction=Direction.RECOGNITION)
        # Second grade: Again on the learning card
        result2 = schedule(result, Rating.AGAIN, direction=Direction.RECOGNITION)
        new_dir = result2.directions[Direction.RECOGNITION]
        expected = _stability_short_term(
            result.directions[Direction.RECOGNITION].stability,
            Rating.AGAIN,
            DEFAULT_FSRS5_PARAMS,
        )
        assert abs(new_dir.stability - expected) < 1e-10

    def test_learning_again_updates_stability(self):
        """LEARNING + AGAIN with prev.stability=0.5 → short-term updates it."""
        from app.srs.fsrs import _stability_short_term

        item = _make_item(state=SRSState.LEARNING, left=2)
        item.directions[Direction.RECOGNITION] = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=datetime.now().date(),
            state=SRSState.LEARNING,
            stability=0.5,
            difficulty=5.0,
            reps=1,
            left=2,
            due_at=datetime.now(UTC),
        )
        result = schedule(item, Rating.AGAIN, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        expected = _stability_short_term(0.5, Rating.AGAIN, DEFAULT_FSRS5_PARAMS)
        assert abs(new_dir.stability - expected) < 1e-10

    def test_learning_hard_no_clamp(self):
        """LEARNING + HARD with sinc < 1, rating=2 < 3 → no clamp, stability decreases."""
        from app.srs.fsrs import _stability_short_term

        item = _make_item(state=SRSState.LEARNING, left=2)
        item.directions[Direction.RECOGNITION] = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=datetime.now().date(),
            state=SRSState.LEARNING,
            stability=1.0,
            difficulty=5.0,
            reps=1,
            left=2,
            due_at=datetime.now(UTC),
        )
        result = schedule(item, Rating.HARD, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        expected = _stability_short_term(1.0, Rating.HARD, DEFAULT_FSRS5_PARAMS)
        assert abs(new_dir.stability - expected) < 1e-10
        # HARD with rating=2 < 3, no clamp → stability decreases
        assert new_dir.stability < 1.0

    def test_relearning_again_short_term_same_day(self, monkeypatch):
        """REVIEW + AGAIN on same day → short-term applied, not lapse formula."""
        monkeypatch.setattr("app.srs.queue_stats.resolve_relearning_steps", lambda db=None: ([10.0], "default"))
        from app.srs.fsrs import _stability_short_term

        now = datetime.now(UTC)
        item = _make_item(state=SRSState.REVIEW)
        item.directions[Direction.RECOGNITION] = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=now.date(),
            state=SRSState.REVIEW,
            stability=2.0,
            difficulty=5.0,
            reps=5,
            last_review=now,  # same day = today
        )
        result = schedule(item, Rating.AGAIN, direction=Direction.RECOGNITION, now=now)
        new_dir = result.directions[Direction.RECOGNITION]
        expected = _stability_short_term(2.0, Rating.AGAIN, DEFAULT_FSRS5_PARAMS)
        assert abs(new_dir.stability - expected) < 1e-10

    def test_relearning_again_lapse_multi_day(self, monkeypatch):
        """REVIEW + AGAIN on multi-day gap → lapse formula, not short-term."""
        monkeypatch.setattr("app.srs.queue_stats.resolve_relearning_steps", lambda db=None: ([10.0], "default"))
        from datetime import timedelta

        now = datetime.now(UTC)
        five_days_ago = now - timedelta(days=5)
        item = _make_item(state=SRSState.REVIEW)
        item.directions[Direction.RECOGNITION] = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=(now - timedelta(days=5)).date(),
            state=SRSState.REVIEW,
            stability=2.0,
            difficulty=5.0,
            reps=5,
            last_review=five_days_ago,
        )
        result = schedule(item, Rating.AGAIN, direction=Direction.RECOGNITION, now=now)
        new_dir = result.directions[Direction.RECOGNITION]
        # Multi-day lapse uses _next_stability_lapse, not short-term
        # This should differ from the short-term result
        from app.srs.fsrs import _stability_short_term

        st_expected = _stability_short_term(2.0, Rating.AGAIN, DEFAULT_FSRS5_PARAMS)
        assert abs(new_dir.stability - st_expected) > 1e-3, "lapse formula must differ from short-term for multi-day"
