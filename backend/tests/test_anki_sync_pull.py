"""Tests for S3.4: sync pull (Anki → TunaTale)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, date, timedelta

import httpx
import pytest

from app.anki.anki_connect import AnkiConnectClient
from app.anki.sync import (
    AnkiSync,
    CardRecord,
    NoteRecord,
    OfflineReader,
    _direction_differs,
)
from app.common.guid import compute_guid
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase


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

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank (financial)",
                disambig_key="",
                mod=0,
                cards=[],
            )
        ]
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

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank (financial)",
                disambig_key="",
                mod=0,
                cards=[],
            )
        ]
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

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank (financial)",
                disambig_key="",
                mod=0,
                cards=[],
            )
        ]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        assert db.get_dirty_fields(guid) == ""

    def test_suspend_recognition_leaves_production_untouched(self):
        """Anki suspends ord=0 → RECOGNITION=SUSPENDED, PRODUCTION unchanged."""
        db = _make_tt_db()
        guid = _add_banka(db)

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=-1,
                        reps=5,
                        lapses=0,
                        stability=10.5,
                        difficulty=4.8,
                        due_date=date.today(),
                    ),
                    CardRecord(
                        anki_card_id=90011,
                        ord=1,
                        queue=2,
                        reps=3,
                        lapses=0,
                        stability=5.2,
                        difficulty=5.1,
                        due_date=date.today(),
                    ),
                ],
            )
        ]
        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        assert report.directions_updated == 2
        item = db.get_collocation_by_guid(guid)
        assert item.directions[Direction.RECOGNITION].state == SRSState.SUSPENDED
        assert item.directions[Direction.PRODUCTION].state != SRSState.SUSPENDED

    def test_dry_run_does_not_write(self):
        """dry_run=True reports planned updates without touching the DB."""
        db = _make_tt_db()
        guid = _add_banka(db)

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="NEW TRANSLATION",
                disambig_key="",
                mod=0,
                cards=[],
            )
        ]
        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull(dry_run=True)

        assert report.notes_updated == 1
        # DB unchanged
        item = db.get_collocation("banka")
        assert item.syntactic_unit.translation == "bank"

    def test_unknown_guid_increments_skip_count(self):
        """anki_guid != compute_guid(l2_text) → skipped, no DB write."""
        db = _make_tt_db()
        _add_banka(db)

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid="wrong_guid_xyz",
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[],
            )
        ]
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

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=2,
                        reps=7,
                        lapses=1,
                        stability=15.0,
                        difficulty=4.5,
                        due_date=date.today(),
                    ),
                ],
            )
        ]
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

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",  # same as local
                disambig_key="",
                mod=0,
                cards=[],
            )
        ]
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

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,  # valid GUID but not in TT DB
                l2_text="jabolko",
                translation="apple",
                disambig_key="",
                mod=0,
                cards=[],
            )
        ]
        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        assert report.notes_updated == 0
        assert report.skipped_unknown_guid == 0

    def test_dry_run_conflict_not_written_to_db(self):
        """dry_run=True with conflict → conflict in report but NOT in db.list_sync_conflicts()."""
        db = _make_tt_db()
        guid = _add_banka(db)
        db.set_dirty_fields(guid, "translation")

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank (financial)",
                disambig_key="",
                mod=0,
                cards=[],
            )
        ]
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

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=2,
                        reps=9,
                        lapses=1,
                        stability=20.0,
                        difficulty=4.0,
                        due_date=date.today(),
                    ),
                ],
            )
        ]
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

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90011,
                        ord=1,  # production, which was deleted from DB
                        queue=2,
                        reps=3,
                        lapses=0,
                        stability=5.0,
                        difficulty=5.0,
                        due_date=date.today(),
                    ),
                ],
            )
        ]
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

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=2,
                        reps=3,
                        lapses=0,
                        stability=0.0,  # placeholder — fsrs_known=False
                        difficulty=0.0,
                        due_date=date.today(),
                        fsrs_known=False,
                    ),
                ],
            )
        ]
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

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=-1,
                        reps=3,
                        lapses=0,
                        stability=0.0,
                        difficulty=0.0,
                        due_date=date.today(),
                        fsrs_known=False,
                    ),
                ],
            )
        ]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()
        updated = db.get_collocation_by_guid(guid)
        assert updated.directions[Direction.RECOGNITION].state == SRSState.SUSPENDED

    def test_fsrs_known_false_maps_queue_minus_2_to_buried(self):
        """queue=-2 (user-buried) → SRSState.BURIED even when fsrs_known=False."""
        db = _make_tt_db()
        guid = _add_banka(db)

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=-2,
                        reps=4,
                        lapses=0,
                        stability=0.0,
                        difficulty=0.0,
                        due_date=date.today(),
                        fsrs_known=False,
                    ),
                ],
            )
        ]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()
        updated = db.get_collocation_by_guid(guid)
        assert updated.directions[Direction.RECOGNITION].state == SRSState.BURIED

    def test_fsrs_known_false_maps_queue_1_to_learning(self):
        """queue=1 (learning) → SRSState.LEARNING even when fsrs_known=False."""
        db = _make_tt_db()
        guid = _add_banka(db)
        today = date.today()

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=1,
                        reps=2,
                        lapses=0,
                        stability=0.0,
                        difficulty=0.0,
                        due_date=today,
                        fsrs_known=False,
                    ),
                ],
            )
        ]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()
        updated = db.get_collocation_by_guid(guid)
        assert updated.directions[Direction.RECOGNITION].state == SRSState.LEARNING

    def test_fsrs_known_false_maps_queue_3_to_relearning(self):
        """queue=3 (day-learn) → SRSState.RELEARNING even when fsrs_known=False."""
        db = _make_tt_db()
        guid = _add_banka(db)
        today = date.today()

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=3,
                        reps=5,
                        lapses=1,
                        stability=0.0,
                        difficulty=0.0,
                        due_date=today,
                        fsrs_known=False,
                    ),
                ],
            )
        ]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()
        updated = db.get_collocation_by_guid(guid)
        assert updated.directions[Direction.RECOGNITION].state == SRSState.RELEARNING

    def test_fsrs_known_false_maps_queue_0_reps_0_to_new(self):
        """queue=0, reps=0 → SRSState.NEW when fsrs_known=False."""
        db = _make_tt_db()
        guid = _add_banka(db)

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=0,
                        reps=0,
                        lapses=0,
                        stability=0.0,
                        difficulty=0.0,
                        due_date=date.today(),
                        fsrs_known=False,
                    ),
                ],
            )
        ]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()
        updated = db.get_collocation_by_guid(guid)
        assert updated.directions[Direction.RECOGNITION].state == SRSState.NEW

    def test_fsrs_known_false_maps_queue_0_reps_gt_0_to_review(self):
        """queue=0, reps>0 → SRSState.REVIEW when fsrs_known=False."""
        db = _make_tt_db()
        guid = _add_banka(db)

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=0,
                        reps=5,
                        lapses=0,
                        stability=0.0,
                        difficulty=0.0,
                        due_date=date.today(),
                        fsrs_known=False,
                    ),
                ],
            )
        ]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()
        updated = db.get_collocation_by_guid(guid)
        assert updated.directions[Direction.RECOGNITION].state == SRSState.REVIEW


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

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=2,
                        reps=3,
                        lapses=0,
                        stability=5.0,
                        difficulty=4.5,
                        due_date=today,
                        fsrs_known=True,
                    ),
                    CardRecord(
                        anki_card_id=90011,
                        ord=1,
                        queue=2,
                        reps=3,
                        lapses=0,
                        stability=5.0,
                        difficulty=4.5,
                        due_date=today,
                        fsrs_known=True,
                    ),
                ],
            )
        ]

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

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=2,
                        reps=3,
                        lapses=0,
                        stability=8.0,  # changed from 5.0
                        difficulty=4.5,
                        due_date=today,
                        fsrs_known=True,
                    ),
                ],
            )
        ]

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
            NoteRecord(
                anki_note_id=NID_A,
                anki_guid=guid,
                l2_text="banka",
                translation="carry",
                disambig_key="",
                mod=0,
                cards=[],
            ),
            NoteRecord(
                anki_note_id=NID_B,
                anki_guid=guid,
                l2_text="banka",
                translation="wear",
                disambig_key="",
                mod=0,
                cards=[],
            ),
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

        records = [
            NoteRecord(
                anki_note_id=8001,
                anki_guid=guid,
                l2_text="banka",
                translation="savings bank",
                disambig_key="",
                mod=0,
                cards=[],
            )
        ]

        report = AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        assert report.notes_updated == 1
        item = db.get_collocation("banka")
        assert item.syntactic_unit.translation == "savings bank"


# ── _factor_to_fsrs_difficulty ────────────────────────────────────────────────


class TestFactorToFsrsDifficulty:
    def test_midpoint_factor_2500(self):
        from app.anki.sync import _factor_to_fsrs_difficulty

        assert _factor_to_fsrs_difficulty(2500) == pytest.approx(4.545, abs=0.01)

    def test_hard_clamp(self):
        from app.anki.sync import _factor_to_fsrs_difficulty

        assert _factor_to_fsrs_difficulty(1300) == 10.0

    def test_easy_clamp(self):
        from app.anki.sync import _factor_to_fsrs_difficulty

        assert _factor_to_fsrs_difficulty(3500) == 1.0

    def test_factor_1450_near_hard(self):
        from app.anki.sync import _factor_to_fsrs_difficulty

        # (3500 - 1450) / 220 ≈ 9.318
        assert _factor_to_fsrs_difficulty(1450) == pytest.approx(9.318, abs=0.01)


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
        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=2,
                        reps=3,
                        lapses=0,
                        stability=10.0,
                        difficulty=4.5,
                        due_date=new_due,
                        fsrs_known=True,
                    )
                ],
            )
        ]

        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()

        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].last_synced_at is not None


class TestSyncPullFsrsKnownQueueMapping:
    """Tests for fsrs_known=True path (offline sync) with new queue→state mappings."""

    def test_queue_minus_2_to_buried(self):
        """fsrs_known=True + queue=-2 → SRSState.BURIED."""
        db = _make_tt_db()
        guid = _add_banka(db)
        today = date.today()

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=-2,
                        reps=4,
                        lapses=0,
                        stability=5.0,
                        difficulty=4.5,
                        due_date=today,
                        fsrs_known=True,
                    ),
                ],
            )
        ]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()
        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].state == SRSState.BURIED

    def test_queue_minus_3_to_buried(self):
        """fsrs_known=True + queue=-3 → SRSState.BURIED."""
        db = _make_tt_db()
        guid = _add_banka(db)
        today = date.today()

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=-3,
                        reps=4,
                        lapses=0,
                        stability=5.0,
                        difficulty=4.5,
                        due_date=today,
                        fsrs_known=True,
                    ),
                ],
            )
        ]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()
        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].state == SRSState.BURIED

    def test_queue_1_to_learning(self):
        """fsrs_known=True + queue=1 → SRSState.LEARNING."""
        db = _make_tt_db()
        guid = _add_banka(db)
        today = date.today()

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=1,
                        reps=2,
                        lapses=0,
                        stability=5.0,
                        difficulty=4.5,
                        due_date=today,
                        fsrs_known=True,
                    ),
                ],
            )
        ]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()
        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].state == SRSState.LEARNING

    def test_queue_3_to_relearning(self):
        """fsrs_known=True + queue=3 → SRSState.RELEARNING."""
        db = _make_tt_db()
        guid = _add_banka(db)
        today = date.today()

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=3,
                        reps=5,
                        lapses=1,
                        stability=5.0,
                        difficulty=4.5,
                        due_date=today,
                        fsrs_known=True,
                    ),
                ],
            )
        ]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()
        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].state == SRSState.RELEARNING

    def test_queue_0_reps_0_to_new(self):
        """fsrs_known=True + queue=0, reps=0 → SRSState.NEW."""
        db = _make_tt_db()
        guid = _add_banka(db)

        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=0,
                        reps=0,
                        lapses=0,
                        stability=1.0,
                        difficulty=5.0,
                        due_date=date.today(),
                        fsrs_known=True,
                    ),
                ],
            )
        ]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()
        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].state == SRSState.NEW


class TestSyncPullWritesAnkiDue:
    def test_pull_writes_anki_due_for_new_card(self):
        """sync_pull writes anki_due from CardRecord for new cards."""
        db = _make_tt_db()
        guid = _add_banka(db)

        # CardRecord with queue=0 and anki_due=842
        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=0,
                        reps=0,
                        lapses=0,
                        stability=1.0,
                        difficulty=5.0,
                        due_date=date.today(),
                        anki_due=842,
                        fsrs_known=True,
                    ),
                ],
            )
        ]
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
        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=0,
                        reps=0,
                        lapses=0,
                        stability=1.0,
                        difficulty=5.0,
                        due_date=date.today(),
                        anki_due=842,
                        fsrs_known=True,
                    ),
                ],
            )
        ]
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
        base_card = CardRecord(
            anki_card_id=90010,
            ord=0,
            queue=0,
            reps=0,
            lapses=0,
            stability=1.0,
            difficulty=5.0,
            due_date=date.today(),
            anki_due=842,
            fsrs_known=True,
        )
        note = NoteRecord(
            anki_note_id=9001,
            anki_guid=guid,
            l2_text="banka",
            translation="bank",
            disambig_key="",
            mod=0,
            cards=[base_card],
        )
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
        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=2,
                        reps=5,
                        lapses=0,
                        stability=7.5,
                        difficulty=4.8,
                        due_date=date.today(),
                        last_review=expected_last_review,
                    ),
                ],
            )
        ]
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
