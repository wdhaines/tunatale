"""Tests for S3.4: sync pull (Anki → TunaTale)."""

from __future__ import annotations

import inspect
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, date, timedelta
from datetime import datetime as _dt
from datetime import time as _time

import httpx
import pytest

from app.anki.anki_connect import AnkiConnectClient
from app.anki.sync import (
    AnkiSync,
    NoteRecord,
    OfflineReader,
    _direction_differs,
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
        self, *, cid: int, ease: int, ivl: int, last_ivl: int, factor: int, time_ms: int, type_: int
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


class FakeReader:
    def __init__(self, records: list[NoteRecord]):
        self._records = records

    def get_note_records(self) -> list[NoteRecord]:
        return self._records


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


# ── Additional tests ──────────────────────────────────────────────────────────


class DispatchTransport(httpx.BaseTransport):
    def __init__(self, handlers: dict):
        self._handlers = handlers

    def handle_request(self, request):
        body = json.loads(request.content)
        action = body["action"]
        result = self._handlers[action](body.get("params", {}))
        return httpx.Response(200, json={"result": result, "error": None})


def _online_client(handlers: dict) -> AnkiConnectClient:
    return AnkiConnectClient(http_client=httpx.Client(transport=DispatchTransport(handlers)))


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

    def test_dirty_fsrs_local_wins_pull_does_not_overwrite(self):
        """local dirty_fsrs + Anki has FSRS data → local wins, no conflict, dirty preserved."""
        db = _make_tt_db()
        guid = _add_banka(db)

        # Mark recognition direction dirty_fsrs via update_direction
        item = db.get_collocation_by_guid(guid)
        ds_dirty = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=item.directions[Direction.RECOGNITION].due_date,
            stability=5.0,
            difficulty=4.8,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds_dirty)

        card = make_card_record(anki_card_id=90010, ord=0, reps=7, lapses=1, stability=15.0, difficulty=4.5)
        records = [make_note_record(anki_guid=guid, cards=[card])]
        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        # No conflict — dirty local data is queued work, not a real divergence
        assert report.conflicts == []
        assert db.list_sync_conflicts() == []
        # Local FSRS state preserved
        updated = db.get_collocation_by_guid(guid)
        assert updated.directions[Direction.RECOGNITION].reps == 3
        assert updated.directions[Direction.RECOGNITION].stability == 5.0
        assert updated.directions[Direction.RECOGNITION].dirty_fsrs is True

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
            due_date=item.directions[Direction.RECOGNITION].due_date,
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

    def test_fsrs_known_false_preserves_local_fsrs_state(self):
        """CardRecord.fsrs_known=False (online reader): sync_pull must not overwrite
        local stability/difficulty/due_date or record a conflict."""
        from datetime import timedelta

        db = _make_tt_db()
        guid = _add_banka(db)
        local_due = date.today() + timedelta(days=42)
        ds_local = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=local_due,
            stability=12.0,
            difficulty=4.5,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds_local)

        card = make_card_record(anki_card_id=90010, ord=0, fsrs_known=False, stability=0.0, difficulty=0.0)
        records = [make_note_record(anki_guid=guid, cards=[card])]
        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        assert report.conflicts == []
        updated = db.get_collocation_by_guid(guid)
        rec = updated.directions[Direction.RECOGNITION]
        assert rec.stability == 12.0
        assert rec.difficulty == 4.5
        assert rec.due_date == local_due
        assert rec.dirty_fsrs is True  # still dirty — push can flush

    def test_fsrs_known_false_still_applies_suspension(self):
        """fsrs_known=False must still pick up queue-based state changes (e.g. suspension)."""
        db = _make_tt_db()
        guid = _add_banka(db)

        card = make_card_record(anki_card_id=90010, ord=0, queue=-1, fsrs_known=False, stability=0.0, difficulty=0.0)
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()
        updated = db.get_collocation_by_guid(guid)
        assert updated.directions[Direction.RECOGNITION].state == SRSState.SUSPENDED


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
                due_date=today,
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
            due_date=today,
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
        # fsrs_known=False path
        (False, -2, 4, SRSState.BURIED),
        (False, 1, 2, SRSState.LEARNING),
        (False, 3, 5, SRSState.RELEARNING),
        (False, 0, 0, SRSState.NEW),
        (False, 0, 5, SRSState.REVIEW),
        # fsrs_known=True path (adds queue=-3)
        (True, -2, 4, SRSState.BURIED),
        (True, -3, 4, SRSState.BURIED),
        (True, 1, 2, SRSState.LEARNING),
        (True, 3, 5, SRSState.RELEARNING),
        (True, 0, 0, SRSState.NEW),
        (True, 0, 5, SRSState.REVIEW),
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


# ── _factor_to_fsrs_difficulty ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "factor,expected",
    [
        (2500, pytest.approx(4.545, abs=0.01)),
        (1300, 10.0),
        (3500, 1.0),
        (1450, pytest.approx(9.318, abs=0.01)),
    ],
)
def test_factor_to_fsrs_difficulty(factor, expected):
    from app.anki.sync import _factor_to_fsrs_difficulty

    assert _factor_to_fsrs_difficulty(factor) == expected


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

    def test_pull_preserves_anki_due_on_dirty_fsrs(self):
        """When local_dir.dirty_fsrs==True, sync still updates anki_due from remote."""
        db = _make_tt_db()
        guid = _add_banka(db)

        # Set local direction as dirty
        item = db.get_collocation("banka")
        rec_dir = item.directions[Direction.RECOGNITION]
        rec_dir.dirty_fsrs = True
        db.update_direction(guid, Direction.RECOGNITION, rec_dir)

        # Remote has anki_due=842
        card = make_card_record(anki_card_id=90010, ord=0, queue=0, reps=0, stability=1.0, difficulty=5.0, anki_due=842)
        records = [make_note_record(anki_guid=guid, cards=[card])]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()
        reloaded = db.get_collocation("banka")
        # anki_due should be updated even though dirty_fsrs is True
        assert reloaded.directions[Direction.RECOGNITION].anki_due == 842
        # FSRS state should be preserved (dirty_fsrs)
        assert reloaded.directions[Direction.RECOGNITION].dirty_fsrs is True

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


class TestDirectionDiffersDetectsLastReviewTransition:
    def test_direction_differs_detects_last_review_transition(self):
        """None → datetime transition detected by _direction_differs."""
        from dataclasses import replace
        from datetime import datetime as _dt
        from datetime import time as _time

        local = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
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
            due_date=date.today(),
            stability=7.5,
            difficulty=4.8,
            reps=5,
            lapses=0,
            state=SRSState.REVIEW,
            last_review=_dt.combine(date(2024, 1, 11), _time.min, tzinfo=UTC),
        )

        assert _direction_differs(ds, ds) is False


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
            due_date=date.today(),
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
            due_date=date.today(),
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
            due_date=date.today(),
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
        """Regression: count_review_due should not count buried directions.

        When a direction is buried in Anki (queue=-2) but TT has it as REVIEW
        with dirty_fsrs, the fix ensures the buried state is mirrored, so
        count_review_due won't overcount.
        """
        db = _make_tt_db()
        guid = _add_banka(db)

        today = date.today()

        # Recognition is dirty and has due_date <= today (would be counted as review-due)
        recent_review = _dt.combine(today, _time.min, tzinfo=UTC)
        ds_rec = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=today,
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
            due_date=today,
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
        review_count = sum(1 for d in updated.directions.values() if d.state == SRSState.REVIEW and d.due_date <= today)
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
