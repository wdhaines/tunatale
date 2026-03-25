"""SRS database tests."""

from datetime import date, timedelta

import pytest

from app.models.srs_item import SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase


@pytest.fixture
def db():
    with SRSDatabase(":memory:") as database:
        yield database


def _unit(text: str = "dober dan", translation: str = "good day") -> SyntacticUnit:
    return SyntacticUnit(text=text, translation=translation, word_count=2, difficulty=1, source="corpus")


# ── CRUD ──────────────────────────────────────────────────────────────────


def test_add_and_get_collocation(db):
    unit = _unit()
    db.add_collocation(unit, language_code="sl")
    retrieved = db.get_collocation("dober dan")
    assert retrieved is not None
    assert retrieved.syntactic_unit.text == "dober dan"


def test_add_duplicate_does_not_raise(db):
    unit = _unit()
    db.add_collocation(unit, language_code="sl")
    db.add_collocation(unit, language_code="sl")  # should not raise


def test_get_nonexistent_returns_none(db):
    assert db.get_collocation("nonexistent") is None


def test_update_collocation(db):
    unit = _unit()
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation("dober dan")
    item.reps = 5
    item.stability = 20.0
    item.state = SRSState.REVIEW
    db.update_collocation(item)

    updated = db.get_collocation("dober dan")
    assert updated.reps == 5
    assert updated.stability == 20.0
    assert updated.state == SRSState.REVIEW


# ── Due queries ───────────────────────────────────────────────────────────


def test_get_due_collocations_includes_overdue(db):
    unit = _unit()
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation("dober dan")
    item.due_date = date.today() - timedelta(days=1)
    item.state = SRSState.REVIEW
    db.update_collocation(item)

    due = db.get_due_collocations(date.today())
    assert any(i.syntactic_unit.text == "dober dan" for i in due)


def test_get_due_collocations_excludes_future(db):
    unit = _unit()
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation("dober dan")
    item.due_date = date.today() + timedelta(days=10)
    item.state = SRSState.REVIEW
    db.update_collocation(item)

    due = db.get_due_collocations(date.today())
    assert not any(i.syntactic_unit.text == "dober dan" for i in due)


def test_get_new_collocations(db):
    db.add_collocation(_unit("dober dan"), language_code="sl")
    db.add_collocation(_unit("hvala lepa", "thank you"), language_code="sl")

    new = db.get_new_collocations(limit=10)
    assert len(new) == 2


# ── Violations ────────────────────────────────────────────────────────────


def test_record_violation(db):
    db.record_violation(
        collocation_text="dober dan", day_number=1, violation_type="unused", details="not used in story"
    )
    violations = db.get_violations(collocation_text="dober dan")
    assert len(violations) == 1
    assert violations[0]["violation_type"] == "unused"


def test_get_violations_empty(db):
    assert db.get_violations("nonexistent") == []


# ── File-based database ───────────────────────────────────────────────────


def test_file_based_database(tmp_path):
    db_path = tmp_path / "test.db"
    db = SRSDatabase(str(db_path))
    unit = _unit()
    db.add_collocation(unit, language_code="sl")
    assert db.get_collocation("dober dan") is not None


# ── Count queries ─────────────────────────────────────────────────────────


def test_count_collocations(db):
    assert db.count_collocations() == 0
    db.add_collocation(_unit("dober dan"), language_code="sl")
    assert db.count_collocations() == 1
