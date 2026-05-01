"""Tests for read-only Anki DB helpers."""

import json
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from app.anki.sqlite_reader import (
    extract_l2,
    extract_l2_from_fields,
    fetch_cards_for_notes,
    fetch_notes_for_deck,
    find_deck_id,
    list_media_refs,
    parse_fsrs_data,
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
