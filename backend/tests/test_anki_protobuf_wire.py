"""Tests for app.srs.anki_mirror.protobuf_wire."""

from __future__ import annotations

from app.srs.anki_mirror.protobuf_wire import (
    compute_anki_day_index,
    decode_varint,
    encode_varint,
    encode_varint_field,
    find_varint_field,
    pb_remove_field,
    pb_replace_or_insert_varint,
    skip_field,
)

_REAL_BLOB = bytes.fromhex("18A12338ABA702")


class TestVarintRoundtrip:
    def test_varint_roundtrip(self):
        for val in (0, 1, 127, 128, 300, 1 << 28):
            encoded = encode_varint(val)
            decoded, pos = decode_varint(encoded, 0)
            assert decoded == val
            assert pos == len(encoded)

    def test_encode_varint_field(self):
        encoded = encode_varint_field(3, 4513)
        tag = encoded[0]
        assert tag == (3 << 3) | 0


class TestFindVarint:
    def test_find_varint_in_present_field(self):
        assert find_varint_field(_REAL_BLOB, 3) == 4513

    def test_find_varint_in_absent_field(self):
        assert find_varint_field(_REAL_BLOB, 4) is None

    def test_find_varint_in_absent_field_nonzero_blob(self):
        encoded = encode_varint_field(3, 100) + encode_varint_field(7, 200)
        assert find_varint_field(encoded, 4) is None


class TestReplaceOrInsertVarint:
    def test_replace_existing_field(self):
        blob = encode_varint_field(3, 100) + encode_varint_field(4, 5) + encode_varint_field(7, 200)
        result = pb_replace_or_insert_varint(blob, 4, 10)
        assert find_varint_field(result, 4) == 10
        assert find_varint_field(result, 3) == 100
        assert find_varint_field(result, 7) == 200

    def test_insert_new_field_appends(self):
        blob = encode_varint_field(3, 100) + encode_varint_field(7, 200)
        result = pb_replace_or_insert_varint(blob, 4, 1)
        assert find_varint_field(result, 4) == 1
        assert find_varint_field(result, 3) == 100
        assert find_varint_field(result, 7) == 200


class TestRemoveField:
    def test_remove_field(self):
        blob = encode_varint_field(3, 100) + encode_varint_field(4, 5) + encode_varint_field(7, 200)
        result = pb_remove_field(blob, 4)
        assert find_varint_field(result, 4) is None
        assert find_varint_field(result, 7) == 200

    def test_remove_field_idempotent_when_absent(self):
        blob = encode_varint_field(3, 100)
        result = pb_remove_field(blob, 4)
        assert result == blob

    def test_remove_field_empty_blob(self):
        assert pb_remove_field(b"", 4) == b""


class TestSkipField:
    def test_skip_varint(self):
        data = encode_varint(300) + encode_varint(42)
        pos = skip_field(data, 0, 0)
        decoded, _ = decode_varint(data, pos)
        assert decoded == 42


class TestComputeAnkiDayIndex:
    def test_day_index_zero_on_col_crt(self):
        from datetime import datetime

        now = datetime.fromtimestamp(1704067200)  # same as col_crt
        idx = compute_anki_day_index(1704067200, rollover_hour=4, now=now)
        assert idx == 0

    def test_day_index_increments_after_rollover(self):
        from datetime import datetime

        col_crt = 1704067200  # 2024-01-01 00:00:00 UTC
        # Day index at rollover time: col_crt + 1 day
        now = datetime.fromtimestamp(col_crt + 86400 + 4 * 3600)
        idx = compute_anki_day_index(col_crt, rollover_hour=4, now=now)
        assert idx == 1
