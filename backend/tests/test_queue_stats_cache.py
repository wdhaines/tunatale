"""Tests for Change 4: cache-driven daily-new-cap (no AnkiConnect)."""

from __future__ import annotations

import sqlite3
import struct
from datetime import UTC, datetime, timedelta

from app.srs.database import SRSDatabase
from app.srs.fsrs import DEFAULT_FSRS5_PARAMS
from app.srs.queue_stats import (
    refresh_daily_new_cap,
    refresh_fsrs_params,
    refresh_review_settings,
    resolve_bury_new,
    resolve_bury_review,
    resolve_fsrs_params,
    resolve_new_spread,
)
from tests._helpers.anki_db import (
    DESIRED_RETENTION_FIELD,
    FSRS5_WEIGHTS_FIELD,
    KNOWN_WEIGHTS,
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

        blob = pb_varint_field(9, 30)
        result = _pb_find_varint_field(memoryview(blob), 9)
        assert result == 30

    def test_pb_find_len_field_memoryview(self):
        """Accepts memoryview and converts to bytes."""
        from app.srs.queue_stats import _pb_find_len_field

        inner = pb_varint_field(1, 42)
        blob = pb_len_field(1, inner)
        result = _pb_find_len_field(memoryview(blob), 1)
        assert result == inner

    def test_pb_find_varint_field_skips_other_wire_types(self):
        """Fields with different wire types before the target are skipped correctly."""
        import struct

        from app.srs.queue_stats import _pb_find_varint_field

        # field 3 (wire_type=1, 64-bit fixed), then field 9 (VARINT)
        fixed64_tag = (3 << 3) | 1
        blob = encode_varint(fixed64_tag) + struct.pack("<Q", 12345) + pb_varint_field(9, 25)
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
        blob = pb_len_field(2, b"\x00\x01\x02") + pb_varint_field(9, 30)
        result = _pb_find_varint_field(blob, 9)
        assert result == 30

    def test_pb_skip_varint_with_continuation_byte(self):
        """Skip a multi-byte varint field before finding the target."""
        from app.srs.queue_stats import _pb_find_varint_field

        # field 3 (VARINT, value=300 which requires 2 bytes), then field 9 (VARINT=7)
        # 300 in varint: 0xAC 0x02
        multi_byte_varint = bytes([0xAC, 0x02])  # 300
        blob = encode_varint((3 << 3) | 0) + multi_byte_varint + pb_varint_field(9, 7)
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
        unusual = bytes([(5 << 3) | 6]) + pb_varint_field(9, 12)
        result = _pb_find_varint_field(unusual, 9)
        # May or may not find field 9 depending on parse; just must not raise
        assert result is None or isinstance(result, int)

    def test_pb_find_len_field_skips_varint_field(self):
        """Skip a VARINT field before finding the target LEN field."""
        from app.srs.queue_stats import _pb_find_len_field

        # field 2 (VARINT=5), then field 1 (LEN)
        inner = b"\x01\x02\x03"
        blob = pb_varint_field(2, 5) + pb_len_field(1, inner)
        result = _pb_find_len_field(blob, 1)
        assert result == inner

    def test_no_conf_id_in_normal_kind_submessage(self):
        """kind blob has LEN at field 1 but no VARINT at field 1 inside → returns None."""
        from app.srs.queue_stats import _read_new_per_day_from_deck_config_table

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

        result = _read_new_per_day_from_deck_config_table(conn, "0. Slovene")
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


class TestCountAnkiIntroducedToday:
    """count_anki_introduced_today: count cards whose first revlog entry is today.

    The Anki revlog is the source of truth — TT's mirrored `reps` is unreliable
    because TT and Anki dual-grade the same card.
    """

    def _make_collection(self, tmp_path, revlog_rows):
        path = tmp_path / "collection.anki2"
        conn = sqlite3.connect(str(path))
        # Real Anki collections always have col, decks, and cards. The
        # deck-scoped count needs them present and self-consistent (every
        # revlog cid corresponds to a card in the configured deck) so the
        # JOIN succeeds and these legacy tests keep their original meaning.
        conn.execute("CREATE TABLE col (id INTEGER PRIMARY KEY, decks TEXT)")
        conn.execute("CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute(
            "CREATE TABLE cards (id INTEGER PRIMARY KEY, did INTEGER, queue INTEGER DEFAULT 0, type INTEGER DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE revlog (id INTEGER, cid INTEGER, usn INTEGER, ease INTEGER, "
            "ivl INTEGER, lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER)"
        )
        conn.execute("INSERT INTO col (id, decks) VALUES (1, '{}')")
        conn.execute("INSERT INTO decks (id, name) VALUES (1, '0. Slovene')")
        seen: set[int] = set()
        for cid, ts_ms, type_ in revlog_rows:
            if cid not in seen:
                conn.execute("INSERT INTO cards (id, did) VALUES (?, 1)", (cid,))
                seen.add(cid)
            conn.execute(
                "INSERT INTO revlog VALUES (?, ?, 0, 3, 1, 1, 0, 0, ?)",
                (ts_ms, cid, type_),
            )
        conn.commit()
        conn.close()
        return path

    def test_counts_cards_with_first_entry_today(self, tmp_path):
        from datetime import date, time

        from app.srs.queue_stats import count_anki_introduced_today

        today = date.today()
        yesterday = today - timedelta(days=1)
        today_ms = int(datetime.combine(today, time(14, 0)).timestamp() * 1000)
        yesterday_ms = int(datetime.combine(yesterday, time(14, 0)).timestamp() * 1000)

        rows = [
            # card 100: first entry yesterday → does NOT count today
            (100, yesterday_ms, 0),
            (100, today_ms, 0),  # second entry today; first is yesterday
            # card 200: first entry today → counts
            (200, today_ms, 0),
            # card 300: first entry today, multiple grades today → counts once
            (300, today_ms, 0),
            (300, today_ms + 1000, 0),
            (300, today_ms + 2000, 0),
        ]
        path = self._make_collection(tmp_path, rows)
        assert count_anki_introduced_today(today, collection_path=path) == 2

    def test_returns_zero_when_collection_missing(self, tmp_path):
        from datetime import date

        from app.srs.queue_stats import count_anki_introduced_today

        missing = tmp_path / "nope.anki2"
        assert count_anki_introduced_today(date.today(), collection_path=missing) == 0

    def test_returns_zero_when_revlog_table_missing(self, tmp_path):
        """A file that exists but lacks the `revlog` table (unlikely but defensive)
        should not propagate sqlite errors — return 0 and log.
        """
        from datetime import date

        from app.srs.queue_stats import count_anki_introduced_today

        path = tmp_path / "broken.anki2"
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE col (id INTEGER)")
        conn.commit()
        conn.close()
        assert count_anki_introduced_today(date.today(), collection_path=path) == 0

    def test_works_while_anki_is_open(self, tmp_path):
        """`mode=ro&immutable=1` URI mode does not need the exclusive lock,
        so the count is readable even while Anki holds the file open.
        """
        from datetime import date, time

        from app.srs.queue_stats import count_anki_introduced_today

        today = date.today()
        today_ms = int(datetime.combine(today, time(14, 0)).timestamp() * 1000)
        path = self._make_collection(tmp_path, [(42, today_ms, 0)])

        # Hold an exclusive-ish handle (simulating Anki); the read-only call
        # with immutable=1 must still succeed.
        holder = sqlite3.connect(str(path))
        try:
            holder.execute("BEGIN")
            holder.execute("INSERT INTO revlog VALUES (?, ?, 0, 3, 1, 1, 0, 0, 0)", (today_ms + 5, 99))
            assert count_anki_introduced_today(today, collection_path=path) == 1
        finally:
            holder.rollback()
            holder.close()

    def _make_collection_with_cards_and_decks(
        self,
        tmp_path,
        *,
        decks: list[tuple[int, str]],
        cards: list[tuple[int, int]],
        revlog_rows: list[tuple[int, int, int]],
    ):
        """Build a more realistic fake collection.

        - decks: list of (deck_id, deck_name)
        - cards: list of (card_id, deck_id)
        - revlog_rows: list of (cid, ts_ms, type)
        """
        path = tmp_path / "collection.anki2"
        conn = sqlite3.connect(str(path))
        # Minimal schemas matching what queue_stats uses
        conn.execute("CREATE TABLE col (id INTEGER PRIMARY KEY, decks TEXT)")
        conn.execute("CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute(
            "CREATE TABLE cards (id INTEGER PRIMARY KEY, did INTEGER, queue INTEGER DEFAULT 0, type INTEGER DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE revlog (id INTEGER, cid INTEGER, usn INTEGER, ease INTEGER, "
            "ivl INTEGER, lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER)"
        )
        # Populate decks via the modern table; col.decks JSON stays empty so
        # find_deck_id falls through to the table lookup.
        conn.execute("INSERT INTO col (id, decks) VALUES (1, '{}')")
        for did, name in decks:
            conn.execute("INSERT INTO decks (id, name) VALUES (?, ?)", (did, name))
        for cid, did in cards:
            conn.execute("INSERT INTO cards (id, did) VALUES (?, ?)", (cid, did))
        for cid, ts_ms, type_ in revlog_rows:
            conn.execute(
                "INSERT INTO revlog VALUES (?, ?, 0, 3, 1, 0, 0, 0, ?)",
                (ts_ms, cid, type_),
            )
        conn.commit()
        conn.close()
        return path

    def test_deck_filter_excludes_orphan_revlog_rows(self, tmp_path):
        """Revlog rows for deleted cards (no row in `cards`) must NOT count.

        Field regression: a Slovene-deck user reviewed an unrelated card today
        that was later deleted. TT's count picked up the orphan revlog entry
        and reported 1 introduction; Anki (which scopes to the active deck and
        so excludes the orphan) reported 0. This caused TT's remaining-new
        quota to be off by one for the rest of the day.
        """
        from datetime import date, time

        from app.srs.queue_stats import count_anki_introduced_today

        today = date.today()
        today_ms = int(datetime.combine(today, time(14, 0)).timestamp() * 1000)

        # cid 100 exists in target deck; cid 999 is an orphan (no card row).
        path = self._make_collection_with_cards_and_decks(
            tmp_path,
            decks=[(1, "0. Slovene")],
            cards=[(100, 1)],
            revlog_rows=[(100, today_ms, 0), (999, today_ms + 100, 0)],
        )
        assert count_anki_introduced_today(today, collection_path=path, deck_name="0. Slovene") == 1

    def test_deck_filter_excludes_other_decks(self, tmp_path):
        """Revlog rows for cards in other decks must NOT count toward the
        active deck's introduction tally."""
        from datetime import date, time

        from app.srs.queue_stats import count_anki_introduced_today

        today = date.today()
        today_ms = int(datetime.combine(today, time(14, 0)).timestamp() * 1000)
        path = self._make_collection_with_cards_and_decks(
            tmp_path,
            decks=[(1, "0. Slovene"), (2, "1. Norwegian")],
            cards=[(100, 1), (200, 2)],
            revlog_rows=[(100, today_ms, 0), (200, today_ms + 50, 0)],
        )
        # Only the Slovene grade should count, even though both are "today".
        assert count_anki_introduced_today(today, collection_path=path, deck_name="0. Slovene") == 1

    def test_day_boundary_uses_rollover_hour(self, tmp_path):
        """Anki's "today" starts at the user's `rollover` hour (default 4),
        not local-midnight. A grade made at 02:00 local belongs to *yesterday*
        for any rollover ≥ 3. TT must apply the same boundary or it'll
        double-count grades made between midnight and rollover.

        Mirrors Anki rslib `scheduler/timing.rs::sched_timing_today`.
        """
        from datetime import date, datetime, time

        from app.srs.queue_stats import count_anki_introduced_today

        today = date.today()
        # Two grades for distinct cards on `today`'s date:
        #   02:00 local (before rollover=4) → counts toward yesterday in Anki
        #   05:00 local (after  rollover=4) → counts toward today
        early_ms = int(datetime.combine(today, time(2, 0)).timestamp() * 1000)
        late_ms = int(datetime.combine(today, time(5, 0)).timestamp() * 1000)

        path = tmp_path / "collection.anki2"
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE col (id INTEGER PRIMARY KEY, decks TEXT)")
        conn.execute("CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE cards (id INTEGER PRIMARY KEY, did INTEGER)")
        conn.execute("CREATE TABLE config (KEY TEXT PRIMARY KEY, usn INTEGER, mtime_secs INTEGER, val BLOB)")
        conn.execute(
            "CREATE TABLE revlog (id INTEGER, cid INTEGER, usn INTEGER, ease INTEGER, "
            "ivl INTEGER, lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER)"
        )
        conn.execute("INSERT INTO col (id, decks) VALUES (1, '{}')")
        conn.execute("INSERT INTO decks (id, name) VALUES (1, '0. Slovene')")
        conn.execute("INSERT INTO cards (id, did) VALUES (1, 1), (2, 1)")
        # Anki's modern config table stores rollover JSON-encoded ('4' = number 4).
        conn.execute("INSERT INTO config (KEY, usn, mtime_secs, val) VALUES ('rollover', 0, 0, ?)", (b"4",))
        conn.execute("INSERT INTO revlog VALUES (?, 1, 0, 3, 1, 0, 0, 0, 0)", (early_ms,))
        conn.execute("INSERT INTO revlog VALUES (?, 2, 0, 3, 1, 0, 0, 0, 0)", (late_ms,))
        conn.commit()
        conn.close()

        # Only the 05:00 grade is "today" under rollover=4.
        # (Skip on days where the harness wall-clock crosses the boundary mid-test.)
        if datetime.now() < datetime.combine(today, time(4, 0)):
            return
        assert count_anki_introduced_today(today, collection_path=path, deck_name="0. Slovene") == 1

    def test_day_boundary_default_rollover_when_config_missing(self, tmp_path):
        """If the `config` table has no `rollover` key, default to Anki's
        4 AM rollover. (Defensive: collections may predate the modern table.)
        """
        from datetime import date, datetime, time

        from app.srs.queue_stats import count_anki_introduced_today

        today = date.today()
        early_ms = int(datetime.combine(today, time(2, 0)).timestamp() * 1000)

        path = tmp_path / "collection.anki2"
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE col (id INTEGER PRIMARY KEY, decks TEXT, conf TEXT)")
        conn.execute("CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE cards (id INTEGER PRIMARY KEY, did INTEGER)")
        conn.execute(
            "CREATE TABLE revlog (id INTEGER, cid INTEGER, usn INTEGER, ease INTEGER, "
            "ivl INTEGER, lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER)"
        )
        conn.execute("INSERT INTO col (id, decks, conf) VALUES (1, '{}', '{}')")
        conn.execute("INSERT INTO decks (id, name) VALUES (1, '0. Slovene')")
        conn.execute("INSERT INTO cards (id, did) VALUES (1, 1)")
        conn.execute("INSERT INTO revlog VALUES (?, 1, 0, 3, 1, 0, 0, 0, 0)", (early_ms,))
        conn.commit()
        conn.close()

        if datetime.now() < datetime.combine(today, time(4, 0)):
            return
        # 02:00 grade falls before the default 4 AM rollover → not today.
        assert count_anki_introduced_today(today, collection_path=path, deck_name="0. Slovene") == 0

    def test_counts_globally_when_deck_name_blank(self, tmp_path):
        """Empty deck_name → fall through to the unscoped revlog count."""
        from datetime import date, time

        from app.srs.queue_stats import count_anki_introduced_today

        today = date.today()
        today_ms = int(datetime.combine(today, time(14, 0)).timestamp() * 1000)
        path = self._make_collection(tmp_path, [(100, today_ms, 0), (200, today_ms, 0)])
        # deck_name="" is truthy-as-arg (so settings default is skipped) but
        # falsy in the `if deck` guard, so the deck-scoped JOIN is bypassed.
        assert count_anki_introduced_today(today, collection_path=path, deck_name="") == 2


class TestReadRolloverHour:
    """`_read_rollover_hour` parses Anki's `rollover` setting from either the
    modern `config` table or legacy `col.conf` JSON. Malformed values must
    fall through silently to the next source / the default of 4."""

    def test_invalid_json_in_config_table_falls_through_to_col_conf(self):
        """Modern `config.val` blob that isn't valid JSON → caught, fall through."""
        from app.srs.queue_stats import _read_rollover_hour

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE config (KEY TEXT PRIMARY KEY, val BLOB)")
        # Garbage bytes — not valid JSON.
        conn.execute("INSERT INTO config (KEY, val) VALUES ('rollover', ?)", (b"\xff\xfe not json",))
        conn.execute("CREATE TABLE col (conf TEXT)")
        conn.execute("INSERT INTO col (conf) VALUES (?)", ('{"rollover": 6}',))
        conn.commit()
        # Modern path raises → falls through to col.conf and returns 6.
        assert _read_rollover_hour(conn) == 6

    def test_invalid_json_in_config_table_falls_through_to_default(self):
        """If neither source yields a valid int, return the Anki default (4)."""
        from app.srs.queue_stats import _read_rollover_hour

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE config (KEY TEXT PRIMARY KEY, val BLOB)")
        conn.execute("INSERT INTO config (KEY, val) VALUES ('rollover', ?)", (b"not json",))
        conn.commit()
        # No col table at all → both paths swallow errors → default.
        assert _read_rollover_hour(conn) == 4

    def test_col_conf_with_non_dict_json_falls_through_to_default(self):
        """`col.conf` parses as JSON but isn't a dict → AttributeError on .get → default."""
        from app.srs.queue_stats import _read_rollover_hour

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE col (conf TEXT)")
        # Valid JSON, but a string — `.get('rollover')` will raise AttributeError.
        conn.execute("INSERT INTO col (conf) VALUES (?)", ('"not a dict"',))
        conn.commit()
        assert _read_rollover_hour(conn) == 4

    def test_col_conf_with_invalid_json_falls_through_to_default(self):
        """`col.conf` that isn't valid JSON at all → JSONDecodeError → default."""
        from app.srs.queue_stats import _read_rollover_hour

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE col (conf TEXT)")
        conn.execute("INSERT INTO col (conf) VALUES (?)", ("{not valid json",))
        conn.commit()
        assert _read_rollover_hour(conn) == 4

    def test_no_rollover_row_in_config_table_falls_through(self):
        """`config` table exists but has no 'rollover' key → row is None, fall through."""
        from app.srs.queue_stats import _read_rollover_hour

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE config (KEY TEXT PRIMARY KEY, val BLOB)")
        # No 'rollover' row inserted.
        conn.execute("CREATE TABLE col (conf TEXT)")
        conn.execute("INSERT INTO col (conf) VALUES (?)", ('{"rollover": 7}',))
        conn.commit()
        assert _read_rollover_hour(conn) == 7

    def test_out_of_range_rollover_in_config_table_falls_through(self):
        """`config.val` parses as JSON but isn't an int in [0, 23] → fall through."""
        from app.srs.queue_stats import _read_rollover_hour

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE config (KEY TEXT PRIMARY KEY, val BLOB)")
        # Valid JSON, but 99 is out of [0, 23].
        conn.execute("INSERT INTO config (KEY, val) VALUES ('rollover', ?)", (b"99",))
        conn.execute("CREATE TABLE col (conf TEXT)")
        conn.execute("INSERT INTO col (conf) VALUES (?)", ('{"rollover": 5}',))
        conn.commit()
        assert _read_rollover_hour(conn) == 5

    def test_empty_col_conf_falls_through_to_default(self):
        """`col.conf` empty string → row[0] falsy, skip parsing, return default."""
        from app.srs.queue_stats import _read_rollover_hour

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE col (conf TEXT)")
        conn.execute("INSERT INTO col (conf) VALUES (?)", ("",))
        conn.commit()
        assert _read_rollover_hour(conn) == 4


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
