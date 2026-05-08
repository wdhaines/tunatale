"""Tests for S3.5: sync push (TunaTale → Anki)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, timedelta

import httpx

from app.anki.anki_connect import AnkiConnectClient
from app.anki.sync import (
    AnkiSync,
    OfflineWriter,
)
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from tests.conftest import make_card_record, make_note_record

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
    last_rating: int = 3,
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
        last_rating=last_rating,
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

    def set_learning_state(self, card_id: int, left: int, due_at: int, *, type_: int = 1) -> None:
        self.calls.append(("set_learning_state", card_id, left, due_at, type_))

    def write_revlog(
        self, *, cid: int, ease: int, ivl: int, last_ivl: int, factor: int, time_ms: int, type_, preferred_id=None
    ) -> None:
        self.calls.append(("write_revlog", cid, ease, ivl, last_ivl, factor, time_ms, type_, preferred_id))

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


# ── TestOfflineWriter
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


def _make_anki_full_db(col_crt: int | None = None) -> sqlite3.Connection:
    """Minimal collection.anki2 shape: col, notes, cards, revlog — enough for writer tests."""
    from datetime import UTC, datetime, timedelta

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE col (
            id INTEGER PRIMARY KEY, crt INTEGER, mod INTEGER, scm INTEGER,
            ver INTEGER, dty INTEGER, usn INTEGER, ls INTEGER
        );
        CREATE TABLE notes (
            id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER, mod INTEGER,
            usn INTEGER, tags TEXT, flds TEXT, sfld TEXT, csum INTEGER,
            flags INTEGER, data TEXT
        );
        CREATE TABLE cards (
            id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, ord INTEGER,
            mod INTEGER, usn INTEGER, type INTEGER, queue INTEGER, due INTEGER,
            ivl INTEGER, factor INTEGER, reps INTEGER, lapses INTEGER,
            left INTEGER, odue INTEGER, odid INTEGER, flags INTEGER, data TEXT
        );
        CREATE TABLE revlog (
            id INTEGER PRIMARY KEY, cid INTEGER, usn INTEGER, ease INTEGER, ivl INTEGER,
            lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER
        );
        """
    )
    if col_crt is None:
        # One year ago at midnight UTC — matches a typical Anki collection epoch.
        col_crt = int(
            (datetime.now(tz=UTC) - timedelta(days=365)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        )
    conn.execute(
        "INSERT INTO col (id, crt, mod, scm, ver, dty, usn, ls) VALUES (1, ?, 0, 0, 18, 0, 0, 0)",
        (col_crt,),
    )
    conn.commit()
    return conn


def _seed_note_and_cards(
    conn: sqlite3.Connection,
    *,
    note_id: int = 9001,
    guid: str = "banka-guid",
    mid: int = 1,
    rec_cid: int = 90010,
    prod_cid: int = 90011,
    flds: tuple[str, ...] = ("banka", "bank", "", "", "", "", ""),
    queue: int = 2,
    card_type: int = 2,
    due: int = 0,
    ivl: int = 1,
) -> None:
    flds_str = "\x1f".join(flds)
    conn.execute(
        "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data) "
        "VALUES (?, ?, ?, 100, 0, '', ?, ?, 0, 0, '')",
        (note_id, guid, mid, flds_str, flds[0]),
    )
    for cid, ord_ in ((rec_cid, 0), (prod_cid, 1)):
        conn.execute(
            "INSERT INTO cards "
            "(id, nid, did, ord, mod, usn, type, queue, due, ivl, factor, reps, lapses, left, odue, odid, flags, data) "
            "VALUES (?, ?, 1, ?, 100, 0, ?, ?, ?, ?, 2500, 0, 0, 0, 0, 0, 0, '')",
            (cid, note_id, ord_, card_type, queue, due, ivl),
        )
    conn.commit()


class TestOfflineWriter:
    def test_write_revlog_inserts_row(self):
        conn = _make_anki_full_db()
        writer = OfflineWriter(conn)
        writer.write_revlog(cid=12345, ease=3, ivl=7, last_ivl=7, factor=2500, time_ms=1000, type_=2)
        row = conn.execute("SELECT * FROM revlog").fetchone()
        assert row is not None
        assert row["cid"] == 12345
        assert row["ease"] == 3
        assert row["ivl"] == 7
        assert row["factor"] == 2500
        assert row["type"] == 2

    def test_write_revlog_bumps_col_mod_and_usn(self):
        conn = _make_anki_full_db()
        writer = OfflineWriter(conn)
        writer.write_revlog(cid=12345, ease=3, ivl=7, last_ivl=7, factor=2500, time_ms=1000, type_=2)
        col = conn.execute("SELECT mod, usn FROM col").fetchone()
        assert col["usn"] == -1
        assert col["mod"] > 0

    def test_update_note_fields_replaces_named_field_and_bumps_usn(self):
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn)
        writer = OfflineWriter(conn)
        writer.update_note_fields(9001, {"English": "bank (financial)"})

        row = conn.execute("SELECT flds, usn, mod FROM notes WHERE id=9001").fetchone()
        parts = row["flds"].split("\x1f")
        assert parts[0] == "banka"  # Slovene untouched
        assert parts[1] == "bank (financial)"  # English replaced
        assert row["usn"] == -1
        assert row["mod"] > 100  # bumped past seed value
        col = conn.execute("SELECT usn FROM col").fetchone()
        assert col["usn"] == -1

    def test_suspend_sets_queue_minus_one_and_usn_minus_one(self):
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn)
        writer = OfflineWriter(conn)
        writer.suspend([90010])

        row = conn.execute("SELECT queue, usn, mod FROM cards WHERE id=90010").fetchone()
        assert row["queue"] == -1
        assert row["usn"] == -1
        assert row["mod"] > 100
        # other card untouched
        other = conn.execute("SELECT queue FROM cards WHERE id=90011").fetchone()
        assert other["queue"] == 2

    def test_unsuspend_restores_queue_from_type(self):
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn, queue=-1, card_type=2)  # suspended review card
        writer = OfflineWriter(conn)
        writer.unsuspend([90010])

        row = conn.execute("SELECT queue, usn FROM cards WHERE id=90010").fetchone()
        assert row["queue"] == 2  # restored to review
        assert row["usn"] == -1

    def test_set_due_date_shifts_due_relative_to_today(self):
        from datetime import date, timedelta

        col_crt = int((date.today() - timedelta(days=200)).strftime("%s"))
        conn = _make_anki_full_db(col_crt=col_crt)
        _seed_note_and_cards(conn, queue=2, card_type=2, due=0, ivl=1)
        writer = OfflineWriter(conn)
        writer.set_due_date([90010], "7")

        row = conn.execute("SELECT due, ivl, usn, mod FROM cards WHERE id=90010").fetchone()
        # due-days-since-crt today is 200; +7 = 207
        assert row["due"] == 207
        assert row["ivl"] == 7
        assert row["usn"] == -1
        assert row["mod"] > 100

    def test_update_note_fields_unknown_note_id_is_noop(self):
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn)
        writer = OfflineWriter(conn)
        writer.update_note_fields(99999, {"English": "nope"})
        row = conn.execute("SELECT flds FROM notes WHERE id=9001").fetchone()
        # Original note untouched.
        assert row["flds"].split("\x1f")[1] == "bank"

    def test_update_note_fields_unknown_field_name_raises(self):
        import pytest

        conn = _make_anki_full_db()
        _seed_note_and_cards(conn)
        writer = OfflineWriter(conn)
        with pytest.raises(ValueError, match="Unknown field"):
            writer.update_note_fields(9001, {"Back": "bank"})

    def test_set_due_date_preserves_suspension(self):
        from datetime import date, timedelta

        col_crt = int((date.today() - timedelta(days=200)).strftime("%s"))
        conn = _make_anki_full_db(col_crt=col_crt)
        _seed_note_and_cards(conn, queue=-1, card_type=2, due=0)
        writer = OfflineWriter(conn)
        writer.set_due_date([90010], "5")

        row = conn.execute("SELECT queue, due FROM cards WHERE id=90010").fetchone()
        assert row["queue"] == -1  # still suspended
        assert row["due"] == 205

    def test_set_learning_state_writes_queue_and_type_for_relearning(self):
        """REVIEW → RELEARNING push must flip queue=2,type=2 → queue=1,type=3.

        Regression: previously only updated left/due, leaving queue=2 — the next
        sync_pull then read a queue=2 card with a unix-timestamp due, which
        crashed compute_due_date with OverflowError on real Anki collections.
        """
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn, queue=2, card_type=2, due=4500, ivl=10)
        writer = OfflineWriter(conn)
        writer.set_learning_state(90010, left=1001, due_at=1778000000, type_=3)

        row = conn.execute("SELECT queue, type, left, due, usn, mod FROM cards WHERE id=90010").fetchone()
        assert row["queue"] == 1, "RELEARNING must set queue=1 (intra-day learning queue)"
        assert row["type"] == 3, "RELEARNING must set type=3 (Anki's lapse type)"
        assert row["left"] == 1001
        assert row["due"] == 1778000000
        assert row["usn"] == -1

    def test_set_learning_state_preserves_suspension(self):
        """Suspended cards (queue=-1) must NOT be unsuspended by set_learning_state."""
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn, queue=-1, card_type=2)
        writer = OfflineWriter(conn)
        writer.set_learning_state(90010, left=1001, due_at=1778000000, type_=3)

        row = conn.execute("SELECT queue, type FROM cards WHERE id=90010").fetchone()
        assert row["queue"] == -1, "suspension must be preserved"


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
        assert "English" in call[2]
        assert call[2]["English"] == "bank"
        assert "Back" not in call[2]

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

    def test_dirty_direction_with_reps_inserts_revlog(self):
        """Pushing a reviewed dirty direction inserts revlog directly."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, reps=3, stability=10.5)

        anki_conn = _make_anki_full_db()
        _seed_note_and_cards(anki_conn, rec_cid=rec_cid)
        writer = OfflineWriter(anki_conn)
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        rows = anki_conn.execute("SELECT * FROM revlog").fetchall()
        assert len(rows) == 1
        assert rows[0]["cid"] == rec_cid
        assert rows[0]["ivl"] == max(1, round(10.5))

    def test_dirty_direction_with_reps_inserts_revlog_offline(self):
        """Offline: pushing a reviewed dirty direction inserts directly into Anki revlog."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, reps=3, stability=10.5)

        anki_conn = _make_anki_full_db()
        _seed_note_and_cards(anki_conn, rec_cid=rec_cid)
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


# ── TestDrainPendingRevlog ────────────────────────────────────────────────────


class TestSyncPushEase:
    """B5: sync_push must emit the learner's actual rating, not a hardcoded ease=3."""

    def test_sync_push_emits_real_ease_from_last_rating(self):
        """When last_rating=2 (Hard), write_revlog must receive ease=2."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today() + timedelta(days=10),
            stability=10.5,
            difficulty=4.8,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=2,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        revlog_calls = [c for c in writer.calls if c[0] == "write_revlog"]
        assert len(revlog_calls) == 1
        _, cid, ease, *_ = revlog_calls[0]
        assert ease == 2

    def test_schedule_to_push_chain_emits_real_ease(self):
        """Full B5 chain: schedule() → update_direction → sync_push → ease matches rating."""
        from app.models.srs_item import Rating
        from app.srs.fsrs import schedule as fsrs_schedule

        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        db.set_anki_ids(guid, 9001, {Direction.RECOGNITION: rec_cid, Direction.PRODUCTION: 90011})

        # Get item with reps so schedule produces a review (not a new)
        item = db.get_collocation_by_guid(guid)
        # Seed reps so it's not a new card
        from dataclasses import replace as dc_replace

        old_rec = item.directions[Direction.RECOGNITION]
        seeded = dc_replace(old_rec, reps=3, stability=5.0, state=SRSState.REVIEW)
        item.directions[Direction.RECOGNITION] = seeded
        db.update_direction(guid, Direction.RECOGNITION, seeded)

        # Schedule with AGAIN (ease=1)
        item = db.get_collocation_by_guid(guid)
        updated_item = fsrs_schedule(item, Rating.AGAIN, direction=Direction.RECOGNITION)
        rec_dir = updated_item.directions[Direction.RECOGNITION]
        db.update_direction(guid, Direction.RECOGNITION, rec_dir)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        revlog_calls = [c for c in writer.calls if c[0] == "write_revlog"]
        assert len(revlog_calls) == 1
        _, _cid, ease, *_ = revlog_calls[0]
        assert ease == Rating.AGAIN.value  # 1

    def test_sync_push_falls_back_ease_3_when_last_rating_null(self):
        """When last_rating is None (pre-migration row), write_revlog uses ease=3."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today() + timedelta(days=10),
            stability=10.5,
            difficulty=4.8,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=None,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        revlog_calls = [c for c in writer.calls if c[0] == "write_revlog"]
        assert len(revlog_calls) == 1
        _, cid, ease, *_ = revlog_calls[0]
        assert ease == 3


# ── B6: revlog (type, ivl, lastIvl) reflects the actual transition ───────────


class TestSyncPushRevlogTransitions:
    """Push must emit revlog rows whose (type, ivl, lastIvl) match the
    transition the user just made — not a hardcoded type=2 with positive ivl.

    The piščanec scenario: a Review card lapsed (Again) into the relearning
    queue. Anki's UI later sees a 1-min step that has no preceding revlog row,
    so the next rating computes against a fictional prior state. We fix this
    by stashing prior_state on the DirectionState at grade time and deriving
    correct revlog values at push time.

    Anki's RevlogReviewKind: 0=Learning, 1=Review, 2=Relearning.
    Anki's ivl encoding: positive integer = days; negative integer = -seconds.
    """

    def test_review_again_writes_review_revlog_with_negative_step_ivl(self):
        """REVIEW + Again → RELEARNING: type=1, ivl=-(relearn_step_min*60), lastIvl≈prior stability days."""
        from datetime import datetime as dt

        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
            stability=0.5,  # post-lapse stability (small)
            difficulty=5.5,
            reps=4,
            lapses=1,
            state=SRSState.RELEARNING,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=1,  # Again
            left=1001,
            due_at=dt.now(UTC) + timedelta(minutes=10),
            prior_state=SRSState.REVIEW,
            prior_left=None,
            prior_stability=10.0,  # was a 10-day Review card
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        revlog_calls = [c for c in writer.calls if c[0] == "write_revlog"]
        assert len(revlog_calls) == 1
        _, _cid, ease, ivl, last_ivl, _factor, _time_ms, type_, _pref = revlog_calls[0]
        assert ease == 1
        assert type_ == 1, f"REVIEW→RELEARNING uses Review revlog kind (1), got {type_}"
        assert ivl == -600, f"expected -(10*60)=-600, got {ivl}"
        assert last_ivl == 10, f"expected last_ivl=prior stability days=10, got {last_ivl}"

    def test_review_good_writes_review_revlog_with_positive_ivl(self):
        """REVIEW + Good → REVIEW: type=1, ivl=stability_days, lastIvl=prior_stability_days."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today() + timedelta(days=15),
            stability=15.3,  # post-good stability (grew)
            difficulty=4.8,
            reps=5,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=3,
            prior_state=SRSState.REVIEW,
            prior_stability=10.0,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        revlog_calls = [c for c in writer.calls if c[0] == "write_revlog"]
        assert len(revlog_calls) == 1
        _, _cid, _ease, ivl, last_ivl, _f, _t, type_, _p = revlog_calls[0]
        assert type_ == 1
        assert ivl == 15
        assert last_ivl == 10

    def test_learning_step_advance_writes_learning_revlog(self):
        """LEARNING(step0, left=2) + Good → LEARNING(step1, left=1): type=0, ivl=-600, lastIvl=-60.

        Anki encoding: low 3 digits = total_remaining; idx = total_steps - total_remaining.
        For learn_steps=[1m, 10m]: total_remaining=2 → step 0 (1m); total_remaining=1 → step 1 (10m).
        """
        from datetime import datetime as dt

        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
            stability=1.0,
            difficulty=5.0,
            reps=2,
            lapses=0,
            state=SRSState.LEARNING,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=3,  # Good
            left=1,  # total_remaining=1 → idx=1 (10min step)
            due_at=dt.now(UTC) + timedelta(minutes=10),
            prior_state=SRSState.LEARNING,
            prior_left=2,  # total_remaining=2 → idx=0 (1min step)
            prior_stability=1.0,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        revlog_calls = [c for c in writer.calls if c[0] == "write_revlog"]
        assert len(revlog_calls) == 1
        _, _cid, ease, ivl, last_ivl, _f, _t, type_, _p = revlog_calls[0]
        assert ease == 3
        assert type_ == 0, f"learning step revlog uses kind=0, got {type_}"
        assert ivl == -600, f"new step is 10min → -600, got {ivl}"
        assert last_ivl == -60, f"prior step was 1min → -60, got {last_ivl}"

    def test_new_to_learning_writes_learning_revlog_with_zero_last_ivl(self):
        """NEW + Good → LEARNING(step1): type=0, ivl=-(step1_min*60), lastIvl=0."""
        from datetime import datetime as dt

        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
            stability=1.0,
            difficulty=5.0,
            reps=1,
            lapses=0,
            state=SRSState.LEARNING,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=3,
            left=1,  # total_remaining=1 → idx=1 (10min step)
            due_at=dt.now(UTC) + timedelta(minutes=10),
            prior_state=SRSState.NEW,
            prior_left=None,
            prior_stability=1.0,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        revlog_calls = [c for c in writer.calls if c[0] == "write_revlog"]
        assert len(revlog_calls) == 1
        _, _cid, _ease, ivl, last_ivl, _f, _t, type_, _p = revlog_calls[0]
        assert type_ == 0
        assert ivl == -600
        assert last_ivl == 0, f"NEW→LEARNING has no prior step → lastIvl=0, got {last_ivl}"

    def test_learning_graduation_writes_learning_revlog_with_positive_ivl(self):
        """LEARNING(last step) + Good → REVIEW: type=0, ivl=stability_days, lastIvl=-(prior_step_min*60)."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today() + timedelta(days=4),
            stability=4.0,
            difficulty=5.0,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=3,
            left=None,
            due_at=None,
            prior_state=SRSState.LEARNING,
            prior_left=1,  # total_remaining=1 → idx=1 (10min step, last)
            prior_stability=1.0,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        revlog_calls = [c for c in writer.calls if c[0] == "write_revlog"]
        assert len(revlog_calls) == 1
        _, _cid, _ease, ivl, last_ivl, _f, _t, type_, _p = revlog_calls[0]
        assert type_ == 0, f"graduation from learning uses kind=0, got {type_}"
        assert ivl == 4, f"new ivl=stability days=4, got {ivl}"
        assert last_ivl == -600, f"prior step was 10min → -600, got {last_ivl}"

    def test_relearning_again_writes_relearning_revlog(self):
        """RELEARNING + Again (restart) → RELEARNING: type=2, ivl=-(relearn_step*60), lastIvl=-(prior_step*60)."""
        from datetime import datetime as dt

        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
            stability=0.3,
            difficulty=6.0,
            reps=5,
            lapses=2,
            state=SRSState.RELEARNING,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=1,
            left=1001,  # 1 of 1 step remaining
            due_at=dt.now(UTC) + timedelta(minutes=10),
            prior_state=SRSState.RELEARNING,
            prior_left=1001,
            prior_stability=0.5,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        revlog_calls = [c for c in writer.calls if c[0] == "write_revlog"]
        assert len(revlog_calls) == 1
        _, _cid, _ease, ivl, last_ivl, _f, _t, type_, _p = revlog_calls[0]
        assert type_ == 2, f"relearning→relearning uses kind=2, got {type_}"
        assert ivl == -600
        assert last_ivl == -600

    def test_unknown_prior_state_falls_back_to_legacy_review_shape(self):
        """prior_state=None (pre-migration row): keep old positive-ivl shape so legacy tests still hold."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, reps=3, stability=10.5, last_rating=3)
        # _mark_direction_dirty doesn't set prior_state → defaults to None

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        revlog_calls = [c for c in writer.calls if c[0] == "write_revlog"]
        assert len(revlog_calls) == 1
        _, _cid, _ease, ivl, last_ivl, _f, _t, _type, _p = revlog_calls[0]
        # Legacy fallback: positive ivl=stability_days (banker's rounding: 10.5 → 10)
        assert ivl == 10
        assert last_ivl == 10

    def test_schedule_then_push_review_again_emits_relearn_step(self):
        """End-to-end: schedule(REVIEW, AGAIN) → DB → push writes type=1, ivl=-600."""
        from dataclasses import replace as dc_replace

        from app.models.srs_item import Rating
        from app.srs.fsrs import schedule as fsrs_schedule

        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)

        item = db.get_collocation_by_guid(guid)
        old_rec = item.directions[Direction.RECOGNITION]
        seeded = dc_replace(old_rec, reps=3, stability=10.0, state=SRSState.REVIEW)
        db.update_direction(guid, Direction.RECOGNITION, seeded)

        item = db.get_collocation_by_guid(guid)
        updated_item = fsrs_schedule(item, Rating.AGAIN, direction=Direction.RECOGNITION)
        new_rec = updated_item.directions[Direction.RECOGNITION]
        assert new_rec.state == SRSState.RELEARNING
        assert new_rec.prior_state == SRSState.REVIEW, "schedule() must stash prior_state"
        db.update_direction(guid, Direction.RECOGNITION, new_rec)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        revlog_calls = [c for c in writer.calls if c[0] == "write_revlog"]
        assert len(revlog_calls) == 1
        _, _cid, ease, ivl, last_ivl, _f, _t, type_, _p = revlog_calls[0]
        assert ease == 1
        assert type_ == 1
        assert ivl == -600
        assert last_ivl == 10  # round(10.0)


# ── B6: helper functions (branch coverage for _step_minutes_from_left and _derive_revlog_shape) ──


class TestRevlogShapeHelpers:
    def test_step_minutes_from_left_returns_none_for_missing_inputs(self):
        from app.anki.sync import _step_minutes_from_left

        assert _step_minutes_from_left(None, [1.0, 10.0]) is None
        assert _step_minutes_from_left(0, [1.0, 10.0]) is None
        assert _step_minutes_from_left(2002, []) is None

    def test_step_minutes_from_left_returns_none_for_zero_packed_fields(self):
        from app.anki.sync import _step_minutes_from_left

        # total_steps == 0 (lower 3 digits are zero)
        assert _step_minutes_from_left(2000, [1.0, 10.0]) is None

    def test_step_minutes_from_left_returns_none_for_out_of_range_step_index(self):
        from app.anki.sync import _step_minutes_from_left

        # left=1003 → steps_remaining=1, total_steps=3, step_index=2; only 2 steps configured
        assert _step_minutes_from_left(1003, [1.0, 10.0]) is None

    def test_derive_shape_falls_back_for_legacy_learning_state(self):
        """prior_state=None + state=LEARNING uses Learning revlog kind (0)."""
        from app.anki.sync import _derive_revlog_shape

        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
            stability=2.0,
            state=SRSState.LEARNING,
            prior_state=None,
        )
        type_, ivl, last_ivl = _derive_revlog_shape(ds, [1.0, 10.0], [10.0])
        assert type_ == 0
        assert ivl == 2
        assert last_ivl == 2

    def test_derive_shape_falls_back_for_legacy_relearning_state(self):
        """prior_state=None + state=RELEARNING uses Relearning revlog kind (2)."""
        from app.anki.sync import _derive_revlog_shape

        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
            stability=2.0,
            state=SRSState.RELEARNING,
            prior_state=None,
        )
        type_, ivl, last_ivl = _derive_revlog_shape(ds, [1.0, 10.0], [10.0])
        assert type_ == 2

    def test_derive_shape_relearning_with_unparseable_left_falls_back_to_first_step(self):
        """state=RELEARNING with left=None still produces -relearn_steps[0]*60 ivl."""
        from app.anki.sync import _derive_revlog_shape

        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
            stability=0.5,
            state=SRSState.RELEARNING,
            left=None,
            prior_state=SRSState.REVIEW,
            prior_stability=5.0,
        )
        type_, ivl, last_ivl = _derive_revlog_shape(ds, [1.0, 10.0], [10.0])
        assert type_ == 1
        assert ivl == -600  # fallback to relearn_steps[0]
        assert last_ivl == 5

    def test_derive_shape_unexpected_prior_state_uses_fallback_last_ivl(self):
        """A prior_state outside the four known transitions (e.g. BURIED) falls
        through to the stability-based last_ivl branch."""
        from app.anki.sync import _derive_revlog_shape

        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
            stability=4.0,
            state=SRSState.REVIEW,
            prior_state=SRSState.BURIED,
        )
        _type_, _ivl, last_ivl = _derive_revlog_shape(ds, [1.0, 10.0], [10.0])
        assert last_ivl == 4

    def test_derive_shape_uses_due_at_minus_last_review_for_learning_ivl(self):
        """Hard-on-first-step parity: revlog ivl must reflect the actual delay
        applied (due_at - last_review), not just the current step's duration.

        Anki's rslib uses (steps[0] + steps[1]) / 2 = 5.5 min for Hard on the
        first learning step with [1, 10]. The revlog should record ivl=-330,
        not -60. This catches the kuhinja regression where TT wrote -60 to
        revlog while Anki wrote -330 for the same grade.
        """
        from datetime import UTC, datetime, timedelta

        from app.anki.sync import _derive_revlog_shape

        last_review = datetime(2026, 5, 8, 17, 5, 28, tzinfo=UTC)
        due_at = last_review + timedelta(seconds=330)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
            stability=1.7,
            state=SRSState.LEARNING,
            left=2,
            due_at=due_at,
            last_review=last_review,
            prior_state=SRSState.LEARNING,
            prior_left=2,
        )
        type_, ivl, last_ivl = _derive_revlog_shape(ds, [1.0, 10.0], [10.0])
        assert type_ == 0
        assert ivl == -330
        assert last_ivl == -60


# ── B14: offline ordering regression ─────────────────────────────────────────


class TestOfflineOrdering:
    """B14 regression: push must run before pull in offline mode.

    If pull runs first, it detects dirty_fsrs=True + fsrs_known=True → anki_wins
    → clears dirty_fsrs before push sees it → push emits nothing.
    """

    def test_push_before_pull_dirty_direction_gets_revlog(self):
        """Push-then-pull sequence fires write_revlog even when pull would anki_wins."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, reps=3, stability=10.5, anki_card_id=rec_cid)

        # Reader returns fsrs_known=True — in pull-first order this clears dirty_fsrs
        class OrderedFakeReader:
            def get_note_records(self):
                card = make_card_record(
                    anki_card_id=rec_cid,
                    ord=0,
                    reps=5,
                    stability=15.0,
                    difficulty=4.5,
                    due_date=date.today() + timedelta(days=15),
                )
                return [make_note_record(anki_guid=guid, cards=[card])]

        writer = FakeWriter()
        sync = AnkiSync(db=db, _reader=OrderedFakeReader(), _writer=writer)

        # NEW correct order: push then pull
        sync.sync_push()
        sync.sync_pull()

        # Push must have fired write_revlog before pull cleared dirty_fsrs
        assert "write_revlog" in writer.action_names()
        # After push+pull, direction is clean
        assert db.list_dirty() == []

    def test_pull_before_push_still_flushes_revlog(self):
        """Pull-then-push correctly preserves dirty_fsrs so push can still fire.

        Previously pull cleared dirty_fsrs (anki_wins), causing push to skip
        the row. Now pull preserves dirty rows, so pull-before-push also works.
        """
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, reps=3, stability=10.5, anki_card_id=rec_cid)

        class OrderedFakeReader:
            def get_note_records(self):
                card = make_card_record(
                    anki_card_id=rec_cid,
                    ord=0,
                    reps=5,
                    stability=15.0,
                    difficulty=4.5,
                    due_date=date.today() + timedelta(days=15),
                )
                return [make_note_record(anki_guid=guid, cards=[card])]

        writer = FakeWriter()
        sync = AnkiSync(db=db, _reader=OrderedFakeReader(), _writer=writer)

        # Pull preserves dirty_fsrs; push sees the dirty row and flushes it
        sync.sync_pull()
        sync.sync_push()

        assert "write_revlog" in writer.action_names()


# ── TestRevlogFactor ─────────────────────────────────────────────────────────


class TestRevlogFactor:
    """revlog.factor must be derived from difficulty, not hardcoded 2500."""

    def _push_with_difficulty(self, difficulty: float) -> list[tuple]:
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today() + timedelta(days=10),
            stability=10.5,
            difficulty=difficulty,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)
        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()
        return writer.calls

    def test_difficulty_3_maps_to_factor_3000(self):
        calls = self._push_with_difficulty(3.0)
        revlog_call = next(c for c in calls if c[0] == "write_revlog")
        assert revlog_call[5] == 3000

    def test_difficulty_8_maps_to_factor_8000(self):
        calls = self._push_with_difficulty(8.0)
        revlog_call = next(c for c in calls if c[0] == "write_revlog")
        assert revlog_call[5] == 8000

    def test_difficulty_0_5_clamped_to_1300(self):
        calls = self._push_with_difficulty(0.5)
        revlog_call = next(c for c in calls if c[0] == "write_revlog")
        assert revlog_call[5] == 1300

    def test_difficulty_15_clamped_to_13000(self):
        calls = self._push_with_difficulty(15.0)
        revlog_call = next(c for c in calls if c[0] == "write_revlog")
        assert revlog_call[5] == 13000


# ── TestPushLearningCardLeftAndDue ────────────────────────────────────────


class TestPushLearningCardLeftAndDue:
    """Step 5: Push round-trip — verify left/due_at written to Anki correctly."""

    def _make_fake_reader(self, guid, rec_cid, queue=1, left=2002):
        """Create a fake reader that returns a single card record."""
        from datetime import date, timedelta

        class FakeReader:
            def __init__(self, records):
                self._records = records

            def get_note_records(self):
                return self._records

        card = make_card_record(
            anki_card_id=rec_cid,
            ord=0,
            queue=queue,
            reps=1,
            stability=1.0,
            difficulty=5.0,
            due_date=date.today() + timedelta(days=1),
        )
        return FakeReader([make_note_record(anki_guid=guid, cards=[card])])

    def test_push_learning_good_advances_step(self, tmp_path):
        """Pushing a LEARNING+GOOD grade writes correct left and due (seconds)."""

        # Setup: create Anki DB with a learning card (left=2002, 2 steps remaining)
        db_path = tmp_path / "collection.anki2"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE col (id INTEGER PRIMARY KEY, crt INTEGER, mod INTEGER, usn INTEGER)")
        conn.execute("INSERT INTO col VALUES (1, 1704067200, 0, 0)")  # crt = 2024-01-01
        conn.execute(
            """CREATE TABLE cards (
                id INTEGER PRIMARY KEY,
                nid INTEGER,
                did INTEGER,
                ord INTEGER,
                mod INTEGER,
                usn INTEGER,
                type INTEGER,
                queue INTEGER,
                due INTEGER,
                ivl INTEGER,
                factor INTEGER,
                reps INTEGER,
                lapses INTEGER,
                left INTEGER,
                odue INTEGER,
                odid INTEGER,
                flags INTEGER,
                data TEXT
            )"""
        )
        # Card: learning state, left=2 (Anki encoding: total_remaining=2 → step 0)
        conn.execute(
            "INSERT INTO cards VALUES (90010, 9001, 123, 0, 0, 0, 1, 1, 1704103200, 0, 0, 1, 0, 2, 0, 0, 0, '{}')"
        )
        # Create revlog table (required by sync_push)
        conn.execute(
            """CREATE TABLE revlog (
                id INTEGER PRIMARY KEY,
                cid INTEGER,
                usn INTEGER,
                ease INTEGER,
                ivl INTEGER,
                lastIvl INTEGER,
                factor INTEGER,
                time INTEGER,
                type INTEGER
            )"""
        )
        conn.commit()
        conn.close()

        # Setup TunaTale DB with matching item
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db, anki_note_id=9001, rec_cid=90010)

        # Simulate a review: call schedule() with Rating.GOOD on a learning card with left=2002
        from datetime import datetime as dt
        from datetime import timedelta

        from app.srs.fsrs import Rating, schedule

        item = db.get_collocation("banka")
        assert item is not None
        rec_state = item.directions[Direction.RECOGNITION]

        # Set up the learning state at step 0 (Anki encoding: total_remaining=2 → left=2)
        now = dt.now(UTC)
        rec_state = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=now.date(),
            stability=1.0,
            difficulty=5.0,
            reps=1,
            lapses=0,
            state=SRSState.LEARNING,
            anki_card_id=rec_cid,
            left=2,
            due_at=now + timedelta(minutes=1),  # Step 0: 1 minute
            dirty_fsrs=True,
            last_rating=3,
        )
        db.update_direction(guid, Direction.RECOGNITION, rec_state)

        # Re-fetch so item reflects the LEARNING+left=2 state we just wrote
        item = db.get_collocation("banka")
        assert item is not None

        # schedule() with GOOD on step 0 of 2-step deck → advance to step 1 (left=1)
        result = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION)
        new_state = result.directions[Direction.RECOGNITION]
        assert new_state.left == 1, f"Expected left=1 (total_remaining=1) after GOOD, got {new_state.left}"

        # Write the post-GOOD state back so sync_push has something to push
        db.update_direction(guid, Direction.RECOGNITION, new_state)

        # Push to Anki
        conn = sqlite3.connect(str(db_path))
        writer = OfflineWriter(conn)
        reader = self._make_fake_reader(guid, rec_cid, queue=1, left=2)
        sync = AnkiSync(db=db, _reader=reader, _writer=writer)
        sync.sync_push()
        conn.close()

        # Verify Anki cards table updated correctly
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT left, due, queue FROM cards WHERE id = 90010").fetchone()
        conn.close()

        # After GOOD on step 0 of 2: total_remaining decrements to 1 → left=1
        assert row is not None, "Card row should exist"
        new_left, new_due, new_queue = row
        assert new_left == 1, f"Expected left=1 after advancing step, got {new_left}"
        assert new_queue == 1, f"Expected queue=1 (still learning), got {new_queue}"
        # due should be an absolute timestamp (seconds) for queue=1
        assert new_due > 1704067200, f"Expected due as absolute timestamp, got {new_due}"

    def test_push_learning_step_advances_left(self, tmp_path):
        """Pushing learning steps correctly decrements steps_remaining in left."""
        db_path = tmp_path / "collection.anki2"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE col (id INTEGER PRIMARY KEY, crt INTEGER, mod INTEGER, usn INTEGER)")
        conn.execute("INSERT INTO col VALUES (1, 1704067200, 0, 0)")
        conn.execute(
            """CREATE TABLE cards (
                id INTEGER PRIMARY KEY,
                nid INTEGER, did INTEGER, ord INTEGER, mod INTEGER, usn INTEGER,
                type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER, factor INTEGER,
                reps INTEGER, lapses INTEGER, left INTEGER, odue INTEGER, odid INTEGER,
                flags INTEGER, data TEXT
            )"""
        )
        # learning, left=1002 (1 step remaining of 2 total)
        conn.execute(
            "INSERT INTO cards VALUES (90010, 9001, 123, 0, 0, 0, 1, 1, 1704103200, 0, 0, 2, 0, 1002, 0, 0, 0, '{}')"
        )
        # Create revlog table (required by sync_push)
        conn.execute(
            """CREATE TABLE revlog (
                id INTEGER PRIMARY KEY,
                cid INTEGER,
                usn INTEGER,
                ease INTEGER,
                ivl INTEGER,
                lastIvl INTEGER,
                factor INTEGER,
                time INTEGER,
                type INTEGER
            )"""
        )
        conn.commit()
        conn.close()

        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db, anki_note_id=9001, rec_cid=90010)
        # Mark as learning with left=1002 (1 step remaining)
        _mark_direction_dirty(
            db, guid, state=SRSState.LEARNING, reps=2, stability=1.0, anki_card_id=90010, last_rating=3
        )

        # Update the direction to have left=1002
        item = db.get_collocation("banka")
        rec_state = item.directions[Direction.RECOGNITION]
        rec_state = DirectionState(
            direction=rec_state.direction,
            due_date=rec_state.due_date,
            stability=rec_state.stability,
            difficulty=rec_state.difficulty,
            reps=rec_state.reps,
            lapses=rec_state.lapses,
            state=SRSState.REVIEW,
            anki_card_id=rec_state.anki_card_id,
            anki_due=rec_state.anki_due,
            left=None,
            due_at=None,
            dirty_fsrs=True,
        )
        db.update_direction(guid, Direction.RECOGNITION, rec_state)

        # Push
        conn = sqlite3.connect(str(db_path))
        writer = OfflineWriter(conn)
        reader = self._make_fake_reader(guid, rec_cid, queue=1, left=1002)
        sync = AnkiSync(db=db, _reader=reader, _writer=writer)
        sync.sync_push()
        conn.close()

        # Verify: after GOOD on last step (1 remaining), should graduate (left=0, queue=2)
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT left, queue FROM cards WHERE id = 90010").fetchone()
        conn.close()
        assert row is not None
        new_left, new_queue = row
        # After graduating from learning, left should be 0 and queue should be 2 (review)
        assert new_left == 0, f"Expected left=0 after graduating, got {new_left}"
        assert new_queue == 2, f"Expected queue=2 (review) after graduating, got {new_queue}"
