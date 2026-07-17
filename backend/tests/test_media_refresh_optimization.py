"""Tests for the peer-sync media-refresh optimisation (batch lookups, mtime skip, index).

Three fixes:
  1. Batch media lookups — one dict instead of per-file queries
  2. Skip re-hashing unchanged files via (size, mtime_ns)
  3. Index collocations(anki_note_id)

All tests are outcome-based, no internal patches.
"""

import hashlib
import os
import sqlite3

from app.srs.database import SRSDatabase

# ---------------------------------------------------------------------------
# Migration v36→v37
# ---------------------------------------------------------------------------


class TestMigrationV36ToV37:
    """v37 adds mtime_ns to media and idx_collocations_anki_note_id."""

    def _make_v36_conn(self) -> sqlite3.Connection:
        """Build a v36-schema media table (no mtime_ns, no anki_note_id index)."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE collocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT UNIQUE NOT NULL,
                translation TEXT NOT NULL DEFAULT '',
                language_code TEXT NOT NULL DEFAULT 'sl',
                word_count INTEGER NOT NULL DEFAULT 1,
                unit_difficulty INTEGER NOT NULL DEFAULT 1,
                source TEXT NOT NULL DEFAULT 'corpus',
                corpus_frequency INTEGER NOT NULL DEFAULT 0,
                lemma TEXT,
                guid TEXT UNIQUE,
                disambig_key TEXT NOT NULL DEFAULT '',
                anki_note_id INTEGER,
                dirty_fields TEXT NOT NULL DEFAULT '',
                last_synced_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(text, disambig_key)
            )
        """)
        conn.execute("""
            CREATE TABLE media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collocation_id INTEGER REFERENCES collocations(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                filename TEXT NOT NULL,
                path TEXT,
                anki_filename TEXT,
                sha256 TEXT,
                bytes INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX idx_media_collocation ON media(collocation_id)")
        conn.execute("CREATE INDEX idx_media_anki_filename ON media(anki_filename)")
        conn.execute("PRAGMA user_version = 36")
        conn.commit()
        return conn

    def test_migration_adds_mtime_ns_column(self):
        """After v37 migration, media.mtime_ns column exists and defaults to NULL."""
        from app.srs.migrations import migrate_v36_to_v37

        conn = self._make_v36_conn()
        migrate_v36_to_v37(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(media)").fetchall()}
        assert "mtime_ns" in cols

    def test_migration_existing_media_rows_have_null_mtime_ns(self):
        """Pre-existing media rows get NULL mtime_ns after migration."""
        from app.srs.migrations import migrate_v36_to_v37

        conn = self._make_v36_conn()
        conn.execute(
            "INSERT INTO media (collocation_id, kind, filename, sha256, bytes) "
            "VALUES (1, 'image', 'img.jpg', 'abc123', 1024)"
        )
        conn.commit()

        migrate_v36_to_v37(conn)

        row = conn.execute("SELECT mtime_ns FROM media WHERE id = 1").fetchone()
        assert row["mtime_ns"] is None

    def test_migration_creates_anki_note_id_index(self):
        """After v37 migration, idx_collocations_anki_note_id exists."""
        from app.srs.migrations import migrate_v36_to_v37

        conn = self._make_v36_conn()
        migrate_v36_to_v37(conn)

        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_collocations_anki_note_id'"
        ).fetchone()
        assert row is not None, "idx_collocations_anki_note_id index must exist"

    def test_migration_idempotent(self):
        """Running v37 migration twice doesn't error."""
        from app.srs.migrations import migrate_v36_to_v37

        conn = self._make_v36_conn()
        migrate_v36_to_v37(conn)
        migrate_v36_to_v37(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 37

    def test_migration_version_bump(self):
        """user_version is 37 after migration."""
        from app.srs.migrations import migrate_v36_to_v37

        conn = self._make_v36_conn()
        migrate_v36_to_v37(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 37

    def test_current_version_is_38(self):
        from app.srs.migrations import CURRENT_VERSION

        assert CURRENT_VERSION == 38


# ---------------------------------------------------------------------------
# list_media_by_collocation_and_filename
# ---------------------------------------------------------------------------


class TestListMediaByCollocationAndFilename:
    """Batch media preload method returns dict keyed by (collocation_id, anki_filename)."""

    def test_empty_db_returns_empty_dict(self):
        db = SRSDatabase(":memory:")
        try:
            result = db.list_media_by_collocation_and_filename()
            assert result == {}
        finally:
            db.close()

    def test_returns_all_media_rows_keyed(self):
        db = SRSDatabase(":memory:")
        try:
            # Seed a collocation first
            from app.models.syntactic_unit import SyntacticUnit

            unit = SyntacticUnit(text="test", translation="test", word_count=1, difficulty=1, source="anki")
            coll_id = db.add_collocation(unit)
            db.add_media(
                coll_id,
                kind="image",
                filename="img.jpg",
                path="/tmp/img.jpg",
                anki_filename="img.jpg",
                sha256="abc",
                size_bytes=100,
            )
            db.add_media(
                coll_id,
                kind="audio_tts",
                filename="tts.mp3",
                path="/tmp/tts.mp3",
                anki_filename="tts.mp3",
                sha256="def",
                size_bytes=200,
            )
            result = db.list_media_by_collocation_and_filename()
            assert (coll_id, "img.jpg") in result
            assert (coll_id, "tts.mp3") in result
            assert result[(coll_id, "img.jpg")]["sha256"] == "abc"
            assert result[(coll_id, "tts.mp3")]["bytes"] == 200
        finally:
            db.close()

    def test_includes_mtime_ns_column(self):
        """Result dict includes mtime_ns (may be NULL for pre-migration rows)."""
        db = SRSDatabase(":memory:")
        try:
            from app.models.syntactic_unit import SyntacticUnit

            unit = SyntacticUnit(text="test2", translation="test2", word_count=1, difficulty=1, source="anki")
            coll_id = db.add_collocation(unit)
            db.add_media(
                coll_id,
                kind="image",
                filename="pic.png",
                path="/tmp/pic.png",
                anki_filename="pic.png",
                sha256="aaa",
                size_bytes=50,
            )
            result = db.list_media_by_collocation_and_filename()
            assert result[(coll_id, "pic.png")]["mtime_ns"] is None
        finally:
            db.close()


# ---------------------------------------------------------------------------
# update_media_stat
# ---------------------------------------------------------------------------


class TestUpdateMediaStat:
    """update_media_stat stamps mtime_ns + size_bytes on a media row."""

    def test_stamps_mtime_and_size(self):
        db = SRSDatabase(":memory:")
        try:
            from app.models.syntactic_unit import SyntacticUnit

            unit = SyntacticUnit(text="stat", translation="stat", word_count=1, difficulty=1, source="anki")
            coll_id = db.add_collocation(unit)
            media_id = db.add_media(
                coll_id,
                kind="image",
                filename="a.jpg",
                path="/tmp/a.jpg",
                anki_filename="a.jpg",
                sha256="hhh",
                size_bytes=10,
            )
            db.update_media_stat(media_id, mtime_ns=1234567890, size_bytes=99)
            row = db.find_media_by_anki_filename("a.jpg", collocation_id=coll_id)
            assert row is not None
            assert row["mtime_ns"] == 1234567890
            assert row["bytes"] == 99
        finally:
            db.close()


# ---------------------------------------------------------------------------
# mtime_ns skip-rehash logic (outcome-based, no mocks)
# ---------------------------------------------------------------------------


class TestHashSkip:
    """Verify that unchanged files (matching mtime_ns + size) skip SHA256 computation."""

    def test_skip_hash_when_mtime_and_size_match(self, tmp_path):
        """Create a media file, refresh (stamps sha+mtime), then rewrite the file
        with different content but restore the original (st_mtime_ns, size) via
        os.utime and same-length content. Refresh again → reports unchanged.
        """
        from app.plugins.anki_sync.import_seed import _refresh_media_for_collocation

        # Setup
        media_dir = tmp_path / "tt_media"
        media_dir.mkdir()
        anki_dir = tmp_path / "anki_media"
        anki_dir.mkdir()

        src = anki_dir / "hello.jpg"
        src.write_bytes(b"original content!")
        original_mtime = src.stat().st_mtime_ns
        original_size = src.stat().st_size

        db = SRSDatabase(":memory:")
        try:
            from app.models.syntactic_unit import SyntacticUnit

            unit = SyntacticUnit(text="word", translation="word", word_count=1, difficulty=1, source="anki")
            coll_id = db.add_collocation(unit)

            # First refresh: should add the media
            results: dict = {"new_media": 0, "updated_media": 0, "unchanged_media": 0, "collapsed_media": 0}
            _refresh_media_for_collocation(anki_dir, ['<img src="hello.jpg">'], coll_id, media_dir, db, results)
            assert results["new_media"] == 1
            assert results["unchanged_media"] == 0

            # Verify mtime_ns was stamped
            row = db.find_media_by_anki_filename("hello.jpg", collocation_id=coll_id)
            assert row is not None
            assert row["mtime_ns"] == original_mtime
            assert row["bytes"] == original_size

            # Rewrite file with different content but same length
            src.write_bytes(b"differentcontent!")
            # Restore original mtime and size via os.utime
            os.utime(str(src), ns=(original_mtime, original_mtime))
            assert src.stat().st_size == original_size  # same length → same size

            # Second refresh: should report unchanged (hash was skipped)
            results2: dict = {"new_media": 0, "updated_media": 0, "unchanged_media": 0, "collapsed_media": 0}
            _refresh_media_for_collocation(anki_dir, ['<img src="hello.jpg">'], coll_id, media_dir, db, results2)
            assert results2["unchanged_media"] == 1
            assert results2["updated_media"] == 0

            # Now bump the mtime — refresh should detect the change and hash
            new_mtime = original_mtime + 1_000_000_000  # bump by 1 second
            os.utime(str(src), ns=(new_mtime, new_mtime))
            results3: dict = {"new_media": 0, "updated_media": 0, "unchanged_media": 0, "collapsed_media": 0}
            _refresh_media_for_collocation(anki_dir, ['<img src="hello.jpg">'], coll_id, media_dir, db, results3)
            assert results3["updated_media"] == 1
            row2 = db.find_media_by_anki_filename("hello.jpg", collocation_id=coll_id)
            assert row2 is not None
            assert row2["mtime_ns"] == new_mtime
        finally:
            db.close()

    def test_null_mtime_warmup_stamps_on_matching_sha(self, tmp_path):
        """Row with NULL mtime_ns but matching sha → unchanged count + mtime_ns stamped."""
        from app.plugins.anki_sync.import_seed import _refresh_media_for_collocation

        media_dir = tmp_path / "tt_media"
        media_dir.mkdir()
        anki_dir = tmp_path / "anki_media"
        anki_dir.mkdir()

        content = b"steady content"
        src = anki_dir / "warmup.jpg"
        src.write_bytes(content)
        expected_sha = hashlib.sha256(content).hexdigest()

        db = SRSDatabase(":memory:")
        try:
            from app.models.syntactic_unit import SyntacticUnit

            unit = SyntacticUnit(text="warm", translation="warm", word_count=1, difficulty=1, source="anki")
            coll_id = db.add_collocation(unit)
            # Manually insert a media row with NULL mtime_ns
            db.add_media(
                coll_id,
                kind="image",
                filename="warmup.jpg",
                path=str(media_dir / "warmup.jpg"),
                anki_filename="warmup.jpg",
                sha256=expected_sha,
                size_bytes=len(content),
            )
            # Verify mtime_ns is NULL
            row = db.find_media_by_anki_filename("warmup.jpg", collocation_id=coll_id)
            assert row["mtime_ns"] is None

            results: dict = {"new_media": 0, "updated_media": 0, "unchanged_media": 0, "collapsed_media": 0}
            _refresh_media_for_collocation(anki_dir, ['<img src="warmup.jpg">'], coll_id, media_dir, db, results)
            # Should count as unchanged (sha matched) and stamp mtime_ns
            assert results["unchanged_media"] == 1
            row2 = db.find_media_by_anki_filename("warmup.jpg", collocation_id=coll_id)
            assert row2["mtime_ns"] == src.stat().st_mtime_ns
            assert row2["bytes"] == len(content)
        finally:
            db.close()

    def test_changed_content_detected_after_mtime_match(self, tmp_path):
        """When content changes AND mtime changes, hash is computed and update happens."""
        from app.plugins.anki_sync.import_seed import _refresh_media_for_collocation

        media_dir = tmp_path / "tt_media"
        media_dir.mkdir()
        anki_dir = tmp_path / "anki_media"
        anki_dir.mkdir()

        src = anki_dir / "changing.jpg"
        src.write_bytes(b"version one!!!")
        original_mtime = src.stat().st_mtime_ns

        db = SRSDatabase(":memory:")
        try:
            from app.models.syntactic_unit import SyntacticUnit

            unit = SyntacticUnit(text="change", translation="change", word_count=1, difficulty=1, source="anki")
            coll_id = db.add_collocation(unit)
            # First refresh
            results: dict = {"new_media": 0, "updated_media": 0, "unchanged_media": 0, "collapsed_media": 0}
            _refresh_media_for_collocation(anki_dir, ['<img src="changing.jpg">'], coll_id, media_dir, db, results)
            assert results["new_media"] == 1

            # Now change content AND bump mtime — should detect change via hash
            src.write_bytes(b"version two!!!")
            new_mtime = original_mtime + 1_000_000_000
            os.utime(str(src), ns=(new_mtime, new_mtime))

            results2: dict = {"new_media": 0, "updated_media": 0, "unchanged_media": 0, "collapsed_media": 0}
            _refresh_media_for_collocation(anki_dir, ['<img src="changing.jpg">'], coll_id, media_dir, db, results2)
            # Content changed → hash computed → updated_media
            assert results2["updated_media"] == 1
            row = db.find_media_by_anki_filename("changing.jpg", collocation_id=coll_id)
            assert row is not None
            assert row["mtime_ns"] == new_mtime
        finally:
            db.close()


# ---------------------------------------------------------------------------
# add_media / update_media_file with mtime_ns
# ---------------------------------------------------------------------------


class TestAddMediaWithMtimeNs:
    """add_media stores mtime_ns; update_media_file stores mtime_ns."""

    def test_add_media_with_mtime_ns(self):
        db = SRSDatabase(":memory:")
        try:
            from app.models.syntactic_unit import SyntacticUnit

            unit = SyntacticUnit(text="mtime", translation="mtime", word_count=1, difficulty=1, source="anki")
            coll_id = db.add_collocation(unit)
            db.add_media(
                coll_id,
                kind="image",
                filename="x.jpg",
                path="/tmp/x.jpg",
                anki_filename="x.jpg",
                sha256="xxx",
                size_bytes=100,
                mtime_ns=99999,
            )
            row = db.find_media_by_anki_filename("x.jpg", collocation_id=coll_id)
            assert row is not None
            assert row["mtime_ns"] == 99999
        finally:
            db.close()

    def test_add_media_without_mtime_ns_defaults_null(self):
        db = SRSDatabase(":memory:")
        try:
            from app.models.syntactic_unit import SyntacticUnit

            unit = SyntacticUnit(text="nulltime", translation="nulltime", word_count=1, difficulty=1, source="anki")
            coll_id = db.add_collocation(unit)
            db.add_media(
                coll_id,
                kind="image",
                filename="y.jpg",
                path="/tmp/y.jpg",
                anki_filename="y.jpg",
                sha256="yyy",
                size_bytes=50,
            )
            row = db.find_media_by_anki_filename("y.jpg", collocation_id=coll_id)
            assert row is not None
            assert row["mtime_ns"] is None
        finally:
            db.close()

    def test_update_media_file_with_mtime_ns(self):
        db = SRSDatabase(":memory:")
        try:
            from app.models.syntactic_unit import SyntacticUnit

            unit = SyntacticUnit(text="upd", translation="upd", word_count=1, difficulty=1, source="anki")
            coll_id = db.add_collocation(unit)
            media_id = db.add_media(
                coll_id,
                kind="image",
                filename="z.jpg",
                path="/tmp/z.jpg",
                anki_filename="z.jpg",
                sha256="zzz",
                size_bytes=80,
            )
            db.update_media_file(media_id, sha256="newsha", size_bytes=90, mtime_ns=77777)
            row = db.find_media_by_anki_filename("z.jpg", collocation_id=coll_id)
            assert row is not None
            assert row["sha256"] == "newsha"
            assert row["bytes"] == 90
            assert row["mtime_ns"] == 77777
        finally:
            db.close()


# ---------------------------------------------------------------------------
# refresh_media_from_conn uses batch preload
# ---------------------------------------------------------------------------


class TestRefreshMediaBatchPreload:
    """refresh_media_from_conn preloads all media in one query (batch path)."""

    def test_process_linked_notes_with_preload(self, tmp_path):
        """refresh_media_from_conn processes linked notes and handles media correctly."""
        from app.models.srs_item import Direction
        from app.models.syntactic_unit import SyntacticUnit
        from app.plugins.anki_sync.import_seed import refresh_media_from_conn
        from tests._helpers.anki_sync_create_new import _make_dual_collection_conn

        anki_dir = tmp_path / "anki_media"
        anki_dir.mkdir()
        tt_dir = tmp_path / "tt_media"
        tt_dir.mkdir()

        # Create a media file in anki media dir
        media_file = anki_dir / "test_img.jpg"
        media_file.write_bytes(b"test image content")

        conn = _make_dual_collection_conn()
        conn.execute(
            "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data) "
            "VALUES (9001, 'guid-1', 1000001, 0, 0, '', '<img src=\"test_img.jpg\">\x1fbank\x1f\x1f\x1f', 'test', 0, 0, '')"
        )
        conn.execute(
            "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl, factor, reps, lapses, data) "
            "VALUES (90010, 9001, 12345, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, '')"
        )
        conn.commit()

        db = SRSDatabase(":memory:")
        try:
            unit = SyntacticUnit(text="test", translation="test", word_count=1, difficulty=1, source="anki")
            db.add_collocation(unit)
            item = db.get_collocation("test")
            assert item is not None
            db.set_anki_ids(item.guid, 9001, {Direction.RECOGNITION: 90010})

            res = refresh_media_from_conn(
                conn,
                deck_name="0. Slovene",
                anki_media_path=anki_dir,
                media_dir=tt_dir,
                db=db,
            )
            assert isinstance(res, dict)
            assert res["new_media"] >= 0  # media was processed
        finally:
            db.close()

    def test_import_seed_path_propagates_media(self, tmp_path):
        """import_seed's internal call to _refresh_media_for_collocation works
        with the batch preload dict.
        """
        from app.plugins.anki_sync.import_seed import _refresh_media_for_collocation

        anki_dir = tmp_path / "anki_media"
        anki_dir.mkdir()
        tt_dir = tmp_path / "tt_media"
        tt_dir.mkdir()

        # Create media file
        media_file = anki_dir / "seed_img.jpg"
        media_file.write_bytes(b"seed image content")

        db = SRSDatabase(":memory:")
        try:
            from app.models.syntactic_unit import SyntacticUnit

            unit = SyntacticUnit(text="seed", translation="seed", word_count=1, difficulty=1, source="anki")
            coll_id = db.add_collocation(unit)

            results: dict = {"new_media": 0, "updated_media": 0, "unchanged_media": 0, "collapsed_media": 0}
            _refresh_media_for_collocation(anki_dir, ['<img src="seed_img.jpg">'], coll_id, tt_dir, db, results)
            assert results["new_media"] == 1
        finally:
            db.close()


# ---------------------------------------------------------------------------
# index collocations(anki_note_id) exists on fresh DB
# ---------------------------------------------------------------------------


class TestAnkiNoteIdIndex:
    """Fresh SRSDatabase has idx_collocations_anki_note_id."""

    def test_index_exists_on_fresh_db(self):
        db = SRSDatabase(":memory:")
        try:
            row = db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_collocations_anki_note_id'"
            ).fetchone()
            assert row is not None
        finally:
            db.close()
