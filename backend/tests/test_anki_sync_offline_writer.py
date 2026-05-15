"""Tests for OfflineWriter bump_deck_new_today and helpers."""

from __future__ import annotations

import sqlite3

from app.anki.protobuf_wire import find_varint_field
from app.anki.sync import OfflineWriter

_REAL_BLOB = bytes.fromhex("18A12338ABA702")
_DECK_ID = 1


def _make_decks_db(common_blob: bytes | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, usn INTEGER)")
    conn.execute("INSERT INTO col (id, crt, mod, usn) VALUES (1, 1704067200, 0, 0)")
    conn.execute("CREATE TABLE decks (id INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, common BLOB)")
    blob = common_blob if common_blob is not None else _REAL_BLOB
    conn.execute("INSERT INTO decks VALUES (?, '0. Slovene', 0, 0, ?)", (_DECK_ID, blob))
    conn.commit()
    return conn


def _make_decks_db_with_review_new_reset() -> sqlite3.Connection:
    """Decks row with fields 3=4512 (yesterday), 4=10, 5=20, 7=100."""
    blob = b""
    from app.anki.protobuf_wire import encode_varint_field

    blob += encode_varint_field(3, 4512)
    blob += encode_varint_field(4, 10)
    blob += encode_varint_field(5, 20)
    blob += encode_varint_field(7, 100)
    return _make_decks_db(blob)


class TestCountRevlogBefore:
    def test_counts_revlog_before_timestamp(self):
        conn = _make_decks_db()
        conn.execute("CREATE TABLE revlog (id INTEGER, cid INTEGER)")
        conn.execute("INSERT INTO revlog VALUES (1000, 10), (2000, 10), (3000, 20)")
        conn.commit()
        writer = OfflineWriter(conn)
        assert writer.count_revlog_before(10, 2500) == 2

    def test_zero_when_no_revlog(self):
        conn = _make_decks_db()
        conn.execute("CREATE TABLE revlog (id INTEGER, cid INTEGER)")
        conn.commit()
        writer = OfflineWriter(conn)
        assert writer.count_revlog_before(10, 2500) == 0


class TestGetDeckIdForCard:
    def test_returns_did(self):
        conn = _make_decks_db()
        conn.execute("CREATE TABLE cards (id INTEGER, did INTEGER)")
        conn.execute("INSERT INTO cards VALUES (100, 5)")
        conn.commit()
        writer = OfflineWriter(conn)
        assert writer.get_deck_id_for_card(100) == 5

    def test_returns_none_for_missing_card(self):
        conn = _make_decks_db()
        conn.execute("CREATE TABLE cards (id INTEGER, did INTEGER)")
        conn.commit()
        writer = OfflineWriter(conn)
        assert writer.get_deck_id_for_card(999) is None


class TestBumpDeckNewToday:
    def test_inserts_field_when_absent(self):
        conn = _make_decks_db()
        writer = OfflineWriter(conn)
        writer.bump_deck_new_today(_DECK_ID, 4513)

        row = conn.execute("SELECT common, usn, mtime_secs FROM decks WHERE id = ?", (_DECK_ID,)).fetchone()
        blob = bytes(row[0]) if row[0] else b""
        assert find_varint_field(blob, 4) == 1
        assert find_varint_field(blob, 3) == 4513
        assert find_varint_field(blob, 7) == 37803
        assert row["usn"] == -1
        assert row["mtime_secs"] > 0

        col = conn.execute("SELECT usn FROM col").fetchone()
        assert col["usn"] == -1

    def test_increments_existing_field(self):
        blob = b""
        from app.anki.protobuf_wire import encode_varint_field

        blob += encode_varint_field(3, 4513)
        blob += encode_varint_field(4, 5)
        blob += encode_varint_field(7, 37803)
        conn = _make_decks_db(blob)
        writer = OfflineWriter(conn)
        writer.bump_deck_new_today(_DECK_ID, 4513)

        row = conn.execute("SELECT common FROM decks WHERE id = ?", (_DECK_ID,)).fetchone()
        assert find_varint_field(bytes(row[0]), 4) == 6

    def test_resets_on_rollover(self):
        conn = _make_decks_db_with_review_new_reset()
        writer = OfflineWriter(conn)
        writer.bump_deck_new_today(_DECK_ID, 4513)

        row = conn.execute("SELECT common FROM decks WHERE id = ?", (_DECK_ID,)).fetchone()
        blob = bytes(row[0]) if row[0] else b""
        assert find_varint_field(blob, 3) == 4513
        assert find_varint_field(blob, 4) == 1
        assert find_varint_field(blob, 5) is None
        assert find_varint_field(blob, 7) is None

    def test_missing_deck_is_noop(self):
        conn = _make_decks_db()
        writer = OfflineWriter(conn)
        # No exception, no row inserted
        writer.bump_deck_new_today(999, 4513)
        row = conn.execute("SELECT COUNT(*) FROM decks WHERE id = 999").fetchone()
        assert row[0] == 0
