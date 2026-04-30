"""Tests for resolve_daily_new_cap() — cache-driven resolver chain."""

from __future__ import annotations

from app.srs.database import SRSDatabase
from app.srs.queue_stats import refresh_review_settings, resolve_daily_new_cap


def _encode_varint(value: int) -> bytes:
    """Encode an unsigned integer as a protobuf varint."""
    parts = []
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            parts.append(b | 0x80)
        else:
            parts.append(b)
            break
    return bytes(parts)


def _pb_varint_field(field_num: int, value: int) -> bytes:
    tag = _encode_varint((field_num << 3) | 0)
    return tag + _encode_varint(value)


def _pb_len_field(field_num: int, payload: bytes) -> bytes:
    tag = _encode_varint((field_num << 3) | 2)
    return tag + _encode_varint(len(payload)) + payload


def _make_db() -> SRSDatabase:
    return SRSDatabase(":memory:")


def test_returns_cache_source_when_cache_present():
    db = _make_db()
    db.set_anki_state_cache("daily_new_cap", "30")
    cap, source = resolve_daily_new_cap(db)
    assert cap == 30
    assert source == "cache"


def test_falls_back_to_config_when_no_cache(monkeypatch):
    from app.srs import queue_stats

    db = _make_db()
    monkeypatch.setattr(queue_stats.settings, "anki_new_per_day_default", 25)
    cap, source = resolve_daily_new_cap(db)
    assert cap == 25
    assert source == "config"


def test_falls_back_to_default_when_config_zero(monkeypatch):
    from app.srs import queue_stats

    db = _make_db()
    monkeypatch.setattr(queue_stats.settings, "anki_new_per_day_default", 0)
    cap, source = resolve_daily_new_cap(db)
    assert cap == 20
    assert source == "default"


def test_refresh_review_settings_skips_on_missing_tables(tmp_path):
    """Test that refresh_review_settings returns early when deck_config table is missing."""
    import sqlite3

    db_path = tmp_path / "test.anki2"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE decks (id INTEGER, name TEXT)")
    conn.commit()
    conn.close()

    db = _make_db()
    # Call with a connection that has no deck_config table
    conn = sqlite3.connect(str(db_path))
    refresh_review_settings(db, conn, "nonexistent")
    # Should not raise - just return early
    conn.close()


def test_refresh_review_settings_early_sqlite_error():
    """Closed connection raises sqlite3.ProgrammingError (sqlite3.Error subclass) on first execute."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.close()
    db = _make_db()
    refresh_review_settings(db, conn, "any")  # must not raise — hits L282-283 except handler


def test_refresh_review_settings_skips_on_missing_deck_config_table(tmp_path):
    """Test early return when deck_config table is missing."""
    import sqlite3

    db_path = tmp_path / "test3.anki2"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE decks (id INTEGER, name TEXT, kind BLOB)")
    conn.execute("CREATE TABLE deck_config (id INTEGER, config BLOB)")
    conn.commit()

    db = _make_db()
    refresh_review_settings(db, conn, "deck")
    conn.close()


def test_refresh_review_settings_skips_on_no_deck_found(tmp_path):
    """Test early return when deck is not found."""
    import sqlite3

    db_path = tmp_path / "test4.anki2"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE decks (id INTEGER, name TEXT, kind BLOB)")
    conn.execute("CREATE TABLE deck_config (id INTEGER, config BLOB)")
    conn.commit()

    db = _make_db()
    refresh_review_settings(db, conn, "nonexistent_deck")
    conn.close()


def test_refresh_review_settings_skips_on_empty_kind(tmp_path):
    """Test early return when deck has no kind blob."""
    import sqlite3

    db_path = tmp_path / "test5.anki2"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE decks (id INTEGER, name TEXT, kind BLOB)")
    conn.execute("CREATE TABLE deck_config (id INTEGER, config BLOB)")
    conn.execute("INSERT INTO decks (id, name, kind) VALUES (1, 'Test', NULL)")
    conn.commit()

    db = _make_db()
    refresh_review_settings(db, conn, "Test")
    conn.close()


def test_refresh_review_settings_skips_on_bad_wire_type(tmp_path):
    """Test early return when kind blob field 1 is not LEN wire type."""
    import sqlite3

    db_path = tmp_path / "test6.anki2"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE decks (id INTEGER, name TEXT, kind BLOB)")
    conn.execute("CREATE TABLE deck_config (id INTEGER, config BLOB)")
    # tag 0x08 = field 1, wire=0 (varint), value=5
    # _pb_find_len_field returns None because wire type is not LEN (2)
    kind_blob = b"\x08\x05"
    conn.execute("INSERT INTO decks (id, name, kind) VALUES (1, 'Test', ?)", (kind_blob,))
    conn.commit()

    db = _make_db()
    refresh_review_settings(db, conn, "Test")
    conn.close()


def test_refresh_review_settings_skips_on_no_config_id(tmp_path):
    """Test early return when no config_id found in kind blob."""
    import sqlite3

    db_path = tmp_path / "test7.anki2"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE decks (id INTEGER, name TEXT, kind BLOB)")
    conn.execute("CREATE TABLE deck_config (id INTEGER, config BLOB)")
    # Outer: tag 0x0a (field=1, wire=LEN), length=2
    # Inner submessage: tag 0x10 (field=2, wire=varint), value=5 — no field 1, so conf_id is None
    kind_blob = b"\x0a\x02\x10\x05"
    conn.execute("INSERT INTO decks (id, name, kind) VALUES (1, 'Test', ?)", (kind_blob,))
    conn.commit()

    db = _make_db()
    refresh_review_settings(db, conn, "Test")
    conn.close()


def test_refresh_review_settings_skips_on_no_config_row(tmp_path):
    """Test early return when deck_config has no matching row."""
    import sqlite3

    db_path = tmp_path / "test7.anki2"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE decks (id INTEGER, name TEXT, kind BLOB)")
    conn.execute("CREATE TABLE deck_config (id INTEGER, config BLOB)")
    # Outer: tag 0x0a, length=3. Inner: tag 0x08 (field=1, varint), value=999
    kind_blob = b"\x0a\x03\x08\xe7\x07"
    conn.execute("INSERT INTO decks (id, name, kind) VALUES (1, 'Test', ?)", (kind_blob,))
    conn.commit()

    db = _make_db()
    refresh_review_settings(db, conn, "Test")
    conn.close()


def test_refresh_review_settings_skips_invalid_new_spread(tmp_path):
    """Test that new_spread outside valid range (0,1,2) is not cached."""
    import sqlite3

    db_path = tmp_path / "test_invalid_spread.anki2"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE decks (id INTEGER, name TEXT, kind BLOB)")
    conn.execute("CREATE TABLE deck_config (id INTEGER, config BLOB)")
    # conf_id=1, new_spread=5 (invalid, not in (0,1,2))
    inner = _pb_varint_field(1, 1)  # conf_id=1 inside kind blob
    inner += _pb_varint_field(30, 5)  # new_spread=5 (invalid)
    kind_blob = _pb_len_field(1, inner)
    # config blob with new_spread=5 (invalid, not in (0,1,2))
    config_blob = _pb_varint_field(30, 5)
    conn.execute("INSERT INTO deck_config VALUES (1, ?)", (config_blob,))
    conn.execute("INSERT INTO decks VALUES (1, 'Test', ?)", (kind_blob,))
    conn.commit()

    db = _make_db()
    refresh_review_settings(db, conn, "Test")
    # new_spread=5 is invalid, so cache should NOT be written
    assert db.get_anki_state_cache("new_spread") is None
    conn.close()


def test_refresh_review_settings_skips_on_no_config_blob(tmp_path):
    """Test early return when config row has no blob."""
    import sqlite3

    db_path = tmp_path / "test8.anki2"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE decks (id INTEGER, name TEXT, kind BLOB)")
    conn.execute("CREATE TABLE deck_config (id INTEGER, config BLOB)")
    # Outer: tag 0x0a, length=2. Inner: tag 0x08, value=1
    kind_blob = b"\x0a\x02\x08\x01"
    conn.execute("INSERT INTO decks (id, name, kind) VALUES (1, 'Test', ?)", (kind_blob,))
    # Insert config row with NULL blob
    conn.execute("INSERT INTO deck_config (id, config) VALUES (1, NULL)")
    conn.commit()

    db = _make_db()
    refresh_review_settings(db, conn, "Test")
    conn.close()


def test_resolve_new_spread_db_none(monkeypatch):
    """Test resolve_new_spread when db is None and settings fail."""
    from app.srs.queue_stats import resolve_new_spread

    # Make SRSDatabase raise an exception
    monkeypatch.setattr(
        "app.srs.database.SRSDatabase.__init__", lambda self, x: (_ for _ in ()).throw(Exception("test"))
    )
    val, source = resolve_new_spread(None)
    assert source == "default"
    assert val == 0


def test_resolve_bury_new_db_none(monkeypatch):
    """Test resolve_bury_new when db is None and settings fail."""
    from app.srs.queue_stats import resolve_bury_new

    monkeypatch.setattr(
        "app.srs.database.SRSDatabase.__init__", lambda self, x: (_ for _ in ()).throw(Exception("test"))
    )
    val, source = resolve_bury_new(None)
    assert source == "default"
    assert val is True


def test_resolve_bury_review_db_none(monkeypatch):
    """Test resolve_bury_review when db is None and settings fail."""
    from app.srs.queue_stats import resolve_bury_review

    monkeypatch.setattr(
        "app.srs.database.SRSDatabase.__init__", lambda self, x: (_ for _ in ()).throw(Exception("test"))
    )
    val, source = resolve_bury_review(None)
    assert source == "default"
    assert val is True


def test_resolve_new_spread_cache_too_old(monkeypatch):
    """Test new_spread cache fallback when cache is too old."""
    from datetime import UTC, datetime, timedelta

    from app.srs.queue_stats import resolve_new_spread

    db = _make_db()
    # Set cache with old timestamp
    old_time = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    db.set_anki_state_cache("new_spread", "1")
    # Manually update the timestamp to be old using sqlite3 directly
    import sqlite3

    # Use a temporary database file instead of :memory:
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        temp_db_path = f.name
    conn = sqlite3.connect(temp_db_path)
    # Create the anki_state_cache table
    conn.execute("CREATE TABLE anki_state_cache (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    conn.execute(
        "INSERT INTO anki_state_cache (key, value, updated_at) VALUES (?, ?, ?)",
        ("new_spread", "1", old_time),
    )
    conn.commit()
    conn.close()

    # Now use this database
    from app.srs.database import SRSDatabase

    db = SRSDatabase(temp_db_path)

    val, source = resolve_new_spread(db)
    assert source == "default"
    assert val == 0


def test_resolve_bury_review_from_cache():
    """Test bury_review resolution from cache."""
    from app.srs.queue_stats import resolve_bury_review

    db = _make_db()
    db.set_anki_state_cache("bury_review", "False")

    val, source = resolve_bury_review(db)
    assert source == "cache"
    assert val is False


def test_resolve_new_spread_invalid_value(monkeypatch):
    """Test new_spread cache with invalid value (not in 0,1,2) falls to default."""
    from app.srs.queue_stats import resolve_new_spread

    db = _make_db()
    # Cache has value "5" which is not in (0, 1, 2)
    db.set_anki_state_cache("new_spread", "5")
    val, source = resolve_new_spread(db)
    # Value invalid, should return default
    assert source == "default"
    assert val == 0


def test_resolve_new_spread_invalid_timestamp(monkeypatch):
    """Test new_spread cache with invalid timestamp falls to default."""
    from datetime import UTC, datetime, timedelta

    from app.srs.queue_stats import resolve_new_spread

    db = _make_db()
    # Write cache with invalid timestamp
    old_ts = (datetime.now(UTC) - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
    db._conn.execute(
        "INSERT INTO anki_state_cache (key, value, updated_at) VALUES (?, ?, ?)",
        ("new_spread", "1", old_ts),
    )
    db._conn.commit()
    val, source = resolve_new_spread(db)
    assert source == "default"
    assert val == 0


def test_resolve_bury_new_cache_too_old(monkeypatch):
    """Test bury_new cache fallback when cache is too old."""
    from datetime import UTC, datetime, timedelta

    from app.srs.queue_stats import resolve_bury_new

    db = _make_db()
    db.set_anki_state_cache("bury_new", "False")

    val, source = resolve_bury_new(db)
    # Cache is fresh by default (just set it)
    assert source == "cache"

    # Now make cache stale
    old_ts = (datetime.now(UTC) - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
    db._conn.execute(
        "UPDATE anki_state_cache SET updated_at = ? WHERE key = ?",
        (old_ts, "bury_new"),
    )
    db._conn.commit()
    val, source = resolve_bury_new(db)
    assert source == "default"
    assert val is True


def test_resolve_bury_review_cache_too_old(monkeypatch):
    """Test bury_review cache fallback when cache is too old."""
    from datetime import UTC, datetime, timedelta

    from app.srs.queue_stats import resolve_bury_review

    db = _make_db()
    db.set_anki_state_cache("bury_review", "False")

    val, source = resolve_bury_review(db)
    assert source == "cache"

    # Make cache stale
    old_ts = (datetime.now(UTC) - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
    db._conn.execute(
        "UPDATE anki_state_cache SET updated_at = ? WHERE key = ?",
        (old_ts, "bury_review"),
    )
    db._conn.commit()
    val, source = resolve_bury_review(db)
    assert source == "default"
    assert val is True


def test_resolve_new_spread_corrupt_cache(monkeypatch):
    """Test new_spread with corrupt cache value (triggers exception handler)."""
    from app.srs.queue_stats import resolve_new_spread

    db = _make_db()
    # Insert cache with non-integer value to trigger ValueError in int(value_str)
    db._conn.execute(
        "INSERT INTO anki_state_cache (key, value, updated_at) VALUES (?, ?, datetime('now'))",
        ("new_spread", "not-a-number"),
    )
    db._conn.commit()
    val, source = resolve_new_spread(db)
    assert source == "default"
    assert val == 0


def test_resolve_bury_new_corrupt_cache(monkeypatch):
    """Test bury_new with corrupt cache value."""
    from app.srs.queue_stats import resolve_bury_new

    db = _make_db()
    # Insert cache with corrupt timestamp to trigger exception
    db._conn.execute(
        "INSERT INTO anki_state_cache (key, value, updated_at) VALUES (?, ?, ?)",
        ("bury_new", "True", "not-a-valid-timestamp"),
    )
    db._conn.commit()
    val, source = resolve_bury_new(db)
    assert source == "default"
    assert val is True


def test_resolve_bury_review_corrupt_cache(monkeypatch):
    """Test bury_review with corrupt cache timestamp."""
    from app.srs.queue_stats import resolve_bury_review

    db = _make_db()
    # Insert cache with corrupt timestamp
    db._conn.execute(
        "INSERT INTO anki_state_cache (key, value, updated_at) VALUES (?, ?, ?)",
        ("bury_review", "False", "invalid-timestamp"),
    )
    db._conn.commit()
    val, source = resolve_bury_review(db)
    assert source == "default"
    assert val is True
