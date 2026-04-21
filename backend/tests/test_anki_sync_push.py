"""Tests for S3.5: sync push (TunaTale → Anki)."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

import httpx

from app.anki.anki_connect import AnkiConnectClient
from app.anki.sync import (
    AnkiSync,
    OfflineWriter,
    OnlineWriter,
)
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase

# ── Shared helpers ─────────────────────────────────────────────────────────────


def _make_tt_db() -> SRSDatabase:
    return SRSDatabase(":memory:")


def _add_banka_with_anki_ids(
    db: SRSDatabase,
    *,
    anki_note_id: int = 9001,
    rec_cid: int = 90010,
    prod_cid: int = 90011,
) -> tuple[str, int, int, int]:
    """Add banka/bank to TT DB with Anki IDs. Returns (guid, note_id, rec_cid, prod_cid)."""
    unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus")
    db.add_collocation(unit)
    item = db.get_collocation("banka")
    assert item is not None
    guid = item.guid
    db.set_anki_ids(guid, anki_note_id, {Direction.RECOGNITION: rec_cid, Direction.PRODUCTION: prod_cid})
    return guid, anki_note_id, rec_cid, prod_cid


def _mark_direction_dirty(
    db: SRSDatabase,
    guid: str,
    direction: Direction = Direction.RECOGNITION,
    *,
    state: SRSState = SRSState.REVIEW,
    reps: int = 3,
    stability: float = 10.5,
    anki_card_id: int = 90010,
    due_date: date | None = None,
) -> None:
    """Update a direction to dirty_fsrs=True, simulating a TT review."""
    ds = DirectionState(
        direction=direction,
        due_date=due_date or (date.today() + timedelta(days=10)),
        stability=stability,
        difficulty=4.8,
        reps=reps,
        lapses=0,
        state=state,
        dirty_fsrs=True,
        anki_card_id=anki_card_id,
    )
    db.update_direction(guid, direction, ds)


class FakeWriter:
    """Records all writer calls for assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def update_note_fields(self, note_id: int, fields: dict[str, str]) -> None:
        self.calls.append(("update_note_fields", note_id, fields))

    def suspend(self, card_ids: list[int]) -> None:
        self.calls.append(("suspend", list(card_ids)))

    def unsuspend(self, card_ids: list[int]) -> None:
        self.calls.append(("unsuspend", list(card_ids)))

    def set_due_date(self, card_ids: list[int], days: str) -> None:
        self.calls.append(("set_due_date", list(card_ids), days))

    def write_revlog(
        self, *, cid: int, ease: int, ivl: int, last_ivl: int, factor: int, time_ms: int, type_: int
    ) -> None:
        self.calls.append(("write_revlog", cid, ease, ivl, last_ivl, factor, time_ms, type_))

    def action_names(self) -> list[str]:
        return [c[0] for c in self.calls]


class RecordingTransport(httpx.BaseTransport):
    """Records calls and returns success responses."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def handle_request(self, request):
        body = json.loads(request.content)
        action = body["action"]
        params = body.get("params", {})
        self.calls.append((action, params))
        return httpx.Response(200, json={"result": None, "error": None})


def _recording_client() -> tuple[AnkiConnectClient, RecordingTransport]:
    transport = RecordingTransport()
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    return client, transport


# ── TestListDirtyFieldEdits ────────────────────────────────────────────────────


class TestListDirtyFieldEdits:
    def test_returns_rows_with_dirty_fields(self):
        db = _make_tt_db()
        guid, *_ = _add_banka_with_anki_ids(db)
        db.set_dirty_fields(guid, "translation")
        rows = db.list_dirty_field_edits()
        assert len(rows) == 1
        row_guid, anki_note_id, dirty_str, item = rows[0]
        assert row_guid == guid
        assert anki_note_id == 9001
        assert dirty_str == "translation"
        assert item.syntactic_unit.translation == "bank"

    def test_excludes_clean_rows(self):
        db = _make_tt_db()
        _add_banka_with_anki_ids(db)  # dirty_fields = '' (default)
        assert db.list_dirty_field_edits() == []

    def test_empty_when_nothing_in_db(self):
        db = _make_tt_db()
        assert db.list_dirty_field_edits() == []


# ── TestOnlineWriter ──────────────────────────────────────────────────────────


class TestOnlineWriter:
    def test_update_note_fields_sends_correct_payload(self):
        client, transport = _recording_client()
        writer = OnlineWriter(client, _make_tt_db())
        writer.update_note_fields(1001, {"Back": "bank"})
        assert transport.calls[0][0] == "updateNoteFields"
        assert transport.calls[0][1]["note"]["id"] == 1001

    def test_suspend_sends_correct_payload(self):
        client, transport = _recording_client()
        writer = OnlineWriter(client, _make_tt_db())
        writer.suspend([90010])
        assert transport.calls[0][0] == "suspend"
        assert 90010 in transport.calls[0][1]["cards"]

    def test_unsuspend_sends_correct_payload(self):
        client, transport = _recording_client()
        writer = OnlineWriter(client, _make_tt_db())
        writer.unsuspend([90010])
        assert transport.calls[0][0] == "unsuspend"
        assert 90010 in transport.calls[0][1]["cards"]

    def test_set_due_date_sends_correct_payload(self):
        client, transport = _recording_client()
        writer = OnlineWriter(client, _make_tt_db())
        writer.set_due_date([90010], "7")
        assert transport.calls[0][0] == "setDueDate"
        assert 90010 in transport.calls[0][1]["cards"]

    def test_write_revlog_queues_to_pending_revlog(self):
        db = _make_tt_db()
        client, _ = _recording_client()
        writer = OnlineWriter(client, db)
        writer.write_revlog(cid=90010, ease=3, ivl=10, last_ivl=10, factor=2500, time_ms=0, type_=2)
        rows = db.drain_pending_revlog()
        assert len(rows) == 1
        assert rows[0]["cid"] == 90010
        assert rows[0]["ease"] == 3


# ── TestOfflineWriter ──────────────────────────────────────────────────────────


def _make_anki_revlog_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE revlog "
        "(id INTEGER PRIMARY KEY, cid INTEGER, usn INTEGER, ease INTEGER, ivl INTEGER,"
        " lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER)"
    )
    conn.commit()
    return conn


class TestOfflineWriter:
    def test_write_revlog_inserts_row(self):
        conn = _make_anki_revlog_db()
        writer = OfflineWriter(conn)
        writer.write_revlog(cid=12345, ease=3, ivl=7, last_ivl=7, factor=2500, time_ms=1000, type_=2)
        row = conn.execute("SELECT * FROM revlog").fetchone()
        assert row is not None
        assert row["cid"] == 12345
        assert row["ease"] == 3
        assert row["ivl"] == 7
        assert row["factor"] == 2500
        assert row["type"] == 2

    def test_noop_methods_do_not_raise(self):
        """S3.5 deferred stubs must not raise."""
        conn = _make_anki_revlog_db()
        writer = OfflineWriter(conn)
        writer.update_note_fields(1, {"Back": "bank"})
        writer.suspend([1, 2])
        writer.unsuspend([1, 2])
        writer.set_due_date([1], "7")
        assert conn.execute("SELECT COUNT(*) FROM revlog").fetchone()[0] == 0


# ── TestSyncPush ──────────────────────────────────────────────────────────────


class FakeReader:
    def get_note_records(self):
        return []


class TestSyncPush:
    def test_dirty_translation_calls_update_note_fields(self):
        db = _make_tt_db()
        guid, note_id, *_ = _add_banka_with_anki_ids(db)
        db.set_dirty_fields(guid, "translation")

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert "update_note_fields" in writer.action_names()
        call = next(c for c in writer.calls if c[0] == "update_note_fields")
        assert call[1] == note_id
        assert "Back" in call[2]
        assert call[2]["Back"] == "bank"

    def test_dirty_translation_clears_dirty_fields_after_push(self):
        db = _make_tt_db()
        guid, *_ = _add_banka_with_anki_ids(db)
        db.set_dirty_fields(guid, "translation")

        AnkiSync(db=db, _reader=FakeReader(), _writer=FakeWriter()).sync_push()

        assert db.get_dirty_fields(guid) == ""

    def test_dirty_direction_calls_set_due_date(self):
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        due = date.today() + timedelta(days=7)
        _mark_direction_dirty(db, guid, due_date=due)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert "set_due_date" in writer.action_names()
        call = next(c for c in writer.calls if c[0] == "set_due_date")
        assert rec_cid in call[1]
        assert call[2] == "7"

    def test_dirty_direction_suspended_calls_suspend(self):
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, state=SRSState.SUSPENDED, reps=0)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert "suspend" in writer.action_names()
        call = next(c for c in writer.calls if c[0] == "suspend")
        assert rec_cid in call[1]

    def test_dirty_direction_not_suspended_calls_unsuspend(self):
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, state=SRSState.REVIEW, reps=3)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert "unsuspend" in writer.action_names()
        call = next(c for c in writer.calls if c[0] == "unsuspend")
        assert rec_cid in call[1]

    def test_suspend_one_direction_only_suspends_that_card(self):
        """Only the RECOGNITION card is suspended; PRODUCTION is untouched."""
        db = _make_tt_db()
        guid, _, rec_cid, prod_cid = _add_banka_with_anki_ids(db)
        # Only recognition is dirty+suspended
        _mark_direction_dirty(db, guid, Direction.RECOGNITION, state=SRSState.SUSPENDED, reps=0, anki_card_id=rec_cid)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        suspended_ids = [id_ for c in writer.calls if c[0] == "suspend" for id_ in c[1]]
        unsuspended_ids = [id_ for c in writer.calls if c[0] == "unsuspend" for id_ in c[1]]
        assert rec_cid in suspended_ids
        assert prod_cid not in suspended_ids
        assert prod_cid not in unsuspended_ids

    def test_set_specific_value_not_called_without_force_fsrs(self):
        """setSpecificValueOfCard must not be called during a normal push."""
        db = _make_tt_db()
        guid, *_ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert "set_specific_value_of_card" not in writer.action_names()

    def test_dirty_direction_with_reps_queues_revlog_online(self):
        """Online: pushing a reviewed dirty direction enqueues a pending_revlog entry."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, reps=3, stability=10.5)

        client, _ = _recording_client()
        writer = OnlineWriter(client, db)
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        rows = db.drain_pending_revlog()
        assert len(rows) == 1
        assert rows[0]["cid"] == rec_cid
        assert rows[0]["ivl"] == max(1, round(10.5))

    def test_dirty_direction_with_reps_inserts_revlog_offline(self):
        """Offline: pushing a reviewed dirty direction inserts directly into Anki revlog."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, reps=3, stability=10.5)

        anki_conn = _make_anki_revlog_db()
        writer = OfflineWriter(anki_conn)
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        row = anki_conn.execute("SELECT * FROM revlog").fetchone()
        assert row is not None
        assert row["cid"] == rec_cid
        assert row["ivl"] == max(1, round(10.5))

    def test_zero_reps_does_not_emit_revlog(self):
        """A direction with reps=0 (never reviewed) does not emit a revlog entry."""
        db = _make_tt_db()
        guid, *_ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, reps=0, state=SRSState.SUSPENDED)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert "write_revlog" not in writer.action_names()

    def test_idempotent_after_push(self):
        """Running sync_push twice: second run finds nothing dirty."""
        db = _make_tt_db()
        guid, *_ = _add_banka_with_anki_ids(db)
        db.set_dirty_fields(guid, "translation")
        _mark_direction_dirty(db, guid)

        AnkiSync(db=db, _reader=FakeReader(), _writer=FakeWriter()).sync_push()

        writer2 = FakeWriter()
        report2 = AnkiSync(db=db, _reader=FakeReader(), _writer=writer2).sync_push()
        assert report2.notes_pushed == 0
        assert report2.directions_pushed == 0
        assert writer2.calls == []

    def test_note_without_anki_id_is_skipped(self):
        """Collocation with dirty_fields but no anki_note_id → no updateNoteFields."""
        db = _make_tt_db()
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus")
        db.add_collocation(unit)
        guid = db.get_collocation("banka").guid
        db.set_dirty_fields(guid, "translation")
        # No set_anki_ids call → anki_note_id remains None

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert "update_note_fields" not in writer.action_names()

    def test_direction_without_card_id_is_skipped(self):
        """Direction with dirty_fsrs but no anki_card_id → nothing pushed."""
        db = _make_tt_db()
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus")
        db.add_collocation(unit)
        guid = db.get_collocation("banka").guid
        # Mark dirty without setting anki_card_id
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today() + timedelta(days=5),
            stability=5.0,
            difficulty=4.8,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
            anki_card_id=None,  # no card ID
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        report = AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert report.directions_pushed == 0
        assert writer.calls == []

    def test_unknown_dirty_field_is_skipped(self):
        """dirty_fields='text' (unrecognised) produces no note update."""
        db = _make_tt_db()
        guid, *_ = _add_banka_with_anki_ids(db)
        db.set_dirty_fields(guid, "text")

        writer = FakeWriter()
        report = AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert report.notes_pushed == 0
        assert "update_note_fields" not in writer.action_names()

    def test_dry_run_does_not_write(self):
        """dry_run=True: counts reported but no writes to DB or writer."""
        db = _make_tt_db()
        guid, *_ = _add_banka_with_anki_ids(db)
        db.set_dirty_fields(guid, "translation")
        _mark_direction_dirty(db, guid)

        writer = FakeWriter()
        report = AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push(dry_run=True)

        assert report.notes_pushed == 1
        assert report.directions_pushed == 1
        assert writer.calls == []
        # DB still dirty
        assert db.get_dirty_fields(guid) == "translation"
        dirty = db.list_dirty()
        assert len(dirty) == 1
