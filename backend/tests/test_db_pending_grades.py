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

    def test_staging_under_a_second_lesson_leaves_the_first_lessons_row_alone(self, db):
        """REWRITTEN at v42 (F-23). This case previously asserted the opposite —
        `len(get_pending_grades("l1")) == 0  # upserted to l2` — which pinned the
        defect as intended behaviour: the global key plus `lesson_id =
        excluded.lesson_id` re-parented the row. On live data that cost day-5 60
        of its 145 staged rows the first time a second lesson was listened to.
        """
        db.stage_pending_grade("l1", 1, "recognition", "good", "due")
        db.stage_pending_grade("l2", 1, "recognition", "hard", "ahead")

        l1_rows = db.get_pending_grades("l1")
        assert len(l1_rows) == 1, "l2's stage stole l1's row"
        assert l1_rows[0]["rating"] == "good", "l1 keeps its own rating"
        l2_rows = db.get_pending_grades("l2")
        assert len(l2_rows) == 1
        assert l2_rows[0]["rating"] == "hard"


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


class TestClearPendingGrade:
    def test_clears_existing(self, db):
        db.stage_pending_grade("l1", 1, "recognition", "good", "due")
        db.clear_pending_grade(1, "recognition")
        assert db.get_pending_grade(1, "recognition") is None

    def test_does_not_raise_when_missing(self, db):
        db.clear_pending_grade(999, "recognition")  # should not raise

    def test_clears_by_guid(self, db):
        from app.models.syntactic_unit import SyntacticUnit

        db.add_collocation(
            SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="test"),
            language_code="sl",
        )
        item = db.get_collocation("banka")
        cid = db.get_collocation_id_by_guid(item.guid)
        db.stage_pending_grade("l1", cid, "recognition", "good", "due")

        db.clear_pending_grade_by_guid(item.guid, "recognition")

        assert db.get_pending_grade(cid, "recognition") is None

    def test_clear_by_guid_ignores_an_unknown_guid(self, db):
        db.stage_pending_grade("l1", 1, "recognition", "good", "due")

        db.clear_pending_grade_by_guid("no-such-guid", "recognition")

        assert db.get_pending_grade(1, "recognition") is not None

    def test_clear_by_guid_is_direction_scoped(self, db):
        from app.models.syntactic_unit import SyntacticUnit

        db.add_collocation(
            SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="test"),
            language_code="sl",
        )
        item = db.get_collocation("banka")
        cid = db.get_collocation_id_by_guid(item.guid)
        db.stage_pending_grade("l1", cid, "recognition", "good", "due")
        db.stage_pending_grade("l1", cid, "production", "good", "due")

        db.clear_pending_grade_by_guid(item.guid, "recognition")

        assert db.get_pending_grade(cid, "recognition") is None
        assert db.get_pending_grade(cid, "production") is not None

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


class TestPerLessonBucket:
    """F-23: the bucket is per-lesson, but grading is card-scoped.

    That asymmetry is deliberate and load-bearing:

    - **INSERT** is keyed ``(lesson_id, collocation_id, direction)`` — two lessons
      sharing a word each keep their own staged row.
    - **DELETE** stays keyed ``(collocation_id, direction)`` — grading the word
      anywhere clears it everywhere, so a lesson that drained to zero stays at
      zero.

    Making the delete lesson-scoped "for consistency" is the one edit that
    reintroduces the bug the user named: a grade in day-5 would leave day-4 armed
    for a card that no longer needs reviewing.
    """

    def test_a_second_lessons_listen_does_not_steal_the_first_lessons_rows(self, db):
        """G4b — the discriminating oracle for F-23.

        Measured on live data 2026-08-05: day-5 held 145 staged rows and was the
        only lesson with any. One ordinary listen on day-4 took it to 85 and
        re-parented 60 rows — `UNIQUE(collocation_id, direction)` is global, and
        the upsert's `lesson_id = excluded.lesson_id` reassigns ownership.
        """
        for cid in (1, 2, 3):
            db.stage_pending_grade("day-5", cid, "recognition", "good", "due")
        before = {(r["collocation_id"], r["direction"]) for r in db.get_pending_grades("day-5")}

        # day-4 listens. It shares cards 2 and 3 with day-5 and stages one of its own.
        db.clear_pending_grades_for_lesson("day-4")
        for cid in (2, 3, 4):
            db.stage_pending_grade("day-4", cid, "recognition", "good", "due")

        after = {(r["collocation_id"], r["direction"]) for r in db.get_pending_grades("day-5")}
        assert after == before, "day-4's listen re-parented day-5's shared rows"
        assert db.count_pending_grades("day-5") == 3
        assert db.count_pending_grades("day-4") == 3
        assert after & {(r["collocation_id"], r["direction"]) for r in db.get_pending_grades("day-4")}, (
            "a card shared by two lessons must sit in BOTH buckets — duplicates across lessons are legal"
        )

    def test_a_relisten_replaces_only_its_own_lessons_rows(self, db):
        """G4a — reset-then-stage, asserted on the SET, never the count.

        Both listens below stage three cards, so a count assertion passes even if
        a skipped row survives. Card 2 is staged by the first listen and skipped
        by the second: it is the row with teeth.
        """
        for cid in (1, 2, 3):
            db.stage_pending_grade("day-5", cid, "recognition", "good", "due")

        db.clear_pending_grades_for_lesson("day-5")
        for cid in (1, 3, 4):
            db.stage_pending_grade("day-5", cid, "recognition", "again", "ahead")

        rows = db.get_pending_grades("day-5")
        assert {(r["collocation_id"], r["direction"]) for r in rows} == {
            (1, "recognition"),
            (3, "recognition"),
            (4, "recognition"),
        }, "a row the second listen did not stage survived from the first"
        assert all(r["rating"] == "again" for r in rows), "survivors carry the second listen's rating"
        assert all(r["grade_class"] == "ahead" for r in rows), "survivors carry the second listen's grade_class"

    def test_grading_a_shared_word_clears_it_from_every_lesson(self, db):
        """The delete stays card-scoped. Grading in one lesson releases the
        staging everywhere, because the card genuinely no longer needs review.
        """
        db.stage_pending_grade("day-5", 7, "recognition", "good", "due")
        db.stage_pending_grade("day-4", 7, "recognition", "good", "due")

        db.clear_pending_grade(7, "recognition")

        assert db.count_pending_grades("day-5") == 0
        assert db.count_pending_grades("day-4") == 0

    def test_another_lessons_listen_does_not_re_arm_a_drained_bucket(self, db):
        """The user's requirement, verbatim (2026-08-05):

            "I just don't want that word reopening a bucket in a lesson with no
            items yesterday, which as I recall was the original bug."

        Only listening to a lesson may add to its bucket, and grading only ever
        removes. So a lesson that drained to zero stays at zero no matter what
        other lessons do with the same word.
        """
        db.stage_pending_grade("day-5", 7, "recognition", "good", "due")
        db.clear_pending_grade(7, "recognition")
        assert db.count_pending_grades("day-5") == 0, "premise: day-5 has drained"

        db.clear_pending_grades_for_lesson("day-4")
        db.stage_pending_grade("day-4", 7, "recognition", "good", "due")

        assert db.count_pending_grades("day-5") == 0, "day-4's listen re-armed a drained bucket"
        assert db.count_pending_grades("day-4") == 1
