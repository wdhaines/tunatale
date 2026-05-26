"""Tests for read-only Anki DB helpers."""

import json
import sqlite3
from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.anki.sqlite_reader import (
    compute_due_at,
    extract_l2,
    extract_l2_from_fields,
    fetch_cards_for_notes,
    fetch_notes_for_deck,
    find_deck_id,
    list_media_refs,
    parse_fsrs_data,
)
from app.models.srs_item import Direction, SRSState


class TestComputeDueAt:
    """Tests for compute_due_at overflow handling."""

    def test_queue_2_normal_days(self):
        """queue 2: due_raw is days since col_crt → midnight + 4h UTC."""
        col_crt = 1388836800  # 2014-01-04
        result = compute_due_at(queue=2, due_raw=10, col_crt=col_crt)
        expected = datetime(2014, 1, 14, 4, 0, tzinfo=UTC)
        assert result == expected

    def test_queue_3_normal_days(self):
        """queue 3: due_raw is days since col_crt."""
        col_crt = 1388836800
        result = compute_due_at(queue=3, due_raw=5, col_crt=col_crt)
        expected = datetime(2014, 1, 9, 4, 0, tzinfo=UTC)
        assert result == expected

    def test_overflow_large_due_raw(self):
        """Large due_raw values (Unix timestamps) are detected by heuristic."""
        col_crt = 1388836800
        result = compute_due_at(queue=2, due_raw=1777999998, col_crt=col_crt)
        expected = datetime.fromtimestamp(1777999998, tz=UTC)
        assert result == expected

    def test_queue_2_due_raw_as_days(self):
        """Normal queue=2: due_raw is days since col_crt."""
        col_crt = 1388836800  # 2014-01-04
        result = compute_due_at(queue=2, due_raw=4503, col_crt=col_crt)
        expected = datetime(2026, 5, 4, 4, 0, tzinfo=UTC)
        assert result == expected

    def test_queue_1_unix_timestamp(self):
        """queue 1: due_raw is absolute Unix timestamp."""
        result = compute_due_at(queue=1, due_raw=1777999998, col_crt=0)
        expected = datetime.fromtimestamp(1777999998, tz=UTC)
        assert result == expected

    def test_queue_0_fallback_to_today(self):
        """queue 0: fall back to today at 04:00 UTC."""
        result = compute_due_at(queue=0, due_raw=999, col_crt=0)
        expected = datetime.combine(date.today(), time(4, 0), tzinfo=UTC)
        assert result == expected

    def test_queue_minus_1_suspended(self):
        """queue -1 with no card_type (defaults to 0/new): today at 04:00 UTC."""
        result = compute_due_at(queue=-1, due_raw=0, col_crt=0)
        expected = datetime.combine(date.today(), time(4, 0), tzinfo=UTC)
        assert result == expected

    def test_queue_minus_2_buried_review_preserves_due(self):
        """Layer 44: sibling-buried review card (queue=-2, card_type=2).

        Anki preserves cards.due (days-since-crt) when a card is buried; only
        cards.queue flips. Without dispatching on card_type, compute_due_at
        discards due_raw and returns "today at 04:00", which silently leaks
        into TT after the daily unbury sweep flips state back to review.
        """
        col_crt = 1388836800  # 2014-01-04 UTC
        result = compute_due_at(queue=-2, due_raw=4522, col_crt=col_crt, card_type=2)
        expected = datetime(2026, 5, 23, 4, 0, tzinfo=UTC)
        assert result == expected

    def test_queue_minus_3_buried_review_preserves_due(self):
        """Sched-buried review card (queue=-3, card_type=2) preserves due."""
        col_crt = 1388836800
        result = compute_due_at(queue=-3, due_raw=4522, col_crt=col_crt, card_type=2)
        expected = datetime(2026, 5, 23, 4, 0, tzinfo=UTC)
        assert result == expected

    def test_queue_minus_1_suspended_review_preserves_due(self):
        """Suspended review card (queue=-1, card_type=2) preserves due."""
        col_crt = 1388836800
        result = compute_due_at(queue=-1, due_raw=4522, col_crt=col_crt, card_type=2)
        expected = datetime(2026, 5, 23, 4, 0, tzinfo=UTC)
        assert result == expected

    def test_queue_minus_2_buried_learning_preserves_due(self):
        """Buried learning card (queue=-2, card_type=1): due_raw is a unix timestamp."""
        due_raw = 1777999998
        result = compute_due_at(queue=-2, due_raw=due_raw, col_crt=0, card_type=1)
        expected = datetime.fromtimestamp(due_raw, tz=UTC)
        assert result == expected

    def test_queue_minus_2_buried_relearning_preserves_due(self):
        """Buried day-relearning card (queue=-2, card_type=3): days-since-crt."""
        col_crt = 1388836800
        result = compute_due_at(queue=-2, due_raw=4522, col_crt=col_crt, card_type=3)
        expected = datetime(2026, 5, 23, 4, 0, tzinfo=UTC)
        assert result == expected

    def test_queue_minus_2_buried_new_falls_back(self):
        """Buried new card (queue=-2, card_type=0): due_raw is a position, fall back."""
        result = compute_due_at(queue=-2, due_raw=999, col_crt=0, card_type=0)
        expected = datetime.combine(date.today(), time(4, 0), tzinfo=UTC)
        assert result == expected


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

    def test_data_lrt_drives_last_review(self):
        """cards.data.lrt is Anki's authoritative FSRS-scheduler-effective
        last-review timestamp. It's used by Anki's `extract_fsrs_retrievability`
        SQL function (rslib/src/storage/sqlite.rs:334) to compute R. TT must
        mirror this rather than using MAX(revlog.id) per card — for cards graded
        multiple times in a session (lapse + relearning step + graduate), `lrt`
        sticks to the last FSRS-touched grade while MAX(revlog.id) advances on
        every learning step. Using MAX(revlog.id) produces a shorter elapsed
        time → higher R → wrong R-asc position.
        """
        from datetime import UTC, datetime

        # lrt is stored in seconds in cards.data
        lrt_seconds = 1778446601  # 2026-05-10 20:56:41 UTC
        state = parse_fsrs_data(
            card_id=99,
            ord=0,
            data_str=json.dumps({"s": 0.04, "d": 5.0, "lrt": lrt_seconds}),
            queue=2,
            reps=5,
            lapses=0,
            col_crt=1388836800,
            due_raw=4510,
            ivl=1,
        )
        assert state.last_review == datetime.fromtimestamp(lrt_seconds, tz=UTC), (
            f"cards.data.lrt must drive last_review; got {state.last_review}"
        )

    def test_missing_lrt_falls_back_to_day_level_last_review(self):
        """When cards.data has no `lrt` field (older Anki versions or pre-FSRS
        cards), preserve the existing day-level `_compute_last_review` value
        derived from due-raw / ivl.
        """
        state = parse_fsrs_data(
            card_id=99,
            ord=0,
            data_str=json.dumps({"s": 5.0, "d": 5.0}),  # no lrt
            queue=2,
            reps=5,
            lapses=0,
            col_crt=1704067200,
            due_raw=10,
            ivl=2,
        )
        # day-level: midnight UTC of (col_crt + due - ivl) days
        from datetime import UTC, datetime, time

        expected = datetime.combine(
            datetime.fromtimestamp(1704067200, tz=UTC).date() + timedelta(days=8),
            time.min,
            tzinfo=UTC,
        )
        assert state.last_review == expected

    def test_queue_2_card_due_at_uses_col_crt(self):
        """queue=2 (review): due_at = datetime based on col_crt + due_raw days + 4h UTC."""
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
        expected = datetime(2024, 1, 11, 4, 0, tzinfo=UTC)
        assert state.due_at == expected

    def test_queue_1_card_due_at_uses_epoch_seconds(self):
        """queue=1 (learning): due_at = datetime.fromtimestamp(due_raw, tz=UTC)."""
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
        expected = datetime.fromtimestamp(due_raw, tz=UTC)
        assert state.due_at == expected

    def test_new_card_falls_back_to_today(self):
        """queue=0 (new card): due_raw is a position, not a date — fall back to today at 04:00 UTC."""
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
        assert state.due_at == datetime.combine(date.today(), time(4, 0), tzinfo=UTC)
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
        """queue=2 (review): last_review = date from (due - ivl) via col_crt
        (Layer 45: preserves col_crt time-of-day, fixing off-by-one)."""
        col_crt = 1388836800  # 2014-01-04 12:00:00 UTC
        # review_col_day=4498 → 1st midnight in col_day 4498 = 2026-04-30
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
        assert state.last_review == datetime.combine(date(2026, 4, 30), time.min, tzinfo=UTC)

    def test_parse_fsrs_data_sets_last_review_for_relearning_card(self):
        """queue=3 (day-relearning): last_review computed same as queue=2."""
        col_crt = 1388836800
        # review_col_day=490 → 1st midnight in col_day 490 = 2015-05-10
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
        assert state.last_review == datetime.combine(date(2015, 5, 10), time.min, tzinfo=UTC)

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
        assert state.last_review == datetime.combine(date(2026, 4, 30), time.min, tzinfo=UTC)

    def test_parse_fsrs_data_queue_2_reps_0_is_review_with_no_last_review(self):
        """Layer 30 mirror: (queue=2, reps=0) is REVIEW, not NEW.

        Anki's "Forget Card" or manual reschedule puts cards on the review
        queue with cleared FSRS data (reps=0). _queue_to_state in sync.py
        already handles this; parse_fsrs_data must mirror it.
        """
        col_crt = 1388836800
        state = parse_fsrs_data(
            card_id=6,
            ord=0,
            data_str="",
            queue=2,
            reps=0,
            lapses=0,
            col_crt=col_crt,
            due_raw=4501,
            ivl=3,
        )
        assert state.state == SRSState.REVIEW
        assert state.last_review is None

    def test_parse_fsrs_data_queue_2_reps_0_no_data_maps_to_review(self):
        """Layer 30 mirror: (queue=2, reps=0, data='{}') is REVIEW, not NEW.

        Signature of Anki's "Forget Card" or manual reschedule — card graduated
        to review queue but FSRS data cleared. _queue_to_state in sync.py already
        handles this; parse_fsrs_data must mirror it or import_seed clobbers
        sync_pull's correct state.
        """
        ds = parse_fsrs_data(
            card_id=1,
            ord=0,
            data_str="{}",
            queue=2,
            reps=0,
            lapses=0,
            col_crt=0,
            due_raw=4514,
            ivl=1,
            left=0,
            card_type=2,
        )
        assert ds.state == SRSState.REVIEW
        assert ds.last_review is None
        assert ds.stability == 1.0
        assert ds.difficulty == 5.0

    def test_parse_fsrs_data_fallback_review_for_unknown_queue(self):
        """Unknown queue values with reps>0 fall back to REVIEW."""
        state = parse_fsrs_data(
            card_id=7,
            ord=0,
            data_str="",
            queue=99,
            reps=5,
            lapses=0,
        )
        assert state.state == SRSState.REVIEW

    def test_parse_fsrs_data_last_review_col_day_matches_anki(self):
        """Layer 45 end-to-end: parse_fsrs_data→_elapsed_days_for_fsrs matches
        Anki's today_col_day - (due - ivl) for the user's real col_crt.

        Regression: _compute_last_review was stripping col_crt's time-of-day,
        producing midnight in the *previous* col_day.  This test pins the full
        pipeline so the off-by-one cannot return.
        """
        from app.anki.protobuf_wire import compute_anki_day_index
        from app.srs.fsrs import _elapsed_days_for_fsrs

        col_crt = 1388836800  # user's real col_crt
        due_raw = 4518
        ivl = 38
        review_col_day = due_raw - ivl  # = 4480

        state = parse_fsrs_data(
            card_id=100,
            ord=0,
            data_str=json.dumps({"s": 10.0, "d": 4.0}),
            queue=2,
            reps=5,
            lapses=0,
            col_crt=col_crt,
            due_raw=due_raw,
            ivl=ivl,
        )

        # Verify: parse_fsrs_data maps to the correct col_day
        lr_col_day = compute_anki_day_index(col_crt, 4, state.last_review)
        assert lr_col_day == review_col_day, (
            f"parse_fsrs_data last_review col_day={lr_col_day}, expected {review_col_day}; "
            f"off-by-one means _compute_last_review still strips col_crt time-of-day"
        )

        # Verify: _elapsed_days_for_fsrs produces correct elapsed against live now
        ref_now = datetime.now(tz=UTC)
        tt_elapsed = _elapsed_days_for_fsrs(state.last_review, ref_now, col_crt=col_crt)

        today_col_day = compute_anki_day_index(col_crt, 4, ref_now)
        expected_elapsed = today_col_day - review_col_day

        assert tt_elapsed == expected_elapsed, (
            f"_elapsed_days_for_fsrs={tt_elapsed} != {expected_elapsed} "
            f"(today_col_day - review_col_day); pipeline broken"
        )


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

    def test_qa_front_with_interrogative_and_question_mark_wins(self):
        """When Field 0 is an English Q&A prompt (e.g. 'What sound is v word-initial...?'),
        return it as the L2 text rather than letting an IPA-laden answer outscore it.

        Reproduces the 11 reversed phonology Q&A notes (cid 790, 791, 792, 793, 794,
        795, 796, 798, 799, 800, 801) where the back had enough IPA chars (ˈ, ː, ɛ, etc.)
        to outweigh the front's English stopword penalty.
        """
        fields = [
            "What sound is <b>v</b> word-initial before a voiced consonant or sonorant?",
            "[sound:sl_vrata.mp3][w] — voiced bilabial, like English <i>w</i>"
            "<br><br><i>vrata</i> → [ˈwɾaːta] — door<br><i>vlak</i> → [wlak] — train",
        ]
        assert extract_l2_from_fields(fields).startswith("What sound is")

    def test_qa_how_question_with_diacritic_back_still_returns_question(self):
        fields = [
            "How is syllabic <b>r</b> pronounced in <i>trg</i> (town square)?",
            "[sound:sl_trg.mp3][tərg] — r acts as the syllable nucleus with a schwa-like quality",
        ]
        assert extract_l2_from_fields(fields).startswith("How is")

    def test_field0_ending_with_question_but_no_interrogative_falls_through_to_scoring(self):
        """A non-question first field that happens to end with '?' (no interrogative
        opener) should still go through the normal Slovene-char scoring path."""
        fields = ["banka?", "<div>bank</div>"]
        # No interrogative, falls through. Both clean strip to short text;
        # neither has Slovene chars, so the earlier field wins by tie-break.
        assert extract_l2_from_fields(fields) == "banka?"

    def test_phonics_qa_prompt_returns_question_not_answer(self):
        """Phonics Q&A notes use Field 0 for an English question and Field 1 for the
        Slovene/IPA answer. The question IS the L2-side prompt for the card; review
        UIs (TT + Anki) show it on the front. Earlier heuristic preferred the IPA
        back because of its higher Slovene/IPA character density; the new rule
        recognises English Q&A patterns (interrogative + '?') and keeps them on the
        front. The earlier behavior caused 11 phonology cards to display with the
        answer on the prompt side in TT — see cleanup_function_word_notes /
        link_tt_images history."""
        fields = [
            "What phoneme does unstressed <b>e before</b> the stressed syllable represent?",
            "[sound:sl_beseda.mp3]/ɛ/ = [ɛ] — open-mid front<br><br><i>besêda</i> [bɛˈseːda]",
        ]
        result = extract_l2_from_fields(fields)
        assert result.startswith("What phoneme"), f"Q&A front should win on the new heuristic; got: {result!r}"

    def test_ipa_chars_boost_score_when_no_qa_pattern(self):
        """Below the Q&A path: when Field 0 isn't a question, IPA chars in Field 1
        still bump its score so it wins over a stopword-heavy Field 0."""
        fields = [
            "Practice the phoneme with the audio",  # no '?', no interrogative
            "[ɛ] besêda [bɛˈseːda]",  # several IPA chars
        ]
        result = extract_l2_from_fields(fields)
        assert "besêda" in result

    def test_dictionary_stress_diacritic_counts_as_slovene(self):
        # Slovene dictionaries mark stress with diacritics (besêda, oblákov)
        # that aren't in the basic čšž set. They should still score positively
        # so the L2 field wins over an English gloss.
        fields = ["before the stressed syllable", "besêda"]
        assert extract_l2_from_fields(fields) == "besêda"

    def test_b_then_i_pattern_returns_b_content(self):
        """Pronunciation/Basic notetype Front field: `<b>SLOVENE</b><br><i>ENGLISH</i>`.
        The regex-strip fallback would concatenate ('ničnothing'); this test pins
        the new behavior that extracts the `<b>...</b>` group as L2.
        """
        fields = ["<b>nič</b><br><i>nothing</i>", "[sound:sl_nic.mp3][nətʃ]"]
        assert extract_l2_from_fields(fields) == "nič"

    def test_b_then_i_pattern_with_whitespace(self):
        """Tolerate whitespace and minor variation between the <b> and <i> tags."""
        fields = ["<b>ulica</b><br/>  <i>street</i>", "[sound:sl_ulica.mp3]"]
        assert extract_l2_from_fields(fields) == "ulica"


class TestExtractGlossFromFields:
    """Layer 31: extract the English gloss from a `<b>L2</b><br><i>EN</i>` field."""

    def test_returns_gloss_for_b_then_i_pattern(self):
        from app.anki.sqlite_reader import extract_gloss_from_fields

        fields = ["<b>nič</b><br><i>nothing</i>", "[sound:sl_nic.mp3]"]
        assert extract_gloss_from_fields(fields) == "nothing"

    def test_returns_gloss_with_whitespace_and_self_closing_br(self):
        from app.anki.sqlite_reader import extract_gloss_from_fields

        fields = ["<b>ulica</b><br/> <i>street</i>", "[sound:sl_ulica.mp3]"]
        assert extract_gloss_from_fields(fields) == "street"

    def test_returns_none_when_no_pattern_match(self):
        from app.anki.sqlite_reader import extract_gloss_from_fields

        fields = ["banka", "bank"]
        assert extract_gloss_from_fields(fields) is None

    def test_returns_none_for_phonics_question_field(self):
        from app.anki.sqlite_reader import extract_gloss_from_fields

        fields = ["What sound is <b>v</b> word-initial?", "[wː]"]
        assert extract_gloss_from_fields(fields) is None


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

    def test_extracts_img_ref_when_alt_text_contains_gt(self):
        """Regression: when an <img> tag's alt text contains literal `>` characters
        (common when image alt is copied from a webpage breadcrumb), the regex
        `<img[^>]+src="..."` terminates at the first `>` inside the alt text and
        misses the actual src.

        Discovered when user replaced vojak's image in Anki with a paste-hash JPG
        whose alt text contained "Army Guard > National Guard >  State Partnership".
        TT's sync_pull → refresh_media never extracted the new filename, so the
        old img_soldier.png stayed in TT's media table indefinitely.
        """
        field = (
            "<img alt=\"New York Sergeant is Army Guard's Best Warrior Soldier > "
            'National Guard >  State Partnership Program News" '
            'src="paste-259d6e7de97422167ab9a82060efc15be3c5bd63.jpg">'
        )
        assert list_media_refs([field]) == ["paste-259d6e7de97422167ab9a82060efc15be3c5bd63.jpg"]

    def test_extracts_img_ref_when_src_appears_before_alt(self):
        """Defensive: src= can appear in any order relative to other attrs."""
        field = '<img src="first.jpg" alt="A > B">'
        assert list_media_refs([field]) == ["first.jpg"]

    def test_skips_data_uri_src(self):
        """Inline ``data:`` URIs are not file references — never pass them downstream.

        Discovered when sync_pull → refresh_media_for_deck crashed with
        ``OSError [Errno 63] File name too long`` because a note pasted a
        base64-encoded JPEG inline (``<img src="data:image/jpeg;base64,...">``)
        and the downstream ``(anki_media_path / filename).exists()`` call tried
        to stat a ~98KB path. Per RFC 2397, ``data:`` URIs embed the resource
        directly; there is no corresponding file in ``collection.media/``.
        """
        field = '<img src="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wCEAAk=">'
        assert list_media_refs([field]) == []

    def test_skips_data_uri_keeps_real_refs_in_same_field(self):
        """A field can mix data: URIs with real refs; only the data: URI is dropped."""
        field = '<img src="data:image/png;base64,iVBORw0KGgoAAA=="> <img src="real.jpg"> [sound:a.mp3]'
        assert set(list_media_refs([field])) == {"real.jpg", "a.mp3"}


class TestExtractInlineImages:
    """``extract_inline_images`` decodes base64 image data: URIs.

    The kratek incident (2026-05-21) needed the inverse of ``list_media_refs``'s
    data-URI skip — three notes in the user's deck stored their picture as an
    inline base64 payload instead of a saved file, and refresh-media had no way
    to materialize them otherwise.
    """

    def test_decodes_base64_jpeg_with_jpg_extension(self):
        from app.anki.sqlite_reader import extract_inline_images

        # Smallest possible valid base64 ("AAAA" → 3 bytes), arbitrary content.
        field = '<img src="data:image/jpeg;base64,AAAA">'
        out = extract_inline_images([field])
        assert len(out) == 1
        assert out[0].ext == "jpg"
        assert out[0].data == b"\x00\x00\x00"

    def test_normalizes_svg_xml_to_svg(self):
        from app.anki.sqlite_reader import extract_inline_images

        field = '<img src="data:image/svg+xml;base64,PHN2Zy8+">'
        out = extract_inline_images([field])
        assert len(out) == 1
        assert out[0].ext == "svg"
        assert out[0].data == b"<svg/>"

    def test_passes_through_unknown_subtype(self):
        """E.g., ``webp`` keeps its own extension; only jpeg/svg+xml need rewriting."""
        from app.anki.sqlite_reader import extract_inline_images

        field = '<img src="data:image/webp;base64,AAAA">'
        out = extract_inline_images([field])
        assert out[0].ext == "webp"

    def test_skips_url_encoded_data_uri(self):
        """Non-base64 data URIs are not supported — skip rather than misdecode."""
        from app.anki.sqlite_reader import extract_inline_images

        field = '<img src="data:image/svg+xml,%3Csvg%2F%3E">'
        assert extract_inline_images([field]) == []

    def test_skips_non_image_data_uri(self):
        from app.anki.sqlite_reader import extract_inline_images

        field = '<img src="data:text/plain;base64,aGk=">'
        assert extract_inline_images([field]) == []

    def test_skips_file_based_src(self):
        from app.anki.sqlite_reader import extract_inline_images

        assert extract_inline_images(['<img src="banka.jpg">']) == []

    def test_skips_invalid_base64(self):
        """Invalid base64 (e.g. illegal chars) is dropped, not crashed on."""
        from app.anki.sqlite_reader import extract_inline_images

        field = '<img src="data:image/jpeg;base64,not!valid!base64!@#$">'
        assert extract_inline_images([field]) == []

    def test_extracts_multiple_inline_images(self):
        from app.anki.sqlite_reader import extract_inline_images

        fields = [
            '<img src="data:image/png;base64,iVBORw0KGgo=">',
            '<img src="data:image/jpeg;base64,/9j/4AA=">',
        ]
        out = extract_inline_images(fields)
        assert {img.ext for img in out} == {"png", "jpg"}
        assert len(out) == 2

    def test_returns_empty_when_no_data_uris(self):
        from app.anki.sqlite_reader import extract_inline_images

        assert extract_inline_images(["no media here", "[sound:a.mp3]"]) == []


class TestLeftAndDueAtFromCards:
    """Tests that left column and due_at are correctly read from Anki cards table."""

    def _make_db(self, tmp_path, cards_rows):
        """Create a minimal Anki DB with given cards rows.

        cards_rows: list of (id, nid, did, ord, queue, reps, lapses, data, due, ivl, left, type)
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
                left INTEGER,
                type INTEGER,
                mod INTEGER DEFAULT 0
            )"""
        )
        conn.executemany(
            "INSERT INTO cards (id, nid, did, ord, queue, reps, lapses, data, due, ivl, left, type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            [(1, 1001, 12345, 0, 1, 1, 0, '{"s": 5.0, "d": 5.0}', due_ts, 0, 2002, 0)],
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
            [(2, 1002, 12345, 0, 3, 5, 1, '{"s": 3.0, "d": 6.0}', 5, 2, 1001, 0)],
        )
        conn = sqlite3.connect(db_path)
        cards = fetch_cards_for_notes(conn, [1002])
        conn.close()
        assert len(cards) == 1
        state = cards[0].fsrs_state
        assert state.left == 1001
        assert state.due_at is not None
        # due_at for queue=3: col_crt + due days → 4am UTC
        expected_due_at = datetime.datetime.fromtimestamp(1704067200 + 5 * 86400, tz=datetime.UTC).replace(
            hour=4, minute=0, second=0, microsecond=0
        )
        assert state.due_at == expected_due_at

    def test_new_card_has_no_left_and_today_due_at(self, tmp_path):
        """queue=0 (new): left is None, due_at falls back to today at 4am UTC."""
        db_path = self._make_db(
            tmp_path,
            [(3, 1003, 12345, 0, 0, 0, 0, "", 0, 0, 0, 0)],
        )
        conn = sqlite3.connect(db_path)
        cards = fetch_cards_for_notes(conn, [1003])
        conn.close()
        assert len(cards) == 1
        state = cards[0].fsrs_state
        assert state.left is None
        assert state.due_at == datetime.combine(date.today(), time(4, 0), tzinfo=UTC)

    def test_review_card_due_at_uses_col_crt(self, tmp_path):
        """queue=2 (review): left is None, due_at = col_crt + due days at 4am UTC."""
        db_path = self._make_db(
            tmp_path,
            [(4, 1004, 12345, 0, 2, 10, 1, '{"s": 100.0, "d": 3.0}', 50, 50, 0, 0)],
        )
        conn = sqlite3.connect(db_path)
        cards = fetch_cards_for_notes(conn, [1004])
        conn.close()
        assert len(cards) == 1
        state = cards[0].fsrs_state
        assert state.left is None
        expected = datetime.fromtimestamp(1704067200 + 50 * 86400, tz=UTC).replace(
            hour=4, minute=0, second=0, microsecond=0
        )
        assert state.due_at == expected

    def test_buried_review_card_preserves_due_at_through_fetch(self, tmp_path):
        """Layer 44 end-to-end: queue=-2 with card_type=2 (sibling-buried review)
        must surface the underlying days-since-crt due, not today's date.

        Reproduces the May 2026 incident where 22 cards were sibling-buried in
        Anki, sync_pull mapped them to state='buried' with due_at = sync-day,
        then the daily unbury sweep released them as state='review' with the
        stale "today" due_at. They then inflated TT's review-due badge.
        """
        # type=2 means review-card type (the "underlying" state before bury).
        # queue=-2 (sibling-buried), due=50 days since col_crt=2024-01-01.
        db_path = self._make_db(
            tmp_path,
            [(5, 1005, 12345, 0, -2, 8, 1, '{"s": 50.0, "d": 4.0}', 50, 10, 0, 2)],
        )
        conn = sqlite3.connect(db_path)
        cards = fetch_cards_for_notes(conn, [1005])
        conn.close()
        assert len(cards) == 1
        state = cards[0].fsrs_state
        assert state.state == SRSState.BURIED
        expected = datetime.fromtimestamp(1704067200 + 50 * 86400, tz=UTC).replace(
            hour=4, minute=0, second=0, microsecond=0
        )
        assert state.due_at == expected, (
            f"buried review card lost underlying due: got {state.due_at}, expected {expected}"
        )
