"""Tests for learning step config readers (resolve_learning_steps, resolve_relearning_steps)."""

import json
import sqlite3
import struct
from datetime import UTC, datetime, timedelta

from app.srs.database import SRSDatabase
from app.srs.queue_stats import (
    _LEARN_STEPS_FIELD,
    _RELEARN_STEPS_FIELD,
    refresh_learning_steps,
    resolve_learning_steps,
    resolve_relearning_steps,
)
from tests._helpers.protobuf import encode_varint, pb_len_field, pb_varint_field


def _make_minimal_anki_db():
    """Create an in-memory Anki-like DB with deck_config table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Create decks table
    conn.execute("""
        CREATE TABLE decks (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            kind BLOB
        )
    """)

    # Create deck_config table
    conn.execute("""
        CREATE TABLE deck_config (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            config BLOB
        )
    """)

    conn.commit()
    return conn


def _make_deck_config_blob_with_steps(learn_steps=None, relearn_steps=None):
    """Build a DeckConfig.Config protobuf blob with learning/relearning steps."""
    blob = b""
    if learn_steps is not None:
        payload = struct.pack(f"<{len(learn_steps)}f", *learn_steps)
        tag = encode_varint((_LEARN_STEPS_FIELD << 3) | 2)
        blob += tag + encode_varint(len(payload)) + payload
    if relearn_steps is not None:
        payload = struct.pack(f"<{len(relearn_steps)}f", *relearn_steps)
        tag = encode_varint((_RELEARN_STEPS_FIELD << 3) | 2)
        blob += tag + encode_varint(len(payload)) + payload
    return blob


def _make_modern_anki_conn_with_steps(deck_name="0. Slovene", learn_steps=None, relearn_steps=None):
    """Build a modern Anki connection with learning steps in deck_config."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    config_id = 1774580286260
    deck_id = 12345

    conn.execute(
        "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
        "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
        "decks TEXT, dconf TEXT, tags TEXT)"
    )
    conn.execute("INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', '', '', '{}')")

    conn.execute(
        "CREATE TABLE deck_config (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB)"
    )
    config_blob = _make_deck_config_blob_with_steps(learn_steps, relearn_steps)
    conn.execute(
        "INSERT INTO deck_config VALUES (?, ?, 0, -1, ?)",
        (config_id, "Slovene", config_blob),
    )

    conn.execute(
        "CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, "
        "usn INTEGER, common BLOB, kind BLOB)"
    )
    # kind blob: field 1 (LEN) containing conf_id at field 1 (VARINT)
    inner = pb_varint_field(1, config_id)
    kind_blob = pb_len_field(1, inner)
    conn.execute(
        "INSERT INTO decks VALUES (?, ?, 0, -1, NULL, ?)",
        (deck_id, deck_name, kind_blob),
    )
    conn.commit()
    return conn


class TestResolveLearningSteps:
    """Tests for resolve_learning_steps and resolve_relearning_steps."""

    def test_resolve_learning_steps_default(self):
        """Without cache or Anki DB, returns default [1.0, 10.0]."""
        steps, source = resolve_learning_steps(db=None)
        assert steps == [1.0, 10.0]
        assert source == "default"

    def test_resolve_relearning_steps_default(self):
        """Without cache or Anki DB, returns default [10.0]."""
        steps, source = resolve_relearning_steps(db=None)
        assert steps == [10.0]
        assert source == "default"

    def test_resolve_learning_steps_returns_default_when_db_is_none(self):
        """Lines 566-567: resolve_learning_steps with db=None and SRSDatabase creation fails."""
        # Use a database URL that will fail to create
        from app.config import settings

        original_url = settings.database_url
        try:
            settings.database_url = "sqlite:////nonexistent/path/db.sqlite"
            steps, source = resolve_learning_steps(db=None)
            assert steps == [1.0, 10.0]
            assert source == "default"
        finally:
            settings.database_url = original_url

    def test_resolve_learning_steps_returns_default_when_cache_missing(self):
        """Lines 569→580: fresh DB, no cache row written."""
        db = SRSDatabase(":memory:")
        steps, source = resolve_learning_steps(db=db)
        assert steps == [1.0, 10.0]
        assert source == "default"

    def test_resolve_learning_steps_returns_default_when_cache_stale(self):
        """Lines 575→580: cache row with updated_at 8+ days old."""
        db = SRSDatabase(":memory:")
        # Write cache with old timestamp
        old_ts = (datetime.now(UTC) - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
        db.set_anki_state_cache_raw("learn_steps", json.dumps([5.0, 15.0]), old_ts)
        steps, source = resolve_learning_steps(db=db)
        assert steps == [1.0, 10.0]
        assert source == "default"

    def test_resolve_learning_steps_returns_default_when_updated_at_invalid(self):
        """Lines 577-578: cache row with invalid updated_at."""
        db = SRSDatabase(":memory:")
        db.set_anki_state_cache_raw("learn_steps", json.dumps([5.0, 15.0]), "not-a-timestamp")
        steps, source = resolve_learning_steps(db=db)
        assert steps == [1.0, 10.0]
        assert source == "default"

    def test_resolve_relearning_steps_returns_default_when_db_is_none(self):
        """Lines 593-594: resolve_relearning_steps with db=None and SRSDatabase creation fails."""
        from app.config import settings

        original_url = settings.database_url
        try:
            settings.database_url = "sqlite:////nonexistent/path/db.sqlite"
            steps, source = resolve_relearning_steps(db=None)
            assert steps == [10.0]
            assert source == "default"
        finally:
            settings.database_url = original_url

    def test_resolve_relearning_steps_returns_default_when_cache_missing(self):
        """Lines 596→607: fresh DB, no cache row written."""
        db = SRSDatabase(":memory:")
        steps, source = resolve_relearning_steps(db=db)
        assert steps == [10.0]
        assert source == "default"

    def test_resolve_relearning_steps_returns_default_when_cache_stale(self):
        """Lines 602→607: cache row with updated_at 8+ days old."""
        db = SRSDatabase(":memory:")
        old_ts = (datetime.now(UTC) - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
        db.set_anki_state_cache_raw("relearn_steps", json.dumps([20.0]), old_ts)
        steps, source = resolve_relearning_steps(db=db)
        assert steps == [10.0]
        assert source == "default"

    def test_resolve_relearning_steps_returns_default_when_updated_at_invalid(self):
        """Lines 604-605: cache row with invalid updated_at."""
        db = SRSDatabase(":memory:")
        db.set_anki_state_cache_raw("relearn_steps", json.dumps([20.0]), "not-a-timestamp")
        steps, source = resolve_relearning_steps(db=db)
        assert steps == [10.0]
        assert source == "default"

    def test_refresh_and_resolve_learning_steps_from_cache(self):
        """After refresh_learning_steps, resolve returns cached value."""
        db = SRSDatabase(":memory:")
        db.set_anki_state_cache("learn_steps", json.dumps([5.0, 15.0]))

        steps, source = resolve_learning_steps(db=db)
        assert steps == [5.0, 15.0]
        assert source == "cache"

    def test_refresh_and_resolve_relearning_steps_from_cache(self):
        """After refresh_learning_steps, resolve returns cached value."""
        db = SRSDatabase(":memory:")
        db.set_anki_state_cache("relearn_steps", json.dumps([20.0]))

        steps, source = resolve_relearning_steps(db=db)
        assert steps == [20.0]
        assert source == "cache"

    def test_resolve_learning_steps_empty_list_from_cache(self):
        """Empty steps list is valid (means graduate immediately)."""
        db = SRSDatabase(":memory:")
        db.set_anki_state_cache("learn_steps", json.dumps([]))

        steps, source = resolve_learning_steps(db=db)
        assert steps == []
        assert source == "cache"

    def test_refresh_learning_steps_with_empty_db(self):
        """refresh_learning_steps doesn't crash with empty DB."""
        db = SRSDatabase(":memory:")
        conn = _make_minimal_anki_db()

        # Should not raise
        refresh_learning_steps(db, conn, "Nonexistent Deck")

        # Should still return defaults
        steps, source = resolve_learning_steps(db=db)
        assert steps == [1.0, 10.0]
        assert source == "default"

    def test_read_learning_steps_returns_none_on_sqlite_error(self):
        """Lines 512-513: _read_learning_steps_from_deck_config_table with closed connection."""
        conn = sqlite3.connect(":memory:")
        conn.close()
        from app.srs.queue_stats import _read_learning_steps_from_deck_config_table

        result = _read_learning_steps_from_deck_config_table(conn, "Test Deck")
        assert result is None

    def test_refresh_learning_steps_writes_both_lists(self):
        """Lines 522-544 + 551-553: refresh_learning_steps writes both learn and relearn steps."""
        db = SRSDatabase(":memory:")
        conn = _make_modern_anki_conn_with_steps(
            deck_name="0. Slovene",
            learn_steps=[1.0, 10.0],
            relearn_steps=[10.0],
        )
        refresh_learning_steps(db, conn, "0. Slovene")

        # Check learn_steps cache
        learn_row = db.get_anki_state_cache("learn_steps")
        assert learn_row is not None
        assert json.loads(learn_row[0]) == [1.0, 10.0]

        # Check relearn_steps cache
        relearn_row = db.get_anki_state_cache("relearn_steps")
        assert relearn_row is not None
        assert json.loads(relearn_row[0]) == [10.0]

    def test_read_learning_steps_returns_none_when_kind_blob_invalid(self):
        """Line 525: _read_learning_steps_from_deck_config_table when kind_blob has no LEN field 1."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, kind BLOB)")
        conn.execute("CREATE TABLE deck_config (id INTEGER PRIMARY KEY, name TEXT, config BLOB)")
        # kind blob with field 1 as VARINT (not LEN) - should make normal_kind_bytes None
        kind_blob = pb_varint_field(1, 123)  # field 1, wire type 0 (VARINT)
        conn.execute("INSERT INTO decks VALUES (1, 'Test', ?)", (kind_blob,))
        conn.commit()

        from app.srs.queue_stats import _read_learning_steps_from_deck_config_table

        result = _read_learning_steps_from_deck_config_table(conn, "Test")
        assert result is None

    def test_read_learning_steps_returns_none_when_conf_id_missing(self):
        """Line 529: _read_learning_steps_from_deck_config_table when conf_id is None."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, kind BLOB)")
        conn.execute("CREATE TABLE deck_config (id INTEGER PRIMARY KEY, name TEXT, config BLOB)")
        # kind blob with field 1 (LEN) containing a submessage without field 1 (conf_id)
        inner = pb_varint_field(2, 999)  # field 2, not field 1 (conf_id)
        kind_blob = pb_len_field(1, inner)  # outer: field 1 (LEN) = inner
        conn.execute("INSERT INTO decks VALUES (1, 'Test', ?)", (kind_blob,))
        conn.commit()

        from app.srs.queue_stats import _read_learning_steps_from_deck_config_table

        result = _read_learning_steps_from_deck_config_table(conn, "Test")
        assert result is None

    def test_read_learning_steps_returns_none_when_config_row_missing(self):
        """Line 533: _read_learning_steps_from_deck_config_table when config row not found."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, kind BLOB)")
        conn.execute("CREATE TABLE deck_config (id INTEGER PRIMARY KEY, name TEXT, config BLOB)")
        # kind blob pointing to conf_id=999 which doesn't exist
        inner = pb_varint_field(1, 999)  # conf_id=999
        kind_blob = pb_len_field(1, inner)
        conn.execute("INSERT INTO decks VALUES (1, 'Test', ?)", (kind_blob,))
        conn.commit()

        from app.srs.queue_stats import _read_learning_steps_from_deck_config_table

        result = _read_learning_steps_from_deck_config_table(conn, "Test")
        assert result is None

    def test_read_learning_steps_returns_none_when_both_steps_none(self):
        """Line 542: _read_learning_steps_from_deck_config_table when both step fields are absent."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, kind BLOB)")
        conn.execute("CREATE TABLE deck_config (id INTEGER PRIMARY KEY, name TEXT, config BLOB)")
        # Config blob with no learn_steps (field 2) or relearn_steps (field 3)
        config_blob = pb_varint_field(9, 20)  # just new_per_day, no step fields
        conn.execute("INSERT INTO deck_config VALUES (1, 'Test', ?)", (config_blob,))
        inner = pb_varint_field(1, 1)  # conf_id=1
        kind_blob = pb_len_field(1, inner)
        conn.execute("INSERT INTO decks VALUES (1, 'Test', ?)", (kind_blob,))
        conn.commit()

        from app.srs.queue_stats import _read_learning_steps_from_deck_config_table

        result = _read_learning_steps_from_deck_config_table(conn, "Test")
        assert result is None
