"""Tests for sync_conflicts scratch table."""

from app.srs.database import SRSDatabase


class TestSyncConflicts:
    def test_record_and_list_conflict(self, srs_db):
        srs_db.record_sync_conflict(
            guid="abc123",
            direction="recognition",
            field="translation",
            local="stari prevod",
            remote="novi prevod",
            resolution="anki_wins",
        )
        rows = srs_db.list_sync_conflicts()
        assert len(rows) == 1
        r = rows[0]
        assert r["guid"] == "abc123"
        assert r["direction"] == "recognition"
        assert r["field"] == "translation"
        assert r["local_value"] == "stari prevod"
        assert r["remote_value"] == "novi prevod"
        assert r["resolution"] == "anki_wins"
        assert r["resolved_at"]

    def test_direction_nullable(self, srs_db):
        srs_db.record_sync_conflict(
            guid="xyz",
            direction=None,
            field="text",
            local="old",
            remote="new",
            resolution="anki_wins",
        )
        rows = srs_db.list_sync_conflicts()
        assert rows[0]["direction"] is None

    def test_multiple_conflicts_returned(self, srs_db):
        for i in range(3):
            srs_db.record_sync_conflict(
                guid=f"guid{i}",
                direction="production",
                field="translation",
                local="a",
                remote="b",
                resolution="anki_wins",
            )
        assert len(srs_db.list_sync_conflicts()) == 3


class TestIdempotentInit:
    def test_create_tables_idempotent_on_reinit(self, tmp_path):
        """_init_schema can be called twice without error (CREATE IF NOT EXISTS)."""
        db_path = str(tmp_path / "test.db")
        db1 = SRSDatabase(db_path)
        db1.close()
        # Opening again re-runs _init_schema; tables already exist → no error
        db2 = SRSDatabase(db_path)
        db2.record_sync_conflict(guid="g", direction=None, field="f", local="l", remote="r", resolution="anki_wins")
        db2.close()
