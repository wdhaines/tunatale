"""Tests for S3.4: sync pull (Anki → TunaTale)."""

from __future__ import annotations

import inspect
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from datetime import datetime as _dt
from datetime import time as _time

import pytest

from app.anki.sync import (
    AnkiSync,
    NoteRecord,
    OfflineReader,
    _direction_differs,
    extract_cloze_note,
    extract_cloze_sentence_translation,
    extract_cloze_translation,
)
from app.common.guid import compute_guid
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from tests.conftest import make_card_record, make_note_record


class FakeWriter:
    """Minimal writer stub for tests that only need AnkiSync construction."""

    def update_note_fields(self, note_id: int, fields: dict[str, str]) -> None:
        pass

    def suspend(self, card_ids: list[int]) -> None:
        pass

    def unsuspend(self, card_ids: list[int]) -> None:
        pass

    def set_due_date(self, card_ids: list[int], days: str) -> None:
        pass

    def write_revlog(
        self,
        *,
        cid: int,
        ease: int,
        ivl: int,
        last_ivl: int,
        factor: int,
        time_ms: int,
        type_: int,
        preferred_id=None,
        is_lapse: bool = False,
        ds_reps: int | None = None,
        ds_lapses: int | None = None,
    ) -> None:
        pass


# ── Shared helpers ────────────────────────────────────────────────────────────


def _make_tt_db() -> SRSDatabase:
    return SRSDatabase(":memory:")


def _add_banka(db: SRSDatabase) -> str:
    """Insert banka/bank; return its computed GUID."""
    unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus")
    db.add_collocation(unit)
    item = db.get_collocation("banka")
    assert item is not None
    return item.guid  # type: ignore[return-value]


def _add_cloze_collocation(db: SRSDatabase, text: str = "vsak", sentence: str = "Odprto je vsak dan") -> str:
    """Insert a cloze collocation; return its computed GUID."""
    unit = SyntacticUnit(
        text=text,
        translation="",
        word_count=1,
        difficulty=1,
        source="cloze",
        lemma=text,
        source_sentence=sentence,
        card_type="cloze",
    )
    db.add_collocation(unit)
    item = db.get_collocation(text)
    assert item is not None
    return item.guid  # type: ignore[return-value]


class FakeReader:
    def __init__(self, records: list[NoteRecord]):
        self._records = records

    def get_note_records(self) -> list[NoteRecord]:
        return self._records

    def get_revlog_for_card(self, card_id: int, after_ms: int = 0) -> list:
        return []


# ── Cloze helper functions ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "label,back_extra,expected",
    [
        ("standard", '<i>every</i><br><br><a href="https://forvo.com/word/vsak/">▶ Forvo</a>', "every"),
        ("with_sentence", '<i>every</i><br><br><span class="st">It is open every day</span>', "every"),
        ("no_i_tag__fallback", "plain text", "plain text"),
        ("empty", "", ""),
        ("with_sound_tag__trailing_sound_stripped", "<i>x</i><br><br>extra<br><br>[sound:y.mp3]", "x"),
        # A morphology cloze (e.g. biti) has no <i> word translation, only a
        # grammar span. The bare-text fallback must NOT strip the span and
        # return its text — that leaks "biti, 3rd person singular" into the
        # translation column on every sync_pull.
        ("grammar_only__no_leak", '<span class="grammar">biti, 3rd person singular</span>', ""),
        (
            "grammar_only_with_sound__no_leak",
            '<span class="grammar">biti, 3rd person singular</span><br><br>[sound:y.mp3]',
            "",
        ),
        ("st_span_only__no_leak", '<span class="st">It is open every day</span>', ""),
    ],
)
def test_extract_cloze_translation(label, back_extra, expected):
    assert extract_cloze_translation(back_extra) == expected


@pytest.mark.parametrize(
    "label,back_extra,expected",
    [
        ("standard", '<i>every</i><br><br><span class="st">It is open every day</span>', "It is open every day"),
        ("no_span", '<i>every</i><br><br><a href="https://forvo.com/word/vsak/">▶ Forvo</a>', ""),
        ("empty", "", ""),
        ("with_sound_tag", '<i>x</i><br><br><span class="st">y</span><br><br>[sound:z.mp3]', "y"),
    ],
)
def test_extract_cloze_sentence_translation(label, back_extra, expected):
    assert extract_cloze_sentence_translation(back_extra) == expected


@pytest.mark.parametrize(
    "label,back_extra,expected",
    [
        (
            "returns_body",
            '<i>every</i><br><br><a href="https://forvo.com/word/vsak/">▶ Forvo</a>',
            '<a href="https://forvo.com/word/vsak/">▶ Forvo</a>',
        ),
        (
            "after_sentence",
            '<i>every</i><br><br><span class="st">It is open every day</span><br><br>some notes',
            "some notes",
        ),
        ("no_i_tag", "plain text", ""),
        ("empty", "", ""),
        ("no_body", "<i>every</i><br><br>", ""),
        ("with_sound_tag", "<i>x</i><br><br>note body<br><br>[sound:z.mp3]", "note body"),
        ("only_sound_tag__note_empty", '<i>x</i><br><br><span class="st">y</span><br><br>[sound:z.mp3]', ""),
    ],
)
def test_extract_cloze_note(label, back_extra, expected):
    assert extract_cloze_note(back_extra) == expected


# ── OfflineReader ─────────────────────────────────────────────────────────────


class TestOfflineReader:
    def test_returns_five_records(self, fake_anki_db):
        conn = sqlite3.connect(str(fake_anki_db))
        records = OfflineReader(conn, "0. Slovene").get_note_records()
        conn.close()
        assert len(records) == 5

    def test_extracts_l2_text_and_translation(self, fake_anki_db):
        conn = sqlite3.connect(str(fake_anki_db))
        records = OfflineReader(conn, "0. Slovene").get_note_records()
        conn.close()
        texts = {r.l2_text for r in records}
        assert "banka" in texts
        assert "hiša" in texts  # stripped from <span class="slovene">

        banka = next(r for r in records if r.l2_text == "banka")
        assert banka.translation == "bank"

    def test_each_note_has_two_cards(self, fake_anki_db):
        conn = sqlite3.connect(str(fake_anki_db))
        records = OfflineReader(conn, "0. Slovene").get_note_records()
        conn.close()
        assert all(len(r.cards) == 2 for r in records)

    def test_suspended_card_queue_minus_one(self, fake_anki_db):
        """Note 1003 (miza) has production card suspended (queue=-1)."""
        conn = sqlite3.connect(str(fake_anki_db))
        records = OfflineReader(conn, "0. Slovene").get_note_records()
        conn.close()
        miza = next(r for r in records if r.l2_text == "miza")
        prod = next(c for c in miza.cards if c.ord == 1)
        assert prod.queue == -1

    def test_unknown_deck_returns_empty(self, fake_anki_db):
        conn = sqlite3.connect(str(fake_anki_db))
        records = OfflineReader(conn, "No Such Deck").get_note_records()
        conn.close()
        assert records == []

    def test_card_record_carries_first_and_last_revlog_ms(self, fake_anki_db):
        """OfflineReader must expose MIN(revlog.id) (`first_review_ms`) and
        MAX(revlog.id) (`last_review_ms`) for each card with revlog history,
        feeding sync_pull's prior_state self-heal (Fix 4b)."""
        conn = sqlite3.connect(str(fake_anki_db))
        # Pick the first card of the first note; seed revlog rows.
        notes_row = conn.execute("SELECT id FROM notes LIMIT 1").fetchone()
        card_row = conn.execute("SELECT id FROM cards WHERE nid=? LIMIT 1", (notes_row[0],)).fetchone()
        cid = card_row[0]
        # Two revlog rows; the smaller id should land in first_review_ms.
        conn.execute(
            "INSERT INTO revlog VALUES (?, ?, 0, 3, 1, 1, 2500, 1200, 0)",
            (1_700_000_000_000, cid),
        )
        conn.execute(
            "INSERT INTO revlog VALUES (?, ?, 0, 3, 10, 1, 2500, 1200, 1)",
            (1_700_000_500_000, cid),
        )
        conn.commit()

        records = OfflineReader(conn, "0. Slovene").get_note_records()
        conn.close()
        target_card = next(c for r in records for c in r.cards if c.anki_card_id == cid)
        assert target_card.first_review_ms == 1_700_000_000_000
        assert target_card.last_review_ms == 1_700_000_500_000

    def test_card_record_fsrs_known_false_for_lrt_only_data(self, fake_anki_db):
        """A card whose Anki `data` has `lrt` but no `s`/`d` must be marked
        fsrs_known=False, so sync_pull preserves TT's real stability instead of
        clobbering it to the parse_fsrs_data fallback default (the 'stuck at 1.0'
        bug — e.g. upogniti's recognition card, data='{"lrt":...}').
        """
        conn = sqlite3.connect(str(fake_anki_db))
        cid = conn.execute("SELECT id FROM cards LIMIT 1").fetchone()[0]
        conn.execute("UPDATE cards SET data=? WHERE id=?", (json.dumps({"lrt": 1779293316}), cid))
        conn.commit()
        records = OfflineReader(conn, "0. Slovene").get_note_records()
        conn.close()
        target = next(c for r in records for c in r.cards if c.anki_card_id == cid)
        assert target.fsrs_known is False

    def test_card_record_fsrs_known_true_for_real_fsrs_data(self, fake_anki_db):
        """A card with real `s`/`d` in Anki data stays fsrs_known=True (Anki wins)."""
        conn = sqlite3.connect(str(fake_anki_db))
        cid = conn.execute("SELECT id FROM cards LIMIT 1").fetchone()[0]
        conn.execute("UPDATE cards SET data=? WHERE id=?", (json.dumps({"s": 7.5, "d": 5.2, "lrt": 1779293316}), cid))
        conn.commit()
        records = OfflineReader(conn, "0. Slovene").get_note_records()
        conn.close()
        target = next(c for r in records for c in r.cards if c.anki_card_id == cid)
        assert target.fsrs_known is True

    def test_last_review_falls_back_to_revlog_for_learning_card(self, fake_anki_db):
        """A relearning card (queue=1) has no day-level FSRS last_review and, when its
        Anki `data` has no `lrt` (the biti-cloze cohort, `data={}`), would be left
        last_review=NULL. It must instead fall back to its latest revlog timestamp so a
        just-graded lapse isn't left without a review time."""
        conn = sqlite3.connect(str(fake_anki_db))
        cid = conn.execute("SELECT id FROM cards LIMIT 1").fetchone()[0]
        conn.execute("UPDATE cards SET queue=1, type=3, data='{}' WHERE id=?", (cid,))
        conn.execute("INSERT INTO revlog VALUES (?, ?, 0, 1, -600, 1, 0, 1200, 1)", (1_700_000_500_000, cid))
        conn.commit()
        records = OfflineReader(conn, "0. Slovene").get_note_records()
        conn.close()
        target = next(c for r in records for c in r.cards if c.anki_card_id == cid)
        assert target.last_review == datetime.fromtimestamp(1_700_000_500_000 / 1000, tz=UTC)

    def test_last_review_prefers_fsrs_lrt_over_revlog(self, fake_anki_db):
        """When Anki's `data` carries `lrt`, that wins — the revlog fallback only
        fills in when the FSRS last-review is genuinely absent (the lrt-authoritative
        R-asc ordering must not be disturbed)."""
        conn = sqlite3.connect(str(fake_anki_db))
        cid = conn.execute("SELECT id FROM cards LIMIT 1").fetchone()[0]
        conn.execute("UPDATE cards SET data=? WHERE id=?", (json.dumps({"s": 7.5, "d": 5.2, "lrt": 1779293316}), cid))
        conn.execute("INSERT INTO revlog VALUES (?, ?, 0, 3, 10, 1, 2500, 1200, 1)", (1_700_000_500_000, cid))
        conn.commit()
        records = OfflineReader(conn, "0. Slovene").get_note_records()
        conn.close()
        target = next(c for r in records for c in r.cards if c.anki_card_id == cid)
        assert target.last_review == datetime.fromtimestamp(1779293316, tz=UTC)

    def test_last_review_none_for_learning_card_without_revlog(self, fake_anki_db):
        """A learning card with no FSRS last-review and no revlog → last_review stays
        None (nothing to fall back to)."""
        conn = sqlite3.connect(str(fake_anki_db))
        cid = conn.execute("SELECT id FROM cards LIMIT 1").fetchone()[0]
        conn.execute("UPDATE cards SET queue=1, type=3, data='{}' WHERE id=?", (cid,))
        conn.execute("DELETE FROM revlog WHERE cid=?", (cid,))
        conn.commit()
        records = OfflineReader(conn, "0. Slovene").get_note_records()
        conn.close()
        target = next(c for r in records for c in r.cards if c.anki_card_id == cid)
        assert target.last_review is None

    def test_note_record_fields(self, fake_anki_db):
        """NoteRecord exposes anki_note_id, anki_guid, mod."""
        conn = sqlite3.connect(str(fake_anki_db))
        records = OfflineReader(conn, "0. Slovene").get_note_records()
        conn.close()
        for rec in records:
            assert rec.anki_note_id > 0
            assert isinstance(rec.anki_guid, str)
            assert isinstance(rec.mod, int)

    def test_deck_with_no_notes_returns_empty(self, tmp_path):
        """Deck exists but has no notes → empty list (not the no-deck path)."""
        db_path = tmp_path / "empty.anki2"
        conn = sqlite3.connect(str(db_path))
        decks_json = json.dumps({"99999": {"id": 99999, "name": "Empty Deck"}})
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER,"
            " dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, decks TEXT, dconf TEXT, tags TEXT)"
        )
        conn.execute(
            "INSERT INTO col VALUES (1,0,0,0,11,0,0,0,'{}','{}',?,'{}','{}')",
            (decks_json,),
        )
        conn.execute(
            "CREATE TABLE notes (id INTEGER, guid TEXT, mid INTEGER, mod INTEGER, usn INTEGER,"
            " tags TEXT, flds TEXT, sfld TEXT, csum INTEGER, flags INTEGER, data TEXT)"
        )
        conn.execute(
            "CREATE TABLE cards (id INTEGER, nid INTEGER, did INTEGER, ord INTEGER, mod INTEGER,"
            " usn INTEGER, type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER, factor INTEGER,"
            " reps INTEGER, lapses INTEGER, left INTEGER, odue INTEGER, odid INTEGER, flags INTEGER, data TEXT)"
        )
        conn.commit()
        records = OfflineReader(conn, "Empty Deck").get_note_records()
        conn.close()
        assert records == []

    def test_cloze_note_parses_back_extra(self, tmp_path):
        """OfflineReader correctly extracts translation and note from Cloze notes."""
        deck_id = 12345
        deck_name = "0. Slovene"
        cloze_mid = 10001
        basic_mid = 10002

        db_path = tmp_path / "cloze.anki2"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(f"""
            CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER,
                dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, decks TEXT,
                dconf TEXT, tags TEXT);
            INSERT INTO col VALUES (1,0,0,0,11,0,0,0,'{{}}','{{}}','{{"{deck_id}":{{"id":{deck_id},"name":"{deck_name}"}}}}','{{}}','{{}}');
            CREATE TABLE notes (id INTEGER, guid TEXT, mid INTEGER, mod INTEGER, usn INTEGER,
                tags TEXT, flds TEXT, sfld TEXT, csum INTEGER, flags INTEGER, data TEXT);
            CREATE TABLE cards (id INTEGER, nid INTEGER, did INTEGER, ord INTEGER, mod INTEGER,
                usn INTEGER, type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER, factor INTEGER,
                reps INTEGER, lapses INTEGER, left INTEGER, odue INTEGER, odid INTEGER, flags INTEGER, data TEXT);
            CREATE TABLE revlog (id INTEGER, cid INTEGER, usn INTEGER, ease INTEGER, ivl INTEGER,
                lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER);
            CREATE TABLE notetypes (id INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, common TEXT);
            INSERT INTO notetypes VALUES ({cloze_mid}, 'Cloze', 0, 0, '{{}}');
            INSERT INTO notetypes VALUES ({basic_mid}, 'Basic', 0, 0, '{{}}');
        """)
        back_extra = '<i>every</i><br><br><a href="https://forvo.com/word/vsak/">\u25b6 Forvo</a>'
        conn.execute(
            "INSERT INTO notes VALUES (2001, 'cloze_guid', ?, 0, 0, '', ?, 'vsak', 0, 0, '')",
            (cloze_mid, f"vsak\x1f{back_extra}"),
        )
        conn.execute(
            "INSERT INTO notes VALUES (2002, 'basic_guid', ?, 0, 0, '', ?, 'banka', 0, 0, '')",
            (basic_mid, "banka\x1fbank"),
        )
        conn.execute(
            "INSERT INTO cards VALUES (3001, 2001, ?, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, '')",
            (deck_id,),
        )
        conn.execute(
            "INSERT INTO cards VALUES (3002, 2002, ?, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, '')",
            (deck_id,),
        )
        conn.commit()

        records = OfflineReader(conn, deck_name).get_note_records()
        conn.close()

        assert len(records) == 2

        cloze = next(r for r in records if r.anki_note_id == 2001)
        assert cloze.translation == "every"
        assert cloze.sentence_translation == ""
        assert cloze.note == '<a href="https://forvo.com/word/vsak/">\u25b6 Forvo</a>'
        assert cloze.l2_text == "vsak"
        assert cloze.disambig_key == ""

        basic = next(r for r in records if r.anki_note_id == 2002)
        assert basic.translation == "bank"
        assert basic.note == ""
        assert basic.l2_text == "banka"


# ── Additional tests ──────────────────────────────────────────────────────────


# ── AnkiSync constructor ──────────────────────────────────────────────────────


# ── AnkiSync.sync_pull algorithm ──────────────────────────────────────────────


class TestSyncPull:
    def test_remote_only_change_overwrites_silently(self):
        """Anki has different translation; no dirty_fields locally → silent overwrite."""
        db = _make_tt_db()
        guid = _add_banka(db)

        records = [make_note_record(anki_guid=guid, translation="bank (financial)", cards=[])]
        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        assert report.notes_updated == 1
        assert report.conflicts == []
        item = db.get_collocation("banka")
        assert item.syntactic_unit.translation == "bank (financial)"

    def test_local_dirty_field_and_remote_changed_produces_conflict(self):
        """dirty_fields contains 'translation' + Anki changed it → conflict, Anki wins."""
        db = _make_tt_db()
        guid = _add_banka(db)
        db.set_dirty_fields(guid, "translation")

        records = [make_note_record(anki_guid=guid, translation="bank (financial)", cards=[])]
        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        assert len(report.conflicts) == 1
        assert report.conflicts[0].field == "translation"
        assert report.conflicts[0].resolution == "anki_wins"
        # Anki wins: translation overwritten
        item = db.get_collocation("banka")
        assert item.syntactic_unit.translation == "bank (financial)"
        # Conflict recorded in DB
        assert len(db.list_sync_conflicts()) == 1

    def test_dirty_bit_cleared_after_conflict(self):
        """After anki_wins conflict on 'translation', dirty_fields no longer contains it."""
        db = _make_tt_db()
        guid = _add_banka(db)
        db.set_dirty_fields(guid, "translation")

        records = [make_note_record(anki_guid=guid, translation="bank (financial)", cards=[])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        assert db.get_dirty_fields(guid) == ""

    def test_suspend_recognition_leaves_production_untouched(self):
        """Anki suspends ord=0 → RECOGNITION=SUSPENDED, PRODUCTION unchanged."""
        db = _make_tt_db()
        guid = _add_banka(db)

        cards = [
            make_card_record(anki_card_id=90010, ord=0, queue=-1, reps=5, stability=10.5, difficulty=4.8),
            make_card_record(anki_card_id=90011, ord=1, queue=2, reps=3, stability=5.2, difficulty=5.1),
        ]
        records = [make_note_record(anki_guid=guid, cards=cards)]
        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        assert report.directions_updated == 2
        item = db.get_collocation_by_guid(guid)
        assert item.directions[Direction.RECOGNITION].state == SRSState.SUSPENDED
        assert item.directions[Direction.PRODUCTION].state != SRSState.SUSPENDED

    def test_sync_pull_cloze_ord_0_maps_to_production(self):
        """Cloze note with ord=0 card maps to PRODUCTION direction."""
        db = _make_tt_db()
        guid = _add_cloze_collocation(db)

        # Sync the GUID so the cloze collocation has the right anki_note_id
        db.set_anki_ids(guid, note_id=9001, card_ids={Direction.PRODUCTION: 90010})

        # Anki sends ord=0 card for the cloze note
        cards = [
            make_card_record(anki_card_id=90010, ord=0, queue=2, reps=3, stability=5.0, difficulty=4.5),
        ]
        records = [make_note_record(anki_guid=guid, cards=cards)]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        # The cloze ord=0 card should update PRODUCTION (not RECOGNITION)
        item = db.get_collocation_by_guid(guid)
        assert Direction.PRODUCTION in item.directions
        assert Direction.RECOGNITION not in item.directions
        assert item.directions[Direction.PRODUCTION].state == SRSState.REVIEW

    def test_sync_pull_cloze_updates_sentence_translation(self):
        """Anki has sentence_translation → sync_pull stores it on the collocation."""
        db = _make_tt_db()
        guid = _add_cloze_collocation(db)
        db.set_anki_ids(guid, note_id=9001, card_ids={Direction.PRODUCTION: 90010})

        cards = [make_card_record(anki_card_id=90010, ord=0, queue=2, reps=0)]
        records = [
            make_note_record(
                anki_guid=guid,
                cards=cards,
                sentence_translation="Anki sentence translation",
            )
        ]
        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        assert report.notes_updated == 1
        item = db.get_collocation_by_guid(guid)
        assert item.syntactic_unit.source_sentence_translation == "Anki sentence translation"

    def test_sync_pull_vocab_ord_0_still_maps_to_recognition(self):
        """Vocab note with ord=0 card still maps to RECOGNITION."""
        db = _make_tt_db()
        guid = _add_banka(db)

        cards = [
            make_card_record(anki_card_id=90010, ord=0, queue=2, reps=3, stability=5.0, difficulty=4.5),
            make_card_record(anki_card_id=90011, ord=1, queue=2, reps=3, stability=5.0, difficulty=4.5),
        ]
        records = [make_note_record(anki_guid=guid, cards=cards)]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        item = db.get_collocation_by_guid(guid)
        assert item.directions[Direction.RECOGNITION].state == SRSState.REVIEW
        assert item.directions[Direction.PRODUCTION].state == SRSState.REVIEW

    def test_dry_run_does_not_write(self):
        """dry_run=True reports planned updates without touching the DB."""
        db = _make_tt_db()
        guid = _add_banka(db)

        records = [make_note_record(anki_guid=guid, translation="NEW TRANSLATION", cards=[])]
        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull(dry_run=True)

        assert report.notes_updated == 1
        # DB unchanged
        item = db.get_collocation("banka")
        assert item.syntactic_unit.translation == "bank"

    def test_unknown_guid_increments_skip_count(self):
        """anki_guid != compute_guid(l2_text) → skipped, no DB write."""
        db = _make_tt_db()
        _add_banka(db)

        records = [make_note_record(anki_guid="wrong_guid_xyz", cards=[])]
        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        assert report.skipped_unknown_guid == 1
        assert report.notes_updated == 0

    def test_no_change_reports_zero_updates(self):
        """When Anki and TT have identical data, nothing is reported as updated."""
        db = _make_tt_db()
        guid = _add_banka(db)

        records = [make_note_record(anki_guid=guid, cards=[])]
        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        assert report.notes_updated == 0
        assert report.directions_updated == 0
        assert report.conflicts == []
        assert report.skipped_unknown_guid == 0

    def test_note_not_in_tt_is_silently_skipped(self):
        """Note in Anki but not yet in TunaTale → skipped (not a GUID mismatch)."""
        db = _make_tt_db()
        # Don't add anything to db
        guid = compute_guid("jabolko", "sl", "")

        records = [make_note_record(anki_guid=guid, l2_text="jabolko", translation="apple", cards=[])]
        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        assert report.notes_updated == 0
        assert report.skipped_unknown_guid == 0

    def test_dry_run_conflict_not_written_to_db(self):
        """dry_run=True with conflict → conflict in report but NOT in db.list_sync_conflicts()."""
        db = _make_tt_db()
        guid = _add_banka(db)
        db.set_dirty_fields(guid, "translation")

        records = [make_note_record(anki_guid=guid, translation="bank (financial)", cards=[])]
        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull(dry_run=True)

        assert len(report.conflicts) == 1
        # DB conflict table untouched
        assert db.list_sync_conflicts() == []

    def test_dry_run_dirty_fsrs_no_conflict_no_db_write(self):
        """dry_run=True with dirty_fsrs → no conflict in report, nothing written to DB."""
        db = _make_tt_db()
        guid = _add_banka(db)
        item = db.get_collocation_by_guid(guid)
        ds_dirty = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(item.directions[Direction.RECOGNITION].due_at.date(), time(4, 0), tzinfo=UTC),
            stability=5.0,
            difficulty=4.8,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds_dirty)

        card = make_card_record(anki_card_id=90010, ord=0, reps=9, lapses=1, stability=20.0, difficulty=4.0)
        records = [make_note_record(anki_guid=guid, cards=[card])]
        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull(dry_run=True)

        # No conflict — dirty local data is queued work
        assert report.conflicts == []
        assert db.list_sync_conflicts() == []
        # DB not updated (dry_run)
        after = db.get_collocation_by_guid(guid)
        assert after.directions[Direction.RECOGNITION].reps == 3  # unchanged
        assert after.directions[Direction.RECOGNITION].dirty_fsrs is True

    def test_direction_not_in_local_is_skipped(self):
        """Card for a direction absent from local DB is silently skipped."""
        db = _make_tt_db()
        guid = _add_banka(db)
        # Directly remove the production direction to simulate a missing row
        db._conn.execute("DELETE FROM collocation_directions WHERE direction = 'production'")
        db._conn.commit()

        card = make_card_record(anki_card_id=90011, ord=1, stability=5.0, difficulty=5.0)
        records = [make_note_record(anki_guid=guid, cards=[card])]
        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()
        assert report.directions_updated == 0

    def test_fsrs_known_false_still_applies_suspension(self):
        """fsrs_known=False must still pick up queue-based state changes (e.g. suspension)."""
        db = _make_tt_db()
        guid = _add_banka(db)

        card = make_card_record(anki_card_id=90010, ord=0, queue=-1, fsrs_known=False, stability=0.0, difficulty=0.0)
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()
        updated = db.get_collocation_by_guid(guid)
        assert updated.directions[Direction.RECOGNITION].state == SRSState.SUSPENDED

    def test_pull_propagates_left_and_due_at_for_queue1_learning(self):
        """Regression: when Anki has a card in queue=1 (LEARNING/RELEARNING),
        sync_pull must carry the per-card learning-step counter (`left`) and
        sub-day due timestamp (`due_at`) into TunaTale's mirror. Without these
        fields the FSRS engine has no way to resume Anki's learning sequence
        and a subsequent grade misclassifies the card as REVIEW.
        """
        db = _make_tt_db()
        guid = _add_banka(db)

        future_due_at = _dt.now(UTC) + timedelta(minutes=10)
        # left=1002 = (steps_remaining=1, total_steps=2): 2-step learn, on step 2.
        card = make_card_record(
            anki_card_id=90010,
            ord=0,
            queue=1,
            card_type=1,
            reps=3,
            lapses=0,
            stability=0.5,
            difficulty=8.0,
            left=1002,
            due_at=future_due_at,
        )
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        rec = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert rec.state == SRSState.LEARNING
        assert rec.left == 1002, "pull must propagate Anki's `left` step counter"
        assert rec.due_at is not None, "pull must propagate Anki's sub-day `due_at`"
        assert abs((rec.due_at - future_due_at).total_seconds()) < 1

    def test_pull_propagates_left_and_due_at_for_queue1_relearning(self):
        """Same contract as the LEARNING case, but for RELEARNING (type=3)."""
        db = _make_tt_db()
        guid = _add_banka(db)

        future_due_at = _dt.now(UTC) + timedelta(minutes=10)
        card = make_card_record(
            anki_card_id=90010,
            ord=0,
            queue=1,
            card_type=3,  # Relearn
            reps=12,
            lapses=2,
            stability=1.5,
            difficulty=9.0,
            left=1001,  # 1-step relearn, on step 1
            due_at=future_due_at,
        )
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        rec = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert rec.state == SRSState.RELEARNING
        assert rec.left == 1001
        assert rec.due_at is not None

    def test_direction_differs_detects_left_change(self):
        """Fix 1: when every sync-relevant field matches except `left`, the diff
        must return True so the row gets updated. Without this, Anki's step
        progress on a card whose other fields happen to match TT's is silently
        dropped.
        """
        base = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.LEARNING,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            stability=0.5,
            difficulty=8.0,
            reps=3,
            lapses=0,
            anki_card_id=100,
            anki_due=0,
            last_review=_dt.now(UTC),
            left=1002,
            dirty_fsrs=False,
        )
        assert _direction_differs(base, replace(base, left=1001)) is True

    def test_direction_differs_detects_due_at_change(self):
        """Fix 1: `due_at` shifting (e.g. a fresh fuzzed step from Anki) must
        register as a difference even when state, reps, and last_review match.
        """
        now = _dt.now(UTC)
        base = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.LEARNING,
            stability=0.5,
            difficulty=8.0,
            reps=3,
            lapses=0,
            anki_card_id=100,
            anki_due=0,
            last_review=now,
            left=1001,
            due_at=now + timedelta(minutes=10),
            dirty_fsrs=False,
        )
        assert _direction_differs(base, replace(base, due_at=now + timedelta(minutes=15))) is True

    def test_pull_sets_prior_state_when_anki_transitions_card_out_of_new(self):
        """Fix 4: when TT has a card in NEW and Anki has graded it (queue 0→1
        or 0→2), sync_pull must record `prior_state='new'` on the merged
        direction. `count_new_introduced_today` filters by `prior_state='new'`
        — without this write, the new badge over-counts (TT thinks "0
        introduced today" while Anki shows N).
        """
        db = _make_tt_db()
        guid = _add_banka(db)
        # banka starts in NEW state by default.
        item = db.get_collocation_by_guid(guid)
        assert item.directions[Direction.RECOGNITION].state == SRSState.NEW

        # Anki has graded the card today → queue=1 LEARNING.
        card = make_card_record(
            anki_card_id=90010,
            ord=0,
            queue=1,
            card_type=1,
            reps=1,
            lapses=0,
            stability=0.5,
            difficulty=8.0,
            left=1001,
            due_at=_dt.now(UTC) + timedelta(minutes=10),
            last_review=_dt.now(UTC) - timedelta(minutes=1),
        )
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        rec = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert rec.state == SRSState.LEARNING
        assert rec.prior_state == SRSState.NEW, "must record the NEW→LEARNING transition"

    def test_pull_sets_prior_state_when_anki_graduates_new_directly_to_review(self):
        """Same Fix 4 contract for the rarer NEW→REVIEW transition (e.g. Easy
        on a fresh card with the FSRS short-term path).
        """
        db = _make_tt_db()
        guid = _add_banka(db)

        card = make_card_record(
            anki_card_id=90010,
            ord=0,
            queue=2,
            card_type=2,
            reps=1,
            lapses=0,
            stability=3.0,
            difficulty=5.0,
            last_review=_dt.now(UTC) - timedelta(minutes=1),
        )
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        rec = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert rec.state == SRSState.REVIEW
        assert rec.prior_state == SRSState.NEW

    def test_pull_self_heals_null_prior_state_from_anki_first_revlog_today(self):
        """Fix 4b: re-sync recovers an existing TT row whose prior_state is None
        (synced before the going-forward fix landed). When TT and Anki agree on
        state but Anki's first revlog for that card is today, infer the
        NEW→graded transition happened today and set prior_state='new'. Without
        this, the new-card badge stays stuck for the rest of the day.
        """
        db = _make_tt_db()
        guid = _add_banka(db)
        db.get_collocation_by_guid(guid)
        # Stale TT state: LEARNING with prior_state=None (pre-fix sync result).
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            stability=0.5,
            difficulty=8.0,
            reps=3,
            lapses=0,
            state=SRSState.LEARNING,
            prior_state=None,
            left=1001,
            due_at=_dt.now(UTC) + timedelta(minutes=10),
            last_review=_dt.now(UTC) - timedelta(minutes=30),
            anki_card_id=90010,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        # Anki record with same state but a first revlog from today.
        today_local_midnight_ms = int(
            _dt.combine(date.today(), _time(0), tzinfo=_dt.now().astimezone().tzinfo).astimezone(UTC).timestamp() * 1000
        )
        first_revlog_today = today_local_midnight_ms + 60_000  # 1m past midnight local
        card = make_card_record(
            anki_card_id=90010,
            ord=0,
            queue=1,
            card_type=1,
            reps=3,
            lapses=0,
            stability=0.5,
            difficulty=8.0,
            left=1001,
            due_at=_dt.now(UTC) + timedelta(minutes=10),
            last_review=_dt.now(UTC) - timedelta(minutes=30),
            first_review_ms=first_revlog_today,
        )
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        rec = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert rec.prior_state == SRSState.NEW, "self-heal must infer prior_state='new'"

    def test_pull_self_heals_when_prior_state_is_learning_but_intro_was_today(self):
        """Broader self-heal: a card introduced today that later graduated
        (LEARNING→REVIEW) loses prior_state='new' from the old grade-endpoint
        behavior. On re-sync, when Anki's first revlog for the card is today
        AND state isn't NEW, restore prior_state='new' regardless of the
        current value. Matches Anki's `newToday` counter (sticky for the day).
        """
        db = _make_tt_db()
        guid = _add_banka(db)
        item = db.get_collocation_by_guid(guid)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(item.directions[Direction.RECOGNITION].due_at.date(), time(4, 0), tzinfo=UTC),
            stability=2.0,
            difficulty=5.0,
            reps=4,
            lapses=0,
            state=SRSState.REVIEW,
            prior_state=SRSState.LEARNING,  # graduated today, lost the intro marker
            last_review=_dt.now(UTC) - timedelta(hours=1),
            anki_card_id=90010,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        today_local_midnight_ms = int(
            _dt.combine(date.today(), _time(0), tzinfo=_dt.now().astimezone().tzinfo).astimezone(UTC).timestamp() * 1000
        )
        card = make_card_record(
            anki_card_id=90010,
            ord=0,
            queue=2,
            card_type=2,
            reps=4,
            lapses=0,
            stability=2.0,
            difficulty=5.0,
            last_review=_dt.now(UTC) - timedelta(hours=1),
            first_review_ms=today_local_midnight_ms + 3_600_000,  # 1h after midnight
        )
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        rec = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert rec.prior_state == SRSState.NEW, (
            "self-heal must restore prior_state='new' for cards introduced today, "
            "regardless of subsequent same-day transitions that lost the marker"
        )

    def test_pull_self_heal_skipped_when_first_revlog_is_before_today(self):
        """Self-heal must not falsely set prior_state='new' on a card whose
        introduction happened on a previous day."""
        db = _make_tt_db()
        guid = _add_banka(db)
        item = db.get_collocation_by_guid(guid)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(item.directions[Direction.RECOGNITION].due_at.date(), time(4, 0), tzinfo=UTC),
            stability=2.0,
            difficulty=5.0,
            reps=5,
            lapses=0,
            state=SRSState.REVIEW,
            prior_state=None,
            last_review=_dt.now(UTC) - timedelta(days=2),
            anki_card_id=90010,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        # First revlog was 3 days ago.
        first_revlog_old = int((_dt.now(UTC) - timedelta(days=3)).timestamp() * 1000)
        card = make_card_record(
            anki_card_id=90010,
            ord=0,
            queue=2,
            card_type=2,
            reps=5,
            lapses=0,
            stability=2.0,
            difficulty=5.0,
            last_review=_dt.now(UTC) - timedelta(days=2),
            first_review_ms=first_revlog_old,
        )
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        rec = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert rec.prior_state is None, "do not back-date introductions older than today"

    def test_pull_preserves_prior_state_when_state_unchanged(self):
        """No state transition → don't overwrite prior_state. (Otherwise repeated
        syncs would clobber the value set by an earlier transition / TT grade.)
        """
        db = _make_tt_db()
        guid = _add_banka(db)
        item = db.get_collocation_by_guid(guid)
        # Seed a REVIEW direction with an existing prior_state.
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(item.directions[Direction.RECOGNITION].due_at.date(), time(4, 0), tzinfo=UTC),
            stability=2.0,
            difficulty=5.0,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            prior_state=SRSState.NEW,
            last_review=_dt.now(UTC) - timedelta(hours=2),
            anki_card_id=90010,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        # Anki returns matching review state — no transition.
        card = make_card_record(
            anki_card_id=90010,
            ord=0,
            queue=2,
            card_type=2,
            reps=3,
            lapses=0,
            stability=2.0,
            difficulty=5.0,
            last_review=_dt.now(UTC) - timedelta(hours=2),
        )
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        rec = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert rec.state == SRSState.REVIEW
        assert rec.prior_state == SRSState.NEW, "prior_state must be preserved across no-op syncs"


# ── B15: diff-before-write in sync_pull ───────────────────────────────────────


class TestSyncPullNoOp:
    """B15: pull with no state change must not update DB or inflate report counters."""

    def test_unchanged_directions_not_counted_as_updated(self):
        """When Anki returns identical state to TT, directions_updated must be 0."""
        db = _make_tt_db()
        guid = _add_banka(db)
        today = date.today()

        # Pre-seed TT to match exactly what Anki will return
        for direction, card_id, _ord in [
            (Direction.RECOGNITION, 90010, 0),
            (Direction.PRODUCTION, 90011, 1),
        ]:
            ds = DirectionState(
                direction=direction,
                due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
                stability=5.0,
                difficulty=4.5,
                reps=3,
                lapses=0,
                state=SRSState.REVIEW,
                dirty_fsrs=False,
                anki_card_id=card_id,
            )
            db.update_direction(guid, direction, ds)

        cards = [
            make_card_record(anki_card_id=90010, ord=0, due_date=today, reps=3),
            make_card_record(anki_card_id=90011, ord=1, due_date=today, reps=3),
        ]
        records = [make_note_record(anki_guid=guid, cards=cards)]

        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        assert report.directions_updated == 0

    def test_changed_direction_is_counted(self):
        """When Anki returns a different stability, that direction IS counted."""
        db = _make_tt_db()
        guid = _add_banka(db)
        today = date.today()

        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
            stability=5.0,
            difficulty=4.5,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=False,
            anki_card_id=90010,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        card = make_card_record(anki_card_id=90010, ord=0, stability=8.0)  # changed from 5.0
        records = [make_note_record(anki_guid=guid, cards=[card])]

        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        assert report.directions_updated == 1


class TestSyncPullIdFirstLookup:
    """B19: primary lookup by anki_note_id prevents duplicate-guid collision."""

    def test_duplicate_anki_notes_only_linked_one_updates_tt(self):
        """Two Anki notes share the same computed guid but have different note IDs and
        translations. Only the one whose anki_note_id is stored in TT should win."""
        db = _make_tt_db()
        guid = _add_banka(db)

        NID_A = 7001
        NID_B = 7002

        # Link TT row to NID_A (the "carry" note — not the default "bank").
        db.set_anki_ids(guid, note_id=NID_A, card_ids={})

        records = [
            make_note_record(anki_note_id=NID_A, anki_guid=guid, translation="carry", cards=[]),
            make_note_record(anki_note_id=NID_B, anki_guid=guid, translation="wear", cards=[]),
        ]

        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        # NID_A's translation wins; NID_B is ignored.
        item = db.get_collocation("banka")
        assert item.syntactic_unit.translation == "carry"

        # Second run: TT already matches NID_A → idempotent (NID_B still ignored).
        report2 = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()
        assert report2.notes_updated == 0

    def test_unlinked_tt_row_still_falls_back_to_guid_lookup(self):
        """TT row with anki_note_id=NULL is still matched via guid fallback."""
        db = _make_tt_db()
        guid = _add_banka(db)
        # Do NOT call set_anki_ids — row stays unlinked (anki_note_id IS NULL).

        records = [make_note_record(anki_guid=guid, translation="savings bank", cards=[])]

        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        assert report.notes_updated == 1
        item = db.get_collocation("banka")
        assert item.syntactic_unit.translation == "savings bank"


# ── Parametrized queue→state mapping tests ──────────────────────────────────


@pytest.mark.parametrize(
    "fsrs_known,queue,reps,expected_state",
    [
        # `queue` is authoritative; `reps` does not override it (Layer 30).
        # fsrs_known=False path
        (False, -2, 4, SRSState.BURIED),
        (False, 1, 2, SRSState.LEARNING),
        (False, 3, 5, SRSState.RELEARNING),
        (False, 0, 0, SRSState.NEW),
        (False, 0, 5, SRSState.NEW),  # queue=0 → NEW even if reps>0 (weird state, mirror Anki)
        # fsrs_known=True path (adds queue=-3)
        (True, -2, 4, SRSState.BURIED),
        (True, -3, 4, SRSState.BURIED),
        (True, 1, 2, SRSState.LEARNING),
        (True, 3, 5, SRSState.RELEARNING),
        (True, 0, 0, SRSState.NEW),
        (True, 0, 5, SRSState.NEW),
        # queue=2 must always map to REVIEW, even when reps=0 (Anki's "Forget"
        # action or manual edit can leave a graduated card with reps=0 while
        # queue stays at 2). Previously the reps==0 fallback wrongly returned
        # NEW for these, surfacing already-graded cards as fresh new cards.
        (True, 2, 0, SRSState.REVIEW),
        (False, 2, 0, SRSState.REVIEW),
        (True, 2, 7, SRSState.REVIEW),
        # Defensive fallback for unknown queue values: trust reps as a last
        # resort. Never happens against current Anki (all queues in -3..3),
        # but the branch is exercised so future-Anki queue additions don't
        # silently miscategorize.
        (True, 99, 0, SRSState.NEW),
        (True, 99, 4, SRSState.REVIEW),
    ],
)
def test_queue_to_state_mapping(fsrs_known, queue, reps, expected_state):
    """Parametrized: queue value + fsrs_known → SRSState."""
    db = _make_tt_db()
    guid = _add_banka(db)

    card = make_card_record(queue=queue, reps=reps, fsrs_known=fsrs_known)
    record = make_note_record(anki_guid=guid, cards=[card])

    AnkiSync(db=db, _reader=FakeReader([record]), _writer=FakeWriter()).sync_pull()
    updated = db.get_collocation_by_guid(guid)
    assert updated.directions[Direction.RECOGNITION].state == expected_state


# ── _discover_today_anki_day ──────────────────────────────────────────────────


# ── _discover_today_anki_day ──────────────────────────────────────────────────


class TestLastSyncedAtOnPull:
    def test_last_synced_at_set_when_direction_updated(self):
        """sync_pull populates last_synced_at when a direction's FSRS state changes."""
        db = _make_tt_db()
        guid = _add_banka(db)
        db.set_anki_ids(guid, note_id=9001, card_ids={Direction.RECOGNITION: 90010, Direction.PRODUCTION: 90011})

        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].last_synced_at is None

        new_due = date.today() + timedelta(days=5)
        card = make_card_record(anki_card_id=90010, ord=0, stability=10.0, due_date=new_due)
        records = [make_note_record(anki_guid=guid, cards=[card])]

        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].last_synced_at is not None


class TestSyncPullWritesAnkiDue:
    def test_pull_writes_anki_due_for_new_card(self):
        """sync_pull writes anki_due from CardRecord for new cards."""
        db = _make_tt_db()
        guid = _add_banka(db)

        # CardRecord with queue=0 and anki_due=842
        card = make_card_record(anki_card_id=90010, ord=0, queue=0, reps=0, stability=1.0, difficulty=5.0, anki_due=842)
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()
        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].anki_due == 842

    def test_pull_propagates_anki_due_change_when_other_fields_unchanged(self):
        """When only anki_due changes (Anki reposition), sync_pull must persist it."""
        db = _make_tt_db()
        guid = _add_banka(db)
        base_card = make_card_record(
            anki_card_id=90010, ord=0, queue=0, reps=0, stability=1.0, difficulty=5.0, anki_due=842
        )
        note = make_note_record(anki_guid=guid, cards=[base_card])
        # First sync: locks in anki_due=842 (anki_card_id change forces write).
        AnkiSync(db=db, _reader=FakeReader([note]), _writer=FakeWriter()).sync_pull()
        assert db.get_collocation("banka").directions[Direction.RECOGNITION].anki_due == 842

        # Second sync: only anki_due changed in Anki (reposition).
        note.cards[0] = replace(base_card, anki_due=100)
        AnkiSync(db=db, _reader=FakeReader([note]), _writer=FakeWriter()).sync_pull()
        assert db.get_collocation("banka").directions[Direction.RECOGNITION].anki_due == 100


# ── Step 4: last_review propagation tests ──────────────────────────────


class TestOfflineReaderPopulatesLastReview:
    def test_offline_reader_populates_last_review(self, fake_anki_db):
        """OfflineReader: CardRecord.last_review set for queue=2 cards."""
        conn = sqlite3.connect(str(fake_anki_db))
        # Update card 10010 (banka recognition) to have queue=2, ivl=5, due=15
        conn.execute("UPDATE cards SET queue=2, ivl=5, due=15 WHERE id=10010")
        conn.commit()
        conn.close()

        conn = sqlite3.connect(str(fake_anki_db))
        records = OfflineReader(conn, "0. Slovene").get_note_records()
        conn.close()

        # Find card 10010 specifically (banka recognition)
        for rec in records:
            if rec.l2_text != "banka":
                continue
            for card in rec.cards:
                if card.anki_card_id == 10010:
                    # col_crt=1704067200 -> 2024-01-01 UTC
                    # due=15, ivl=5 -> +10 days -> 2024-01-11 (midnight UTC)
                    from datetime import datetime as _dt
                    from datetime import time as _time

                    assert card.last_review == _dt.combine(date(2024, 1, 11), _time.min, tzinfo=UTC)
                    break


class TestSyncPullWritesLastReviewToDb:
    def test_sync_pull_writes_last_review_to_db(self):
        """sync_pull persists CardRecord.last_review into collocation_directions."""
        from datetime import datetime as _dt
        from datetime import time as _time

        db = _make_tt_db()
        guid = _add_banka(db)

        expected_last_review = _dt.combine(date(2024, 1, 11), _time.min, tzinfo=UTC)
        card = make_card_record(
            anki_card_id=90010, ord=0, reps=5, stability=7.5, difficulty=4.8, last_review=expected_last_review
        )
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].last_review == expected_last_review

    def test_sync_pull_uses_card_rec_last_review_directly(self):
        """sync_pull writes `card_rec.last_review` straight through. That value
        is already FSRS-correct (cards.data.lrt → precise UTC datetime, or
        day-level midnight UTC fallback for pre-FSRS cards). Even when a more
        recent revlog ms is available (e.g. learning-step grades after a lapse),
        it must NOT override the FSRS-effective lrt timestamp — Anki's
        `extract_fsrs_retrievability` uses lrt, so mirroring lrt is what makes
        R-asc match. Earlier preference for MAX(revlog.id) here caused the
        svetilka-vs-kopalnica head-card divergence.
        """
        from datetime import datetime as _dt

        db = _make_tt_db()
        guid = _add_banka(db)

        # Simulates the lrt-derived value parse_fsrs_data would populate.
        lrt_dt = _dt(2026, 5, 10, 20, 56, 41, tzinfo=UTC)
        # Later revlog ms (from a relearning-step grade after the lapse) — must
        # not be preferred over lrt.
        later_revlog_ms = int(_dt(2026, 5, 11, 1, 32, 37, tzinfo=UTC).timestamp() * 1000)

        card = make_card_record(
            anki_card_id=90010,
            ord=0,
            reps=5,
            last_review=lrt_dt,
            last_review_ms=later_revlog_ms,
        )
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        item = db.get_collocation("banka")
        stored = item.directions[Direction.RECOGNITION].last_review
        assert stored == lrt_dt, (
            f"sync_pull must use card_rec.last_review (lrt-derived), not revlog ms; got {stored.isoformat()}"
        )

    def test_sync_pull_writes_day_level_last_review_for_pre_fsrs_cards(self):
        """For pre-FSRS cards (cards.data has no lrt), parse_fsrs_data populates
        card_rec.last_review with the day-level midnight UTC value. sync_pull
        passes it through unchanged.
        """
        from datetime import datetime as _dt

        db = _make_tt_db()
        guid = _add_banka(db)

        day_level_ts = _dt(2024, 1, 11, 0, 0, 0, tzinfo=UTC)
        card = make_card_record(anki_card_id=90010, ord=0, reps=5, last_review=day_level_ts, last_review_ms=None)
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].last_review == day_level_ts

    def test_sync_pull_advances_learning_cutoff_from_revlog_ms(self):
        """sync_pull must advance learning_cutoff to the most recent Anki revlog timestamp.

        Without this, an Anki-only grading session would leave TT's cutoff frozen at
        the last *TT* grade, so intraday-learning cards that ticked past-due during
        the Anki session would never become eligible until TT itself recorded a grade.
        """
        from datetime import datetime as _dt

        db = _make_tt_db()
        guid = _add_banka(db)

        # Stale local cutoff: simulate a TT grade from earlier today.
        stale_cutoff = _dt(2026, 5, 9, 10, 0, 0, tzinfo=UTC)
        db.set_anki_state_cache("learning_cutoff", stale_cutoff.isoformat())

        # Anki revlog row for this card is 3 hours newer than the stale cutoff.
        anki_grade_ms = int(_dt(2026, 5, 9, 13, 0, 0, tzinfo=UTC).timestamp() * 1000)
        card = make_card_record(anki_card_id=90010, ord=0, reps=6, last_review_ms=anki_grade_ms)
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        cached = db.get_anki_state_cache("learning_cutoff")
        assert cached is not None
        cached_at = _dt.fromisoformat(cached[0])
        assert cached_at == _dt.fromtimestamp(anki_grade_ms / 1000, UTC), (
            f"cutoff must advance to the latest ingested revlog ts, got {cached_at.isoformat()}"
        )

    def test_sync_pull_dry_run_does_not_advance_learning_cutoff(self):
        """Dry-run sync_pull must not mutate the cache."""
        from datetime import datetime as _dt

        db = _make_tt_db()
        guid = _add_banka(db)

        original_cutoff = _dt(2026, 5, 9, 10, 0, 0, tzinfo=UTC)
        db.set_anki_state_cache("learning_cutoff", original_cutoff.isoformat())

        anki_grade_ms = int(_dt(2026, 5, 9, 13, 0, 0, tzinfo=UTC).timestamp() * 1000)
        card = make_card_record(anki_card_id=90010, ord=0, reps=6, last_review_ms=anki_grade_ms)
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull(dry_run=True)

        cached = db.get_anki_state_cache("learning_cutoff")
        assert cached is not None
        assert _dt.fromisoformat(cached[0]) == original_cutoff


class TestSyncPullInvalidatesSessionMainQueue:
    """sync_pull must invalidate the frozen session_main_queue cache on completion.

    Mirrors Anki's `requires_study_queue_rebuild` for sync (queue/mod.rs:211-215):
    Anki rebuilds its review queue after sync round-trip. TT mirrors by clearing
    the cache so the next /review-queue rebuilds from current state — otherwise
    a card whose Anki-side state transitioned (e.g. learning→review post-graduation
    yesterday, ingested today via sync) stays at its stale cached position instead
    of moving to its current R-asc spot.
    """

    def test_sync_pull_rebuilds_session_main_queue_on_completion(self):
        """Layer 29: non-dry-run sync_pull EAGERLY REBUILDS session_main_queue —
        the stale placeholder is replaced with a freshly computed order so the
        freeze moment matches Anki's session-open rebuild."""
        from datetime import date

        db = _make_tt_db()
        guid = _add_banka(db)

        # Seed a stale cache from earlier today with bogus row ids.
        today = date.today()
        from app.srs.queue_stats import get_session_main_queue, set_session_main_queue

        set_session_main_queue(db, today, [(9999, "recognition"), (8888, "production")])

        # Sync ingests a card record (state may or may not change).
        card = make_card_record(anki_card_id=90010, ord=0, reps=5, stability=7.5, difficulty=4.8)
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        # Cache must hold the rebuilt order — the bogus placeholders are gone.
        cached = get_session_main_queue(db, today)
        assert cached is not None, "sync_pull must rebuild, not just clear"
        assert (9999, "recognition") not in cached
        assert (8888, "production") not in cached

    def test_sync_pull_rebuilds_session_main_queue_eagerly(self):
        """sync_pull must REBUILD session_main_queue (not just clear) so the
        cached order reflects the pool at sync time. Otherwise TT's first /review-
        queue request can happen long after sync, freezing a queue from a different
        pool moment than Anki's session-start rebuild."""
        from datetime import date

        from app.models.srs_item import Direction, DirectionState, SRSState
        from app.models.syntactic_unit import SyntacticUnit

        db = _make_tt_db()
        # Add one review-state card and one new-state card. After sync, the
        # cache should hold a non-empty rebuilt order.
        today = date.today()
        for txt in ("rev_card", "new_card"):
            db.add_collocation(
                SyntacticUnit(text=txt, translation="t", word_count=1, difficulty=1, source="test"),
                language_code="sl",
            )
        rows, _ = db.list_collocations(search="rev_card", limit=1)
        row_id, _, _ = rows[0]
        db.update_direction_by_id(
            row_id,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.REVIEW,
                due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
                stability=1.0,
                reps=5,
                anki_card_id=100,
            ),
        )

        # Seed stale cache and let sync_pull run.
        from app.srs.queue_stats import set_session_main_queue

        set_session_main_queue(db, today, [(9999, "recognition")])

        guid = _add_banka(db)
        card = make_card_record(anki_card_id=90010, ord=0, reps=5, stability=7.5, difficulty=4.8)
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        # Cache must now hold the freshly rebuilt order, not be empty and not be
        # the stale placeholder.
        from app.srs.queue_stats import get_session_main_queue

        cached = get_session_main_queue(db, today)
        assert cached is not None, "sync_pull should eagerly rebuild — not leave the cache empty"
        # The stale (9999, "recognition") placeholder must be gone.
        assert (9999, "recognition") not in cached
        # rev_card's row_id should be in the rebuilt order (it's the only review).
        assert (row_id, "recognition") in cached

    def test_sync_pull_dry_run_does_not_clear_session_main_queue(self):
        """Dry-run must not mutate the cache."""
        from datetime import date

        db = _make_tt_db()
        guid = _add_banka(db)
        today = date.today()
        from app.srs.queue_stats import set_session_main_queue

        items = [(1, "recognition")]
        set_session_main_queue(db, today, items)

        card = make_card_record(anki_card_id=90010, ord=0, reps=5)
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull(dry_run=True)

        from app.srs.queue_stats import get_session_main_queue

        assert get_session_main_queue(db, today) == items


class TestDirectionDiffersDetectsLastReviewTransition:
    def test_direction_differs_detects_last_review_transition(self):
        """None → datetime transition detected by _direction_differs."""
        from dataclasses import replace
        from datetime import datetime as _dt
        from datetime import time as _time

        local = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            stability=7.5,
            difficulty=4.8,
            reps=5,
            lapses=0,
            state=SRSState.REVIEW,
        )
        candidate = replace(local, last_review=_dt.combine(date(2024, 1, 11), _time.min, tzinfo=UTC))

        assert _direction_differs(local, candidate) is True

    def test_direction_differs_no_change_when_same_last_review(self):
        """Same last_review → no difference."""
        from datetime import datetime as _dt
        from datetime import time as _time

        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            stability=7.5,
            difficulty=4.8,
            reps=5,
            lapses=0,
            state=SRSState.REVIEW,
            last_review=_dt.combine(date(2024, 1, 11), _time.min, tzinfo=UTC),
        )

        assert _direction_differs(ds, ds) is False

    def test_sync_pull_bury_trace_logs_user_bury(self, caplog):
        """Exercise the anki_queue_minus2_seen counter and confirm the
        BURY_TRACE line records the queue=-2 case (so future investigators
        can grep for ``anki_queue=-2`` to spot manual-bury sources).
        """
        import logging

        db = _make_tt_db()
        today = date.today()
        unit = SyntacticUnit(text="ub", translation="t", word_count=1, difficulty=1, source="corpus")
        db.add_collocation(unit)
        guid = db.get_collocation("ub").guid

        db.update_direction(
            guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
                stability=5.0,
                difficulty=4.5,
                reps=3,
                lapses=0,
                state=SRSState.REVIEW,
                anki_card_id=2001,
                anki_due=4500,
                last_review=_dt.now(UTC),
                dirty_fsrs=False,
            ),
        )

        records = [
            make_note_record(
                anki_note_id=2001,
                anki_guid=guid,
                l2_text="ub",
                cards=[make_card_record(anki_card_id=2001, ord=0, queue=-2, due_date=today)],
            )
        ]
        with caplog.at_level(logging.INFO, logger="app.anki.sync"):
            AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        msg = "\n".join(r.getMessage() for r in caplog.records)
        assert "anki_queue=-2" in msg
        assert "'anki_queue_minus2_seen': 1" in msg

    def test_sync_pull_classifies_queue_minus_2_as_sched(self):
        """Anki's binary writes queue=-2 for grade-time sibling-bury, NOT
        queue=-3 as the source code says. Verified 2026-05-17 by running
        ``col.sched.answerCard`` against a copy of the user's collection:
        grading ord=1 placed the ord=0 sibling at queue=-2 in the same
        atomic transaction (matching mods). The user's deck had 19 cards
        in queue=-2 with zero in queue=-3, all created by grading siblings.

        Anki releases queue=-2 at rollover too (``unbury_on_day_rollover``
        releases both -2 and -3, ``rslib/storage/card/sqlwriter.rs:471-476``).
        TT must mirror that: classify queue=-2 as ``'sched'`` so the daily
        unbury sweep releases it. The previous mapping (queue=-2 → ``'user'``)
        left TT holding sibling-buries indefinitely while Anki had already
        released them — producing the 19-card cohort on 2026-05-17 (and the
        earlier 140-row incident on 2026-05-16, same root cause).

        Rule 13: trust the binary, not the source.
        """
        db = _make_tt_db()
        today = date.today()
        unit = SyntacticUnit(text="sib", translation="t", word_count=1, difficulty=1, source="corpus")
        db.add_collocation(unit)
        guid = db.get_collocation("sib").guid

        db.update_direction(
            guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
                stability=5.0,
                difficulty=4.5,
                reps=3,
                lapses=0,
                state=SRSState.REVIEW,
                anki_card_id=2001,
                anki_due=4500,
                last_review=_dt.now(UTC),
                dirty_fsrs=False,
            ),
        )

        records = [
            make_note_record(
                anki_note_id=2001,
                anki_guid=guid,
                l2_text="sib",
                cards=[make_card_record(anki_card_id=2001, ord=0, queue=-2, due_date=today)],
            )
        ]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        result = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert result.state == SRSState.BURIED
        assert result.bury_kind == "sched", (
            f"queue=-2 must classify as 'sched' so the daily unbury sweep "
            f"releases it (Anki releases queue=-2 at rollover). "
            f"Got bury_kind={result.bury_kind!r}."
        )

    def test_sync_pull_bury_trace_counters_and_log(self, caplog):
        """Exercise BURY_TRACE counter branches and log emission.

        Walks four collocations through sync_pull, each producing a different
        bury_stats outcome:
          1. TT REVIEW + Anki queue=-3 → released_to_buried_writes, minus3 seen.
          2. TT BURIED + Anki queue=2  → buried_to_released_writes.
          3. TT BURIED kind='user' + Anki queue=-3 (kind flip) → kind_only_flips.
          4. TT BURIED kind='sched' + Anki queue=-3 (full match) → no write,
             buried_state_match_no_write counter increments.

        Confirms the per-card BURY_TRACE INFO log and the summary log both
        fire. These traces are the diagnostic for any future bury-kind drift.
        """
        import logging

        db = _make_tt_db()
        today = date.today()

        # Set up 4 collocations, each with a recognition card (ord=0).
        guids = []
        for i, text in enumerate(["b1_review", "b2_buried", "b3_user", "b4_sched"]):
            unit = SyntacticUnit(text=text, translation=f"t{i}", word_count=1, difficulty=1, source="corpus")
            db.add_collocation(unit)
            item = db.get_collocation(text)
            guids.append(item.guid)

        now_ts = _dt.now(UTC)
        common = dict(
            direction=Direction.RECOGNITION,
            due_at=_dt.combine(today, _time(4, 0), tzinfo=UTC),
            stability=5.0,
            difficulty=4.5,
            reps=3,
            lapses=0,
            anki_card_id=0,
            anki_due=4500,
            last_review=now_ts,
            dirty_fsrs=False,
        )
        db.update_direction(
            guids[0],
            Direction.RECOGNITION,
            DirectionState(**{**common, "state": SRSState.REVIEW, "anki_card_id": 1001}),
        )
        db.update_direction(
            guids[1],
            Direction.RECOGNITION,
            DirectionState(**{**common, "state": SRSState.BURIED, "anki_card_id": 1002, "bury_kind": "sched"}),
        )
        db.update_direction(
            guids[2],
            Direction.RECOGNITION,
            DirectionState(**{**common, "state": SRSState.BURIED, "anki_card_id": 1003, "bury_kind": "user"}),
        )
        db.update_direction(
            guids[3],
            Direction.RECOGNITION,
            DirectionState(**{**common, "state": SRSState.BURIED, "anki_card_id": 1004, "bury_kind": "sched"}),
        )

        # Mark today's unbury sweep as already-done so the buried test rows
        # survive into sync_pull's main loop. (sync_pull's first action is
        # `unbury_if_needed(today)`, which would otherwise release b2/b4.)
        db.set_anki_state_cache("last_unbury_day", today.isoformat())

        # Anki returns each in the queue value that produces the desired counter.
        # NOTE: l2_text must match the TT collocation so compute_guid alignment works
        # (sync_pull resolves records by guid when anki_note_id isn't set).
        # For b4, every field must align between local and candidate so the diff
        # actually returns False and the no_write branch fires.
        texts = ["b1_review", "b2_buried", "b3_user", "b4_sched"]
        cids = [1001, 1002, 1003, 1004]
        anki_queues = [-3, 2, -3, -3]
        records = []
        for i, (text, cid, q) in enumerate(zip(texts, cids, anki_queues, strict=True)):
            records.append(
                make_note_record(
                    anki_note_id=9000 + i,
                    anki_guid=guids[i],
                    l2_text=text,
                    translation=f"t{i}",
                    cards=[
                        make_card_record(
                            anki_card_id=cid,
                            ord=0,
                            queue=q,
                            reps=3,  # match local
                            stability=5.0,  # match local
                            difficulty=4.5,  # match local
                            due_date=today,
                            anki_due=4500,
                            last_review=now_ts,  # match local
                            last_review_ms=int(now_ts.timestamp() * 1000),
                        )
                    ],
                )
            )
        with caplog.at_level(logging.INFO, logger="app.anki.sync"):
            AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        # Per-card BURY_TRACE lines exist for each of the 4
        trace_lines = [r.getMessage() for r in caplog.records if "BURY_TRACE cid=" in r.getMessage()]
        assert len(trace_lines) == 4
        # Summary line emitted with the expected counters
        summaries = [r.getMessage() for r in caplog.records if "BURY_TRACE summary" in r.getMessage()]
        assert len(summaries) == 1
        s = summaries[0]
        assert "'anki_queue_minus3_seen': 3" in s
        assert "'released_to_buried_writes': 1" in s
        assert "'buried_to_released_writes': 1" in s
        assert "'kind_only_flips_written': 1" in s
        assert "'buried_state_match_no_write': 1" in s

        # Also assert the user→sched flip actually persisted (proves the
        # _direction_differs bury_kind fix landed end-to-end).
        rec3 = db.get_collocation_by_guid(guids[2]).directions[Direction.RECOGNITION]
        assert rec3.bury_kind == "sched"

    def test_direction_differs_detects_bury_kind_change(self):
        """When state matches but ``bury_kind`` differs, the diff must return
        True so sync_pull's bury-kind reclassification actually lands.

        Without this, the Layer 35 migration's pessimistic ``'user'`` backfill
        cannot be corrected to ``'sched'`` or ``None`` on subsequent syncs
        when Anki's state happens to match TT's (both BURIED, or both REVIEW
        after a manual Anki unbury), silently locking the row in the wrong
        kind forever.
        """
        base = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.BURIED,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            stability=0.5,
            difficulty=8.0,
            reps=3,
            lapses=0,
            anki_card_id=100,
            anki_due=4500,
            last_review=_dt.now(UTC),
            dirty_fsrs=False,
            bury_kind="user",
        )
        assert _direction_differs(base, replace(base, bury_kind="sched")) is True
        assert _direction_differs(base, replace(base, bury_kind=None)) is True
        assert _direction_differs(base, base) is False

    def test_direction_differs_detects_anki_card_mod_change(self):
        """``anki_card_mod`` feeds the FNV tiebreaker in
        ``_merge_by_retrievability_ascending`` (Anki's ``fnvhash(id, mod)``
        appended last in ``review_order_sql``). When Anki bumps ``cards.mod``
        without changing any FSRS field, the tiebreak input drifts and TT
        serves a different card than Anki from a pool of R-tied cards.
        The diff must fire on mod-only changes so sync_pull refreshes it.
        """
        base = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            stability=5.0,
            difficulty=4.5,
            reps=3,
            lapses=0,
            anki_card_id=100,
            anki_card_mod=1778703057,
            anki_due=4500,
            last_review=_dt.now(UTC),
            dirty_fsrs=False,
        )
        assert _direction_differs(base, replace(base, anki_card_mod=1778812978)) is True
        assert _direction_differs(base, base) is False

    def test_sync_pull_refreshes_stale_anki_card_mod(self):
        """End-to-end: a TT direction with stale ``anki_card_mod`` must be
        rewritten when sync_pull observes a newer ``cards.mod`` from Anki,
        even if no other FSRS field changed. Without this, the FNV
        tiebreaker (``fnvhash(anki_card_id, anki_card_mod)``) sorts on
        stale input and TT picks a different head card than Anki from
        R-tied groups.
        """
        db = _make_tt_db()
        today = date.today()
        unit = SyntacticUnit(text="modrefresh", translation="t", word_count=1, difficulty=1, source="corpus")
        db.add_collocation(unit)
        guid = db.get_collocation("modrefresh").guid

        stale_mod = 1778703057
        fresh_mod = 1778812978
        last_review = _dt.now(UTC)

        db.update_direction(
            guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.REVIEW,
                due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
                stability=5.0,
                difficulty=4.5,
                reps=3,
                lapses=0,
                anki_card_id=3001,
                anki_card_mod=stale_mod,
                anki_due=4500,
                last_review=last_review,
                dirty_fsrs=False,
            ),
        )

        records = [
            make_note_record(
                anki_note_id=3001,
                anki_guid=guid,
                l2_text="modrefresh",
                cards=[
                    make_card_record(
                        anki_card_id=3001,
                        ord=0,
                        queue=2,
                        reps=3,
                        lapses=0,
                        stability=5.0,
                        difficulty=4.5,
                        due_date=today,
                        anki_due=4500,
                        anki_card_mod=fresh_mod,
                        last_review=last_review,
                    )
                ],
            )
        ]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        item = db.get_collocation("modrefresh")
        assert item.directions[Direction.RECOGNITION].anki_card_mod == fresh_mod


# ── TestAnkiSyncConstructor ────────────────────────────────────────────────────


class TestAnkiSyncConstructor:
    def test_missing_reader_raises(self):
        """AnkiSync requires _reader."""
        db = _make_tt_db()
        try:
            AnkiSync(db=db, _writer=FakeWriter())
        except ValueError as e:
            assert "_reader is required" in str(e)
        else:
            raise AssertionError("Expected ValueError")

    def test_missing_writer_raises(self):
        """AnkiSync requires _writer."""
        db = _make_tt_db()
        try:
            AnkiSync(db=db, _reader=FakeReader([]))
        except ValueError as e:
            assert "_writer is required" in str(e)
        else:
            raise AssertionError("Expected ValueError")


# ── Regression: bury/suspend not mirrored when dirty_fsrs=True ──────────────


class TestDirtyFsrsBuriedSyncRegression:
    """Regression tests for sync bug (S3.4): Anki bury/suspend state not mirrored
    when local direction had dirty_fsrs=True (TunaTale's grade was "newer" by timestamp).

    Realistic scenario: User rates direction A in TunaTale (dirty_fsrs=True), then
    in Anki rates A again AND Anki buries that same direction. On sync, TunaTale's
    timestamp wins for FSRS data, but Anki's bury state must still be applied.

    Fix: sync_pull now applies Anki's bury/suspend state even when local dirty_fsrs
    wins on FSRS data.
    """

    def test_buried_state_mirrored_when_same_direction_dirty_fsrs(self):
        """Most realistic bug scenario: same direction is dirty in TT AND buried in Anki.

        User rated recognition in TunaTale (dirty_fsrs=True), then in Anki rated it
        again and Anki buried it (queue=-2). On sync, TT's timestamp wins for FSRS,
        but Anki's bury state must still be applied.
        """
        db = _make_tt_db()
        guid = _add_banka(db)

        # Only recognition is dirty (rated in TunaTale) with newer timestamp
        recent_review = _dt.combine(date.today(), _time.min, tzinfo=UTC)
        ds_rec = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            stability=5.0,
            difficulty=4.5,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
            last_review=recent_review,
            last_review_time_ms=5000,  # TunaTale's review is newer
        )
        db.update_direction(guid, Direction.RECOGNITION, ds_rec)

        # Production is clean (already synced, not dirty)
        ds_prod = DirectionState(
            direction=Direction.PRODUCTION,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            stability=5.0,
            difficulty=4.5,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=False,
        )
        db.update_direction(guid, Direction.PRODUCTION, ds_prod)

        # Anki: recognition was rated then buried (queue=-2), production is review-ready
        cards = [
            make_card_record(
                anki_card_id=90010,
                ord=0,
                queue=-2,  # buried in Anki after rating
                reps=5,
                stability=10.0,
                difficulty=4.0,
                last_review_ms=1000,  # older than TunaTale's
            ),
            make_card_record(
                anki_card_id=90011,
                ord=1,
                queue=2,  # review-ready
                reps=5,
                stability=10.0,
                difficulty=4.0,
                last_review_ms=2000,
            ),
        ]
        records = [make_note_record(anki_guid=guid, cards=cards)]
        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        assert report.directions_updated == 2

        updated = db.get_collocation_by_guid(guid)
        # Recognition: was dirty, Anki buried it → BURIED state applied despite dirty_fsrs
        assert updated.directions[Direction.RECOGNITION].state == SRSState.BURIED
        # Production: clean direction, synced normally
        assert updated.directions[Direction.PRODUCTION].state == SRSState.REVIEW

    def test_suspended_state_mirrored_when_same_direction_dirty_fsrs(self):
        """Same as above but with suspended (queue=-1) instead of buried."""
        db = _make_tt_db()
        guid = _add_banka(db)

        # Recognition is dirty in TunaTale
        recent_review = _dt.combine(date.today(), _time.min, tzinfo=UTC)
        ds_rec = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            stability=5.0,
            difficulty=4.5,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
            last_review=recent_review,
            last_review_time_ms=5000,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds_rec)

        # Anki: recognition suspended (queue=-1)
        cards = [
            make_card_record(
                anki_card_id=90010,
                ord=0,
                queue=-1,  # suspended in Anki
                reps=5,
                last_review_ms=1000,
            ),
        ]
        records = [make_note_record(anki_guid=guid, cards=cards)]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        updated = db.get_collocation_by_guid(guid)
        # Despite dirty_fsrs, Anki's suspend state is applied
        assert updated.directions[Direction.RECOGNITION].state == SRSState.SUSPENDED

    def test_count_review_due_not_inflated_by_buried_direction(self):
        """Regression: the review badge should not count buried directions.

        When a direction is buried in Anki (queue=-2) but TT has it as REVIEW
        with dirty_fsrs, the fix ensures the buried state is mirrored, so
        count_review_due_collocations won't overcount.
        """
        db = _make_tt_db()
        guid = _add_banka(db)

        today = date.today()

        # Recognition is dirty and has due_date <= today (would be counted as review-due)
        recent_review = _dt.combine(today, _time.min, tzinfo=UTC)
        ds_rec = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
            stability=5.0,
            difficulty=4.5,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
            last_review=recent_review,
            last_review_time_ms=5000,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds_rec)

        # Production is clean, also due today
        ds_prod = DirectionState(
            direction=Direction.PRODUCTION,
            due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
            stability=5.0,
            difficulty=4.5,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=False,
        )
        db.update_direction(guid, Direction.PRODUCTION, ds_prod)

        # Simulate Anki sync: recognition got buried, production stays review
        cards = [
            make_card_record(anki_card_id=90010, ord=0, queue=-2, due_date=today, last_review_ms=1000),
            make_card_record(anki_card_id=90011, ord=1, queue=2, due_date=today, last_review_ms=2000),
        ]
        records = [make_note_record(anki_guid=guid, cards=cards)]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        updated = db.get_collocation_by_guid(guid)
        assert updated.directions[Direction.RECOGNITION].state == SRSState.BURIED
        assert updated.directions[Direction.PRODUCTION].state == SRSState.REVIEW

        # Only 1 direction should be counted as review-due (not the buried one)
        review_count = sum(
            1 for d in updated.directions.values() if d.state == SRSState.REVIEW and d.due_at.date() <= today
        )
        assert review_count == 1  # Only production, not buried recognition


# ── Gap 2 regression: reviewed card (queue=2, reps=1) stuck as NEW ──────────


class TestGap1MissingPhonicsCards:
    """Regression for Gap 1: 13 phonics cards added 2026-03-27 never imported.

    The batch was added to Anki (nid range 1774631907157-1774631907195) but never
    landed in TunaTale's collocations table. Other phonics cards from earlier batches
    synced fine — same note type, same deck (did=1).

    Root cause: sync_pull only updates EXISTING TunaTale items. Notes not yet in
    TunaTale are skipped at line 544-545:
        local_item = self._db.get_collocation_by_guid(rec.anki_guid)
        if local_item is None:
            continue  # <-- SKIPS!

    The import step (import_seed.py or sync_create_new) must be run to add
    new Anki notes to TunaTale. If this step was missed or failed, the notes
    would never be imported.

    Another possibility: the user ran sync_pull thinking it would import new
    notes, but sync_pull skips new notes (by design).
    """

    def test_sync_pull_skips_notes_not_in_tt(self):
        """sync_pull skips notes that don't exist in TunaTale.

        This is expected behavior: sync_pull updates existing items, it doesn't
        import new ones. The import step must be done separately.
        """
        db = _make_tt_db()
        # Don't add anything to db

        new_guid = compute_guid("phonika", "sl", "")
        card = make_card_record(anki_card_id=99999, ord=0)
        records = [make_note_record(anki_guid=new_guid, l2_text="phonika", cards=[card])]

        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        assert report.notes_updated == 0
        assert report.directions_updated == 0
        assert report.skipped_unknown_guid == 0  # guid matches, just not in TT

    def test_import_seed_fetches_all_notes(self):
        """Verify that import_seed fetches ALL notes for the deck.

        This is a code review test to verify import_seed doesn't have a
        timestamp filter that would skip the 2026-03-27 batch.
        """
        from app.anki.import_seed import import_seed

        source = inspect.getsource(import_seed)
        # Verify it calls fetch_notes_for_deck (which has no timestamp filter)
        assert "fetch_notes_for_deck" in source
        # Verify no timestamp-based filtering
        lines = source.split("\n")
        for line in lines:
            if "mod" in line and ">" in line:
                pytest.fail(f"Found timestamp filter in import_seed: {line.strip()}")

    def test_gap1_likely_cause_missing_import_step(self):
        """The most likely cause of Gap 1: import_seed wasn't run after
        the batch was added to Anki.

        This isn't a code bug but a workflow issue. The fix could be:
        1. Document that new Anki notes require running import_seed
        2. Add detection of new Anki notes during sync_pull
        3. Auto-trigger import if new notes are detected
        """
        # This test documents the expected behavior
        assert True  # See AGENTS.md for import instructions


class TestSyncPullCardType:
    """Tests for card_type-aware state mapping in sync_pull.

    Anki uses queue=1 for both Learn (type=1) and Relearn (type=3) cards.
    TunaTale must distinguish them to match Anki's FSRS short-term scheduler.
    """

    def test_queue_1_type_3_maps_to_relearning(self):
        """queue=1 + card_type=3 (Anki Relearn) → SRSState.RELEARNING."""
        db = _make_tt_db()
        guid = _add_banka(db)
        item = db.get_collocation_by_guid(guid)
        assert item is not None

        # Simulate Anki card: queue=1, type=3 (Relearn)
        card = make_card_record(
            queue=1,
            card_type=3,  # Anki's CardType::Relearn
            reps=7,
            lapses=0,
            stability=0.086,
            difficulty=5.0,
        )
        records = [make_note_record(anki_guid=guid, cards=[card])]
        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        assert report.directions_updated == 1
        after = db.get_collocation_by_guid(guid)
        assert after is not None
        recog = after.directions[Direction.RECOGNITION]
        assert recog.state == SRSState.RELEARNING, f"Expected RELEARNING for queue=1 type=3, got {recog.state}"

    def test_queue_1_type_1_maps_to_learning(self):
        """queue=1 + card_type=1 (Anki Learn) → SRSState.LEARNING."""
        db = _make_tt_db()
        guid = _add_banka(db)
        item = db.get_collocation_by_guid(guid)
        assert item is not None

        # Simulate Anki card: queue=1, type=1 (Learn)
        # This is the rožnat case after the short-term promotion
        card = make_card_record(
            queue=1,
            card_type=1,  # Anki's CardType::Learn
            reps=18,
            lapses=1,
            stability=0.086,
            difficulty=5.0,
        )
        records = [make_note_record(anki_guid=guid, cards=[card])]
        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        assert report.directions_updated == 1
        after = db.get_collocation_by_guid(guid)
        assert after is not None
        recog = after.directions[Direction.RECOGNITION]
        assert recog.state == SRSState.LEARNING, f"Expected LEARNING for queue=1 type=1, got {recog.state}"

    def test_queue_1_default_type_0_maps_to_learning(self):
        """queue=1 + card_type=0 (default) → SRSState.LEARNING (current behavior)."""
        db = _make_tt_db()
        guid = _add_banka(db)

        # Simulate Anki card: queue=1, type=0 (New, unexpected but handle gracefully)
        card = make_card_record(
            queue=1,
            card_type=0,
            reps=1,
            lapses=0,
            stability=1.0,
            difficulty=5.0,
        )
        records = [make_note_record(anki_guid=guid, cards=[card])]
        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        assert report.directions_updated == 1
        after = db.get_collocation_by_guid(guid)
        assert after is not None
        recog = after.directions[Direction.RECOGNITION]
        # Default to LEARNING (safer than RELEARNING for type=0)
        assert recog.state == SRSState.LEARNING

    def test_anki_roznat_reproduction(self):
        """Reproduce the rožnat case: queue=1 type=1 after short-term promotion.

        Rožnat (anki_card_id=1775264031901):
        - Anki: queue=1, type=1, reps=18, lapses=1
        - Should map to LEARNING (not REVIEW as TT was doing)
        """
        db = _make_tt_db()
        guid = _add_banka(db)

        # Rožnat: queue=1, type=1 (Learn), reps=18, lapses=1
        card = make_card_record(
            anki_card_id=1775264031901,
            queue=1,
            card_type=1,  # Anki CardType::Learn (after short-term promotion)
            reps=18,
            lapses=1,
            stability=0.086,
            difficulty=5.0,
        )
        records = [make_note_record(anki_guid=guid, cards=[card])]
        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        assert report.directions_updated == 1
        after = db.get_collocation_by_guid(guid)
        assert after is not None
        recog = after.directions[Direction.RECOGNITION]
        # Should be LEARNING to match Anki's cards.type=1
        assert recog.state == SRSState.LEARNING, f"Rožnat should be LEARNING (type=1), got {recog.state}"
        # Should NOT be REVIEW (the original bug)
        assert recog.state != SRSState.REVIEW


class TestResolveIntroducedAt:
    """Layer 26 helper — stamps introduced_at once per intro arc."""

    def test_preserves_existing_introduced_at(self):
        """Sticky: if local already has introduced_at, return it unchanged."""
        from datetime import date as _date

        from app.anki.sync import _resolve_introduced_at
        from app.models.srs_item import Direction, DirectionState, SRSState

        existing = _dt(2026, 1, 15, 10, 30, tzinfo=UTC)
        local_dir = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(_date(2026, 5, 1), time(4, 0), tzinfo=UTC),
            state=SRSState.REVIEW,
            introduced_at=existing,
        )
        result = _resolve_introduced_at(local_dir, SRSState.REVIEW, first_review_ms=999_999_999_999)
        assert result == existing

    def test_returns_none_for_new_state(self):
        from datetime import date as _date

        from app.anki.sync import _resolve_introduced_at
        from app.models.srs_item import Direction, DirectionState, SRSState

        local_dir = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(_date(2026, 5, 1), time(4, 0), tzinfo=UTC),
            state=SRSState.NEW,
        )
        assert _resolve_introduced_at(local_dir, SRSState.NEW, first_review_ms=123) is None

    def test_returns_none_when_no_revlog(self):
        from datetime import date as _date

        from app.anki.sync import _resolve_introduced_at
        from app.models.srs_item import Direction, DirectionState, SRSState

        local_dir = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(_date(2026, 5, 1), time(4, 0), tzinfo=UTC),
            state=SRSState.NEW,
        )
        assert _resolve_introduced_at(local_dir, SRSState.REVIEW, first_review_ms=None) is None

    def test_stamps_from_first_revlog_when_local_is_null(self):
        from datetime import date as _date

        from app.anki.sync import _resolve_introduced_at
        from app.models.srs_item import Direction, DirectionState, SRSState

        local_dir = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(_date(2026, 5, 1), time(4, 0), tzinfo=UTC),
            state=SRSState.NEW,
        )
        first_ms = 1_700_000_000_000  # 2023-11-14
        result = _resolve_introduced_at(local_dir, SRSState.REVIEW, first_review_ms=first_ms)
        assert result == _dt.fromtimestamp(first_ms / 1000, tz=UTC)


class TestSyncPullIngestsAnkiRevlogIntoTtRevlog:
    """Stage 0: every sync_pull harvests Anki revlog rows into tt_revlog.

    Covers the loop body of ``_ingest_anki_revlog_for_card`` and the threshold
    parsing — the production Anki→TT event-sync path.
    """

    def _seed_anki_revlog(self, anki_db_path, cid: int, rows: list[tuple]) -> None:
        """Insert rows into the fake_anki_db's revlog table.

        Each row is (id_ms, ease, ivl, lastIvl, factor, time, type).
        """
        conn = sqlite3.connect(str(anki_db_path))
        for r in rows:
            conn.execute(
                "INSERT INTO revlog VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?)",
                (r[0], cid, *r[1:]),
            )
        conn.commit()
        conn.close()

    def _link_banka_to_card(self, db: SRSDatabase, guid: str, card_id: int) -> None:
        db.set_anki_ids(guid, note_id=1001, card_ids={Direction.RECOGNITION: card_id})

    def test_revlog_rows_appear_in_tt_revlog_after_pull(self, fake_anki_db):
        db = _make_tt_db()
        guid = _add_banka(db)
        cid = 10010  # rec card for note 1001 in fake_anki_db
        self._link_banka_to_card(db, guid, cid)
        self._seed_anki_revlog(
            fake_anki_db,
            cid,
            [
                # (id_ms, ease, ivl, lastIvl, factor, time, type)
                (1_700_000_000_000, 3, 1, 0, 0, 4500, 0),  # Learn
                (1_700_000_500_000, 3, 10, 1, 2500, 3200, 1),  # Review
            ],
        )
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row  # production uses safe_open which sets this
        try:
            AnkiSync(db=db, _reader=OfflineReader(conn, "0. Slovene"), _writer=FakeWriter()).sync_pull()
        finally:
            conn.close()

        with db._get_conn() as tt_conn:
            rows = tt_conn.execute(
                "SELECT id, button_chosen, interval, last_interval, factor, taken_millis, review_kind, anki_card_id "
                "FROM tt_revlog WHERE anki_card_id = ? ORDER BY id",
                (cid,),
            ).fetchall()
        assert len(rows) == 2
        assert rows[0]["id"] == 1_700_000_000_000
        assert rows[0]["button_chosen"] == 3
        assert rows[0]["review_kind"] == 0  # Learn
        assert rows[1]["id"] == 1_700_000_500_000
        assert rows[1]["review_kind"] == 1  # Review
        assert rows[1]["taken_millis"] == 3200

    def test_ingest_is_idempotent_across_repeated_pulls(self, fake_anki_db):
        db = _make_tt_db()
        guid = _add_banka(db)
        cid = 10010
        self._link_banka_to_card(db, guid, cid)
        self._seed_anki_revlog(fake_anki_db, cid, [(1_700_000_000_000, 3, 1, 0, 0, 4500, 0)])
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        try:
            sync = AnkiSync(db=db, _reader=OfflineReader(conn, "0. Slovene"), _writer=FakeWriter())
            sync.sync_pull()
            sync.sync_pull()
        finally:
            conn.close()

        with db._get_conn() as tt_conn:
            count = tt_conn.execute("SELECT COUNT(*) FROM tt_revlog WHERE anki_card_id = ?", (cid,)).fetchone()[0]
        assert count == 1, "INSERT OR IGNORE on PK must dedupe across repeated pulls"

    def test_ingest_ignores_last_synced_at_and_backfills_older_rows(self, fake_anki_db):
        """Ingest reconciles against Anki's full revlog, NOT a last_synced_at watermark.

        Regression (Stage 3b soak, 2026-05-27): the old ``id > last_synced_at``
        filter dropped any grade older than the watermark. A grade made during a
        multi-day sync gap could land *interior* to the ids already held and be
        skipped permanently, silently understating the event-sourced FSRS replay.
        Both rows must ingest regardless of how far last_synced_at has advanced.
        """
        db = _make_tt_db()
        guid = _add_banka(db)
        cid = 10010
        self._link_banka_to_card(db, guid, cid)
        # Watermark advanced well past BOTH grades — under the old filter this
        # would have skipped them both.
        cutoff = _dt(2026, 5, 19, 0, 0, 0, tzinfo=UTC)
        cutoff_ms = int(cutoff.timestamp() * 1000)
        item = db.get_collocation_by_guid(guid)
        rec = item.directions[Direction.RECOGNITION]
        rec.last_synced_at = cutoff.isoformat()
        db.update_direction(guid, Direction.RECOGNITION, rec)

        self._seed_anki_revlog(
            fake_anki_db,
            cid,
            [
                (cutoff_ms - 120_000, 3, 1, 0, 0, 4500, 0),  # 2 min before cutoff — still ingests
                (cutoff_ms - 60_000, 3, 10, 1, 2500, 3200, 1),  # 1 min before cutoff — still ingests
            ],
        )
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row  # production uses safe_open which sets this
        try:
            AnkiSync(db=db, _reader=OfflineReader(conn, "0. Slovene"), _writer=FakeWriter()).sync_pull()
        finally:
            conn.close()

        with db._get_conn() as tt_conn:
            ids = [
                r["id"]
                for r in tt_conn.execute(
                    "SELECT id FROM tt_revlog WHERE anki_card_id = ? ORDER BY id", (cid,)
                ).fetchall()
            ]
        assert ids == [cutoff_ms - 120_000, cutoff_ms - 60_000]

    def test_interior_revlog_gap_is_backfilled(self, fake_anki_db):
        """A grade older than the newest already-ingested row is still backfilled.

        Real incident (gor/zahod, 2026-05-27): a Good was graded in a ~41h sync
        gap, *between* two grades TT already held. The old wall-clock watermark
        skipped it permanently because its id sat below last_synced_at, while the
        newer grade ingested fine — leaving an interior hole that made the
        event-sourced replay understate stability. Ingest must reconcile the
        card's full Anki revlog against the ids it holds and fill the hole.
        """
        from app.models.srs_item import RevlogRow

        db = _make_tt_db()
        guid = _add_banka(db)
        cid = 10010
        self._link_banka_to_card(db, guid, cid)
        coll_id = db.get_collocation_id_by_guid(guid)

        first_ms, interior_ms, last_ms = 1_700_000_000_000, 1_700_000_100_000, 1_700_000_200_000
        # TT already holds the FIRST and LAST grades from a prior sync.
        for gid, ease, kind in ((first_ms, 1, 0), (last_ms, 3, 1)):
            db.append_revlog(
                RevlogRow(
                    id=gid,
                    collocation_id=coll_id,
                    direction=Direction.RECOGNITION,
                    button_chosen=ease,
                    interval=0,
                    last_interval=0,
                    factor=0,
                    taken_millis=4500,
                    review_kind=kind,
                    anki_card_id=cid,
                )
            )
        # Watermark advanced to the newest held grade — the bug's precondition.
        item = db.get_collocation_by_guid(guid)
        rec = item.directions[Direction.RECOGNITION]
        rec.last_synced_at = _dt.fromtimestamp(last_ms / 1000, tz=UTC).isoformat()
        db.update_direction(guid, Direction.RECOGNITION, rec)

        # Anki has all three, including the interior grade TT never ingested.
        self._seed_anki_revlog(
            fake_anki_db,
            cid,
            [
                (first_ms, 1, 1, 0, 0, 4500, 0),
                (interior_ms, 3, 1, 1, 0, 4500, 0),  # interior — dropped by the old filter
                (last_ms, 3, 10, 1, 2500, 3200, 1),
            ],
        )
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        try:
            AnkiSync(db=db, _reader=OfflineReader(conn, "0. Slovene"), _writer=FakeWriter()).sync_pull()
        finally:
            conn.close()

        with db._get_conn() as tt_conn:
            ids = [
                r["id"]
                for r in tt_conn.execute(
                    "SELECT id FROM tt_revlog WHERE anki_card_id = ? ORDER BY id", (cid,)
                ).fetchall()
            ]
        assert ids == [first_ms, interior_ms, last_ms], "interior sync-gap grade must be backfilled"

    def test_skips_anki_row_that_duplicates_tt_grade(self, fake_anki_db):
        """A TT-written grade row already in tt_revlog suppresses the Anki copy.

        When the user grades a card in TT (Stage 0 writes the revlog row with
        ``id = now_ms``), and the same grade later round-trips through Anki, the
        Anki revlog row has a different ``id`` (different millisecond timestamp).
        The dedup heuristic: same direction, within 5s, same ease — skip import.
        """
        from app.models.srs_item import RevlogRow

        db = _make_tt_db()
        guid = _add_banka(db)
        cid = 10010
        self._link_banka_to_card(db, guid, cid)
        # TT writes its own row first (simulating a TT-side grade).
        coll_id = db.get_collocation_id_by_guid(guid)
        tt_grade_ms = 1_700_000_000_000
        db.append_revlog(
            RevlogRow(
                id=tt_grade_ms,
                collocation_id=coll_id,
                direction=Direction.RECOGNITION,
                button_chosen=3,
                interval=10,
                last_interval=1,
                factor=0,
                taken_millis=4500,
                review_kind=1,
                anki_card_id=cid,
            )
        )
        # Anki's revlog has the same event at a different millisecond (+2000ms).
        self._seed_anki_revlog(
            fake_anki_db,
            cid,
            [(tt_grade_ms + 2000, 3, 10, 1, 0, 4500, 1)],
        )
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        try:
            AnkiSync(db=db, _reader=OfflineReader(conn, "0. Slovene"), _writer=FakeWriter()).sync_pull()
        finally:
            conn.close()

        with db._get_conn() as tt_conn:
            rows = tt_conn.execute(
                "SELECT id FROM tt_revlog WHERE anki_card_id = ? ORDER BY id",
                (cid,),
            ).fetchall()
        assert [r["id"] for r in rows] == [tt_grade_ms], "Anki copy within 5s of TT row with same ease must be skipped"

    def test_ingest_keeps_anki_row_when_ease_differs(self, fake_anki_db):
        """Within 5s but *different* ease → not the same event; keep both."""
        from app.models.srs_item import RevlogRow

        db = _make_tt_db()
        guid = _add_banka(db)
        cid = 10010
        self._link_banka_to_card(db, guid, cid)
        coll_id = db.get_collocation_id_by_guid(guid)
        tt_grade_ms = 1_700_000_000_000
        db.append_revlog(
            RevlogRow(
                id=tt_grade_ms,
                collocation_id=coll_id,
                direction=Direction.RECOGNITION,
                button_chosen=1,  # AGAIN
                interval=0,
                last_interval=0,
                factor=0,
                taken_millis=4500,
                review_kind=0,
                anki_card_id=cid,
            )
        )
        # Different ease — legitimate separate grade, must be kept.
        self._seed_anki_revlog(fake_anki_db, cid, [(tt_grade_ms + 2000, 3, 10, 1, 0, 4500, 1)])
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        try:
            AnkiSync(db=db, _reader=OfflineReader(conn, "0. Slovene"), _writer=FakeWriter()).sync_pull()
        finally:
            conn.close()

        with db._get_conn() as tt_conn:
            ids = [
                r["id"]
                for r in tt_conn.execute(
                    "SELECT id FROM tt_revlog WHERE anki_card_id = ? ORDER BY id", (cid,)
                ).fetchall()
            ]
        assert ids == [tt_grade_ms, tt_grade_ms + 2000]

    def test_ingest_keeps_distinct_anki_grades_within_5s_same_ease(self, fake_anki_db):
        """Two distinct *Anki* grades <5s apart with the same ease both ingest.

        Layer 60 regression (live, 2026-05-29 phone session — samoglasnik /
        pridevnik): a rapid learning sequence puts two genuine Anki grades a few
        seconds apart with the same button. The near-match guard wrongly treated
        the first (already ingested this pass) as a TT-written mirror of the
        second and dropped it, understating the event-sourced replay. The guard
        must only suppress against TT-*written* rows, never against an
        already-ingested Anki row — both Anki rows are real and Anki replays both.
        """
        db = _make_tt_db()
        guid = _add_banka(db)
        cid = 10010
        self._link_banka_to_card(db, guid, cid)
        # No TT-written row. Two Anki grades, same ease (Good), 3s apart.
        base = 1_700_000_000_000
        self._seed_anki_revlog(
            fake_anki_db,
            cid,
            [
                (base, 3, 1, 0, 0, 4500, 0),  # 22:38:50-style Good
                (base + 3000, 3, 10, 1, 2500, 3200, 0),  # 22:38:53-style Good, 3s later
            ],
        )
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        try:
            AnkiSync(db=db, _reader=OfflineReader(conn, "0. Slovene"), _writer=FakeWriter()).sync_pull()
        finally:
            conn.close()

        with db._get_conn() as tt_conn:
            ids = [
                r["id"]
                for r in tt_conn.execute(
                    "SELECT id FROM tt_revlog WHERE anki_card_id = ? ORDER BY id", (cid,)
                ).fetchall()
            ]
        assert ids == [base, base + 3000], "both distinct Anki grades must ingest, not be deduped against each other"

    def test_malformed_last_synced_at_falls_back_to_all_rows(self, fake_anki_db):
        """Defensive: a corrupt last_synced_at string falls back to threshold=0
        (ingest everything), not a crash."""
        db = _make_tt_db()
        guid = _add_banka(db)
        cid = 10010
        self._link_banka_to_card(db, guid, cid)
        item = db.get_collocation_by_guid(guid)
        rec = item.directions[Direction.RECOGNITION]
        rec.last_synced_at = "not-a-datetime"
        db.update_direction(guid, Direction.RECOGNITION, rec)

        self._seed_anki_revlog(fake_anki_db, cid, [(1_700_000_000_000, 3, 1, 0, 0, 4500, 0)])
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row  # production uses safe_open which sets this
        try:
            AnkiSync(db=db, _reader=OfflineReader(conn, "0. Slovene"), _writer=FakeWriter()).sync_pull()
        finally:
            conn.close()

        with db._get_conn() as tt_conn:
            count = tt_conn.execute("SELECT COUNT(*) FROM tt_revlog WHERE anki_card_id = ?", (cid,)).fetchone()[0]
        assert count == 1


# ── Layer 70: pull recency guard ───────────────────────────────────────────────


class TestPullRecencyGuardLayer70:
    """Pull must not revert a TT grade with Anki's stale memory state.

    The cid=428 loss (2026-06-10): sync_push writes scheduling + revlog for a
    TT grade but not ``cards.data``, and clears ``dirty_fsrs`` before pull runs
    in the same sync. Pull's non-dirty fsrs_known branch then took Anki's stale
    s/d/lrt unconditionally, reverting the grade's FSRS effect on both sides.
    The guard: when TT's ``last_review`` postdates Anki's memory-state
    timestamp (``card_rec.last_review``, lrt-derived), keep TT's memory state;
    scheduling fields stay pass-through from Anki.
    """

    _GRADED_AT = datetime(2026, 6, 10, 21, 4, 8, tzinfo=UTC)
    _STALE_LRT = datetime(2026, 6, 1, 2, 49, 4, tzinfo=UTC)

    def _seed_clean_graded(
        self,
        db: SRSDatabase,
        guid: str,
        *,
        last_review: datetime,
        stability: float = 18.2671,
        difficulty: float = 8.883,
        state: SRSState = SRSState.REVIEW,
    ) -> DirectionState:
        """Seed banka RECOGNITION as a just-pushed TT grade (clean, dirty_fsrs=0)."""
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=last_review + timedelta(days=27),
            stability=stability,
            difficulty=difficulty,
            reps=12,
            lapses=2,
            state=state,
            dirty_fsrs=False,
            anki_card_id=90010,
            last_review=last_review,
            last_review_time_ms=int(last_review.timestamp() * 1000),
            last_rating=3,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)
        return ds

    def test_stale_anki_memory_keeps_local_fsrs(self):
        """Anki lrt 9 days behind the TT grade → local s/d/last_review survive."""
        db = _make_tt_db()
        guid = _add_banka(db)
        self._seed_clean_graded(db, guid, last_review=self._GRADED_AT)

        card = make_card_record(
            anki_card_id=90010,
            ord=0,
            reps=13,
            lapses=2,
            stability=8.2442,
            difficulty=8.385,
            last_review=self._STALE_LRT,
        )
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        after = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert after.stability == 18.2671
        assert after.difficulty == 8.883
        assert after.last_review == self._GRADED_AT
        assert after.last_rating == 3

    def test_stale_anki_memory_still_takes_anki_scheduling(self):
        """Guard keeps memory state only; reps/lapses/due_at/state come from Anki."""
        db = _make_tt_db()
        guid = _add_banka(db)
        self._seed_clean_graded(db, guid, last_review=self._GRADED_AT)

        anki_due_at = datetime.combine(date.today() + timedelta(days=27), time(4, 0), tzinfo=UTC)
        card = make_card_record(
            anki_card_id=90010,
            ord=0,
            reps=13,
            lapses=2,
            stability=8.2442,
            difficulty=8.385,
            last_review=self._STALE_LRT,
            due_at=anki_due_at,
            anki_due=900,
            anki_card_mod=1_781_000_000,
        )
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        after = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert after.reps == 13
        assert after.lapses == 2
        assert after.due_at == anki_due_at
        assert after.anki_due == 900
        assert after.anki_card_mod == 1_781_000_000
        assert after.state == SRSState.REVIEW
        assert after.dirty_fsrs is False

    def test_stale_anki_memory_maps_queue_to_state(self):
        """Guard + queue=1/type=3 (mid relearning arc): local s/d kept, state RELEARNING."""
        db = _make_tt_db()
        guid = _add_banka(db)
        self._seed_clean_graded(db, guid, last_review=self._GRADED_AT, stability=1.8681, state=SRSState.RELEARNING)

        card = make_card_record(
            anki_card_id=90010,
            ord=0,
            queue=1,
            card_type=3,
            reps=13,
            lapses=2,
            stability=8.2442,
            difficulty=8.385,
            last_review=self._STALE_LRT,
            left=1001,
        )
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        after = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert after.stability == 1.8681
        assert after.state == SRSState.RELEARNING
        assert after.left == 1001

    def test_fresh_anki_memory_takes_anki(self):
        """Anki lrt newer than the TT grade → Anki's memory state wins (unchanged)."""
        db = _make_tt_db()
        guid = _add_banka(db)
        anki_lrt = self._GRADED_AT + timedelta(days=1)
        self._seed_clean_graded(db, guid, last_review=self._GRADED_AT)

        card = make_card_record(
            anki_card_id=90010,
            ord=0,
            reps=13,
            stability=8.2442,
            difficulty=8.385,
            last_review=anki_lrt,
        )
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        after = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert after.stability == 8.2442
        assert after.difficulty == 8.385
        assert after.last_review == anki_lrt

    def test_missing_anki_last_review_takes_anki(self):
        """No Anki timestamp at all → conservative take-Anki (unchanged)."""
        db = _make_tt_db()
        guid = _add_banka(db)
        self._seed_clean_graded(db, guid, last_review=self._GRADED_AT)

        card = make_card_record(anki_card_id=90010, ord=0, reps=13, stability=8.2442, difficulty=8.385)
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        after = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert after.stability == 8.2442

    def test_equal_timestamps_take_anki(self):
        """lrt == TT last_review (same grade already round-tripped) → take Anki."""
        db = _make_tt_db()
        guid = _add_banka(db)
        self._seed_clean_graded(db, guid, last_review=self._GRADED_AT)

        card = make_card_record(
            anki_card_id=90010,
            ord=0,
            reps=13,
            stability=8.2442,
            difficulty=8.385,
            last_review=self._GRADED_AT,
        )
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        after = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert after.stability == 8.2442

    def test_day_level_local_timestamp_does_not_block_take_anki(self):
        """Layer 72 (upogniti, 2026-06-12): a midnight-UTC local last_review is
        parse_fsrs_data's day-level reconstruction (due - ivl, no lrt) round-
        tripped back from Anki — not a TT grade time. Day truncation can
        overshoot the real grade by up to 24h, so it may postdate Anki's lrt;
        the guard must NOT read that as "TT graded later" or a placeholder
        s/d gets protected against every future pull, permanently."""
        db = _make_tt_db()
        guid = _add_banka(db)
        midnight = datetime(2026, 5, 21, 0, 0, 0, tzinfo=UTC)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=midnight + timedelta(days=23),
            stability=1.0,
            difficulty=5.0,
            reps=1,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=False,
            anki_card_id=90010,
            last_review=midnight,
            last_review_time_ms=0,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        # Anki's real lrt: the actual grade, ~8h BEFORE the midnight stamp.
        anki_lrt = datetime(2026, 5, 20, 16, 8, 36, tzinfo=UTC)
        card = make_card_record(
            anki_card_id=90010,
            ord=0,
            reps=1,
            stability=4.8369,
            difficulty=3.379,
            last_review=anki_lrt,
        )
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        after = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert after.stability == 4.8369
        assert after.difficulty == 3.379
        assert after.last_review == anki_lrt

    def test_local_never_graded_takes_anki(self):
        """Local last_review is NULL (never TT-graded) → take Anki (unchanged)."""
        db = _make_tt_db()
        guid = _add_banka(db)

        card = make_card_record(
            anki_card_id=90010,
            ord=0,
            reps=13,
            stability=8.2442,
            difficulty=8.385,
            last_review=self._STALE_LRT,
        )
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        after = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert after.stability == 8.2442
