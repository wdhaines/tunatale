"""Tests for dirty_fields tracking on collocation text/translation edits."""

from __future__ import annotations

import pytest

from app.models.srs_item import Direction
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase


def _unit(text: str, translation: str = "test") -> SyntacticUnit:
    return SyntacticUnit(text=text, translation=translation, word_count=1, difficulty=1, source="corpus")


@pytest.fixture
def db():
    with SRSDatabase(":memory:") as d:
        yield d


def _dirty_fields(db: SRSDatabase, item_id: int) -> str:
    with db._get_conn() as conn:
        row = conn.execute("SELECT dirty_fields FROM collocations WHERE id = ?", (item_id,)).fetchone()
    return row["dirty_fields"] if row else ""


class TestDirtyFieldsOnPatch:
    def test_no_changes_leaves_dirty_fields_empty(self, db):
        db.add_collocation(_unit("okno", "window"), language_code="sl")
        rows, _ = db.list_collocations(search="okno", limit=1)
        item_id = rows[0][0]
        db.update_collocation_fields(item_id, text="okno", translation="window")
        assert _dirty_fields(db, item_id) == ""

    def test_translation_change_marks_translation_dirty(self, db):
        db.add_collocation(_unit("okno", "window"), language_code="sl")
        rows, _ = db.list_collocations(search="okno", limit=1)
        item_id = rows[0][0]
        db.update_collocation_fields(item_id, text="okno", translation="pane")
        assert _dirty_fields(db, item_id) == "translation"

    def test_text_change_marks_text_dirty(self, db):
        db.add_collocation(_unit("okno", "window"), language_code="sl")
        rows, _ = db.list_collocations(search="okno", limit=1)
        item_id = rows[0][0]
        db.update_collocation_fields(item_id, text="okno2", translation="window")
        assert _dirty_fields(db, item_id) == "text"

    def test_both_changed_marks_both_dirty(self, db):
        db.add_collocation(_unit("okno", "window"), language_code="sl")
        rows, _ = db.list_collocations(search="okno", limit=1)
        item_id = rows[0][0]
        db.update_collocation_fields(item_id, text="okno2", translation="pane")
        assert _dirty_fields(db, item_id) == "text,translation"

    def test_second_edit_merges_into_existing_dirty(self, db):
        db.add_collocation(_unit("okno", "window"), language_code="sl")
        rows, _ = db.list_collocations(search="okno", limit=1)
        item_id = rows[0][0]
        # First edit: only translation
        db.update_collocation_fields(item_id, text="okno", translation="pane")
        # Second edit: only text
        db.update_collocation_fields(item_id, text="okno2", translation="pane")
        assert _dirty_fields(db, item_id) == "text,translation"


class TestListDirty:
    def test_no_dirty_returns_empty(self, db):
        db.add_collocation(_unit("beseda"), language_code="sl")
        assert db.list_dirty() == []

    def test_after_schedule_direction_appears_dirty(self, db):
        from app.srs.fsrs import Rating, schedule

        db.add_collocation(_unit("voda", "water"), language_code="sl")
        item = db.get_collocation("voda")
        assert item is not None
        updated = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION)
        db.update_direction(item.guid, Direction.RECOGNITION, updated.directions[Direction.RECOGNITION])

        dirty = db.list_dirty()
        assert len(dirty) == 1
        guid, direction, ds = dirty[0]
        assert direction == Direction.RECOGNITION
        assert ds.dirty_fsrs is True

    def test_list_dirty_filtered_by_direction(self, db):
        from app.srs.fsrs import Rating, schedule

        db.add_collocation(_unit("voda", "water"), language_code="sl")
        item = db.get_collocation("voda")
        assert item is not None
        updated = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION)
        db.update_direction(item.guid, Direction.RECOGNITION, updated.directions[Direction.RECOGNITION])

        assert len(db.list_dirty(direction=Direction.RECOGNITION)) == 1
        assert len(db.list_dirty(direction=Direction.PRODUCTION)) == 0


class TestMarkDirectionClean:
    def test_mark_clean_clears_dirty_fsrs(self, db):
        from app.srs.fsrs import Rating, schedule

        db.add_collocation(_unit("voda", "water"), language_code="sl")
        item = db.get_collocation("voda")
        assert item is not None
        updated = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION)
        db.update_direction(item.guid, Direction.RECOGNITION, updated.directions[Direction.RECOGNITION])

        assert len(db.list_dirty()) == 1
        db.mark_direction_clean(item.guid, Direction.RECOGNITION)
        assert db.list_dirty() == []

    def test_mark_clean_sets_last_synced_at(self, db):
        from app.srs.fsrs import Rating, schedule

        db.add_collocation(_unit("voda", "water"), language_code="sl")
        item = db.get_collocation("voda")
        assert item is not None
        updated = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION)
        db.update_direction(item.guid, Direction.RECOGNITION, updated.directions[Direction.RECOGNITION])

        db.mark_direction_clean(item.guid, Direction.RECOGNITION)

        with db._get_conn() as conn:
            row = conn.execute(
                "SELECT d.last_synced_at FROM collocations c "
                "JOIN collocation_directions d ON d.collocation_id = c.id "
                "WHERE c.guid = ? AND d.direction = ?",
                (item.guid, Direction.RECOGNITION.value),
            ).fetchone()
        assert row is not None
        assert row["last_synced_at"] is not None

    def test_mark_clean_unknown_guid_is_no_op(self, db):
        db.mark_direction_clean("nonexistent000000", Direction.RECOGNITION)  # should not raise
