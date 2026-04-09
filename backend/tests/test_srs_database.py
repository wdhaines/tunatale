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


class TestAdminMutations:
    """Tests for admin mutation methods."""

    def test_get_collocation_by_id(self, srs_db):
        srs_db.add_collocation(_unit("zdravo", "hello"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id, item, lang = rows[0]
        result = srs_db.get_collocation_by_id(row_id)
        assert result is not None
        rid, ritem, rlang = result
        assert rid == row_id
        assert ritem.syntactic_unit.text == "zdravo"
        assert rlang == "sl"

    def test_get_collocation_by_id_missing_returns_none(self, srs_db):
        assert srs_db.get_collocation_by_id(9999) is None

    def test_update_collocation_fields_changes_text_and_translation(self, srs_db):
        srs_db.add_collocation(_unit("zdravo", "hello"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id, _, _ = rows[0]
        srs_db.update_collocation_fields(row_id, text="zdravo!", translation="hello!")
        result = srs_db.get_collocation_by_id(row_id)
        assert result[1].syntactic_unit.text == "zdravo!"
        assert result[1].syntactic_unit.translation == "hello!"

    def test_update_collocation_fields_duplicate_text_raises(self, srs_db):
        srs_db.add_collocation(_unit("a", "aa"), language_code="sl")
        srs_db.add_collocation(_unit("b", "bb"), language_code="sl")
        rows, _ = srs_db.list_collocations(order_by="text")
        id_b = next(r[0] for r in rows if r[1].syntactic_unit.text == "b")
        import pytest

        with pytest.raises(ValueError, match="already exists"):
            srs_db.update_collocation_fields(id_b, text="a", translation="dup")

    def test_delete_collocation_removes_row_and_violations(self, srs_db):
        srs_db.add_collocation(_unit("nasvidenje", "goodbye"), language_code="sl")
        srs_db.record_violation("nasvidenje", 1, "unused")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        srs_db.delete_collocation(row_id)
        assert srs_db.get_collocation("nasvidenje") is None
        assert srs_db.get_violations("nasvidenje") == []

    def test_bulk_delete_returns_count_and_removes_rows(self, srs_db):
        srs_db.add_collocation(_unit("a", "aa"), language_code="sl")
        srs_db.add_collocation(_unit("b", "bb"), language_code="sl")
        srs_db.add_collocation(_unit("c", "cc"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        ids = [r[0] for r in rows[:2]]
        deleted = srs_db.delete_collocations(ids)
        assert deleted == 2
        assert srs_db.count_collocations() == 1

    def test_reset_collocation_zeros_scheduling_fields(self, srs_db):
        srs_db.add_collocation(_unit("hvala", "thank you"), language_code="sl")
        item = srs_db.get_collocation("hvala")
        item.reps = 5
        item.lapses = 2
        item.state = SRSState.REVIEW
        item.stability = 30.0
        srs_db.update_collocation(item)

        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        srs_db.reset_collocation(row_id)
        reset = srs_db.get_collocation("hvala")
        assert reset.reps == 0
        assert reset.lapses == 0
        assert reset.state == SRSState.NEW
        assert reset.last_review is None

    def test_suspend_then_unsuspend_flow(self, srs_db):
        srs_db.add_collocation(_unit("lep", "nice"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]

        srs_db.set_suspended(row_id, True)
        item = srs_db.get_collocation("lep")
        assert item.state == SRSState.SUSPENDED

        srs_db.set_suspended(row_id, False)
        item = srs_db.get_collocation("lep")
        assert item.state == SRSState.NEW


class TestListCollocations:
    """Tests for the paginated list_collocations admin method."""

    def _seed(self, srs_db, texts):
        for t in texts:
            srs_db.add_collocation(_unit(t, f"trans_{t}"), language_code="sl")

    def test_list_collocations_pagination(self, srs_db):
        self._seed(srs_db, ["a", "b", "c", "d", "e"])
        rows, total = srs_db.list_collocations(limit=2, offset=2)
        assert len(rows) == 2
        assert total == 5

    def test_list_collocations_search_matches_text_or_translation(self, srs_db):
        srs_db.add_collocation(_unit("zdravo", "hello"), language_code="sl")
        srs_db.add_collocation(_unit("nasvidenje", "goodbye"), language_code="sl")
        rows, total = srs_db.list_collocations(search="hello")
        assert total == 1
        assert rows[0][1].syntactic_unit.text == "zdravo"

    def test_list_collocations_filter_by_state(self, srs_db):
        srs_db.add_collocation(_unit("a", "a"), language_code="sl")
        srs_db.add_collocation(_unit("b", "b"), language_code="sl")
        item = srs_db.get_collocation("a")
        item.state = SRSState.REVIEW
        srs_db.update_collocation(item)

        rows, total = srs_db.list_collocations(state=SRSState.REVIEW)
        assert total == 1
        assert rows[0][1].syntactic_unit.text == "a"

    def test_list_collocations_sort_by_due_date_desc(self, srs_db):
        self._seed(srs_db, ["a", "b", "c"])
        item_a = srs_db.get_collocation("a")
        item_a.due_date = date.today() - timedelta(days=5)
        srs_db.update_collocation(item_a)
        item_c = srs_db.get_collocation("c")
        item_c.due_date = date.today() + timedelta(days=5)
        srs_db.update_collocation(item_c)

        rows, _ = srs_db.list_collocations(order_by="due_date", order_dir="desc")
        texts = [r[1].syntactic_unit.text for r in rows]
        assert texts.index("c") < texts.index("a")

    def test_list_collocations_returns_total_count_independent_of_limit(self, srs_db):
        self._seed(srs_db, ["a", "b", "c", "d", "e"])
        rows, total = srs_db.list_collocations(limit=2, offset=0)
        assert total == 5
        assert len(rows) == 2

    def test_list_collocations_rejects_unknown_order_by(self, srs_db):
        import pytest

        with pytest.raises(ValueError):
            srs_db.list_collocations(order_by="injected_column")


class TestSuspended:
    """Tests for SUSPENDED state filtering."""

    def test_suspended_items_excluded_from_due_queue(self, srs_db):
        unit = _unit("hvala", "thank you")
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("hvala")
        item.due_date = date.today() - timedelta(days=1)
        item.state = SRSState.REVIEW
        srs_db.update_collocation(item)

        before = srs_db.count_due_collocations(date.today())
        assert before == 1

        item.state = SRSState.SUSPENDED
        srs_db.update_collocation(item)

        due = srs_db.get_due_collocations(date.today())
        assert not any(i.syntactic_unit.text == "hvala" for i in due)
        assert srs_db.count_due_collocations(date.today()) == 0

    def test_suspended_state_roundtrip(self, srs_db):
        unit = _unit("nasvidenje", "goodbye")
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("nasvidenje")
        item.state = SRSState.SUSPENDED
        srs_db.update_collocation(item)

        retrieved = srs_db.get_collocation("nasvidenje")
        assert retrieved.state == SRSState.SUSPENDED


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

    def test_delete_collocations_returns_zero_for_empty_list(self, srs_db):
        assert srs_db.delete_collocations([]) == 0

    def test_list_collocations_raises_for_invalid_order_dir(self, srs_db):
        import pytest

        with pytest.raises(ValueError, match="Invalid order_dir"):
            srs_db.list_collocations(order_dir="sideways")


class TestDeleteEdgeCases:
    """Tests for delete edge-case branches."""

    def test_delete_nonexistent_collocation_is_noop(self, srs_db):
        """delete_collocation with a nonexistent ID silently does nothing."""
        srs_db.delete_collocation(99999)  # should not raise

    def test_delete_collocations_with_all_nonexistent_ids(self, srs_db):
        """delete_collocations with IDs that don't match any rows returns 0."""
        deleted = srs_db.delete_collocations([99999, 88888])
        assert deleted == 0


class TestFileDatabaseWriteOperations:
    """Exercise all write methods with a file-backed DB to cover if self._in_memory: False branches."""

    def test_file_db_write_operations(self, tmp_path):
        db = SRSDatabase(str(tmp_path / "test.db"))

        # add_collocation (covers else: self._conn.commit() via False path already handled)
        db.add_collocation(_unit("zdravo", "hello"), language_code="sl")

        # update_collocation (167->exit False branch: file-DB skips self._conn.commit())
        item = db.get_collocation("zdravo")
        item.reps = 1
        db.update_collocation(item)

        # record_violation (179->exit False branch)
        db.record_violation("zdravo", 1, "unused")

        # update_collocation_fields (237->exit False branch)
        rows, _ = db.list_collocations()
        row_id = rows[0][0]
        db.update_collocation_fields(row_id, text="zdravo!", translation="hello!")

        # delete_collocation (249->exit False branch)
        db.record_violation("zdravo!", 2, "unused")
        db.delete_collocation(row_id)

        # delete_collocations (264->266 False branch)
        db.add_collocation(_unit("hvala", "thanks"), language_code="sl")
        rows, _ = db.list_collocations()
        ids = [r[0] for r in rows]
        db.delete_collocations(ids)

        # reset_collocation (281->exit False branch)
        db.add_collocation(_unit("prosim", "please"), language_code="sl")
        rows, _ = db.list_collocations()
        row_id = rows[0][0]
        db.reset_collocation(row_id)

        # set_suspended (292->exit False branch)
        db.set_suspended(row_id, True)
        db.set_suspended(row_id, False)

        # Verify persistence works
        assert db.get_collocation("prosim") is not None
