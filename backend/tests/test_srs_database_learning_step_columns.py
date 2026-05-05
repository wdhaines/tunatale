"""Tests for learning step columns (left, due_at) on collocation_directions."""

from datetime import datetime

from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit


def _unit(text: str = "test_word", translation: str = "test") -> SyntacticUnit:
    return SyntacticUnit(text=text, translation=translation, word_count=2, difficulty=1, source="corpus")


class TestLearningStepColumns:
    """Verify left and due_at columns exist and round-trip correctly."""

    def test_direction_state_has_left_field(self):
        """DirectionState should have left: int | None field."""
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=datetime.now().date(),
        )
        assert hasattr(ds, "left")
        assert ds.left is None

    def test_direction_state_has_due_at_field(self):
        """DirectionState should have due_at: datetime | None field."""
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=datetime.now().date(),
        )
        assert hasattr(ds, "due_at")
        assert ds.due_at is None

    def test_update_direction_round_trips_left(self, srs_db):
        """update_direction preserves left value through round-trip."""
        unit = _unit("word1", "translation1")
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("word1")
        guid = item.guid

        rec_dir = item.directions[Direction.RECOGNITION]
        rec_dir.left = 2002  # Anki packed left: 2 steps left, 2 total steps
        srs_db.update_direction(guid, Direction.RECOGNITION, rec_dir)

        reloaded = srs_db.get_collocation("word1")
        assert reloaded.directions[Direction.RECOGNITION].left == 2002

    def test_update_direction_round_trips_due_at(self, srs_db):
        """update_direction preserves due_at datetime through round-trip."""
        unit = _unit("word2", "translation2")
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("word2")
        guid = item.guid

        due_at = datetime.now()
        rec_dir = item.directions[Direction.RECOGNITION]
        rec_dir.due_at = due_at
        srs_db.update_direction(guid, Direction.RECOGNITION, rec_dir)

        reloaded = srs_db.get_collocation("word2")
        reloaded_due_at = reloaded.directions[Direction.RECOGNITION].due_at
        assert reloaded_due_at is not None
        assert abs((reloaded_due_at - due_at).total_seconds()) < 1

    def test_update_direction_left_none_stores_null(self, srs_db):
        """left=None stores as NULL in DB."""
        unit = _unit("word3", "translation3")
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("word3")
        guid = item.guid

        rec_dir = item.directions[Direction.RECOGNITION]
        rec_dir.left = None
        srs_db.update_direction(guid, Direction.RECOGNITION, rec_dir)

        reloaded = srs_db.get_collocation("word3")
        assert reloaded.directions[Direction.RECOGNITION].left is None

    def test_update_direction_due_at_none_stores_null(self, srs_db):
        """due_at=None stores as NULL in DB."""
        unit = _unit("word4", "translation4")
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("word4")
        guid = item.guid

        rec_dir = item.directions[Direction.RECOGNITION]
        rec_dir.due_at = None
        srs_db.update_direction(guid, Direction.RECOGNITION, rec_dir)

        reloaded = srs_db.get_collocation("word4")
        assert reloaded.directions[Direction.RECOGNITION].due_at is None

    def test_get_collocation_by_id_round_trips_learning_fields(self, srs_db):
        """get_collocation_by_id preserves left and due_at."""
        unit = _unit("word5", "translation5")
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("word5")

        # Get row id properly
        with srs_db._get_conn() as conn:
            row = conn.execute("SELECT id FROM collocations WHERE guid = ?", (item.guid,)).fetchone()
            row_id = row["id"]

        # Set learning fields
        rec_dir = item.directions[Direction.RECOGNITION]
        rec_dir.left = 1001
        rec_dir.due_at = datetime.now()
        rec_dir.state = SRSState.LEARNING
        srs_db.update_direction(item.guid, Direction.RECOGNITION, rec_dir)

        # Retrieve by id
        result = srs_db.get_collocation_by_id(row_id)
        assert result is not None
        _, reloaded, _ = result
        reloaded_dir = reloaded.directions[Direction.RECOGNITION]
        assert reloaded_dir.left == 1001
        assert reloaded_dir.due_at is not None
        assert reloaded_dir.state == SRSState.LEARNING

    def test_new_direction_defaults_have_no_learning_fields(self, srs_db):
        """Newly added collocations have left=None, due_at=None."""
        unit = _unit("word6", "translation6")
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("word6")

        rec_dir = item.directions[Direction.RECOGNITION]
        assert rec_dir.left is None
        assert rec_dir.due_at is None
        assert rec_dir.state == SRSState.NEW
