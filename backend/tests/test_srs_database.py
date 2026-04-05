"""SRS database tests."""

from datetime import date, timedelta

from app.models.srs_item import SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase


def _unit(text: str = "dober dan", translation: str = "good day") -> SyntacticUnit:
    return SyntacticUnit(text=text, translation=translation, word_count=2, difficulty=1, source="corpus")


class TestCRUD:
    """Tests for basic add/get/update collocation operations."""

    def test_add_and_get_collocation(self, srs_db):
        unit = _unit()
        srs_db.add_collocation(unit, language_code="sl")
        retrieved = srs_db.get_collocation("dober dan")
        assert retrieved is not None
        assert retrieved.syntactic_unit.text == "dober dan"

    def test_add_duplicate_does_not_raise(self, srs_db):
        unit = _unit()
        srs_db.add_collocation(unit, language_code="sl")
        srs_db.add_collocation(unit, language_code="sl")  # should not raise

    def test_get_nonexistent_returns_none(self, srs_db):
        assert srs_db.get_collocation("nonexistent") is None

    def test_update_collocation(self, srs_db):
        unit = _unit()
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("dober dan")
        item.reps = 5
        item.stability = 20.0
        item.state = SRSState.REVIEW
        srs_db.update_collocation(item)

        updated = srs_db.get_collocation("dober dan")
        assert updated.reps == 5
        assert updated.stability == 20.0
        assert updated.state == SRSState.REVIEW


class TestDueQueries:
    """Tests for due/new collocation queries."""

    def test_get_due_collocations_includes_overdue(self, srs_db):
        unit = _unit()
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("dober dan")
        item.due_date = date.today() - timedelta(days=1)
        item.state = SRSState.REVIEW
        srs_db.update_collocation(item)

        due = srs_db.get_due_collocations(date.today())
        assert any(i.syntactic_unit.text == "dober dan" for i in due)

    def test_get_due_collocations_excludes_future(self, srs_db):
        unit = _unit()
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("dober dan")
        item.due_date = date.today() + timedelta(days=10)
        item.state = SRSState.REVIEW
        srs_db.update_collocation(item)

        due = srs_db.get_due_collocations(date.today())
        assert not any(i.syntactic_unit.text == "dober dan" for i in due)

    def test_get_new_collocations(self, srs_db):
        srs_db.add_collocation(_unit("dober dan"), language_code="sl")
        srs_db.add_collocation(_unit("hvala lepa", "thank you"), language_code="sl")

        new = srs_db.get_new_collocations(limit=10)
        assert len(new) == 2

    def test_count_collocations(self, srs_db):
        assert srs_db.count_collocations() == 0
        srs_db.add_collocation(_unit("dober dan"), language_code="sl")
        assert srs_db.count_collocations() == 1


class TestViolations:
    """Tests for recording and querying SRS violations."""

    def test_record_violation(self, srs_db):
        srs_db.record_violation(
            collocation_text="dober dan", day_number=1, violation_type="unused", details="not used in story"
        )
        violations = srs_db.get_violations(collocation_text="dober dan")
        assert len(violations) == 1
        assert violations[0]["violation_type"] == "unused"

    def test_get_violations_empty(self, srs_db):
        assert srs_db.get_violations("nonexistent") == []


class TestFileBased:
    """Tests for file-backed SRS database persistence."""

    def test_file_based_database(self, tmp_path):
        db_path = tmp_path / "test.db"
        db = SRSDatabase(str(db_path))
        unit = _unit()
        db.add_collocation(unit, language_code="sl")
        assert db.get_collocation("dober dan") is not None
