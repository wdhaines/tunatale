"""Tests for DbPendingGradesMixin — TT-only pending-listen-grades table."""

import pytest

from app.srs.database import SRSDatabase


@pytest.fixture
def db() -> SRSDatabase:
    d = SRSDatabase(":memory:")
    try:
        yield d
    finally:
        d.close()


class TestStagePendingGrade:
    def test_stores_new_row(self, db):
        db.stage_pending_grade("l1", 1, "recognition", "good", "due")
        rows = db.get_pending_grades("l1")
        assert len(rows) == 1
        assert rows[0]["collocation_id"] == 1
        assert rows[0]["direction"] == "recognition"
        assert rows[0]["rating"] == "good"
        assert rows[0]["grade_class"] == "due"
        assert rows[0]["lesson_id"] == "l1"

    def test_upserts_on_duplicate_card(self, db):
        db.stage_pending_grade("l1", 1, "recognition", "good", "due")
        db.stage_pending_grade("l1", 1, "recognition", "again", "due")
        rows = db.get_pending_grades("l1")
        assert len(rows) == 1  # upserted, not duplicated
        assert rows[0]["rating"] == "again"

    def test_same_collocation_different_direction(self, db):
        db.stage_pending_grade("l1", 1, "recognition", "good", "due")
        db.stage_pending_grade("l1", 1, "production", "good", "due")
        assert len(db.get_pending_grades("l1")) == 2

    def test_same_direction_different_collocation(self, db):
        db.stage_pending_grade("l1", 1, "recognition", "good", "due")
        db.stage_pending_grade("l1", 2, "recognition", "good", "due")
        assert len(db.get_pending_grades("l1")) == 2

    def test_null_grade_class(self, db):
        db.stage_pending_grade("l1", 1, "recognition", "good")
        row = db.get_pending_grade(1, "recognition")
        assert row["grade_class"] is None

    def test_upsert_updates_lesson_id(self, db):
        db.stage_pending_grade("l1", 1, "recognition", "good", "due")
        db.stage_pending_grade("l2", 1, "recognition", "hard", "ahead")
        assert len(db.get_pending_grades("l1")) == 0  # upserted to l2
        rows = db.get_pending_grades("l2")
        assert len(rows) == 1
        assert rows[0]["rating"] == "hard"


class TestGetPendingGrade:
    def test_returns_none_when_missing(self, db):
        assert db.get_pending_grade(999, "recognition") is None

    def test_returns_row(self, db):
        db.stage_pending_grade("l1", 1, "recognition", "good", "due")
        row = db.get_pending_grade(1, "recognition")
        assert row is not None
        assert row["collocation_id"] == 1
        assert row["direction"] == "recognition"
        assert row["rating"] == "good"

    def test_wrong_direction_returns_none(self, db):
        db.stage_pending_grade("l1", 1, "recognition", "good", "due")
        assert db.get_pending_grade(1, "production") is None


class TestPendingGradeIds:
    def test_empty(self, db):
        assert db.pending_grade_ids() == set()

    def test_returns_all_collocation_ids(self, db):
        db.stage_pending_grade("l1", 1, "recognition", "good", "due")
        db.stage_pending_grade("l1", 2, "production", "good", "due")
        db.stage_pending_grade("l2", 3, "recognition", "hard", "ahead")
        assert db.pending_grade_ids() == {1, 2, 3}

    def test_deduplicates(self, db):
        db.stage_pending_grade("l1", 1, "recognition", "good", "due")
        db.stage_pending_grade("l1", 1, "production", "good", "due")
        assert db.pending_grade_ids() == {1}


class TestClearPendingGrade:
    def test_clears_existing(self, db):
        db.stage_pending_grade("l1", 1, "recognition", "good", "due")
        db.clear_pending_grade(1, "recognition")
        assert db.get_pending_grade(1, "recognition") is None

    def test_does_not_raise_when_missing(self, db):
        db.clear_pending_grade(999, "recognition")  # should not raise

    def test_clears_only_one_direction(self, db):
        db.stage_pending_grade("l1", 1, "recognition", "good", "due")
        db.stage_pending_grade("l1", 1, "production", "good", "due")
        db.clear_pending_grade(1, "recognition")
        assert db.get_pending_grade(1, "production") is not None
        assert db.get_pending_grade(1, "recognition") is None


class TestCountPendingGrades:
    def test_zero(self, db):
        assert db.count_pending_grades("l1") == 0

    def test_counts_by_lesson(self, db):
        db.stage_pending_grade("l1", 1, "recognition", "good", "due")
        db.stage_pending_grade("l1", 2, "recognition", "good", "due")
        db.stage_pending_grade("l2", 3, "recognition", "good", "due")
        assert db.count_pending_grades("l1") == 2
        assert db.count_pending_grades("l2") == 1

    def test_after_clear(self, db):
        db.stage_pending_grade("l1", 1, "recognition", "good", "due")
        db.stage_pending_grade("l1", 2, "recognition", "good", "due")
        db.clear_pending_grade(1, "recognition")
        assert db.count_pending_grades("l1") == 1


class TestGetPendingGrades:
    def test_empty_lesson(self, db):
        assert db.get_pending_grades("nonexistent") == []

    def test_returns_all_lesson_rows(self, db):
        db.stage_pending_grade("l1", 1, "recognition", "good", "due")
        db.stage_pending_grade("l1", 2, "recognition", "good", "due")
        rows = db.get_pending_grades("l1")
        assert len(rows) == 2
        collocation_ids = {r["collocation_id"] for r in rows}
        assert collocation_ids == {1, 2}
