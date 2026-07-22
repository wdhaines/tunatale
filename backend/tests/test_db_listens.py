"""Tests for DbListensMixin (lesson_listens table)."""

from datetime import UTC, datetime

import pytest

from app.srs.database import SRSDatabase


@pytest.fixture
def db():
    d = SRSDatabase(":memory:")
    yield d
    d.close()


class TestRecordListen:
    def test_single_listen(self, db):
        db.record_listen("lesson-1")
        assert db.has_listen("lesson-1")
        assert db.count_listens("lesson-1") == 1

    def test_two_listens_increment_count(self, db):
        db.record_listen("lesson-1")
        db.record_listen("lesson-1")
        assert db.count_listens("lesson-1") == 2

    def test_source_default_listen(self, db):
        db.record_listen("lesson-1")
        with db._get_conn() as conn:
            row = conn.execute("SELECT source FROM lesson_listens WHERE lesson_id = 'lesson-1'").fetchone()
        assert row["source"] == "listen"

    def test_source_import_stored_verbatim(self, db):
        db.record_listen("lesson-1", source="import")
        with db._get_conn() as conn:
            row = conn.execute("SELECT source FROM lesson_listens WHERE lesson_id = 'lesson-1'").fetchone()
        assert row["source"] == "import"

    def test_listened_at_is_isoformat(self, db):
        before = datetime.now(UTC).isoformat()
        db.record_listen("lesson-1")
        after = datetime.now(UTC).isoformat()
        with db._get_conn() as conn:
            row = conn.execute("SELECT listened_at FROM lesson_listens WHERE lesson_id = 'lesson-1'").fetchone()
        assert before <= row["listened_at"] <= after


class TestHasListen:
    def test_false_when_empty(self, db):
        assert not db.has_listen("nonexistent")

    def test_true_after_record(self, db):
        db.record_listen("lesson-1")
        assert db.has_listen("lesson-1")

    def test_false_for_different_lesson(self, db):
        db.record_listen("lesson-1")
        assert not db.has_listen("lesson-2")


class TestCountListens:
    def test_zero_when_empty(self, db):
        assert db.count_listens("lesson-1") == 0

    def test_counts_per_lesson(self, db):
        db.record_listen("a")
        db.record_listen("a")
        db.record_listen("b")
        assert db.count_listens("a") == 2
        assert db.count_listens("b") == 1


class TestGetListenedLessons:
    def test_empty_when_no_listens(self, db):
        assert db.get_listened_lessons() == []

    def test_single_lesson(self, db):
        db.record_listen("lesson-1")
        result = db.get_listened_lessons()
        assert len(result) == 1
        assert result[0]["lesson_id"] == "lesson-1"
        assert result[0]["listen_count"] == 1
        assert "last_listened_at" in result[0]

    def test_aggregation_across_lessons(self, db):
        db.record_listen("a")
        db.record_listen("a")
        db.record_listen("b")
        result = db.get_listened_lessons()
        by_id = {r["lesson_id"]: r for r in result}
        assert by_id["a"]["listen_count"] == 2
        assert by_id["b"]["listen_count"] == 1

    def test_sorted_by_last_listened_at_desc(self, db):
        db.record_listen("old-lesson")
        db.record_listen("new-lesson")
        result = db.get_listened_lessons()
        assert result[0]["lesson_id"] == "new-lesson"
        assert result[1]["lesson_id"] == "old-lesson"


class TestLatestListenAt:
    def test_none_when_empty(self, db):
        assert db.latest_listen_at("lesson-1") is None

    def test_returns_max_when_multiple(self, db):
        db.record_listen("lesson-1")
        db.record_listen("lesson-1")
        result = db.latest_listen_at("lesson-1")
        assert result is not None
        with db._get_conn() as conn:
            all_listens = conn.execute("SELECT listened_at FROM lesson_listens WHERE lesson_id = 'lesson-1'").fetchall()
        all_timestamps = [r["listened_at"] for r in all_listens]
        assert result == max(all_timestamps)

    def test_per_lesson_isolated(self, db):
        db.record_listen("a")
        db.record_listen("b")
        assert db.latest_listen_at("a") is not None
        assert db.latest_listen_at("b") is not None
        assert db.latest_listen_at("c") is None
