"""Tests for the Anki-surface database methods: upsert_by_guid, set_anki_ids, add_media."""

from datetime import date

import pytest

from app.common.guid import compute_guid
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit


def _unit(text: str, translation: str = "") -> SyntacticUnit:
    return SyntacticUnit(text=text, translation=translation, word_count=1, difficulty=1, source="anki")


def _dirs(reps_rec: int = 5, reps_prod: int = 3, stability: float = 12.5) -> dict[Direction, DirectionState]:
    today = date.today()
    return {
        Direction.RECOGNITION: DirectionState(
            direction=Direction.RECOGNITION,
            due_date=today,
            stability=stability,
            difficulty=4.8,
            reps=reps_rec,
            state=SRSState.REVIEW,
        ),
        Direction.PRODUCTION: DirectionState(
            direction=Direction.PRODUCTION,
            due_date=today,
            stability=stability / 2,
            difficulty=5.1,
            reps=reps_prod,
            state=SRSState.REVIEW,
        ),
    }


class TestUpsertByGuid:
    def test_inserts_when_new(self, srs_db):
        coll_id = srs_db.upsert_by_guid(_unit("banka", "bank"), "sl", _dirs(), anki_note_id=1001)
        assert isinstance(coll_id, int) and coll_id > 0
        item = srs_db.get_collocation("banka")
        assert item is not None
        assert item.syntactic_unit.text == "banka"
        assert item.anki_note_id == 1001

    def test_creates_both_direction_rows(self, srs_db):
        srs_db.upsert_by_guid(_unit("banka", "bank"), "sl", _dirs())
        item = srs_db.get_collocation("banka")
        assert Direction.RECOGNITION in item.directions
        assert Direction.PRODUCTION in item.directions
        assert item.directions[Direction.RECOGNITION].reps == 5
        assert item.directions[Direction.PRODUCTION].reps == 3

    def test_updates_parent_scalars_on_second_call(self, srs_db):
        srs_db.upsert_by_guid(_unit("banka", "bank"), "sl", _dirs())
        srs_db.upsert_by_guid(_unit("banka", "financial institution"), "sl", _dirs())
        item = srs_db.get_collocation("banka")
        assert item.syntactic_unit.translation == "financial institution"

    def test_preserves_direction_fsrs_when_reps_gt_zero(self, srs_db):
        srs_db.upsert_by_guid(_unit("banka", "bank"), "sl", _dirs(stability=12.5))
        # Re-import with changed FSRS values but same reps > 0
        new_dirs = {
            Direction.RECOGNITION: DirectionState(
                direction=Direction.RECOGNITION,
                due_date=date.today(),
                stability=99.0,
                difficulty=1.0,
                reps=5,
                state=SRSState.REVIEW,
            ),
            Direction.PRODUCTION: DirectionState(
                direction=Direction.PRODUCTION,
                due_date=date.today(),
                stability=99.0,
                difficulty=1.0,
                reps=3,
                state=SRSState.REVIEW,
            ),
        }
        srs_db.upsert_by_guid(_unit("banka", "bank"), "sl", new_dirs)
        item = srs_db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].stability == 12.5
        assert item.directions[Direction.PRODUCTION].stability == 12.5 / 2

    def test_refreshes_direction_when_reps_zero(self, srs_db):
        zero_dirs = {
            Direction.RECOGNITION: DirectionState(
                direction=Direction.RECOGNITION, due_date=date.today(), stability=1.0, reps=0
            ),
            Direction.PRODUCTION: DirectionState(
                direction=Direction.PRODUCTION, due_date=date.today(), stability=1.0, reps=0
            ),
        }
        srs_db.upsert_by_guid(_unit("hiša", "house"), "sl", zero_dirs)
        updated_dirs = {
            Direction.RECOGNITION: DirectionState(
                direction=Direction.RECOGNITION, due_date=date.today(), stability=20.0, reps=0
            ),
            Direction.PRODUCTION: DirectionState(
                direction=Direction.PRODUCTION, due_date=date.today(), stability=10.0, reps=0
            ),
        }
        srs_db.upsert_by_guid(_unit("hiša", "house"), "sl", updated_dirs)
        item = srs_db.get_collocation("hiša")
        assert item.directions[Direction.RECOGNITION].stability == 20.0
        assert item.directions[Direction.PRODUCTION].stability == 10.0

    def test_preserves_anki_card_id_even_when_reps_gt_zero(self, srs_db):
        dirs_no_card_id = {
            Direction.RECOGNITION: DirectionState(
                direction=Direction.RECOGNITION, due_date=date.today(), reps=5, anki_card_id=None
            ),
            Direction.PRODUCTION: DirectionState(
                direction=Direction.PRODUCTION, due_date=date.today(), reps=3, anki_card_id=None
            ),
        }
        srs_db.upsert_by_guid(_unit("miza", "table"), "sl", dirs_no_card_id)
        dirs_with_card_id = {
            Direction.RECOGNITION: DirectionState(
                direction=Direction.RECOGNITION, due_date=date.today(), stability=99.0, reps=5, anki_card_id=10010
            ),
            Direction.PRODUCTION: DirectionState(
                direction=Direction.PRODUCTION, due_date=date.today(), stability=99.0, reps=3, anki_card_id=10011
            ),
        }
        srs_db.upsert_by_guid(_unit("miza", "table"), "sl", dirs_with_card_id)
        item = srs_db.get_collocation("miza")
        assert item.directions[Direction.RECOGNITION].anki_card_id == 10010
        assert item.directions[Direction.PRODUCTION].anki_card_id == 10011


class TestSetAnkiIds:
    def test_sets_note_id_and_card_ids(self, srs_db):
        srs_db.upsert_by_guid(_unit("miza", "table"), "sl", _dirs())
        guid = compute_guid("miza", "sl")
        srs_db.set_anki_ids(guid, note_id=2001, card_ids={Direction.RECOGNITION: 20010, Direction.PRODUCTION: 20011})
        item = srs_db.get_collocation("miza")
        assert item.anki_note_id == 2001
        assert item.directions[Direction.RECOGNITION].anki_card_id == 20010
        assert item.directions[Direction.PRODUCTION].anki_card_id == 20011

    def test_set_anki_ids_missing_guid_is_noop(self, srs_db):
        srs_db.set_anki_ids("nonexistent_guid", note_id=999, card_ids={})  # should not raise


class TestAddMedia:
    def test_add_media_returns_id(self, srs_db):
        coll_id = srs_db.upsert_by_guid(_unit("stol", "chair"), "sl", _dirs())
        media_id = srs_db.add_media(
            coll_id,
            kind="audio_forvo",
            filename="sl_stol.mp3",
            path="/tmp/sl_stol.mp3",
            anki_filename="sl_stol.mp3",
            sha256="abc123",
            size_bytes=1024,
        )
        assert isinstance(media_id, int) and media_id > 0

    def test_find_media_by_anki_filename_returns_row(self, srs_db):
        coll_id = srs_db.upsert_by_guid(_unit("stol", "chair"), "sl", _dirs())
        srs_db.add_media(
            coll_id,
            kind="audio_forvo",
            filename="sl_stol.mp3",
            path="/tmp/sl_stol.mp3",
            anki_filename="sl_stol.mp3",
            sha256="abc123",
            size_bytes=1024,
        )
        row = srs_db.find_media_by_anki_filename("sl_stol.mp3")
        assert row is not None
        assert row["filename"] == "sl_stol.mp3"
        assert row["sha256"] == "abc123"
        assert row["kind"] == "audio_forvo"

    def test_find_media_missing_returns_none(self, srs_db):
        assert srs_db.find_media_by_anki_filename("ghost.mp3") is None

    def test_inserts_missing_direction_row_for_existing_parent(self, srs_db):
        """When an existing parent is missing a direction row, upsert inserts it."""
        from app.common.guid import compute_guid

        unit = _unit("stol", "chair")
        guid = compute_guid("stol", "sl")
        today = date.today().isoformat()
        # Insert parent with only recognition direction (bypassing upsert_by_guid)
        with srs_db._get_conn() as conn:
            conn.execute(
                "INSERT INTO collocations (text,translation,language_code,word_count,"
                "unit_difficulty,source,corpus_frequency,guid) VALUES (?,?,?,?,?,?,?,?)",
                ("stol", "chair", "sl", 1, 1, "anki", 0, guid),
            )
            conn.execute(
                "INSERT INTO collocation_directions (collocation_id,direction,due_date)"
                " VALUES (last_insert_rowid(),'recognition',?)",
                (today,),
            )
            srs_db._commit(conn)
        # Now upsert with both directions — production direction should be inserted
        dirs = _dirs()
        srs_db.upsert_by_guid(unit, "sl", dirs)
        item = srs_db.get_collocation("stol")
        assert Direction.PRODUCTION in item.directions


class TestBeginTransaction:
    def test_transaction_commits_on_success(self, srs_db):
        with srs_db.begin_transaction():
            srs_db.upsert_by_guid(_unit("banka", "bank"), "sl", _dirs())
        assert srs_db.get_collocation("banka") is not None

    def test_dry_run_rolls_back(self, srs_db):
        with srs_db.begin_transaction(dry_run=True):
            srs_db.upsert_by_guid(_unit("banka", "bank"), "sl", _dirs())
        assert srs_db.get_collocation("banka") is None

    def test_exception_rolls_back(self, srs_db):
        with pytest.raises(ValueError, match="deliberate"), srs_db.begin_transaction():
            srs_db.upsert_by_guid(_unit("banka", "bank"), "sl", _dirs())
            srs_db.upsert_by_guid(_unit("hiša", "house"), "sl", _dirs())
            raise ValueError("deliberate")
        assert srs_db.get_collocation("banka") is None
        assert srs_db.get_collocation("hiša") is None
