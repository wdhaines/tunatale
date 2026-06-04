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


class TestBumpDeckNewToday:
    def test_inserts_field_when_absent(self):
        conn = _make_decks_db()
        conn.execute("UPDATE col SET usn = 7")
        conn.commit()
        writer = OfflineWriter(conn)
        writer.bump_deck_new_today(_DECK_ID, 4513)

        row = conn.execute("SELECT common, usn, mtime_secs FROM decks WHERE id = ?", (_DECK_ID,)).fetchone()
        blob = bytes(row[0]) if row[0] else b""
        assert find_varint_field(blob, 4) == 1
        assert find_varint_field(blob, 3) == 4513
        assert find_varint_field(blob, 7) == 37803
        assert row["usn"] == -1
        assert row["mtime_secs"] > 0

        # col.usn anchor preserved (Layer 61); the deck row pushes via its own usn=-1
        col = conn.execute("SELECT usn FROM col").fetchone()
        assert col["usn"] == 7

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


class TestListDecksWithRevlogToday:
    def test_returns_distinct_deck_ids(self):
        conn = _make_decks_db()
        conn.execute("CREATE TABLE revlog (id INTEGER, cid INTEGER)")
        conn.execute("CREATE TABLE cards (id INTEGER, did INTEGER)")
        # Two cards in deck 1, one in deck 2; revlog has entries today (>= 1000) and yesterday (< 1000).
        conn.execute("INSERT INTO cards VALUES (10, 1), (11, 1), (20, 2)")
        conn.execute("INSERT INTO revlog VALUES (500, 10), (1500, 10), (2500, 11), (3500, 20)")
        conn.commit()
        writer = OfflineWriter(conn)
        result = sorted(writer.list_decks_with_revlog_today(1000))
        assert result == [1, 2]

    def test_excludes_pre_today_only_decks(self):
        conn = _make_decks_db()
        conn.execute("CREATE TABLE revlog (id INTEGER, cid INTEGER)")
        conn.execute("CREATE TABLE cards (id INTEGER, did INTEGER)")
        conn.execute("INSERT INTO cards VALUES (10, 1), (20, 2)")
        # Deck 1 has only pre-today revlog; Deck 2 has today's.
        conn.execute("INSERT INTO revlog VALUES (500, 10), (1500, 20)")
        conn.commit()
        writer = OfflineWriter(conn)
        result = writer.list_decks_with_revlog_today(1000)
        assert result == [2]

    def test_no_revlog_table_returns_empty(self):
        conn = _make_decks_db()
        # No revlog or cards table — must not raise.
        writer = OfflineWriter(conn)
        assert writer.list_decks_with_revlog_today(1000) == []


class TestCountFirstGradesTodayForDeck:
    def test_counts_only_first_revlog_today(self):
        conn = _make_decks_db()
        conn.execute("CREATE TABLE revlog (id INTEGER, cid INTEGER)")
        conn.execute("CREATE TABLE cards (id INTEGER, did INTEGER)")
        conn.execute("INSERT INTO cards VALUES (10, 1), (11, 1), (12, 1), (20, 2)")
        # cid=10: first-grade today (>= 1000) → counts.
        # cid=11: first-grade YESTERDAY (< 1000), graded again today → does NOT count.
        # cid=12: first-grade today → counts.
        # cid=20: in deck 2 → not in deck 1's count.
        conn.execute("""
            INSERT INTO revlog VALUES
              (1500, 10),
              (500, 11), (1600, 11),
              (1700, 12),
              (1800, 20)
        """)
        conn.commit()
        writer = OfflineWriter(conn)
        assert writer.count_first_grades_today_for_deck(1, 1000) == 2

    def test_zero_when_no_cards(self):
        conn = _make_decks_db()
        conn.execute("CREATE TABLE revlog (id INTEGER, cid INTEGER)")
        conn.execute("CREATE TABLE cards (id INTEGER, did INTEGER)")
        conn.commit()
        writer = OfflineWriter(conn)
        assert writer.count_first_grades_today_for_deck(1, 1000) == 0

    def test_catches_operational_error_when_revlog_table_missing(self):
        conn = _make_decks_db()
        writer = OfflineWriter(conn)
        assert writer.count_first_grades_today_for_deck(1, 1000) == 0


class TestSetDeckNewToday:
    def test_writes_explicit_value(self):
        conn = _make_decks_db()
        writer = OfflineWriter(conn)
        writer.set_deck_new_today(_DECK_ID, 4513, 17)

        row = conn.execute("SELECT common, usn FROM decks WHERE id = ?", (_DECK_ID,)).fetchone()
        blob = bytes(row[0]) if row[0] else b""
        assert find_varint_field(blob, 4) == 17
        assert find_varint_field(blob, 3) == 4513
        assert row["usn"] == -1

    def test_overwrites_existing_value(self):
        blob = b""
        from app.anki.protobuf_wire import encode_varint_field

        blob += encode_varint_field(3, 4513)
        blob += encode_varint_field(4, 30)  # the overcounted value
        conn = _make_decks_db(blob)
        writer = OfflineWriter(conn)
        # Recompute to truth.
        writer.set_deck_new_today(_DECK_ID, 4513, 24)
        row = conn.execute("SELECT common FROM decks WHERE id = ?", (_DECK_ID,)).fetchone()
        assert find_varint_field(bytes(row[0]), 4) == 24

    def test_applies_rollover_when_last_day_older(self):
        conn = _make_decks_db_with_review_new_reset()
        writer = OfflineWriter(conn)
        writer.set_deck_new_today(_DECK_ID, 4513, 5)
        row = conn.execute("SELECT common FROM decks WHERE id = ?", (_DECK_ID,)).fetchone()
        blob = bytes(row[0]) if row[0] else b""
        assert find_varint_field(blob, 3) == 4513
        assert find_varint_field(blob, 4) == 5
        assert find_varint_field(blob, 5) is None
        assert find_varint_field(blob, 7) is None

    def test_missing_deck_is_noop(self):
        conn = _make_decks_db()
        writer = OfflineWriter(conn)
        writer.set_deck_new_today(999, 4513, 5)
        row = conn.execute("SELECT COUNT(*) FROM decks WHERE id = 999").fetchone()
        assert row[0] == 0


def _make_cards_db(col_usn: int = 5) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, usn INTEGER)")
    conn.execute("INSERT INTO col (id, crt, mod, usn) VALUES (1, 1704067200, 0, ?)", (col_usn,))
    conn.execute(
        "CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, ord INTEGER, "
        "mod INTEGER, usn INTEGER, type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER, "
        "factor INTEGER, reps INTEGER, lapses INTEGER, left INTEGER, odue INTEGER, odid INTEGER, "
        "flags INTEGER, data TEXT)"
    )
    conn.commit()
    return conn


def _insert_review_card(conn: sqlite3.Connection, *, cid: int = 90011, due: int = 4533) -> None:
    conn.execute(
        "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl, factor, reps, "
        "lapses, left, odue, odid, flags, data) "
        'VALUES (?, 1, 1, 1, 100, 5, 2, 2, ?, 8, 2500, 3, 1, 0, 5, 9, 0, \'{"s":7.5,"d":5.2}\')',
        (cid, due),
    )
    conn.commit()


class TestForgetCard:
    """forget_card resets an Anki card to NEW (Anki's "Forget") so a TT reset
    propagates instead of leaving Anki on the graduated review."""

    def test_resets_review_card_to_new(self):
        conn = _make_cards_db()
        _insert_review_card(conn, cid=90011)
        OfflineWriter(conn).forget_card(90011)
        row = conn.execute(
            "SELECT type, queue, reps, lapses, ivl, factor, odue, odid, data, usn FROM cards WHERE id = 90011"
        ).fetchone()
        assert row["type"] == 0
        assert row["queue"] == 0
        assert row["reps"] == 0
        assert row["lapses"] == 0
        assert row["ivl"] == 0
        assert row["factor"] == 0
        assert row["odue"] == 0
        assert row["odid"] == 0
        assert row["data"] == "{}"
        assert row["usn"] == -1

    def test_bumps_col_mod_preserves_usn_anchor(self):
        conn = _make_cards_db(col_usn=7)
        _insert_review_card(conn)
        OfflineWriter(conn).forget_card(90011)
        col = conn.execute("SELECT mod, usn FROM col").fetchone()
        assert col["mod"] > 0
        assert col["usn"] == 7  # anchor preserved (Layer 61)

    def test_places_card_at_tail_of_new_queue(self):
        conn = _make_cards_db()
        # Existing new card sitting at position 12 → forgotten card lands at 13.
        conn.execute(
            "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl, factor, reps, "
            "lapses, left, odue, odid, flags, data) "
            "VALUES (5, 2, 1, 0, 0, 0, 0, 0, 12, 0, 0, 0, 0, 0, 0, 0, 0, '{}')"
        )
        _insert_review_card(conn, cid=90011)
        OfflineWriter(conn).forget_card(90011)
        row = conn.execute("SELECT due FROM cards WHERE id = 90011").fetchone()
        assert row["due"] == 13

    def test_first_new_card_lands_at_position_one(self):
        conn = _make_cards_db()
        _insert_review_card(conn, cid=90011)
        OfflineWriter(conn).forget_card(90011)
        row = conn.execute("SELECT due FROM cards WHERE id = 90011").fetchone()
        assert row["due"] == 1
