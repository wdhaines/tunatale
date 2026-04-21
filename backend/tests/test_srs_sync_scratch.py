"""Tests for sync_conflicts and pending_revlog scratch tables."""

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


class TestPendingRevlog:
    def test_enqueue_and_drain_roundtrip(self, srs_db):
        srs_db.enqueue_pending_revlog(cid=101, ease=3, ivl=14, last_ivl=7, factor=2500, time_ms=8000, type_=1)
        rows = srs_db.drain_pending_revlog()
        assert len(rows) == 1
        r = rows[0]
        assert r["cid"] == 101
        assert r["ease"] == 3
        assert r["ivl"] == 14
        assert r["last_ivl"] == 7
        assert r["factor"] == 2500
        assert r["time_ms"] == 8000
        assert r["type"] == 1

    def test_drain_deletes_rows(self, srs_db):
        srs_db.enqueue_pending_revlog(cid=1, ease=4, ivl=21, last_ivl=14, factor=2500, time_ms=5000, type_=1)
        srs_db.drain_pending_revlog()
        assert srs_db.drain_pending_revlog() == []

    def test_drain_multiple_returns_all(self, srs_db):
        for cid in [10, 20, 30]:
            srs_db.enqueue_pending_revlog(cid=cid, ease=3, ivl=7, last_ivl=3, factor=2500, time_ms=3000, type_=1)
        rows = srs_db.drain_pending_revlog()
        assert {r["cid"] for r in rows} == {10, 20, 30}
        assert srs_db.drain_pending_revlog() == []

    def test_drain_atomic_clears_on_success(self, srs_db):
        srs_db.enqueue_pending_revlog(cid=99, ease=2, ivl=3, last_ivl=1, factor=2000, time_ms=2000, type_=0)
        first = srs_db.drain_pending_revlog()
        assert len(first) == 1
        second = srs_db.drain_pending_revlog()
        assert second == []


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
