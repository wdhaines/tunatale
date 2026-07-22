"""Tests for DbReviewsMixin (lesson_reviews table)."""

from datetime import UTC, datetime

import pytest

from app.srs.database import SRSDatabase


@pytest.fixture
def db():
    d = SRSDatabase(":memory:")
    yield d
    d.close()


class TestRecordReview:
    def test_single_review(self, db):
        db.record_review("lesson-1")
        assert db.latest_review_at("lesson-1") is not None

    def test_reviewed_at_is_isoformat(self, db):
        before = datetime.now(UTC).isoformat()
        db.record_review("lesson-1")
        after = datetime.now(UTC).isoformat()
        assert before <= db.latest_review_at("lesson-1") <= after

    def test_two_reviews_both_recorded(self, db):
        db.record_review("lesson-1")
        db.record_review("lesson-1")
        with db._get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM lesson_reviews WHERE lesson_id = 'lesson-1'").fetchone()[0]
        assert count == 2


class TestLatestReviewAt:
    def test_none_when_empty(self, db):
        assert db.latest_review_at("lesson-1") is None

    def test_returns_max_when_multiple(self, db):
        db.record_review("lesson-1")
        db.record_review("lesson-1")
        result = db.latest_review_at("lesson-1")
        assert result is not None
        with db._get_conn() as conn:
            all_reviews = conn.execute("SELECT reviewed_at FROM lesson_reviews WHERE lesson_id = 'lesson-1'").fetchall()
        all_timestamps = [r["reviewed_at"] for r in all_reviews]
        assert result == max(all_timestamps)

    def test_per_lesson_isolated(self, db):
        db.record_review("a")
        db.record_review("b")
        assert db.latest_review_at("a") is not None
        assert db.latest_review_at("b") is not None
        assert db.latest_review_at("c") is None
