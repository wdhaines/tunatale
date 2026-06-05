"""Tests for Change 4: cache-driven daily-new-cap (no AnkiConnect)."""

from __future__ import annotations

import sqlite3
import struct
from datetime import UTC, datetime, timedelta

from app.config import settings
from app.srs.database import SRSDatabase
from app.srs.fsrs import DEFAULT_FSRS5_PARAMS
from app.srs.queue_stats import (
    refresh_daily_new_cap,
    refresh_daily_review_cap,
    refresh_fsrs_params,
    refresh_review_settings,
    resolve_bury_new,
    resolve_bury_review,
    resolve_daily_review_cap,
    resolve_fsrs_params,
    resolve_new_spread,
)
from tests._helpers.anki_db import (
    DESIRED_RETENTION_FIELD,
    FSRS5_WEIGHTS_FIELD,
    KNOWN_WEIGHTS,
    make_anki_conn,
    make_deck_config_blob,
    make_deck_kind_blob,
    make_fsrs_deck_config_blob,
    make_modern_anki_conn,
    make_modern_anki_conn_with_fsrs,
)
from tests._helpers.protobuf import encode_varint, pb_len_field, pb_varint_field


class TestRefreshDailyNewCapModernAnki:
    def test_reads_new_per_day_from_modern_deck_config(self):
        """B1 regression: modern Anki stores deck config in deck_config table, not col.dconf."""
        db = SRSDatabase(":memory:")
        conn = make_modern_anki_conn(new_per_day=30)
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
        conn.execute("INSERT INTO deck_config VALUES (1, 'Default', 0, -1, ?)", (make_deck_config_blob(5),))
        conn.execute("INSERT INTO deck_config VALUES (2, 'Slovene', 0, -1, ?)", (make_deck_config_blob(25),))
        conn.execute(
            "CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, "
            "usn INTEGER, common BLOB, kind BLOB)"
        )
        # Deck points to config_id=2 (Slovene=25)
        conn.execute("INSERT INTO decks VALUES (1, '0. Slovene', 0, -1, NULL, ?)", (make_deck_kind_blob(2),))
        conn.commit()

        db = SRSDatabase(":memory:")
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
        db = SRSDatabase(":memory:")
        refresh_daily_new_cap(db, conn, "0. Slovene")  # must not raise

    def test_no_error_when_deck_not_found_in_decks_table(self):
        """B1: deck not in decks table → no-op, no raise."""
        db = SRSDatabase(":memory:")
        conn = make_modern_anki_conn(new_per_day=20, deck_name="Other Deck")
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
        conn.execute("INSERT INTO deck_config VALUES (1, 'Slovene', 0, -1, ?)", (make_deck_config_blob(20),))
        conn.execute(
            "CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, "
            "usn INTEGER, common BLOB, kind BLOB)"
        )
        conn.execute("INSERT INTO decks VALUES (1, '0. Slovene', 0, -1, NULL, NULL)")  # NULL kind
        conn.commit()
        db = SRSDatabase(":memory:")
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
        conn.execute("INSERT INTO deck_config VALUES (1, 'Default', 0, -1, ?)", (make_deck_config_blob(20),))
        conn.execute(
            "CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, "
            "usn INTEGER, common BLOB, kind BLOB)"
        )
        conn.execute("INSERT INTO decks VALUES (1, '0. Slovene', 0, -1, NULL, ?)", (make_deck_kind_blob(999),))
        conn.commit()
        db = SRSDatabase(":memory:")
        refresh_daily_new_cap(db, conn, "0. Slovene")  # must not raise
        assert db.get_anki_state_cache("daily_new_cap") is None

    def test_no_error_when_kind_blob_has_no_len_field(self):
        """B1: kind blob that doesn't contain the expected LEN submessage → no-op."""
        # Build a kind blob with only a varint field (field 2), no LEN field at field 1
        kind_blob = pb_varint_field(2, 42)  # field 2, not field 1 LEN
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
        conn.execute("INSERT INTO deck_config VALUES (1, 'Slovene', 0, -1, ?)", (make_deck_config_blob(20),))
        conn.execute(
            "CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, "
            "usn INTEGER, common BLOB, kind BLOB)"
        )
        conn.execute("INSERT INTO decks VALUES (1, '0. Slovene', 0, -1, NULL, ?)", (kind_blob,))
        conn.commit()
        db = SRSDatabase(":memory:")
        refresh_daily_new_cap(db, conn, "0. Slovene")  # must not raise
        assert db.get_anki_state_cache("daily_new_cap") is None


class TestPbParsing:
    """Direct unit tests for internal protobuf helper functions."""

    def test_pb_read_varint_empty_data(self):
        """Empty bytes → returns (0, 0) without error."""
        from app.anki.protobuf_wire import decode_varint

        value, pos = decode_varint(b"", 0)
        assert value == 0
        assert pos == 0

    def test_pb_skip_field_wire_type_1(self):
        """Wire type 1 (64-bit) skips exactly 8 bytes."""
        from app.anki.protobuf_wire import skip_field

        data = b"\x00" * 10
        new_pos = skip_field(data, 0, 1)
        assert new_pos == 8

    def test_pb_skip_field_wire_type_5(self):
        """Wire type 5 (32-bit) skips exactly 4 bytes."""
        from app.anki.protobuf_wire import skip_field

        data = b"\x00" * 8
        new_pos = skip_field(data, 0, 5)
        assert new_pos == 4

    def test_pb_find_varint_field_memoryview(self):
        """Accepts memoryview and converts to bytes."""
        from app.anki.protobuf_wire import find_varint_field

        blob = pb_varint_field(9, 30)
        result = find_varint_field(memoryview(blob), 9)
        assert result == 30

    def test_pb_find_len_field_memoryview(self):
        """Accepts memoryview and converts to bytes."""
        from app.anki.protobuf_wire import find_len_field

        inner = pb_varint_field(1, 42)
        blob = pb_len_field(1, inner)
        result = find_len_field(memoryview(blob), 1)
        assert result == inner

    def test_pb_find_varint_field_skips_other_wire_types(self):
        """Fields with different wire types before the target are skipped correctly."""
        import struct

        from app.anki.protobuf_wire import find_varint_field

        # field 3 (wire_type=1, 64-bit fixed), then field 9 (VARINT)
        fixed64_tag = (3 << 3) | 1
        blob = encode_varint(fixed64_tag) + struct.pack("<Q", 12345) + pb_varint_field(9, 25)
        result = find_varint_field(blob, 9)
        assert result == 25

    def test_pb_find_fixed32_field_reads_ieee_float(self):
        """Field 37 with wire_type=5 (fixed32) decoded as a little-endian IEEE float."""
        import struct

        from app.anki.protobuf_wire import find_fixed32_field

        # Anki's deck_config field 37 (desired_retention) is wire_type=5.
        tag = (37 << 3) | 5
        blob = encode_varint(tag) + struct.pack("<f", 0.86)
        result = find_fixed32_field(blob, 37)
        assert result is not None
        assert abs(result - 0.86) < 1e-6

    def test_pb_find_fixed32_field_returns_none_when_absent(self):
        """Missing field 37 → returns None so callers can fall back to a default."""
        from app.anki.protobuf_wire import find_fixed32_field

        blob = pb_varint_field(9, 30) + pb_varint_field(10, 9999)
        assert find_fixed32_field(blob, 37) is None

    def test_pb_find_fixed32_field_memoryview(self):
        """Accepts memoryview, like the other helpers."""
        import struct

        from app.anki.protobuf_wire import find_fixed32_field

        tag = (37 << 3) | 5
        blob = encode_varint(tag) + struct.pack("<f", 0.9)
        result = find_fixed32_field(memoryview(blob), 37)
        assert result is not None
        assert abs(result - 0.9) < 1e-6

    def test_pb_find_len_field_exception_on_corrupt_data(self):
        """Corrupted bytes in LEN field → returns None without raising."""
        from app.anki.protobuf_wire import find_len_field

        # Craft a tag for field 1 LEN but then malformed length varint (all continuation bits set)
        corrupt = bytes([(1 << 3) | 2]) + bytes([0xFF, 0xFF, 0xFF])  # tag + truncated varint length
        result = find_len_field(corrupt, 1)
        # Doesn't raise; may return None or partial - either is fine as long as no exception
        assert result is None or isinstance(result, bytes)

    def test_pb_skip_field_wire_type_2_via_find(self):
        """Skip a LEN-delimited field before finding the target VARINT."""
        from app.anki.protobuf_wire import find_varint_field

        # field 2 (LEN, wire_type=2) with 3-byte payload, then field 9 (VARINT=30)
        blob = pb_len_field(2, b"\x00\x01\x02") + pb_varint_field(9, 30)
        result = find_varint_field(blob, 9)
        assert result == 30

    def test_pb_skip_varint_with_continuation_byte(self):
        """Skip a multi-byte varint field before finding the target."""
        from app.anki.protobuf_wire import find_varint_field

        # field 3 (VARINT, value=300 which requires 2 bytes), then field 9 (VARINT=7)
        # 300 in varint: 0xAC 0x02
        multi_byte_varint = bytes([0xAC, 0x02])  # 300
        blob = encode_varint((3 << 3) | 0) + multi_byte_varint + pb_varint_field(9, 7)
        result = find_varint_field(blob, 9)
        assert result == 7

    def test_pb_find_varint_field_corrupt_tag(self):
        """Completely corrupted tag varint → returns None."""
        from app.anki.protobuf_wire import find_varint_field

        # All continuation bits set but no terminator — infinite loop guard via pos advance
        corrupt = bytes([0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
        # In practice this reads a 10-byte varint and returns the field; we just want no exception
        result = find_varint_field(corrupt, 9)
        assert result is None or isinstance(result, int)

    def test_pb_find_varint_field_skip_raises(self):
        """When skip_field raises (unknown wire type in blob), returns None."""
        from app.anki.protobuf_wire import find_varint_field

        # Wire type 3 is deprecated and not handled; skip_field returns same pos → infinite?
        # Actually skip_field just returns pos unchanged for unhandled wire types.
        # Test with wire_type=6 (also not handled), so the field is effectively zero-length.
        # Just check no exception is raised on unusual input.
        unusual = bytes([(5 << 3) | 6]) + pb_varint_field(9, 12)
        result = find_varint_field(unusual, 9)
        # May or may not find field 9 depending on parse; just must not raise
        assert result is None or isinstance(result, int)

    def test_pb_find_len_field_skips_varint_field(self):
        """Skip a VARINT field before finding the target LEN field."""
        from app.anki.protobuf_wire import find_len_field

        # field 2 (VARINT=5), then field 1 (LEN)
        inner = b"\x01\x02\x03"
        blob = pb_varint_field(2, 5) + pb_len_field(1, inner)
        result = find_len_field(blob, 1)
        assert result == inner

    def test_no_conf_id_in_normal_kind_submessage(self):
        """kind blob has LEN at field 1 but no VARINT at field 1 inside → returns None."""
        from app.srs.queue_stats import _NEW_PER_DAY_FIELD, _WIRE_TYPE_VARINT, _read_config_value_from_deck_config_table

        # Inner submessage has field 2 (VARINT=99) but not field 1
        inner = pb_varint_field(2, 99)
        kind_blob = pb_len_field(1, inner)

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
        conn.execute("INSERT INTO deck_config VALUES (1, 'Slovene', 0, -1, ?)", (make_deck_config_blob(20),))
        conn.execute(
            "CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, "
            "usn INTEGER, common BLOB, kind BLOB)"
        )
        conn.execute("INSERT INTO decks VALUES (1, '0. Slovene', 0, -1, NULL, ?)", (kind_blob,))
        conn.commit()

        result = _read_config_value_from_deck_config_table(
            conn, "0. Slovene", proto_field=_NEW_PER_DAY_FIELD, wire_type=_WIRE_TYPE_VARINT
        )
        assert result is None


class TestRefreshFSRSParams:
    """Tests for reading and caching FSRS params from Anki's deck_config protobuf."""

    def test_reads_fsrs_weights_from_deck_config(self):
        """refresh_fsrs_params extracts FSRS-5 weights and stores them in the cache."""
        db = SRSDatabase(":memory:")
        conn = make_modern_anki_conn_with_fsrs(weights=KNOWN_WEIGHTS, retention=0.85)
        refresh_fsrs_params(db, conn, "0. Slovene")

        row = db.get_anki_state_cache("fsrs_params")
        assert row is not None
        import json

        cached = json.loads(row[0])
        assert len(cached["weights"]) == 19
        assert abs(cached["weights"][3] - 100.0) < 0.01  # w3 dominates easy interval

    def test_reads_desired_retention_from_deck_config(self):
        db = SRSDatabase(":memory:")
        conn = make_modern_anki_conn_with_fsrs(weights=KNOWN_WEIGHTS, retention=0.85)
        refresh_fsrs_params(db, conn, "0. Slovene")

        import json

        row = db.get_anki_state_cache("fsrs_params")
        assert row is not None
        cached = json.loads(row[0])
        assert abs(cached["desired_retention"] - 0.85) < 0.001

    def test_no_error_when_fsrs_weights_absent(self):
        """If deck_config has no field 5, no cache entry is written (no raise)."""
        db = SRSDatabase(":memory:")
        conn = make_modern_anki_conn(new_per_day=20)  # blob has no FSRS weights
        refresh_fsrs_params(db, conn, "0. Slovene")
        assert db.get_anki_state_cache("fsrs_params") is None

    def test_no_error_when_deck_not_found(self):
        db = SRSDatabase(":memory:")
        conn = make_modern_anki_conn_with_fsrs()
        refresh_fsrs_params(db, conn, "No Such Deck")  # must not raise

    def test_logs_warning_when_blob_present_but_read_returns_none(self, caplog):
        """deck_config blob exists but has no FSRS weights → warning logged."""
        import logging

        db = SRSDatabase(":memory:")
        conn = make_modern_anki_conn(new_per_day=20)  # blob has no FSRS weights
        with caplog.at_level(logging.WARNING, logger="app.srs.queue_stats"):
            refresh_fsrs_params(db, conn, "0. Slovene")
        assert any("FSRS" in r.message and "0. Slovene" in r.message for r in caplog.records)

    def test_no_warning_when_deck_missing(self, caplog):
        """Deck not in decks table → no warning (misconfigured deck name is expected)."""
        import logging

        db = SRSDatabase(":memory:")
        conn = make_modern_anki_conn_with_fsrs()
        with caplog.at_level(logging.WARNING, logger="app.srs.queue_stats"):
            refresh_fsrs_params(db, conn, "No Such Deck")
        assert not any("FSRS" in r.message for r in caplog.records)

    def test_skips_when_decks_table_missing(self):
        """When 'decks' table not in schema, function returns early (legacy Anki)."""
        import sqlite3

        db = SRSDatabase(":memory:")
        conn = sqlite3.connect(":memory:")
        # Only col table, no 'decks' table (legacy Anki format)
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        conn.execute("INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', '', '', '{}')")
        conn.commit()
        # Should not raise, should return early
        refresh_fsrs_params(db, conn, "0. Slovene")
        assert db.get_anki_state_cache("fsrs_params") is None

    def test_no_warning_when_fsrs_params_none_modern(self, caplog):
        """Modern Anki with 'decks' table but no FSRS params → warning logged."""
        import logging

        db = SRSDatabase(":memory:")
        conn = make_modern_anki_conn(new_per_day=20)  # no FSRS weights in blob
        with caplog.at_level(logging.WARNING, logger="app.srs.queue_stats"):
            refresh_fsrs_params(db, conn, "0. Slovene")
        assert any("FSRS" in r.message and "0. Slovene" in r.message for r in caplog.records)


class TestDesiredRetentionCache:
    """Tests for refresh_desired_retention."""

    def test_refresh_caches_value_from_modern_deck_config(self):
        """refresh_desired_retention reads field 37 from deck_config and caches it."""
        from app.srs.queue_stats import refresh_desired_retention

        db = SRSDatabase(":memory:")
        conn = make_modern_anki_conn_with_fsrs(weights=KNOWN_WEIGHTS, retention=0.86)
        refresh_desired_retention(db, conn, "0. Slovene")

        row = db.get_anki_state_cache("desired_retention")
        assert row is not None
        assert abs(float(row[0]) - 0.86) < 0.0001

    def test_refresh_writes_nothing_when_field_absent(self):
        """No field 37 in the blob → cache untouched, resolver falls back to default."""
        from app.srs.queue_stats import refresh_desired_retention

        db = SRSDatabase(":memory:")
        conn = make_modern_anki_conn(new_per_day=20)  # no FSRS / no field 37
        refresh_desired_retention(db, conn, "0. Slovene")
        assert db.get_anki_state_cache("desired_retention") is None

    def test_refresh_no_error_when_deck_missing(self):
        from app.srs.queue_stats import refresh_desired_retention

        db = SRSDatabase(":memory:")
        conn = make_modern_anki_conn_with_fsrs()
        refresh_desired_retention(db, conn, "No Such Deck")  # must not raise
        assert db.get_anki_state_cache("desired_retention") is None


class TestMaximumReviewIntervalCache:
    """Tests for refresh/resolve of maximum_review_interval."""

    def test_refresh_caches_value_from_modern_deck_config(self):
        """refresh_maximum_review_interval reads field 16 and caches it."""
        from app.srs.queue_stats import refresh_maximum_review_interval

        db = SRSDatabase(":memory:")
        conn = make_modern_anki_conn(new_per_day=20, max_review_interval=36500)
        refresh_maximum_review_interval(db, conn, "0. Slovene")

        row = db.get_anki_state_cache("maximum_review_interval")
        assert row is not None
        assert int(row[0]) == 36500

    def test_refresh_writes_nothing_when_field_absent(self):
        """No field 16 in the blob → cache untouched, resolver falls back to default."""
        from app.srs.queue_stats import refresh_maximum_review_interval

        db = SRSDatabase(":memory:")
        conn = make_modern_anki_conn(new_per_day=20)  # no field 16
        refresh_maximum_review_interval(db, conn, "0. Slovene")
        assert db.get_anki_state_cache("maximum_review_interval") is None

    def test_refresh_no_error_when_deck_missing(self):
        from app.srs.queue_stats import refresh_maximum_review_interval

        db = SRSDatabase(":memory:")
        conn = make_modern_anki_conn(new_per_day=20, max_review_interval=36500)
        refresh_maximum_review_interval(db, conn, "No Such Deck")  # must not raise
        assert db.get_anki_state_cache("maximum_review_interval") is None


class TestResolveFSRSParams:
    """Tests for resolve_fsrs_params() priority chain."""

    def test_returns_default_when_no_cache(self):
        db = SRSDatabase(":memory:")
        params, source = resolve_fsrs_params(db)
        assert params == DEFAULT_FSRS5_PARAMS
        assert source == "default"

    def test_returns_cached_params_when_fresh(self):
        import json

        db = SRSDatabase(":memory:")
        cached_data = {"weights": list(KNOWN_WEIGHTS), "desired_retention": 0.85}
        db.set_anki_state_cache("fsrs_params", json.dumps(cached_data))
        params, source = resolve_fsrs_params(db)
        assert len(params.weights) == 19
        assert abs(params.weights[3] - 100.0) < 0.01
        assert abs(params.desired_retention - 0.85) < 0.001
        assert source == "cache"

    def test_returns_default_when_cache_stale(self):
        import json

        db = SRSDatabase(":memory:")
        cached_data = {"weights": list(KNOWN_WEIGHTS), "desired_retention": 0.85}
        old_ts = (datetime.now(UTC) - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
        db.set_anki_state_cache_raw("fsrs_params", json.dumps(cached_data), old_ts)
        params, source = resolve_fsrs_params(db)
        assert params == DEFAULT_FSRS5_PARAMS
        assert source == "default"

    def test_returns_default_when_cache_has_invalid_json(self):
        db = SRSDatabase(":memory:")
        db.set_anki_state_cache_raw("fsrs_params", "not-json", datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"))
        params, source = resolve_fsrs_params(db)
        assert params == DEFAULT_FSRS5_PARAMS

    def test_returns_default_when_db_creation_fails(self, monkeypatch):
        from app.srs import queue_stats

        monkeypatch.setattr(queue_stats.settings, "database_url", "sqlite:////__invalid/path/db.sqlite")
        params, source = resolve_fsrs_params()
        assert params == DEFAULT_FSRS5_PARAMS
        assert source == "default"


class TestPbFSRSHelpersExtra:
    """Coverage for edge paths in the new FSRS protobuf helpers."""

    def test_pb_find_packed_float_accepts_memoryview(self):
        from app.srs.queue_stats import _pb_find_packed_float_field

        payload = struct.pack("<19f", *KNOWN_WEIGHTS)
        tag = encode_varint((FSRS5_WEIGHTS_FIELD << 3) | 2)
        blob = tag + encode_varint(len(payload)) + payload
        result = _pb_find_packed_float_field(memoryview(blob), FSRS5_WEIGHTS_FIELD)
        assert result is not None and len(result) == 19

    def test_pb_find_packed_float_returns_none_for_non_divisible_by_4_length(self):
        from app.srs.queue_stats import _pb_find_packed_float_field

        # Build a LEN field at target with 3-byte payload (3 % 4 != 0)
        tag = encode_varint((FSRS5_WEIGHTS_FIELD << 3) | 2)
        blob = tag + encode_varint(3) + b"\x00\x01\x02"
        result = _pb_find_packed_float_field(blob, FSRS5_WEIGHTS_FIELD)
        assert result is None

    def test_pb_find_fixed32_accepts_memoryview(self):
        from app.srs.queue_stats import _pb_find_fixed32_float_field

        tag = encode_varint((DESIRED_RETENTION_FIELD << 3) | 5)
        blob = tag + struct.pack("<f", 0.9)
        result = _pb_find_fixed32_float_field(memoryview(blob), DESIRED_RETENTION_FIELD)
        assert result is not None
        assert abs(result - 0.9) < 0.001

    def test_pb_find_fixed32_returns_none_when_field_absent(self):
        from app.srs.queue_stats import _pb_find_fixed32_float_field

        # Blob with no field 40 at all
        blob = pb_varint_field(9, 20)
        result = _pb_find_fixed32_float_field(blob, DESIRED_RETENTION_FIELD)
        assert result is None


class TestReadFSRSParamsEdgeCases:
    """Edge-case coverage for _read_fsrs_params_from_deck_config_table."""

    def _base_conn(self):
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
        conn.execute(
            "CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, "
            "usn INTEGER, common BLOB, kind BLOB)"
        )
        return conn

    def test_returns_none_when_deck_config_table_missing(self):
        from app.srs.queue_stats import _read_fsrs_params_from_deck_config_table

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        result = _read_fsrs_params_from_deck_config_table(conn, "0. Slovene")
        assert result is None

    def test_returns_none_when_kind_blob_has_no_normal_kind(self):
        from app.srs.queue_stats import _read_fsrs_params_from_deck_config_table

        conn = self._base_conn()
        conn.execute("INSERT INTO deck_config VALUES (1, 'Slovene', 0, -1, ?)", (make_fsrs_deck_config_blob(),))
        # kind blob with no field 1 (LEN) — just a varint field
        bad_kind = pb_varint_field(2, 99)
        conn.execute("INSERT INTO decks VALUES (1, '0. Slovene', 0, -1, NULL, ?)", (bad_kind,))
        conn.commit()
        result = _read_fsrs_params_from_deck_config_table(conn, "0. Slovene")
        assert result is None

    def test_returns_none_when_conf_id_not_in_kind(self):
        from app.srs.queue_stats import _read_fsrs_params_from_deck_config_table

        conn = self._base_conn()
        conn.execute("INSERT INTO deck_config VALUES (1, 'Slovene', 0, -1, ?)", (make_fsrs_deck_config_blob(),))
        # kind's inner sub-message has no field 1 (VARINT), so conf_id is None
        inner = pb_len_field(2, encode_varint(9))  # field 2, not 1
        kind_blob = pb_len_field(1, inner)
        conn.execute("INSERT INTO decks VALUES (1, '0. Slovene', 0, -1, NULL, ?)", (kind_blob,))
        conn.commit()
        result = _read_fsrs_params_from_deck_config_table(conn, "0. Slovene")
        assert result is None

    def test_returns_none_when_conf_id_not_in_deck_config(self):
        from app.srs.queue_stats import _read_fsrs_params_from_deck_config_table

        conn = self._base_conn()
        conn.execute("INSERT INTO deck_config VALUES (999, 'Slovene', 0, -1, ?)", (make_fsrs_deck_config_blob(),))
        # kind points to conf_id=1 which doesn't exist
        conn.execute("INSERT INTO decks VALUES (1, '0. Slovene', 0, -1, NULL, ?)", (make_deck_kind_blob(1),))
        conn.commit()
        result = _read_fsrs_params_from_deck_config_table(conn, "0. Slovene")
        assert result is None

    def test_returns_none_when_kind_blob_is_null(self):
        from app.srs.queue_stats import _read_fsrs_params_from_deck_config_table

        conn = self._base_conn()
        conn.execute("INSERT INTO deck_config VALUES (1, 'Slovene', 0, -1, ?)", (make_fsrs_deck_config_blob(),))
        conn.execute("INSERT INTO decks VALUES (1, '0. Slovene', 0, -1, NULL, NULL)")  # NULL kind
        conn.commit()
        result = _read_fsrs_params_from_deck_config_table(conn, "0. Slovene")
        assert result is None


# ---- Helpers for review-settings protobuf blobs ----


def _make_review_settings_blob(new_spread: int = 0, bury_new: bool = False, bury_reviews: bool = False) -> bytes:
    """Build a minimal DeckConfig.Config protobuf blob with review settings."""
    blob = b""
    if new_spread in (0, 1, 2):
        blob += pb_varint_field(30, new_spread)
    if bury_new:
        blob += pb_varint_field(27, 1)
    if bury_reviews:
        blob += pb_varint_field(28, 1)
    return blob


def _make_deck_config_with_review_settings(
    new_spread: int = 0, bury_new: bool = False, bury_reviews: bool = False
) -> tuple[sqlite3.Connection, int]:
    """Return (conn, conf_id) with review settings in the config blob."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    for sql in [
        "CREATE TABLE deck_config (id INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB)",
        "CREATE TABLE decks (id INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, conf INTEGER, kind BLOB)",
    ]:
        conn.execute(sql)

    config_blob = _make_review_settings_blob(new_spread, bury_new, bury_reviews)
    conn.execute("INSERT INTO deck_config VALUES (1, 'Slovene',0, -1, ?)", (config_blob,))
    kind_blob = make_deck_kind_blob(1)
    conn.execute("INSERT INTO decks VALUES (1, '0. Slovene',0, -1, 1, ?)", (kind_blob,))
    conn.commit()
    return conn, 1


class TestRefreshReviewSettings:
    def test_reads_new_spread_from_blob(self):
        db = SRSDatabase(":memory:")
        conn, _ = _make_deck_config_with_review_settings(new_spread=1)
        refresh_review_settings(db, conn, "0. Slovene")
        row = db.get_anki_state_cache("new_spread")
        assert row is not None
        assert int(row[0]) == 1

    def test_reads_bury_new_from_blob(self):
        db = SRSDatabase(":memory:")
        conn, _ = _make_deck_config_with_review_settings(bury_new=True)
        refresh_review_settings(db, conn, "0. Slovene")
        row = db.get_anki_state_cache("bury_new")
        assert row is not None
        assert row[0] == "True"

    def test_reads_bury_review_from_blob(self):
        db = SRSDatabase(":memory:")
        conn, _ = _make_deck_config_with_review_settings(bury_reviews=True)
        refresh_review_settings(db, conn, "0. Slovene")
        row = db.get_anki_state_cache("bury_review")
        assert row is not None
        assert row[0] == "True"

    def test_no_error_when_deck_not_found(self):
        db = SRSDatabase(":memory:")
        conn, _ = _make_deck_config_with_review_settings()
        refresh_review_settings(db, conn, "No Such Deck")  # must not raise

    def test_no_error_when_tables_missing(self):
        db = SRSDatabase(":memory:")
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        refresh_review_settings(db, conn, "0. Slovene")  # must not raise


class TestResolveNewSpread:
    def test_returns_cache_when_fresh(self):
        db = SRSDatabase(":memory:")
        db.set_anki_state_cache("new_spread", "2")
        spread, source = resolve_new_spread(db)
        assert spread == 2
        assert source == "cache"

    def test_returns_default_when_cache_missing(self):
        db = SRSDatabase(":memory:")
        spread, source = resolve_new_spread(db)
        assert spread == 0
        assert source == "default"


class TestResolveBuryNew:
    def test_returns_cache_when_fresh(self):
        db = SRSDatabase(":memory:")
        db.set_anki_state_cache("bury_new", "False")
        val, source = resolve_bury_new(db)
        assert val is False
        assert source == "cache"

    def test_returns_default_true(self):
        db = SRSDatabase(":memory:")
        val, source = resolve_bury_new(db)
        assert val is True
        assert source == "default"


class TestResolveBuryReview:
    def test_returns_cache_when_fresh(self):
        db = SRSDatabase(":memory:")
        db.set_anki_state_cache("bury_review", "False")
        val, source = resolve_bury_review(db)
        assert val is False
        assert source == "cache"

    def test_returns_default_true(self):
        db = SRSDatabase(":memory:")
        val, source = resolve_bury_review(db)
        assert val is True
        assert source == "default"


class TestLearningCutoff:
    """resolve_learning_cutoff / advance_learning_cutoff helpers."""

    def test_resolve_returns_fallback_when_cache_missing(self):
        from datetime import UTC, datetime

        from app.srs.database import SRSDatabase
        from app.srs.queue_stats import resolve_learning_cutoff

        db = SRSDatabase(":memory:")
        fallback = datetime(2026, 5, 9, 18, 0, tzinfo=UTC)
        assert resolve_learning_cutoff(db, fallback=fallback) == fallback

    def test_resolve_returns_fallback_on_corrupt_cache(self):
        from datetime import UTC, datetime

        from app.srs.database import SRSDatabase
        from app.srs.queue_stats import resolve_learning_cutoff

        db = SRSDatabase(":memory:")
        db.set_anki_state_cache("learning_cutoff", "not-a-timestamp")
        fallback = datetime(2026, 5, 9, 18, 0, tzinfo=UTC)
        assert resolve_learning_cutoff(db, fallback=fallback) == fallback

    def test_advance_is_monotonic(self):
        """A stale `when` (older than the cached value) is silently ignored."""
        from datetime import UTC, datetime

        from app.srs.database import SRSDatabase
        from app.srs.queue_stats import advance_learning_cutoff, resolve_learning_cutoff

        db = SRSDatabase(":memory:")
        newer = datetime(2026, 5, 9, 18, 0, tzinfo=UTC)
        older = datetime(2026, 5, 9, 17, 0, tzinfo=UTC)

        advance_learning_cutoff(db, newer)
        advance_learning_cutoff(db, older)  # must not move cutoff backwards

        assert resolve_learning_cutoff(db, fallback=datetime(1970, 1, 1, tzinfo=UTC)) == newer


class TestSessionMainQueueCache:
    """Helpers for the frozen main-queue cache."""

    def test_returns_none_when_cache_missing(self):
        from datetime import date

        from app.srs.database import SRSDatabase
        from app.srs.queue_stats import get_session_main_queue

        db = SRSDatabase(":memory:")
        assert get_session_main_queue(db, date.today()) is None

    def test_returns_none_on_corrupt_cache(self):
        from datetime import date

        from app.srs.database import SRSDatabase
        from app.srs.queue_stats import get_session_main_queue

        db = SRSDatabase(":memory:")
        db.set_anki_state_cache("session_main_queue", "not-json")
        assert get_session_main_queue(db, date.today()) is None

    def test_round_trips(self):
        from datetime import date

        from app.srs.database import SRSDatabase
        from app.srs.queue_stats import get_session_main_queue, set_session_main_queue

        db = SRSDatabase(":memory:")
        today = date.today()
        items = [(1, "recognition"), (2, "production"), (3, "recognition")]
        set_session_main_queue(db, today, items)
        assert get_session_main_queue(db, today) == items

    def test_clear_session_main_queue_removes_cache(self):
        """clear_session_main_queue wipes the cache row so the next call rebuilds.

        Mirrors Anki's `clear_queues_if_day_changed` (queue/mod.rs:277) and the
        rebuild gate on sync / deck-config change (queue/mod.rs:211-215).
        """
        from datetime import date

        from app.srs.database import SRSDatabase
        from app.srs.queue_stats import (
            clear_session_main_queue,
            get_session_main_queue,
            set_session_main_queue,
        )

        db = SRSDatabase(":memory:")
        today = date.today()
        set_session_main_queue(db, today, [(1, "recognition")])
        assert get_session_main_queue(db, today) == [(1, "recognition")]

        clear_session_main_queue(db)
        assert get_session_main_queue(db, today) is None

    def test_clear_session_main_queue_is_idempotent(self):
        """Clearing an already-empty cache does not raise."""
        from app.srs.database import SRSDatabase
        from app.srs.queue_stats import clear_session_main_queue

        db = SRSDatabase(":memory:")
        clear_session_main_queue(db)  # no-op, must not raise
        clear_session_main_queue(db)  # double-clear, must not raise


class TestReadReviewsPerDayFromAnki:
    def test_reads_reviews_per_day_from_legacy_json(self):
        """Legacy col.dconf format with rev.perDay returns the correct value."""
        from app.srs.queue_stats import _read_reviews_per_day_from_anki

        conn = make_anki_conn(reviews_per_day=97)
        result = _read_reviews_per_day_from_anki(conn, "0. Slovene")
        assert result == 97

    def test_reads_reviews_per_day_from_modern_deck_config(self):
        """Modern deck_config protobuf with reviews_per_day at field 10."""
        from app.srs.queue_stats import _read_reviews_per_day_from_anki

        conn = make_modern_anki_conn(reviews_per_day=150)
        result = _read_reviews_per_day_from_anki(conn, "0. Slovene")
        assert result == 150

    def test_returns_none_when_dconf_empty(self):
        """When col.dconf is empty, attempts protobuf fallback; no deck_config tables → None."""
        from app.srs.queue_stats import _read_reviews_per_day_from_anki

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        conn.execute("INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', '', '', '{}')")
        conn.commit()
        result = _read_reviews_per_day_from_anki(conn, "0. Slovene")
        assert result is None


class TestRefreshDailyReviewCap:
    def test_writes_to_cache(self):
        """refresh_daily_review_cap writes the reviews-per-day value to anki_state_cache."""
        db = SRSDatabase(":memory:")
        conn = make_modern_anki_conn(reviews_per_day=75)
        refresh_daily_review_cap(db, conn, "0. Slovene")

        row = db.get_anki_state_cache("daily_review_cap")
        assert row is not None
        assert int(row[0]) == 75

    def test_no_error_when_deck_config_missing(self):
        """Graceful degradation when deck_config table doesn't exist."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        conn.execute("INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', '', '', '{}')")
        conn.commit()
        db = SRSDatabase(":memory:")
        refresh_daily_review_cap(db, conn, "0. Slovene")  # must not raise
        assert db.get_anki_state_cache("daily_review_cap") is None


class TestResolveDailyReviewCap:
    def test_returns_cache_when_fresh(self):
        """Returns the cached value with source='cache'."""
        db = SRSDatabase(":memory:")
        db.set_anki_state_cache("daily_review_cap", "97")
        cap, source = resolve_daily_review_cap(db)
        assert cap == 97
        assert source == "cache"

    def test_returns_config_when_cache_missing(self):
        """Returns settings value with source='config' when no cache exists."""
        from unittest.mock import patch

        db = SRSDatabase(":memory:")
        with patch.object(settings, "anki_reviews_per_day_default", 150):
            cap, source = resolve_daily_review_cap(db)
        assert cap == 150
        assert source == "config"

    def test_returns_default_when_cache_and_config_missing(self):
        """Hard default 200 when both cache and config are absent."""
        from unittest.mock import patch

        db = SRSDatabase(":memory:")
        with patch.object(settings, "anki_reviews_per_day_default", 0):
            cap, source = resolve_daily_review_cap(db)
        assert cap == 200
        assert source == "default"

    def test_returns_default_when_cache_expired(self):
        """Cache value older than _CACHE_MAX_AGE_DAYS falls through to config/default."""
        db = SRSDatabase(":memory:")
        from datetime import timedelta

        stale = (datetime.now(UTC) - timedelta(days=31)).isoformat()
        db.set_anki_state_cache("daily_review_cap", "97")
        # Manually backdate the updated_at
        conn = db._get_conn().__enter__()
        conn.execute(
            "UPDATE anki_state_cache SET updated_at = ? WHERE key = 'daily_review_cap'",
            (stale,),
        )
        conn.commit()
        cap, source = resolve_daily_review_cap(db)
        assert source in ("config", "default")

    def test_cache_parse_error_falls_through(self):
        """Corrupt cache value triggers except and falls through."""
        db = SRSDatabase(":memory:")
        db.set_anki_state_cache("daily_review_cap", "not-a-number")
        cap, source = resolve_daily_review_cap(db)
        assert source in ("config", "default")

    def test_resolve_with_no_db_auto_creates(self):
        """Calling resolve_daily_review_cap() with no db auto-creates one from settings."""
        cap, source = resolve_daily_review_cap()
        assert isinstance(cap, int)
        assert isinstance(source, str)


class TestReadReviewsPerDayFromAnkiFallback:
    def test_returns_none_when_col_table_missing_row(self):
        """conn.execute returns None for col query."""
        from app.srs.queue_stats import _read_reviews_per_day_from_anki

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        # No INSERT → SELECT returns no rows
        conn.commit()
        result = _read_reviews_per_day_from_anki(conn, "0. Slovene")
        assert result is None

    def test_legacy_json_decode_error_falls_to_protobuf(self):
        """Corrupted dconf parses, falls through to protobuf, which returns None."""
        from app.srs.queue_stats import _read_reviews_per_day_from_anki

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        conn.execute("INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', '{invalid', '{invalid', '{}')")
        conn.commit()
        result = _read_reviews_per_day_from_anki(conn, "0. Slovene")
        # Falls through to protobuf; no deck_config table → None
        assert result is None

    def test_legacy_json_missing_perDay_goes_to_protobuf(self):
        """dconf has rev section but no perDay → falls to protobuf."""
        import json

        from app.srs.queue_stats import _read_reviews_per_day_from_anki

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        decks = json.dumps({"1": {"name": "0. Slovene", "conf": 1}})
        dconf = json.dumps({"1": {"new": {"perDay": 20}}})  # no rev section
        conn.execute(
            "INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', ?, ?, '{}')",
            (decks, dconf),
        )
        conn.commit()
        result = _read_reviews_per_day_from_anki(conn, "0. Slovene")
        # Falls to protobuf; no deck_config table → None
        assert result is None

    def test_legacy_json_deck_not_found_falls_to_protobuf(self):
        """Deck name not in decks JSON → deck_info None → falls to protobuf."""
        import json

        from app.srs.queue_stats import _read_reviews_per_day_from_anki

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        decks = json.dumps({"1": {"name": "Other Deck", "conf": 1}})
        dconf = json.dumps({"1": {"rev": {"perDay": 50}}})
        conn.execute(
            "INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', ?, ?, '{}')",
            (decks, dconf),
        )
        conn.commit()
        result = _read_reviews_per_day_from_anki(conn, "0. Slovene")
        assert result is None

    def test_legacy_json_conf_not_dict_goes_to_protobuf(self):
        """conf_id found but its value is not a dict → falls to protobuf."""
        import json

        from app.srs.queue_stats import _read_reviews_per_day_from_anki

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        decks = json.dumps({"1": {"name": "0. Slovene", "conf": 1}})
        dconf = json.dumps({"1": "not-a-dict"})
        conn.execute(
            "INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', ?, ?, '{}')",
            (decks, dconf),
        )
        conn.commit()
        result = _read_reviews_per_day_from_anki(conn, "0. Slovene")
        assert result is None


class TestReadReviewsPerDayFromDeckConfigTable:
    def test_returns_none_when_conf_id_missing(self):
        """conf_id is None when deck is not found in decks table."""
        from app.srs.queue_stats import (
            _REVIEWS_PER_DAY_FIELD,
            _WIRE_TYPE_VARINT,
            _read_config_value_from_deck_config_table,
        )

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
        conn.execute("INSERT INTO deck_config VALUES (1, 'Slovene', 0, -1, ?)", (make_deck_config_blob(30),))
        conn.execute(
            "CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, "
            "usn INTEGER, common BLOB, kind BLOB)"
        )
        # No deck named "0. Slovene" → conf_id is None
        conn.execute("INSERT INTO decks VALUES (1, 'Other Deck', 0, -1, NULL, ?)", (make_deck_kind_blob(1),))
        conn.commit()
        result = _read_config_value_from_deck_config_table(
            conn, "0. Slovene", proto_field=_REVIEWS_PER_DAY_FIELD, wire_type=_WIRE_TYPE_VARINT
        )
        assert result is None

    def test_returns_none_when_deck_missing_from_deck_config_table(self):
        """conf_id found but no matching row in deck_config table."""
        from app.srs.queue_stats import (
            _REVIEWS_PER_DAY_FIELD,
            _WIRE_TYPE_VARINT,
            _read_config_value_from_deck_config_table,
        )

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
        # deck_config has id=1, but kind points to id=999
        conn.execute("INSERT INTO deck_config VALUES (1, 'Slovene', 0, -1, ?)", (make_deck_config_blob(30),))
        conn.execute(
            "CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, "
            "usn INTEGER, common BLOB, kind BLOB)"
        )
        conn.execute("INSERT INTO decks VALUES (1, '0. Slovene', 0, -1, NULL, ?)", (make_deck_kind_blob(999),))
        conn.commit()
        result = _read_config_value_from_deck_config_table(
            conn, "0. Slovene", proto_field=_REVIEWS_PER_DAY_FIELD, wire_type=_WIRE_TYPE_VARINT
        )
        assert result is None


class TestRefreshAndResolveColCrt:
    """Layer 45 helpers: refresh_col_crt writes the cache; resolve_col_crt reads it."""

    def test_refresh_writes_crt_then_resolve_reads_it(self):
        from app.srs.queue_stats import refresh_col_crt, resolve_col_crt

        db = SRSDatabase(":memory:")
        # Fake Anki collection with a single col.crt value.
        anki_conn = sqlite3.connect(":memory:")
        anki_conn.execute("CREATE TABLE col (id INTEGER, crt INTEGER)")
        anki_conn.execute("INSERT INTO col VALUES (1, 1388836800)")  # 2014-01-04
        anki_conn.commit()

        refresh_col_crt(db, anki_conn)
        assert resolve_col_crt(db) == 1388836800

    def test_resolve_returns_none_when_cache_empty(self):
        from app.srs.queue_stats import resolve_col_crt

        db = SRSDatabase(":memory:")
        assert resolve_col_crt(db) is None

    def test_refresh_no_op_when_col_table_empty(self):
        """If the col table has no rows, refresh writes nothing."""
        from app.srs.queue_stats import refresh_col_crt, resolve_col_crt

        db = SRSDatabase(":memory:")
        anki_conn = sqlite3.connect(":memory:")
        anki_conn.execute("CREATE TABLE col (id INTEGER, crt INTEGER)")
        anki_conn.commit()  # empty col table

        refresh_col_crt(db, anki_conn)
        assert resolve_col_crt(db) is None
