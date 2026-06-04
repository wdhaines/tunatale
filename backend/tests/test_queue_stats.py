"""Tests for resolve_daily_new_cap() — cache-driven resolver chain."""

from __future__ import annotations

import json
import sqlite3
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.srs.database import SRSDatabase
from app.srs.queue_stats import refresh_review_settings, resolve_daily_new_cap
from tests._helpers.protobuf import pb_len_field, pb_varint_field


def test_returns_cache_source_when_cache_present():
    db = SRSDatabase(":memory:")
    db.set_anki_state_cache("daily_new_cap", "30")
    cap, source = resolve_daily_new_cap(db)
    assert cap == 30
    assert source == "cache"


def test_falls_back_to_config_when_no_cache(monkeypatch):
    from app.srs import queue_stats

    db = SRSDatabase(":memory:")
    monkeypatch.setattr(queue_stats.settings, "anki_new_per_day_default", 25)
    cap, source = resolve_daily_new_cap(db)
    assert cap == 25
    assert source == "config"


def test_falls_back_to_default_when_config_zero(monkeypatch):
    from app.srs import queue_stats

    db = SRSDatabase(":memory:")
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

    db = SRSDatabase(":memory:")
    # Call with a connection that has no deck_config table
    conn = sqlite3.connect(str(db_path))
    refresh_review_settings(db, conn, "nonexistent")
    # Should not raise - just return early
    conn.close()

    # Confirm the early-return path wrote nothing to the cache
    assert db.get_anki_state_cache("daily_new_cap") is None
    assert db.get_anki_state_cache("new_spread") is None
    assert db.get_anki_state_cache("bury_new") is None
    assert db.get_anki_state_cache("bury_review") is None


def test_refresh_review_settings_early_sqlite_error():
    """Closed connection raises sqlite3.ProgrammingError (sqlite3.Error subclass) on first execute."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.close()
    db = SRSDatabase(":memory:")
    refresh_review_settings(db, conn, "any")  # must not raise — hits L282-283 except handler

    # Confirm the early-return path wrote nothing to the cache
    assert db.get_anki_state_cache("daily_new_cap") is None
    assert db.get_anki_state_cache("new_spread") is None
    assert db.get_anki_state_cache("bury_new") is None
    assert db.get_anki_state_cache("bury_review") is None


def test_refresh_review_settings_skips_on_missing_deck_config_table(tmp_path):
    """Test early return when deck_config table is missing."""
    import sqlite3

    db_path = tmp_path / "test3.anki2"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE decks (id INTEGER, name TEXT, kind BLOB)")
    conn.execute("CREATE TABLE deck_config (id INTEGER, config BLOB)")
    conn.commit()

    db = SRSDatabase(":memory:")
    refresh_review_settings(db, conn, "deck")
    conn.close()

    # Confirm the early-return path wrote nothing to the cache
    assert db.get_anki_state_cache("daily_new_cap") is None
    assert db.get_anki_state_cache("new_spread") is None
    assert db.get_anki_state_cache("bury_new") is None
    assert db.get_anki_state_cache("bury_review") is None


def test_refresh_review_settings_skips_on_no_deck_found(tmp_path):
    """Test early return when deck is not found."""
    import sqlite3

    db_path = tmp_path / "test4.anki2"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE decks (id INTEGER, name TEXT, kind BLOB)")
    conn.execute("CREATE TABLE deck_config (id INTEGER, config BLOB)")
    conn.commit()

    db = SRSDatabase(":memory:")
    refresh_review_settings(db, conn, "nonexistent_deck")
    conn.close()

    # Confirm the early-return path wrote nothing to the cache
    assert db.get_anki_state_cache("daily_new_cap") is None
    assert db.get_anki_state_cache("new_spread") is None
    assert db.get_anki_state_cache("bury_new") is None
    assert db.get_anki_state_cache("bury_review") is None


def test_refresh_review_settings_skips_on_empty_kind(tmp_path):
    """Test early return when deck has no kind blob."""
    import sqlite3

    db_path = tmp_path / "test5.anki2"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE decks (id INTEGER, name TEXT, kind BLOB)")
    conn.execute("CREATE TABLE deck_config (id INTEGER, config BLOB)")
    conn.execute("INSERT INTO decks (id, name, kind) VALUES (1, 'Test', NULL)")
    conn.commit()

    db = SRSDatabase(":memory:")
    refresh_review_settings(db, conn, "Test")
    conn.close()

    # Confirm the early-return path wrote nothing to the cache
    assert db.get_anki_state_cache("daily_new_cap") is None
    assert db.get_anki_state_cache("new_spread") is None
    assert db.get_anki_state_cache("bury_new") is None
    assert db.get_anki_state_cache("bury_review") is None


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

    db = SRSDatabase(":memory:")
    refresh_review_settings(db, conn, "Test")
    conn.close()

    # Confirm the early-return path wrote nothing to the cache
    assert db.get_anki_state_cache("daily_new_cap") is None
    assert db.get_anki_state_cache("new_spread") is None
    assert db.get_anki_state_cache("bury_new") is None
    assert db.get_anki_state_cache("bury_review") is None


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

    db = SRSDatabase(":memory:")
    refresh_review_settings(db, conn, "Test")
    conn.close()

    # Confirm the early-return path wrote nothing to the cache
    assert db.get_anki_state_cache("daily_new_cap") is None
    assert db.get_anki_state_cache("new_spread") is None
    assert db.get_anki_state_cache("bury_new") is None
    assert db.get_anki_state_cache("bury_review") is None


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

    db = SRSDatabase(":memory:")
    refresh_review_settings(db, conn, "Test")
    conn.close()

    # Confirm the early-return path wrote nothing to the cache
    assert db.get_anki_state_cache("daily_new_cap") is None
    assert db.get_anki_state_cache("new_spread") is None
    assert db.get_anki_state_cache("bury_new") is None
    assert db.get_anki_state_cache("bury_review") is None


def test_refresh_review_settings_skips_invalid_new_spread(tmp_path):
    """Test that new_spread outside valid range (0,1,2) is not cached."""
    import sqlite3

    db_path = tmp_path / "test_invalid_spread.anki2"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE decks (id INTEGER, name TEXT, kind BLOB)")
    conn.execute("CREATE TABLE deck_config (id INTEGER, config BLOB)")
    # conf_id=1, new_spread=5 (invalid, not in (0,1,2))
    inner = pb_varint_field(1, 1)  # conf_id=1 inside kind blob
    inner += pb_varint_field(30, 5)  # new_spread=5 (invalid)
    kind_blob = pb_len_field(1, inner)
    # config blob with new_spread=5 (invalid, not in (0,1,2))
    config_blob = pb_varint_field(30, 5)
    conn.execute("INSERT INTO deck_config VALUES (1, ?)", (config_blob,))
    conn.execute("INSERT INTO decks VALUES (1, 'Test', ?)", (kind_blob,))
    conn.commit()

    db = SRSDatabase(":memory:")
    refresh_review_settings(db, conn, "Test")
    # new_spread=5 is invalid, so cache should NOT be written
    assert db.get_anki_state_cache("daily_new_cap") is None
    assert db.get_anki_state_cache("new_spread") is None
    assert db.get_anki_state_cache("bury_new") is None
    assert db.get_anki_state_cache("bury_review") is None
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

    db = SRSDatabase(":memory:")
    refresh_review_settings(db, conn, "Test")
    conn.close()

    # Confirm the early-return path wrote nothing to the cache
    assert db.get_anki_state_cache("daily_new_cap") is None
    assert db.get_anki_state_cache("new_spread") is None
    assert db.get_anki_state_cache("bury_new") is None
    assert db.get_anki_state_cache("bury_review") is None


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

    from app.srs.database import SRSDatabase
    from app.srs.queue_stats import resolve_new_spread

    db = SRSDatabase(":memory:")
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

    db = SRSDatabase(":memory:")
    db.set_anki_state_cache("bury_review", "False")

    val, source = resolve_bury_review(db)
    assert source == "cache"
    assert val is False


def test_resolve_new_spread_invalid_value(monkeypatch):
    """Test new_spread cache with invalid value (not in 0,1,2) falls to default."""
    from app.srs.queue_stats import resolve_new_spread

    db = SRSDatabase(":memory:")
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

    db = SRSDatabase(":memory:")
    # Write cache with invalid timestamp
    old_ts = (datetime.now(UTC) - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
    db.set_anki_state_cache_raw("new_spread", "1", old_ts)
    val, source = resolve_new_spread(db)
    assert source == "default"
    assert val == 0


def test_resolve_bury_new_cache_too_old(monkeypatch):
    """Test bury_new cache fallback when cache is too old."""
    from datetime import UTC, datetime, timedelta

    from app.srs.queue_stats import resolve_bury_new

    db = SRSDatabase(":memory:")
    db.set_anki_state_cache("bury_new", "False")

    val, source = resolve_bury_new(db)
    # Cache is fresh by default (just set it)
    assert source == "cache"

    # Now make cache stale
    old_ts = (datetime.now(UTC) - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
    db.set_anki_state_cache_raw("bury_new", "False", old_ts)
    val, source = resolve_bury_new(db)
    assert source == "default"
    assert val is True


def test_resolve_bury_review_cache_too_old(monkeypatch):
    """Test bury_review cache fallback when cache is too old."""
    from datetime import UTC, datetime, timedelta

    from app.srs.queue_stats import resolve_bury_review

    db = SRSDatabase(":memory:")
    db.set_anki_state_cache("bury_review", "False")

    val, source = resolve_bury_review(db)
    assert source == "cache"

    # Make cache stale
    old_ts = (datetime.now(UTC) - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
    db.set_anki_state_cache_raw("bury_review", "False", old_ts)
    val, source = resolve_bury_review(db)
    assert source == "default"
    assert val is True


def test_resolve_new_spread_corrupt_cache(monkeypatch):
    """Test new_spread with corrupt cache value (triggers exception handler)."""
    from datetime import UTC, datetime

    from app.srs.queue_stats import resolve_new_spread

    db = SRSDatabase(":memory:")
    # Insert cache with non-integer value to trigger ValueError in int(value_str)
    db.set_anki_state_cache_raw("new_spread", "not-a-number", datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"))
    val, source = resolve_new_spread(db)
    assert source == "default"
    assert val == 0


def test_resolve_bury_new_corrupt_cache(monkeypatch):
    """Test bury_new with corrupt cache value."""
    from app.srs.queue_stats import resolve_bury_new

    db = SRSDatabase(":memory:")
    # Insert cache with corrupt timestamp to trigger exception
    db.set_anki_state_cache_raw("bury_new", "True", "not-a-valid-timestamp")
    val, source = resolve_bury_new(db)
    assert source == "default"
    assert val is True


def test_resolve_bury_review_corrupt_cache(monkeypatch):
    """Test bury_review with corrupt cache timestamp."""
    from app.srs.queue_stats import resolve_bury_review

    db = SRSDatabase(":memory:")
    # Insert cache with corrupt timestamp
    db.set_anki_state_cache_raw("bury_review", "False", "invalid-timestamp")
    val, source = resolve_bury_review(db)
    assert source == "default"
    assert val is True


# ---- refresh_daily_new_cap: legacy JSON deck config (col.dconf) ----
#
# refresh_daily_new_cap writes the cap to the cache only when it can read
# one from Anki. These tests pin the legacy-JSON-format branches by asserting
# what does and doesn't land in db.get_anki_state_cache("daily_new_cap").


def _make_legacy_col_conn(tmp_path: Path, name: str, decks_json: str = "", dconf_json: str = "") -> sqlite3.Connection:
    """Build an in-memory-style col-only Anki conn for legacy JSON tests."""
    conn = sqlite3.connect(str(tmp_path / name))
    conn.execute(
        "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
        "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
        "decks TEXT, dconf TEXT, tags TEXT)"
    )
    if decks_json or dconf_json:
        conn.execute(
            "INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', ?, ?, '{}')",
            (decks_json, dconf_json),
        )
    conn.commit()
    return conn


def test_refresh_daily_new_cap_writes_nothing_when_col_row_missing(tmp_path):
    """Empty col table → cache stays empty."""
    from app.srs.queue_stats import refresh_daily_new_cap

    db = SRSDatabase(":memory:")
    conn = _make_legacy_col_conn(tmp_path, "empty.anki2")
    refresh_daily_new_cap(db, conn, "0. Slovene")
    assert db.get_anki_state_cache("daily_new_cap") is None


def test_refresh_daily_new_cap_writes_nothing_on_corrupt_legacy_json(tmp_path):
    """Bad JSON in col.decks/col.dconf → cache stays empty (no crash)."""
    from app.srs.queue_stats import refresh_daily_new_cap

    db = SRSDatabase(":memory:")
    conn = sqlite3.connect(str(tmp_path / "bad_json.anki2"))
    conn.execute(
        "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
        "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
        "decks TEXT, dconf TEXT, tags TEXT)"
    )
    conn.execute("INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, 'not-json', '{}', 'not-json', 'not-json', '{}')")
    conn.commit()
    refresh_daily_new_cap(db, conn, "0. Slovene")
    assert db.get_anki_state_cache("daily_new_cap") is None


def test_refresh_daily_new_cap_caches_value_from_legacy_json(tmp_path):
    """Legacy JSON deck config with perDay=15 → cache is set to "15"."""
    from app.srs.queue_stats import refresh_daily_new_cap

    db = SRSDatabase(":memory:")
    conn = _make_legacy_col_conn(
        tmp_path,
        "legacy.anki2",
        decks_json=json.dumps({"1": {"id": 1, "name": "0. Slovene", "conf": 1}}),
        dconf_json=json.dumps({"1": {"id": 1, "name": "Default", "new": {"perDay": 15}}}),
    )
    refresh_daily_new_cap(db, conn, "0. Slovene")
    row = db.get_anki_state_cache("daily_new_cap")
    assert row is not None
    assert row[0] == "15"


def test_refresh_daily_new_cap_writes_nothing_when_legacy_conf_id_absent(tmp_path):
    """Deck points to conf_id=999 that's not in dconf → cache stays empty."""
    from app.srs.queue_stats import refresh_daily_new_cap

    db = SRSDatabase(":memory:")
    conn = _make_legacy_col_conn(
        tmp_path,
        "legacy_no_conf.anki2",
        decks_json=json.dumps({"1": {"id": 1, "name": "0. Slovene", "conf": 999}}),
        dconf_json=json.dumps({"1": {"id": 1, "name": "Default", "new": {"perDay": 15}}}),
    )
    refresh_daily_new_cap(db, conn, "0. Slovene")
    assert db.get_anki_state_cache("daily_new_cap") is None


def test_refresh_daily_new_cap_writes_nothing_when_legacy_perday_not_numeric(tmp_path):
    """perDay is a string → int() raises, cache stays empty."""
    from app.srs.queue_stats import refresh_daily_new_cap

    db = SRSDatabase(":memory:")
    conn = _make_legacy_col_conn(
        tmp_path,
        "legacy_bad_perday.anki2",
        decks_json=json.dumps({"1": {"id": 1, "name": "0. Slovene", "conf": 1}}),
        dconf_json=json.dumps({"1": {"id": 1, "name": "Default", "new": {"perDay": "fifteen"}}}),
    )
    refresh_daily_new_cap(db, conn, "0. Slovene")
    assert db.get_anki_state_cache("daily_new_cap") is None


def test_refresh_daily_new_cap_writes_nothing_when_legacy_new_key_missing(tmp_path):
    """conf has no 'new' key → KeyError, cache stays empty."""
    from app.srs.queue_stats import refresh_daily_new_cap

    db = SRSDatabase(":memory:")
    conn = _make_legacy_col_conn(
        tmp_path,
        "legacy_no_new.anki2",
        decks_json=json.dumps({"1": {"id": 1, "name": "0. Slovene", "conf": 1}}),
        dconf_json=json.dumps({"1": {"id": 1, "name": "Default"}}),
    )
    refresh_daily_new_cap(db, conn, "0. Slovene")
    assert db.get_anki_state_cache("daily_new_cap") is None


def test_resolve_daily_new_cap_db_creation_fails(monkeypatch):
    """Lines 332-337: db is None and SRSDatabase creation fails."""
    from app.srs.queue_stats import resolve_daily_new_cap

    monkeypatch.setattr(
        "app.srs.database.SRSDatabase.__init__", lambda self, x: (_ for _ in ()).throw(Exception("test"))
    )
    # Make config default 0 so it falls through to hard default
    monkeypatch.setattr("app.srs.queue_stats.settings.anki_new_per_day_default", 0)
    cap, source = resolve_daily_new_cap(None)
    assert source == "default"
    assert cap == 20


def test_resolve_daily_new_cap_corrupt_cache_value(monkeypatch):
    """Lines 347-348: Cache has invalid value_str that int() raises ValueError."""
    from app.srs.queue_stats import resolve_daily_new_cap

    db = SRSDatabase(":memory:")
    # Insert cache with non-integer value to trigger ValueError in int(value_str)
    db.set_anki_state_cache_raw("daily_new_cap", "not-a-number", datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"))
    cap, source = resolve_daily_new_cap(db)
    # Falls through to config/default
    assert source in ("config", "default")


def test_resolve_daily_new_cap_cache_too_old(monkeypatch):
    """Lines 345->350: Cache exists but is older than _CACHE_MAX_AGE_DAYS (30 days)."""
    from app.srs.queue_stats import resolve_daily_new_cap

    db = SRSDatabase(":memory:")
    # Set cache with timestamp older than 30 days
    old_ts = (datetime.now(UTC) - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
    db.set_anki_state_cache_raw("daily_new_cap", "30", old_ts)
    cap, source = resolve_daily_new_cap(db)
    # Should fall through to config/default since cache is stale
    assert source in ("config", "default")


def test_resolve_daily_new_cap_corrupt_cache_timestamp(monkeypatch):
    """Lines 347-348: Cache has invalid timestamp that fromisoformat() raises ValueError."""
    from app.srs.queue_stats import resolve_daily_new_cap

    db = SRSDatabase(":memory:")
    # Insert cache with corrupt timestamp
    db.set_anki_state_cache_raw("daily_new_cap", "30", "not-a-valid-timestamp")
    cap, source = resolve_daily_new_cap(db)
    # Falls through to config/default
    assert source in ("config", "default")


# ── resolve_daily_review_cap ────────────────────────────────────────────────────


def test_resolve_daily_review_cap_db_creation_fails(monkeypatch):
    """db is None and SRSDatabase creation fails."""
    from app.srs.queue_stats import resolve_daily_review_cap

    monkeypatch.setattr(
        "app.srs.database.SRSDatabase.__init__", lambda self, x: (_ for _ in ()).throw(Exception("test"))
    )
    monkeypatch.setattr("app.srs.queue_stats.settings.anki_reviews_per_day_default", 0)
    cap, source = resolve_daily_review_cap(None)
    assert source == "default"
    assert cap == 200


def test_resolve_daily_review_cap_cache_corrupt_timestamp():
    """Cache has invalid timestamp that fromisoformat raises ValueError."""
    from app.srs.queue_stats import resolve_daily_review_cap

    db = SRSDatabase(":memory:")
    db.set_anki_state_cache_raw("daily_review_cap", "30", "not-a-valid-timestamp")
    cap, source = resolve_daily_review_cap(db)
    assert source in ("config", "default")


def test_resolve_daily_review_cap_cache_too_old():
    """Cache exists but is older than _CACHE_MAX_AGE_DAYS (30 days)."""
    from app.srs.queue_stats import resolve_daily_review_cap

    db = SRSDatabase(":memory:")
    old_ts = (datetime.now(UTC) - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
    db.set_anki_state_cache_raw("daily_review_cap", "30", old_ts)
    cap, source = resolve_daily_review_cap(db)
    assert source in ("config", "default")


def test_refresh_desired_retention_skips_when_config_row_missing(tmp_path):
    """deck points to a conf_id that doesn't exist in deck_config → cache untouched."""
    import sqlite3

    from app.srs.queue_stats import refresh_desired_retention

    db = SRSDatabase(":memory:")
    conn = sqlite3.connect(":memory:")
    # Decks table has a deck whose kind points to conf_id=999 — but deck_config has no row 999.
    from tests._helpers.protobuf import pb_len_field, pb_varint_field

    conn.executescript("""
        CREATE TABLE decks (id INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, common BLOB, kind BLOB);
        CREATE TABLE deck_config (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB);
    """)
    kind_blob = pb_len_field(1, pb_varint_field(1, 999))
    conn.execute("INSERT INTO decks VALUES (1, ?, 0, 0, NULL, ?)", ("0. Slovene", kind_blob))
    conn.commit()
    refresh_desired_retention(db, conn, "0. Slovene")
    assert db.get_anki_state_cache("desired_retention") is None


# ── FSRS-6 protobuf field-6 reader tests ──────────────────────────────────────


def _packed_float_field(field_num: int, floats: list[float]) -> bytes:
    """Build a protobuf LEN-delimited packed f32 field."""
    payload = struct.pack(f"<{len(floats)}f", *floats)
    return pb_len_field(field_num, payload)


def _make_deck_config_blob(tmp_path, deck_name, config_blob: bytes, conf_id: int = 1):
    """Create an Anki DB with given deck_config protobuf and return a conn."""
    conn = sqlite3.connect(str(tmp_path / "test_fsrs6.anki2"))
    conn.executescript("""
        CREATE TABLE decks (id INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, common BLOB, kind BLOB);
        CREATE TABLE deck_config (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB);
    """)
    kind_blob = pb_len_field(1, pb_varint_field(1, conf_id))
    conn.execute("INSERT INTO decks VALUES (1, ?, 0, 0, NULL, ?)", (deck_name, kind_blob))
    conn.execute(
        "INSERT INTO deck_config VALUES (?, 'Default', 0, 0, ?)",
        (conf_id, config_blob),
    )
    conn.commit()
    return conn


class TestReadFSRSParamsFromDeckConfig:
    """Tests for _read_fsrs_params_from_deck_config_table with FSRS-6 support."""

    def test_reads_fsrs6_field_6(self, tmp_path):
        """Field 6 with 21 packed floats → FSRS-6 params with version=6."""
        from app.srs.queue_stats import _read_fsrs_params_from_deck_config_table

        fsrs6_weights = [0.4 + i * 0.01 for i in range(21)]
        config_blob = _packed_float_field(6, fsrs6_weights)
        conn = _make_deck_config_blob(tmp_path, "Test", config_blob, conf_id=1)
        result = _read_fsrs_params_from_deck_config_table(conn, "Test")
        conn.close()

        assert result is not None
        assert result.version == 6
        # Protobuf packed f32 loses precision; compare with tolerance
        assert result.decay == pytest.approx(fsrs6_weights[20], abs=1e-6)
        for a, b in zip(result.weights, fsrs6_weights, strict=True):
            assert a == pytest.approx(b, abs=1e-6)

    def test_prefers_field_6_when_both_present(self, tmp_path):
        """Both field 5 (19 floats) and field 6 (21 floats) → returns FSRS-6."""
        from app.srs.queue_stats import _read_fsrs_params_from_deck_config_table

        fsrs5_weights = [0.4 + i * 0.01 for i in range(19)]
        fsrs6_weights = [0.4 + i * 0.01 for i in range(21)]
        # Build config blob with both fields
        config_blob = _packed_float_field(5, fsrs5_weights) + _packed_float_field(6, fsrs6_weights)
        conn = _make_deck_config_blob(tmp_path, "Test", config_blob, conf_id=1)
        result = _read_fsrs_params_from_deck_config_table(conn, "Test")
        conn.close()

        assert result is not None
        assert result.version == 6, "field 6 (21 weights) must be preferred"
        for a, b in zip(result.weights, fsrs6_weights, strict=True):
            assert a == pytest.approx(b, abs=1e-6)

    def test_falls_back_to_field_5_when_field_6_is_19_floats(self, tmp_path):
        """Field 6 with 19 floats (Anki dual-write artifact) → fall back to field 5."""
        from app.srs.queue_stats import _read_fsrs_params_from_deck_config_table

        fsrs5_weights = [0.4 + i * 0.01 for i in range(19)]
        fsrs6_19 = [0.5 + i * 0.01 for i in range(19)]  # field 6 has 19 floats (dual-write)
        config_blob = _packed_float_field(5, fsrs5_weights) + _packed_float_field(6, fsrs6_19)
        conn = _make_deck_config_blob(tmp_path, "Test", config_blob, conf_id=1)
        result = _read_fsrs_params_from_deck_config_table(conn, "Test")
        conn.close()

        assert result is not None
        assert result.version == 5, "field 5 (19 weights) must be used when field 6 has 19 floats"
        for a, b in zip(result.weights, fsrs5_weights, strict=True):
            assert a == pytest.approx(b, abs=1e-6)

    def test_falls_back_to_defaults_when_no_field(self, tmp_path):
        """No field 5 or 6 → returns None."""
        from app.srs.queue_stats import _read_fsrs_params_from_deck_config_table

        config_blob = b""  # empty config
        conn = _make_deck_config_blob(tmp_path, "Test", config_blob, conf_id=1)
        result = _read_fsrs_params_from_deck_config_table(conn, "Test")
        conn.close()
        assert result is None

    def test_reads_fsrs_short_term_when_present(self, tmp_path):
        """_read_fsrs_short_term_from_config_table returns True for b'true'."""
        from app.srs.queue_stats import _read_fsrs_short_term_from_config_table

        db_path = tmp_path / "test.anki2"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE config (key TEXT PRIMARY KEY, val BLOB, mtime_secs INT, usn INT)")
        conn.execute("INSERT INTO config (key, val) VALUES ('fsrsShortTermWithStepsEnabled', ?)", (b"true",))
        conn.commit()

        assert _read_fsrs_short_term_from_config_table(conn) is True
        conn.close()

    def test_reads_fsrs_short_term_when_false(self, tmp_path):
        """_read_fsrs_short_term_from_config_table returns False for b'false'."""
        from app.srs.queue_stats import _read_fsrs_short_term_from_config_table

        db_path = tmp_path / "test.anki2"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE config (key TEXT PRIMARY KEY, val BLOB, mtime_secs INT, usn INT)")
        conn.execute("INSERT INTO config (key, val) VALUES ('fsrsShortTermWithStepsEnabled', ?)", (b"false",))
        conn.commit()

        assert _read_fsrs_short_term_from_config_table(conn) is False
        conn.close()

    def test_reads_fsrs_short_term_when_missing(self, tmp_path):
        """_read_fsrs_short_term_from_config_table returns None when key absent."""
        from app.srs.queue_stats import _read_fsrs_short_term_from_config_table

        db_path = tmp_path / "test.anki2"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE config (key TEXT PRIMARY KEY, val BLOB, mtime_secs INT, usn INT)")
        conn.commit()

        assert _read_fsrs_short_term_from_config_table(conn) is None
        conn.close()

    def test_refresh_fsrs_short_term_flag_writes_cache(self, tmp_path):
        """refresh_fsrs_short_term_flag writes the flag to anki_state_cache."""
        from app.srs.queue_stats import refresh_fsrs_short_term_flag

        db_path = tmp_path / "test.anki2"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE config (key TEXT PRIMARY KEY, val BLOB, mtime_secs INT, usn INT)")
        conn.execute("INSERT INTO config (key, val) VALUES ('fsrsShortTermWithStepsEnabled', ?)", (b"true",))
        conn.commit()

        db = SRSDatabase(":memory:")
        refresh_fsrs_short_term_flag(db, conn)
        row = db.get_anki_state_cache("fsrs_short_term_with_steps_enabled")
        assert row is not None
        assert row[0] == "true"
        conn.close()


class TestResolveFSRSParams:
    """Tests for refresh_fsrs_params + resolve_fsrs_params with version."""

    def test_resolve_fsrs_params_round_trip_with_version(self, srs_db):
        """resolve_fsrs_params returns cached params with correct version after refresh."""
        import json

        from app.srs.queue_stats import resolve_fsrs_params

        fsrs6_weights = list(range(21))  # dummy FSRS-6 weights
        fsrs6_weights[20] = 0.1542  # decay param
        srs_db.set_anki_state_cache(
            "fsrs_params",
            json.dumps(
                {
                    "weights": fsrs6_weights,
                    "desired_retention": 0.9,
                    "version": 6,
                }
            ),
        )

        params, source = resolve_fsrs_params(srs_db)
        assert source == "cache"
        assert params.version == 6
        assert params.decay == fsrs6_weights[20]

    def test_backward_compat_no_version_in_cache(self, srs_db):
        """Old cache rows without 'version' still work; version inferred from weight count."""
        import json

        from app.srs.queue_stats import resolve_fsrs_params

        # Old cache format: no "version" key
        fsrs5_weights = [0.4 + i * 0.01 for i in range(19)]
        srs_db.set_anki_state_cache(
            "fsrs_params",
            json.dumps(
                {
                    "weights": fsrs5_weights,
                    "desired_retention": 0.9,
                }
            ),
        )

        params, source = resolve_fsrs_params(srs_db)
        assert source == "cache"
        # Without 'version' in cache, FSRSParams infers from weight count
        assert params.version == 5
        assert params.decay == 0.5


class TestMaximumReviewInterval:
    """Tests for maximum_review_interval reading and resolution."""

    def test_read_maximum_review_interval_from_deck_config(self, tmp_path):
        """Field 16 (VARINT uint32) is read from deck_config."""
        from app.srs.queue_stats import _read_config_value_from_deck_config_table

        blob = pb_varint_field(16, 36500)
        conn = _make_deck_config_blob(tmp_path, "Test", blob)
        result = _read_config_value_from_deck_config_table(conn, "Test", proto_field=16, wire_type=0)
        conn.close()
        assert result == 36500

    def test_read_maximum_review_interval_absent_returns_none(self, tmp_path):
        """No field 16 in blob → None."""
        from app.srs.queue_stats import _read_config_value_from_deck_config_table

        blob = pb_varint_field(9, 20)  # only new_per_day, no field 16
        conn = _make_deck_config_blob(tmp_path, "Test", blob)
        result = _read_config_value_from_deck_config_table(conn, "Test", proto_field=16, wire_type=0)
        conn.close()
        assert result is None

    def test_resolve_maximum_review_interval_returns_cache(self, srs_db):
        """When cache is set and fresh, returns the cached value."""
        from app.srs.queue_stats import resolve_maximum_review_interval

        srs_db.set_anki_state_cache("maximum_review_interval", "36500")
        val, source = resolve_maximum_review_interval(srs_db)
        assert val == 36500
        assert source == "cache"

    def test_resolve_maximum_review_interval_fallback_default(self, srs_db):
        """No cache → returns hard default 36500."""
        from app.srs.queue_stats import resolve_maximum_review_interval

        val, source = resolve_maximum_review_interval(srs_db)
        assert val == 36500
        assert source == "default"

    def test_resolve_maximum_review_interval_stale_cache(self, srs_db):
        """Cache older than 30 days → falls back to default."""
        from datetime import timedelta

        from app.srs.queue_stats import resolve_maximum_review_interval

        old_ts = (datetime.now(UTC) - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
        srs_db.set_anki_state_cache_raw("maximum_review_interval", "1000", old_ts)
        val, source = resolve_maximum_review_interval(srs_db)
        assert source == "default"

    def test_resolve_maximum_review_interval_corrupted_cache(self, srs_db):
        """Corrupted cache value (non-int) → falls back to default."""
        from app.srs.queue_stats import resolve_maximum_review_interval

        srs_db.set_anki_state_cache_raw("maximum_review_interval", "not_a_number", datetime.now(UTC).isoformat())
        val, source = resolve_maximum_review_interval(srs_db)
        assert source == "default"

    def test_resolve_maximum_review_interval_no_db_creates_fresh(self):
        """When db=None, creates a fresh in-memory DB and returns default."""
        from unittest.mock import patch

        from app.config import settings as app_settings
        from app.srs.queue_stats import resolve_maximum_review_interval

        with patch.object(app_settings, "database_url", "sqlite:///:memory:"):
            val, source = resolve_maximum_review_interval(db=None)
        assert val == 36500
        assert source == "default"

    def test_resolve_maximum_review_interval_no_db_fallback(self):
        """When db=None and DB creation fails, returns default."""
        from unittest.mock import patch

        from app.srs.database import SRSDatabase
        from app.srs.queue_stats import resolve_maximum_review_interval

        call_count = 0

        def _fail_init(self, db_path=":memory:"):
            nonlocal call_count
            call_count += 1
            raise Exception("simulated failure")

        with patch.object(SRSDatabase, "__init__", _fail_init):
            val, source = resolve_maximum_review_interval(db=None)
        assert call_count == 1, f"expected 1 __init__ call, got {call_count}"
        assert val == 36500
        assert source == "default"
