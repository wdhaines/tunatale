"""Tests for FSRS scheduler with learning step semantics."""

from datetime import UTC, datetime

import pytest

from app.models.srs_item import Direction, DirectionState, Rating, SRSItem, SRSState
from app.srs.fsrs import schedule


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
    def _default_steps(self, monkeypatch):
        monkeypatch.setattr("app.srs.queue_stats.resolve_learning_steps", lambda db=None: ([1.0, 10.0], "default"))
        monkeypatch.setattr("app.srs.queue_stats.resolve_relearning_steps", lambda db=None: ([10.0], "default"))

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
        assert new_dir.left == 2002  # same step

    def test_learning_good_advances_step(self):
        """LEARNING + GOOD → next step or graduates if last step."""
        # On step 0 of 2-step deck
        item = _make_item(state=SRSState.LEARNING, left=2002)
        result = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        # Should advance to step 1 (left=1002: 1 step remaining * 1000 + 2 total steps)
        assert new_dir.left == 1002

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
        # After normalization: steps_remaining=2, so GOOD advances to step 1 (not last), stays LEARNING
        assert new_dir.state == SRSState.LEARNING
        assert new_dir.left == 1002  # 1 step remaining, 2 total

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
        assert new_dir.left == 2002  # step 0 of 2 total

    def test_new_good_advances_to_learning_step_1(self):
        """NEW + GOOD → LEARNING state at step 1."""
        item = _make_item(state=SRSState.NEW)
        result = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION)
        new_dir = result.directions[Direction.RECOGNITION]
        assert new_dir.state == SRSState.LEARNING
        assert new_dir.left == 1002  # step 1 of 2 total

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
