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


class TestLemmaSupport:
    """Tests for lemma column and get_collocation_by_lemma."""

    def test_add_with_lemma_and_retrieve_by_lemma(self, srs_db):
        unit = SyntacticUnit(
            text="zdravo", translation="hello", word_count=1, difficulty=1, source="llm", lemma="zdravo"
        )
        srs_db.add_collocation(unit, language_code="sl")
        retrieved = srs_db.get_collocation_by_lemma("zdravo")
        assert retrieved is not None
        assert retrieved.syntactic_unit.text == "zdravo"
        assert retrieved.syntactic_unit.lemma == "zdravo"

    def test_get_by_lemma_returns_none_for_unknown(self, srs_db):
        assert srs_db.get_collocation_by_lemma("unknown_lemma") is None

    def test_add_without_lemma_not_found_by_lemma(self, srs_db):
        unit = _unit("dober dan")  # no lemma set
        srs_db.add_collocation(unit, language_code="sl")
        # lemma is NULL → get_collocation_by_lemma should not return it
        assert srs_db.get_collocation_by_lemma("dober dan") is None

    def test_init_schema_is_idempotent(self, tmp_path):
        db_path = tmp_path / "test.db"
        db1 = SRSDatabase(str(db_path))
        unit = _unit()
        db1.add_collocation(unit, language_code="sl")
        # Re-opening triggers _init_schema again (runs ALTER TABLE again — should not error)
        db2 = SRSDatabase(str(db_path))
        assert db2.get_collocation("dober dan") is not None

    def test_lemma_on_retrieved_item_without_lemma_is_none(self, srs_db):
        unit = _unit("banka")
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("banka")
        assert item.syntactic_unit.lemma is None
