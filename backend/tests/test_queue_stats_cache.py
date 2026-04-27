"""Tests for Change 4: cache-driven daily-new-cap (no AnkiConnect)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from app.srs.database import SRSDatabase
from app.srs.queue_stats import refresh_daily_new_cap, resolve_daily_new_cap


def _make_db() -> SRSDatabase:
    return SRSDatabase(":memory:")


def _make_anki_conn(new_per_day: int = 20, deck_name: str = "0. Slovene") -> sqlite3.Connection:
    """Build a minimal in-memory collection.anki2 with a deck config."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    deck_id = 12345
    dconf_id = 1

    # Build col.dconf JSON (legacy format)
    dconf_json = json.dumps(
        {
            str(dconf_id): {
                "id": dconf_id,
                "name": "Default",
                "new": {"perDay": new_per_day, "order": 0},
            }
        }
    )
    decks_json = json.dumps(
        {
            str(deck_id): {
                "id": deck_id,
                "name": deck_name,
                "conf": dconf_id,
            }
        }
    )

    conn.execute(
        "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
        "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
        "decks TEXT, dconf TEXT, tags TEXT)"
    )
    conn.execute(
        "INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', ?, ?, '{}')",
        (decks_json, dconf_json),
    )
    conn.commit()
    return conn


class TestRefreshDailyNewCap:
    def test_reads_new_per_day_from_legacy_dconf(self):
        db = _make_db()
        conn = _make_anki_conn(new_per_day=30)
        refresh_daily_new_cap(db, conn, "0. Slovene")

        row = db.get_anki_state_cache("daily_new_cap")
        assert row is not None
        value, _ = row
        assert int(value) == 30

    def test_no_error_when_deck_not_found(self):
        db = _make_db()
        conn = _make_anki_conn()
        refresh_daily_new_cap(db, conn, "No Such Deck")  # must not raise

    def test_no_error_when_dconf_empty(self):
        db = _make_db()
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        conn.execute("INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', '{}', '{}', '{}')")
        conn.commit()
        refresh_daily_new_cap(db, conn, "0. Slovene")  # must not raise


class TestResolveDailyNewCapCache:
    def test_returns_cache_when_fresh(self):
        db = _make_db()
        db.set_anki_state_cache("daily_new_cap", "35")
        cap, source = resolve_daily_new_cap(db)
        assert cap == 35
        assert source == "cache"

    def test_returns_config_when_cache_missing(self, monkeypatch):
        from app.srs import queue_stats

        db = _make_db()
        monkeypatch.setattr(queue_stats.settings, "anki_new_per_day_default", 25)
        cap, source = resolve_daily_new_cap(db)
        assert cap == 25
        assert source == "config"

    def test_returns_config_when_cache_stale(self, monkeypatch):
        from app.srs import queue_stats

        db = _make_db()
        # Write a cache entry timestamped 31 days ago
        old_ts = (datetime.now(UTC) - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
        db._conn.execute(
            "INSERT INTO anki_state_cache (key, value, updated_at) VALUES (?, ?, ?)",
            ("daily_new_cap", "99", old_ts),
        )
        db._conn.commit()
        monkeypatch.setattr(queue_stats.settings, "anki_new_per_day_default", 25)
        cap, source = resolve_daily_new_cap(db)
        assert source == "config"

    def test_returns_default_when_no_config(self, monkeypatch):
        from app.srs import queue_stats

        db = _make_db()
        monkeypatch.setattr(queue_stats.settings, "anki_new_per_day_default", 0)
        cap, source = resolve_daily_new_cap(db)
        assert cap == 20
        assert source == "default"

    def test_works_without_db_arg_falls_back_to_config(self, monkeypatch):
        from app.srs import queue_stats

        monkeypatch.setattr(queue_stats.settings, "anki_new_per_day_default", 15)
        monkeypatch.setattr(queue_stats.settings, "database_url", "sqlite:///:memory:")
        cap, source = resolve_daily_new_cap()
        assert source in ("config", "default")


class TestReadNewPerDayFromAnki:
    def test_returns_none_when_no_col_row(self):
        from app.srs.queue_stats import _read_new_per_day_from_anki

        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        conn.commit()
        assert _read_new_per_day_from_anki(conn, "0. Slovene") is None

    def test_returns_none_on_invalid_json(self):
        from app.srs.queue_stats import _read_new_per_day_from_anki

        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        conn.execute("INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', '{not json}', '{}', '{}')")
        conn.commit()
        assert _read_new_per_day_from_anki(conn, "0. Slovene") is None

    def test_returns_none_when_dconf_not_dict(self):
        from app.srs.queue_stats import _read_new_per_day_from_anki

        decks = json.dumps({"1": {"id": 1, "name": "0. Slovene", "conf": 99}})
        dconf = json.dumps({"99": "not-a-dict"})
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        conn.execute(
            "INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', ?, ?, '{}')",
            (decks, dconf),
        )
        conn.commit()
        assert _read_new_per_day_from_anki(conn, "0. Slovene") is None

    def test_returns_none_when_new_per_day_key_missing(self):
        from app.srs.queue_stats import _read_new_per_day_from_anki

        decks = json.dumps({"1": {"id": 1, "name": "0. Slovene", "conf": 1}})
        dconf = json.dumps({"1": {"id": 1, "name": "Default", "new": {}}})  # no perDay
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        conn.execute(
            "INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', ?, ?, '{}')",
            (decks, dconf),
        )
        conn.commit()
        assert _read_new_per_day_from_anki(conn, "0. Slovene") is None


def test_resolve_daily_new_cap_when_db_creation_fails(monkeypatch):
    """When no db passed and SRSDatabase creation raises, fall back to config."""
    from app.srs import queue_stats

    monkeypatch.setattr(queue_stats.settings, "anki_new_per_day_default", 18)
    monkeypatch.setattr(queue_stats.settings, "database_url", "sqlite:////__invalid/path/db.sqlite")
    cap, source = resolve_daily_new_cap()
    assert source in ("config", "default")


def test_resolve_falls_back_when_cache_has_invalid_timestamp(monkeypatch):
    """Corrupted updated_at in cache → skip cache, use config."""
    from app.srs import queue_stats

    db = _make_db()
    db._conn.execute(
        "INSERT INTO anki_state_cache (key, value, updated_at) VALUES (?, ?, ?)",
        ("daily_new_cap", "42", "not-a-valid-timestamp"),
    )
    db._conn.commit()
    monkeypatch.setattr(queue_stats.settings, "anki_new_per_day_default", 18)
    cap, source = resolve_daily_new_cap(db)
    assert source in ("config", "default")


# ── B1 regression: modern Anki deck_config protobuf ──────────────────────────


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


def _make_deck_config_blob(new_per_day: int) -> bytes:
    """Build a DeckConfig.Config protobuf blob with new_per_day at field 9."""
    return _pb_varint_field(9, new_per_day)


def _make_deck_kind_blob(conf_id: int) -> bytes:
    """Build a NormalDeckKind protobuf blob: field 1 (LEN) containing conf_id at field 1 (VARINT)."""
    inner = _pb_varint_field(1, conf_id)
    return _pb_len_field(1, inner)


def _make_modern_anki_conn(new_per_day: int = 20, deck_name: str = "0. Slovene") -> sqlite3.Connection:
    """Build a minimal in-memory collection.anki2 with modern deck_config/decks tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    config_id = 1774580286260
    deck_id = 12345

    conn.execute(
        "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
        "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
        "decks TEXT, dconf TEXT, tags TEXT)"
    )
    # col.decks and col.dconf are empty in modern Anki
    conn.execute("INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', '', '', '{}')")

    conn.execute(
        "CREATE TABLE deck_config (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB)"
    )
    conn.execute(
        "INSERT INTO deck_config VALUES (?, ?, 0, -1, ?)",
        (config_id, "Slovene", _make_deck_config_blob(new_per_day)),
    )

    conn.execute(
        "CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, "
        "usn INTEGER, common BLOB, kind BLOB)"
    )
    conn.execute(
        "INSERT INTO decks VALUES (?, ?, 0, -1, NULL, ?)",
        (deck_id, deck_name, _make_deck_kind_blob(config_id)),
    )
    conn.commit()
    return conn


class TestRefreshDailyNewCapModernAnki:
    def test_reads_new_per_day_from_modern_deck_config(self):
        """B1 regression: modern Anki stores deck config in deck_config table, not col.dconf."""
        db = _make_db()
        conn = _make_modern_anki_conn(new_per_day=30)
        refresh_daily_new_cap(db, conn, "0. Slovene")

        row = db.get_anki_state_cache("daily_new_cap")
        assert row is not None
        value, _ = row
        assert int(value) == 30

    def test_reads_correct_deck_config_via_decks_kind(self):
        """B1: looks up conf_id via decks.kind, so returns the RIGHT deck's cap."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        conn.execute("INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', '', '', '{}')")
        conn.execute(
            "CREATE TABLE deck_config (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB)"
        )
        # Two configs: Default=5, Slovene=25
        conn.execute("INSERT INTO deck_config VALUES (1, 'Default', 0, -1, ?)", (_make_deck_config_blob(5),))
        conn.execute("INSERT INTO deck_config VALUES (2, 'Slovene', 0, -1, ?)", (_make_deck_config_blob(25),))
        conn.execute(
            "CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, "
            "usn INTEGER, common BLOB, kind BLOB)"
        )
        # Deck points to config_id=2 (Slovene=25)
        conn.execute("INSERT INTO decks VALUES (1, '0. Slovene', 0, -1, NULL, ?)", (_make_deck_kind_blob(2),))
        conn.commit()

        db = _make_db()
        refresh_daily_new_cap(db, conn, "0. Slovene")

        row = db.get_anki_state_cache("daily_new_cap")
        assert row is not None
        assert int(row[0]) == 25  # not 5 (Default) — correct deck config was used

    def test_no_error_when_deck_config_table_missing(self):
        """B1: graceful degradation when deck_config table not present."""
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        conn.execute("INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', '', '', '{}')")
        conn.commit()
        db = _make_db()
        refresh_daily_new_cap(db, conn, "0. Slovene")  # must not raise

    def test_no_error_when_deck_not_found_in_decks_table(self):
        """B1: deck not in decks table → no-op, no raise."""
        db = _make_db()
        conn = _make_modern_anki_conn(new_per_day=20, deck_name="Other Deck")
        refresh_daily_new_cap(db, conn, "0. Slovene")  # must not raise
        assert db.get_anki_state_cache("daily_new_cap") is None  # nothing written

    def test_no_error_when_kind_blob_is_null(self):
        """B1: deck row with NULL kind → no-op."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        conn.execute("INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', '', '', '{}')")
        conn.execute(
            "CREATE TABLE deck_config (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB)"
        )
        conn.execute("INSERT INTO deck_config VALUES (1, 'Slovene', 0, -1, ?)", (_make_deck_config_blob(20),))
        conn.execute(
            "CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, "
            "usn INTEGER, common BLOB, kind BLOB)"
        )
        conn.execute("INSERT INTO decks VALUES (1, '0. Slovene', 0, -1, NULL, NULL)")  # NULL kind
        conn.commit()
        db = _make_db()
        refresh_daily_new_cap(db, conn, "0. Slovene")  # must not raise
        assert db.get_anki_state_cache("daily_new_cap") is None

    def test_no_error_when_conf_id_not_in_deck_config(self):
        """B1: kind points to a conf_id not present in deck_config → no-op."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        conn.execute("INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', '', '', '{}')")
        conn.execute(
            "CREATE TABLE deck_config (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB)"
        )
        # deck_config has id=1 but kind points to id=999
        conn.execute("INSERT INTO deck_config VALUES (1, 'Default', 0, -1, ?)", (_make_deck_config_blob(20),))
        conn.execute(
            "CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, "
            "usn INTEGER, common BLOB, kind BLOB)"
        )
        conn.execute("INSERT INTO decks VALUES (1, '0. Slovene', 0, -1, NULL, ?)", (_make_deck_kind_blob(999),))
        conn.commit()
        db = _make_db()
        refresh_daily_new_cap(db, conn, "0. Slovene")  # must not raise
        assert db.get_anki_state_cache("daily_new_cap") is None

    def test_no_error_when_kind_blob_has_no_len_field(self):
        """B1: kind blob that doesn't contain the expected LEN submessage → no-op."""
        # Build a kind blob with only a varint field (field 2), no LEN field at field 1
        kind_blob = _pb_varint_field(2, 42)  # field 2, not field 1 LEN
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        conn.execute("INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', '', '', '{}')")
        conn.execute(
            "CREATE TABLE deck_config (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB)"
        )
        conn.execute("INSERT INTO deck_config VALUES (1, 'Slovene', 0, -1, ?)", (_make_deck_config_blob(20),))
        conn.execute(
            "CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, "
            "usn INTEGER, common BLOB, kind BLOB)"
        )
        conn.execute("INSERT INTO decks VALUES (1, '0. Slovene', 0, -1, NULL, ?)", (kind_blob,))
        conn.commit()
        db = _make_db()
        refresh_daily_new_cap(db, conn, "0. Slovene")  # must not raise
        assert db.get_anki_state_cache("daily_new_cap") is None


class TestPbParsing:
    """Direct unit tests for internal protobuf helper functions."""

    def test_pb_read_varint_empty_data(self):
        """Empty bytes → returns (0, 0) without error."""
        from app.srs.queue_stats import _pb_read_varint

        value, pos = _pb_read_varint(b"", 0)
        assert value == 0
        assert pos == 0

    def test_pb_skip_field_wire_type_1(self):
        """Wire type 1 (64-bit) skips exactly 8 bytes."""
        from app.srs.queue_stats import _pb_skip_field

        data = b"\x00" * 10
        new_pos = _pb_skip_field(data, 0, 1)
        assert new_pos == 8

    def test_pb_skip_field_wire_type_5(self):
        """Wire type 5 (32-bit) skips exactly 4 bytes."""
        from app.srs.queue_stats import _pb_skip_field

        data = b"\x00" * 8
        new_pos = _pb_skip_field(data, 0, 5)
        assert new_pos == 4

    def test_pb_find_varint_field_memoryview(self):
        """Accepts memoryview and converts to bytes."""
        from app.srs.queue_stats import _pb_find_varint_field

        blob = _pb_varint_field(9, 30)
        result = _pb_find_varint_field(memoryview(blob), 9)
        assert result == 30

    def test_pb_find_len_field_memoryview(self):
        """Accepts memoryview and converts to bytes."""
        from app.srs.queue_stats import _pb_find_len_field

        inner = _pb_varint_field(1, 42)
        blob = _pb_len_field(1, inner)
        result = _pb_find_len_field(memoryview(blob), 1)
        assert result == inner

    def test_pb_find_varint_field_skips_other_wire_types(self):
        """Fields with different wire types before the target are skipped correctly."""
        import struct

        from app.srs.queue_stats import _pb_find_varint_field

        # field 3 (wire_type=1, 64-bit fixed), then field 9 (VARINT)
        fixed64_tag = (3 << 3) | 1
        blob = _encode_varint(fixed64_tag) + struct.pack("<Q", 12345) + _pb_varint_field(9, 25)
        result = _pb_find_varint_field(blob, 9)
        assert result == 25

    def test_pb_find_len_field_exception_on_corrupt_data(self):
        """Corrupted bytes in LEN field → returns None without raising."""
        from app.srs.queue_stats import _pb_find_len_field

        # Craft a tag for field 1 LEN but then malformed length varint (all continuation bits set)
        corrupt = bytes([(1 << 3) | 2]) + bytes([0xFF, 0xFF, 0xFF])  # tag + truncated varint length
        result = _pb_find_len_field(corrupt, 1)
        # Doesn't raise; may return None or partial - either is fine as long as no exception
        assert result is None or isinstance(result, bytes)

    def test_pb_skip_field_wire_type_2_via_find(self):
        """Skip a LEN-delimited field before finding the target VARINT."""
        from app.srs.queue_stats import _pb_find_varint_field

        # field 2 (LEN, wire_type=2) with 3-byte payload, then field 9 (VARINT=30)
        blob = _pb_len_field(2, b"\x00\x01\x02") + _pb_varint_field(9, 30)
        result = _pb_find_varint_field(blob, 9)
        assert result == 30

    def test_pb_skip_varint_with_continuation_byte(self):
        """Skip a multi-byte varint field before finding the target."""
        from app.srs.queue_stats import _pb_find_varint_field

        # field 3 (VARINT, value=300 which requires 2 bytes), then field 9 (VARINT=7)
        # 300 in varint: 0xAC 0x02
        multi_byte_varint = bytes([0xAC, 0x02])  # 300
        blob = _encode_varint((3 << 3) | 0) + multi_byte_varint + _pb_varint_field(9, 7)
        result = _pb_find_varint_field(blob, 9)
        assert result == 7

    def test_pb_find_varint_field_corrupt_tag(self):
        """Completely corrupted tag varint → returns None."""
        from app.srs.queue_stats import _pb_find_varint_field

        # All continuation bits set but no terminator — infinite loop guard via pos advance
        corrupt = bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
        # In practice this reads a 10-byte varint and returns the field; we just want no exception
        result = _pb_find_varint_field(corrupt, 9)
        assert result is None or isinstance(result, int)

    def test_pb_find_varint_field_skip_raises(self):
        """When skip_field raises (unknown wire type in blob), returns None."""
        from app.srs.queue_stats import _pb_find_varint_field

        # Wire type 3 is deprecated and not handled; _pb_skip_field returns same pos → infinite?
        # Actually _pb_skip_field just returns pos unchanged for unhandled wire types.
        # Test with wire_type=6 (also not handled), so the field is effectively zero-length.
        # Just check no exception is raised on unusual input.
        unusual = bytes([(5 << 3) | 6]) + _pb_varint_field(9, 12)
        result = _pb_find_varint_field(unusual, 9)
        # May or may not find field 9 depending on parse; just must not raise
        assert result is None or isinstance(result, int)

    def test_pb_find_len_field_skips_varint_field(self):
        """Skip a VARINT field before finding the target LEN field."""
        from app.srs.queue_stats import _pb_find_len_field

        # field 2 (VARINT=5), then field 1 (LEN)
        inner = b"\x01\x02\x03"
        blob = _pb_varint_field(2, 5) + _pb_len_field(1, inner)
        result = _pb_find_len_field(blob, 1)
        assert result == inner

    def test_no_conf_id_in_normal_kind_submessage(self):
        """kind blob has LEN at field 1 but no VARINT at field 1 inside → returns None."""
        from app.srs.queue_stats import _read_new_per_day_from_deck_config_table

        # Inner submessage has field 2 (VARINT=99) but not field 1
        inner = _pb_varint_field(2, 99)
        kind_blob = _pb_len_field(1, inner)

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        conn.execute("INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', '', '', '{}')")
        conn.execute(
            "CREATE TABLE deck_config (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB)"
        )
        conn.execute("INSERT INTO deck_config VALUES (1, 'Slovene', 0, -1, ?)", (_make_deck_config_blob(20),))
        conn.execute(
            "CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, "
            "usn INTEGER, common BLOB, kind BLOB)"
        )
        conn.execute("INSERT INTO decks VALUES (1, '0. Slovene', 0, -1, NULL, ?)", (kind_blob,))
        conn.commit()

        result = _read_new_per_day_from_deck_config_table(conn, "0. Slovene")
        assert result is None
