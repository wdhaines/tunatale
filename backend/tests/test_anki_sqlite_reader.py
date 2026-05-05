"""Tests for read-only Anki DB helpers."""

import json
import sqlite3
from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.anki.sqlite_reader import (
    extract_l2,
    extract_l2_from_fields,
    fetch_cards_for_notes,
    fetch_notes_for_deck,
    find_deck_id,
    list_media_refs,
    parse_fsrs_data,
    read_fsrs_state_for_cards,
)
from app.models.srs_item import Direction, SRSState


class TestFindDeckId:
    def test_finds_deck_via_col_json(self, fake_anki_db):
        conn = sqlite3.connect(str(fake_anki_db))
        did = find_deck_id(conn, "0. Slovene")
        conn.close()
        assert did == 12345

    def test_finds_deck_via_decks_table(self, fake_anki_db_modern):
        conn = sqlite3.connect(str(fake_anki_db_modern))
        did = find_deck_id(conn, "0. Slovene")
        conn.close()
        assert did == 12345

    def test_returns_none_for_missing_deck(self, fake_anki_db):
        conn = sqlite3.connect(str(fake_anki_db))
        did = find_deck_id(conn, "Nonexistent Deck")
        conn.close()
        assert did is None

    def test_returns_none_when_col_has_malformed_json(self, tmp_path):
        """Malformed col.decks JSON falls through to decks table lookup (no table → None)."""
        db_path = tmp_path / "bad_json.anki2"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE col (id INTEGER, decks TEXT)")
        conn.execute("INSERT INTO col VALUES (1, 'not valid json')")
        conn.commit()
        conn.close()
        conn = sqlite3.connect(str(db_path))
        did = find_deck_id(conn, "0. Slovene")
        conn.close()
        assert did is None  # no decks table and JSON failed

    def test_returns_none_when_col_row_missing(self, tmp_path):
        """If col table is empty, row is None; falls through to decks table (also absent)."""
        db_path = tmp_path / "empty_col.anki2"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE col (id INTEGER, decks TEXT)")
        conn.commit()
        conn.close()
        conn = sqlite3.connect(str(db_path))
        did = find_deck_id(conn, "0. Slovene")
        conn.close()
        assert did is None

    def test_returns_none_when_modern_deck_not_in_list(self, fake_anki_db_modern):
        """No match in decks table → returns None."""
        conn = sqlite3.connect(str(fake_anki_db_modern))
        did = find_deck_id(conn, "Deck That Does Not Exist")
        conn.close()
        assert did is None


class TestFetchNotesForDeck:
    def test_returns_all_five_notes(self, fake_anki_db):
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        notes = fetch_notes_for_deck(conn, 12345)
        conn.close()
        assert len(notes) == 5

    def test_fields_split_on_unit_separator(self, fake_anki_db):
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        notes = fetch_notes_for_deck(conn, 12345)
        conn.close()
        # First note: "banka\x1fbank"
        banka = next(n for n in notes if n.id == 1001)
        assert banka.fields[0] == "banka"
        assert banka.fields[1] == "bank"

    def test_note_ids_match_expected(self, fake_anki_db):
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        notes = fetch_notes_for_deck(conn, 12345)
        conn.close()
        ids = {n.id for n in notes}
        assert ids == {1001, 1002, 1003, 1004, 1005}


class TestFetchCardsForNotes:
    def test_returns_ten_cards(self, fake_anki_db):
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        cards = fetch_cards_for_notes(conn, [1001, 1002, 1003, 1004, 1005])
        conn.close()
        assert len(cards) == 10

    def test_ord_zero_is_recognition(self, fake_anki_db):
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        cards = fetch_cards_for_notes(conn, [1001])
        conn.close()
        rec = next(c for c in cards if c.ord == 0)
        assert rec.direction == Direction.RECOGNITION

    def test_ord_one_is_production(self, fake_anki_db):
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        cards = fetch_cards_for_notes(conn, [1001])
        conn.close()
        prod = next(c for c in cards if c.ord == 1)
        assert prod.direction == Direction.PRODUCTION

    def test_suspended_card_has_suspended_state(self, fake_anki_db):
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        # note 1003 production card is suspended (queue=-1)
        cards = fetch_cards_for_notes(conn, [1003])
        conn.close()
        prod = next(c for c in cards if c.ord == 1)
        assert prod.fsrs_state.state == SRSState.SUSPENDED

    def test_fsrs_data_parsed_from_cards_data(self, fake_anki_db):
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        cards = fetch_cards_for_notes(conn, [1001])
        conn.close()
        rec = next(c for c in cards if c.ord == 0)
        assert rec.fsrs_state.stability == pytest.approx(10.5)
        assert rec.fsrs_state.difficulty == pytest.approx(4.8)

    def test_empty_data_falls_back_to_new(self, fake_anki_db, tmp_path):
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        fallback_log = tmp_path / "fallback.log"
        cards = fetch_cards_for_notes(conn, [1005], fallback_log_path=fallback_log)
        conn.close()
        assert all(c.fsrs_state.stability == pytest.approx(1.0) for c in cards)
        assert all(c.fsrs_state.difficulty == pytest.approx(5.0) for c in cards)
        assert fallback_log.exists()

    def test_empty_note_ids_returns_empty_list(self, fake_anki_db):
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        cards = fetch_cards_for_notes(conn, [])
        conn.close()
        assert cards == []


class TestParseFsrsData:
    def test_valid_json_parsed(self):
        state = parse_fsrs_data(
            card_id=99,
            ord=0,
            data_str=json.dumps({"s": 15.0, "d": 3.5}),
            queue=2,
            reps=4,
            lapses=1,
        )
        assert state.stability == pytest.approx(15.0)
        assert state.difficulty == pytest.approx(3.5)
        assert state.reps == 4
        assert state.direction == Direction.RECOGNITION

    def test_empty_data_falls_back(self):
        state = parse_fsrs_data(card_id=99, ord=0, data_str="", queue=0, reps=0, lapses=0)
        assert state.stability == pytest.approx(1.0)
        assert state.difficulty == pytest.approx(5.0)

    def test_malformed_json_falls_back(self):
        state = parse_fsrs_data(card_id=99, ord=0, data_str="{bad json}", queue=0, reps=0, lapses=0)
        assert state.stability == pytest.approx(1.0)

    def test_missing_keys_fall_back(self):
        state = parse_fsrs_data(card_id=99, ord=0, data_str=json.dumps({"x": 1}), queue=0, reps=0, lapses=0)
        assert state.stability == pytest.approx(1.0)

    def test_suspended_queue_sets_state(self):
        state = parse_fsrs_data(
            card_id=99,
            ord=0,
            data_str=json.dumps({"s": 5.0, "d": 5.0}),
            queue=-1,
            reps=3,
            lapses=0,
        )
        assert state.state == SRSState.SUSPENDED

    def test_fallback_appends_to_log(self, tmp_path):
        log = tmp_path / "fallback.log"
        parse_fsrs_data(card_id=42, ord=0, data_str="", queue=0, reps=0, lapses=0, fallback_log_path=log)
        assert "42" in log.read_text()

    def test_production_ord_sets_direction(self):
        state = parse_fsrs_data(
            card_id=99,
            ord=1,
            data_str=json.dumps({"s": 5.0, "d": 5.0}),
            queue=2,
            reps=2,
            lapses=0,
        )
        assert state.direction == Direction.PRODUCTION

    def test_queue_2_card_due_date_uses_col_crt(self):
        """queue=2 (review): due_date = date.fromtimestamp(col_crt) + timedelta(days=due_raw)."""
        col_crt = 1704067200  # 2024-01-01 00:00:00 UTC
        due_raw = 10
        state = parse_fsrs_data(
            card_id=1,
            ord=0,
            data_str=json.dumps({"s": 5.0, "d": 5.0}),
            queue=2,
            reps=3,
            lapses=0,
            col_crt=col_crt,
            due_raw=due_raw,
        )
        expected = date.fromtimestamp(col_crt) + timedelta(days=due_raw)
        assert state.due_date == expected

    def test_queue_1_card_due_date_uses_epoch_seconds(self):
        """queue=1 (learning): due_date = datetime.fromtimestamp(due_raw).date()."""
        due_raw = 1704067200 + 86400 * 5  # 5 days after col_crt epoch
        state = parse_fsrs_data(
            card_id=2,
            ord=0,
            data_str=json.dumps({"s": 5.0, "d": 5.0}),
            queue=1,
            reps=1,
            lapses=0,
            col_crt=1704067200,
            due_raw=due_raw,
        )
        expected = datetime.fromtimestamp(due_raw).date()
        assert state.due_date == expected

    def test_new_card_falls_back_to_today(self):
        """queue=0 (new card): due_raw is a position, not a date — fall back to today."""
        state = parse_fsrs_data(
            card_id=3,
            ord=0,
            data_str="",
            queue=0,
            reps=0,
            lapses=0,
            col_crt=1704067200,
            due_raw=5,
        )
        assert state.due_date == date.today()
        assert state.anki_due == 5

    def test_review_card_captures_anki_due(self):
        """queue=2 (review): anki_due captures due_raw (days since col.crt)."""
        col_crt = 1704067200  # 2024-01-01
        state = parse_fsrs_data(
            card_id=10,
            ord=0,
            data_str=json.dumps({"s": 5.0, "d": 5.0}),
            queue=2,
            reps=3,
            lapses=0,
            col_crt=col_crt,
            due_raw=10,
        )
        assert state.anki_due == 10

    def test_queue_minus_2_sets_state_buried(self):
        """queue=-2 (user-buried) → SRSState.BURIED."""
        state = parse_fsrs_data(
            card_id=4,
            ord=0,
            data_str=json.dumps({"s": 5.0, "d": 5.0}),
            queue=-2,
            reps=4,
            lapses=0,
        )
        assert state.state == SRSState.BURIED

    def test_queue_minus_3_sets_state_buried(self):
        """queue=-3 (sibling-buried) → SRSState.BURIED."""
        state = parse_fsrs_data(
            card_id=5,
            ord=0,
            data_str=json.dumps({"s": 5.0, "d": 5.0}),
            queue=-3,
            reps=4,
            lapses=0,
        )
        assert state.state == SRSState.BURIED

    def test_queue_1_sets_state_learning(self):
        """queue=1 (learning) → SRSState.LEARNING (not REVIEW)."""
        state = parse_fsrs_data(
            card_id=6,
            ord=0,
            data_str=json.dumps({"s": 5.0, "d": 5.0}),
            queue=1,
            reps=2,
            lapses=0,
            col_crt=1704067200,
            due_raw=1704067200 + 86400,
        )
        assert state.state == SRSState.LEARNING

    def test_queue_3_sets_state_relearning(self):
        """queue=3 (day-learn / relearning) → SRSState.RELEARNING."""
        state = parse_fsrs_data(
            card_id=7,
            ord=0,
            data_str=json.dumps({"s": 5.0, "d": 5.0}),
            queue=3,
            reps=5,
            lapses=1,
            col_crt=1704067200,
            due_raw=5,
        )
        assert state.state == SRSState.RELEARNING

    def test_parse_fsrs_data_sets_last_review_for_review_card(self):
        """queue=2 (review): last_review = date from (due - ivl) via col_crt."""
        col_crt = 1388836800  # 2014-01-04 12:00:00 UTC
        # due_raw=4501, ivl=3 → last_review_day = 4498 → 2014-01-04 + 4498 days = 2026-04-29
        state = parse_fsrs_data(
            card_id=1,
            ord=0,
            data_str=json.dumps({"s": 0.001, "d": 5.0}),
            queue=2,
            reps=4,
            lapses=0,
            col_crt=col_crt,
            due_raw=4501,
            ivl=3,
        )
        assert state.last_review == datetime.combine(date(2026, 4, 29), time.min, tzinfo=UTC)

    def test_parse_fsrs_data_sets_last_review_for_relearning_card(self):
        """queue=3 (day-relearning): last_review computed same as queue=2."""
        col_crt = 1388836800
        # due_raw=500, ivl=10 → last_review_day = 490 → 2014-01-04 + 490 days = 2015-05-09
        state = parse_fsrs_data(
            card_id=2,
            ord=1,
            data_str=json.dumps({"s": 0.5, "d": 6.0}),
            queue=3,
            reps=5,
            lapses=1,
            col_crt=col_crt,
            due_raw=500,
            ivl=10,
        )
        assert state.last_review == datetime.combine(date(2015, 5, 9), time.min, tzinfo=UTC)

    def test_parse_fsrs_data_last_review_none_for_new_card(self):
        """queue=0 (new): last_review is None."""
        state = parse_fsrs_data(
            card_id=3,
            ord=0,
            data_str="",
            queue=0,
            reps=0,
            lapses=0,
            col_crt=1704067200,
            due_raw=5,
            ivl=0,
        )
        assert state.last_review is None

    def test_parse_fsrs_data_last_review_none_for_learning_card(self):
        """queue=1 (sub-day learning): last_review is None (no due/ivl formula)."""
        state = parse_fsrs_data(
            card_id=4,
            ord=0,
            data_str=json.dumps({"s": 5.0, "d": 5.0}),
            queue=1,
            reps=1,
            lapses=0,
            col_crt=1704067200,
            due_raw=1704067200 + 86400,
            ivl=0,
        )
        assert state.last_review is None

    def test_parse_fsrs_data_last_review_fallback_branch(self):
        """Fallback branch (no JSON data) also computes last_review for queue 2."""
        col_crt = 1388836800
        state = parse_fsrs_data(
            card_id=5,
            ord=0,
            data_str="",  # no JSON → fallback
            queue=2,
            reps=3,
            lapses=0,
            col_crt=col_crt,
            due_raw=4501,
            ivl=3,
        )
        assert state.last_review == datetime.combine(date(2026, 4, 29), time.min, tzinfo=UTC)


class TestExtractL2:
    def test_extracts_from_class_slovene(self):
        html = '<span class="slovene">hiša</span>'
        assert extract_l2(html) == "hiša"

    def test_falls_back_to_stripped_text(self):
        html = "<b>banka</b>"
        assert extract_l2(html) == "banka"

    def test_plain_text_returned_as_is(self):
        assert extract_l2("banka") == "banka"


class TestExtractL2FromFields:
    def test_returns_first_field_when_it_has_l2(self):
        assert extract_l2_from_fields(['<span class="slovene">hiša</span>', "house"]) == "hiša"

    def test_falls_back_to_second_field_when_first_is_image_only(self):
        fields = ['<div class="img"><img src="dog.jpg"></div>', '<div class="slovene">pes</div>']
        assert extract_l2_from_fields(fields) == "pes"

    def test_falls_back_to_second_field_plain_text_when_first_empty(self):
        fields = ["<div></div>", "<b>banka</b>"]
        assert extract_l2_from_fields(fields) == "banka"

    def test_returns_empty_when_no_field_yields_text(self):
        assert extract_l2_from_fields(["<div></div>", "  "]) == ""

    def test_empty_list_returns_empty(self):
        assert extract_l2_from_fields([]) == ""


class TestListMediaRefs:
    def test_extracts_sound_ref(self):
        fields = ["[sound:sl_banka.mp3]"]
        assert list_media_refs(fields) == ["sl_banka.mp3"]

    def test_extracts_img_ref(self):
        fields = ['<img src="banka.jpg">']
        assert list_media_refs(fields) == ["banka.jpg"]

    def test_empty_fields_returns_empty(self):
        assert list_media_refs(["no media here"]) == []

    def test_multiple_refs_in_multiple_fields(self):
        fields = ["[sound:a.mp3]", "[sound:b.mp3]", '<img src="c.jpg">']
        refs = list_media_refs(fields)
        assert set(refs) == {"a.mp3", "b.mp3", "c.jpg"}


class TestReadFsrsStateForCards:
    def _write_db(self, tmp_path, rows: list[tuple[int, str | None]]):
        path = tmp_path / "collection.anki2"
        with sqlite3.connect(str(path)) as conn:
            conn.execute("CREATE TABLE cards (id INTEGER PRIMARY KEY, data TEXT)")
            conn.executemany("INSERT INTO cards (id, data) VALUES (?, ?)", rows)
        return str(path)

    def test_empty_card_ids_returns_empty(self, tmp_path):
        path = self._write_db(tmp_path, [(1, '{"s": 1.0, "d": 5.0}')])
        assert read_fsrs_state_for_cards(path, []) == {}

    def test_null_data_column_skipped(self, tmp_path):
        path = self._write_db(tmp_path, [(1, None), (2, '{"s": 0.5, "d": 7.0}')])
        result = read_fsrs_state_for_cards(path, [1, 2])
        assert 1 not in result
        assert result[2] == (0.5, 7.0)

    def test_nonexistent_path_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_fsrs_state_for_cards(tmp_path / "missing.anki2", [1])

    def test_invalid_json_skipped(self, tmp_path):
        """Invalid JSON in data column is skipped."""
        path = self._write_db(
            tmp_path,
            [
                (1, '{"s": 1.0, "d": 5.0}'),
                (2, "not valid json"),
                (3, '{"s": "bad", "d": 7.0}'),
            ],
        )
        result = read_fsrs_state_for_cards(path, [1, 2, 3])
        assert 1 in result
        assert 2 not in result
        assert 3 not in result


class TestLeftAndDueAtFromCards:
    """Tests that left column and due_at are correctly read from Anki cards table."""

    def _make_db(self, tmp_path, cards_rows):
        """Create a minimal Anki DB with given cards rows.

        cards_rows: list of (id, nid, did, ord, queue, reps, lapses, data, due, ivl, left)
        """
        path = tmp_path / "collection.anki2"
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE col (id INTEGER PRIMARY KEY, crt INTEGER)")
        conn.execute("INSERT INTO col VALUES (1, 1704067200)")  # 2024-01-01 UTC
        conn.execute(
            """CREATE TABLE cards (
                id INTEGER PRIMARY KEY,
                nid INTEGER,
                did INTEGER,
                ord INTEGER,
                queue INTEGER,
                reps INTEGER,
                lapses INTEGER,
                data TEXT,
                due INTEGER,
                ivl INTEGER,
                left INTEGER
            )"""
        )
        conn.executemany(
            "INSERT INTO cards (id, nid, did, ord, queue, reps, lapses, data, due, ivl, left) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            cards_rows,
        )
        conn.commit()
        conn.close()
        return str(path)

    def test_learning_card_populates_left_and_due_at(self, tmp_path):
        """queue=1 (learning): left column read, due_at set from cards.due (unix timestamp)."""
        import datetime

        due_ts = int(datetime.datetime(2024, 1, 1, 10, 0, 0, tzinfo=datetime.UTC).timestamp())
        db_path = self._make_db(
            tmp_path,
            [(1, 1001, 12345, 0, 1, 1, 0, '{"s": 5.0, "d": 5.0}', due_ts, 0, 2002)],
        )
        conn = sqlite3.connect(db_path)
        cards = fetch_cards_for_notes(conn, [1001])
        conn.close()
        assert len(cards) == 1
        state = cards[0].fsrs_state
        assert state.left == 2002
        assert state.due_at is not None
        # due_at should match the due_ts converted to UTC datetime
        expected_due_at = datetime.datetime.fromtimestamp(due_ts, tz=datetime.UTC)
        assert state.due_at == expected_due_at

    def test_relearning_card_populates_left_and_due_at(self, tmp_path):
        """queue=3 (relearning): left column read, due_at set from cards.due (days since epoch)."""
        import datetime

        # queue=3: due is days since col_crt (1704067200 = 2024-01-01)
        # due=5 means 2024-01-06
        db_path = self._make_db(
            tmp_path,
            [(2, 1002, 12345, 0, 3, 5, 1, '{"s": 3.0, "d": 6.0}', 5, 2, 1001)],
        )
        conn = sqlite3.connect(db_path)
        cards = fetch_cards_for_notes(conn, [1002])
        conn.close()
        assert len(cards) == 1
        state = cards[0].fsrs_state
        assert state.left == 1001
        assert state.due_at is not None
        # due_at for queue=3: col_crt + due days → midnight UTC
        expected_due_at = datetime.datetime.fromtimestamp(1704067200 + 5 * 86400, tz=datetime.UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        assert state.due_at == expected_due_at

    def test_new_card_has_no_left_or_due_at(self, tmp_path):
        """queue=0 (new): left may be present but due_at is None (not a learning card)."""
        db_path = self._make_db(
            tmp_path,
            [(3, 1003, 12345, 0, 0, 0, 0, "", 0, 0, 0)],
        )
        conn = sqlite3.connect(db_path)
        cards = fetch_cards_for_notes(conn, [1003])
        conn.close()
        assert len(cards) == 1
        state = cards[0].fsrs_state
        assert state.left is None
        assert state.due_at is None

    def test_review_card_has_no_due_at(self, tmp_path):
        """queue=2 (review): left ignored, due_at is None (not sub-day learning)."""
        db_path = self._make_db(
            tmp_path,
            [(4, 1004, 12345, 0, 2, 10, 1, '{"s": 100.0, "d": 3.0}', 50, 50, 0)],
        )
        conn = sqlite3.connect(db_path)
        cards = fetch_cards_for_notes(conn, [1004])
        conn.close()
        assert len(cards) == 1
        state = cards[0].fsrs_state
        assert state.left is None
        assert state.due_at is None
