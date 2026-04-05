"""Content enforcer tests."""

import pytest

from app.generation.enforcer import ContentEnforcer
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase


@pytest.fixture
def db():
    with SRSDatabase(":memory:") as database:
        # Add some known collocations
        unit = SyntacticUnit(text="dober dan", translation="good day", word_count=2, difficulty=1, source="corpus")
        database.add_collocation(unit, language_code="sl")
        unit2 = SyntacticUnit(text="hvala lepa", translation="thank you", word_count=2, difficulty=1, source="corpus")
        database.add_collocation(unit2, language_code="sl")
        yield database


@pytest.fixture
def enforcer(db):
    return ContentEnforcer(srs_db=db)


class TestContentEnforcer:
    """Tests for ContentEnforcer: replacement dict, enforce(), boundary handling."""

    def test_replacement_dict_built_from_srs(self, enforcer):
        replacements = enforcer.get_replacement_dict()
        assert "good day" in replacements
        assert replacements["good day"] == "dober dan"

    def test_replacement_dict_no_hardcoded_entries(self, enforcer):
        replacements = enforcer.get_replacement_dict()
        # No hardcoded words like "water" → "tubig" should exist
        assert "water" not in replacements

    def test_enforce_replaces_known_l1_with_l2(self, enforcer):
        text = "She said good day to the waiter."
        result = enforcer.enforce(text)
        assert "dober dan" in result
        assert "good day" not in result

    def test_enforce_case_insensitive_matching(self, enforcer):
        text = "He said GOOD DAY and smiled."
        result = enforcer.enforce(text)
        assert "dober dan" in result.lower()

    def test_enforce_word_boundary_prevents_partial_replacement(self, enforcer):
        """'good day' should not partially replace 'good days'."""
        text = "Have good days ahead."
        result = enforcer.enforce(text)
        # Should NOT partially replace "good days" → "dober dans"
        assert "dober dans" not in result

    def test_enforce_preserves_unknown_words(self, enforcer):
        text = "The café is lovely."
        result = enforcer.enforce(text)
        assert "lovely" in result

    def test_enforce_records_violation(self, enforcer, db):
        text = "She said good day and thank you."
        enforcer.enforce(text, day_number=1)
        # No exception should be raised - violations are recorded if any

    def test_enforce_empty_text(self, enforcer):
        result = enforcer.enforce("")
        assert result == ""

    def test_no_replacements_when_db_empty(self):
        with SRSDatabase(":memory:") as db:
            enforcer = ContentEnforcer(srs_db=db)
            text = "Hello, how are you?"
            result = enforcer.enforce(text)
            assert result == text
