"""Tests for S3.6: --force-fsrs gate + setSpecificValueOfCard."""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx
import pytest

from app.anki.anki_connect import AnkiConnectClient
from app.anki.sync import (
    KNOWN_ANKI_SCHEMA_VER,
    AnkiSync,
    ForceFsrsNotAcknowledgedError,
    OfflineWriter,
    OnlineWriter,
    SetSpecificValueMissingError,
    ensure_force_fsrs_ack,
    preflight_set_specific_value_of_card,
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
    difficulty: float = 4.8,
    anki_card_id: int = 90010,
    due_date: date | None = None,
) -> None:
    ds = DirectionState(
        direction=direction,
        due_date=due_date or (date.today() + timedelta(days=10)),
        stability=stability,
        difficulty=difficulty,
        reps=reps,
        lapses=0,
        state=state,
        dirty_fsrs=True,
        anki_card_id=anki_card_id,
    )
    db.update_direction(guid, direction, ds)


class FakeReader:
    def get_note_records(self):
        return []


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

    def set_specific_value_of_card(self, card_id: int, keys: list[str], new_values: list[str]) -> None:
        self.calls.append(("set_specific_value_of_card", card_id, list(keys), list(new_values)))

    def action_names(self) -> list[str]:
        return [c[0] for c in self.calls]


# ── TestKnownAnkiSchemaVer ──────────────────────────────────────────────────────


class TestKnownAnkiSchemaVer:
    def test_constant_is_18(self):
        assert KNOWN_ANKI_SCHEMA_VER == 18


# ── TestEnsureForceFsrsAck ──────────────────────────────────────────────────────


class TestEnsureForceFsrsAck:
    def test_raises_when_no_ack_file_and_non_interactive(self, tmp_path):
        ack_path = tmp_path / "force_fsrs_ack.txt"
        with pytest.raises(ForceFsrsNotAcknowledgedError):
            ensure_force_fsrs_ack(ack_path, interactive=False)

    def test_raises_when_ack_file_empty_and_non_interactive(self, tmp_path):
        ack_path = tmp_path / "force_fsrs_ack.txt"
        ack_path.write_text("")
        with pytest.raises(ForceFsrsNotAcknowledgedError):
            ensure_force_fsrs_ack(ack_path, interactive=False)

    def test_passes_when_ack_file_present(self, tmp_path):
        ack_path = tmp_path / "force_fsrs_ack.txt"
        ack_path.write_text("acknowledged at 2026-04-21T12:00:00\n")
        # Should not raise
        ensure_force_fsrs_ack(ack_path, interactive=False)

    def test_interactive_y_writes_ack_file(self, tmp_path, monkeypatch):
        ack_path = tmp_path / "force_fsrs_ack.txt"
        monkeypatch.setattr("builtins.input", lambda: "y")
        ensure_force_fsrs_ack(ack_path, interactive=True)
        assert ack_path.exists()
        assert ack_path.read_text().strip() != ""

    def test_interactive_n_raises(self, tmp_path, monkeypatch):
        ack_path = tmp_path / "force_fsrs_ack.txt"
        monkeypatch.setattr("builtins.input", lambda: "n")
        with pytest.raises(ForceFsrsNotAcknowledgedError):
            ensure_force_fsrs_ack(ack_path, interactive=True)

    def test_interactive_n_does_not_write_ack_file(self, tmp_path, monkeypatch):
        ack_path = tmp_path / "force_fsrs_ack.txt"
        monkeypatch.setattr("builtins.input", lambda: "n")
        with pytest.raises(ForceFsrsNotAcknowledgedError):
            ensure_force_fsrs_ack(ack_path, interactive=True)
        assert not ack_path.exists()


# ── TestPreflightSetSpecificValue ───────────────────────────────────────────────


class TestPreflightSetSpecificValue:
    def _make_client(self, actions: list[str]) -> AnkiConnectClient:
        def handle_request(request):
            body = json.loads(request.content)
            if body["action"] == "apiReflect":
                return httpx.Response(200, json={"result": {"actions": actions}, "error": None})
            return httpx.Response(200, json={"result": None, "error": None})

        transport = httpx.MockTransport(handle_request)
        return AnkiConnectClient(http_client=httpx.Client(transport=transport))

    def test_raises_when_action_missing(self):
        client = self._make_client(["version", "findNotes"])
        with pytest.raises(SetSpecificValueMissingError):
            preflight_set_specific_value_of_card(client)

    def test_passes_when_action_present(self):
        client = self._make_client(["version", "findNotes", "setSpecificValueOfCard"])
        # Should not raise
        preflight_set_specific_value_of_card(client)

    def test_error_message_mentions_action(self):
        client = self._make_client([])
        with pytest.raises(SetSpecificValueMissingError, match="setSpecificValueOfCard"):
            preflight_set_specific_value_of_card(client)


# ── TestOnlineWriterSetSpecificValue ────────────────────────────────────────────


class TestOnlineWriterSetSpecificValue:
    def _make_recording_client(self) -> tuple[AnkiConnectClient, list]:
        calls = []

        def handle_request(request):
            body = json.loads(request.content)
            calls.append((body["action"], body.get("params", {})))
            return httpx.Response(200, json={"result": None, "error": None})

        transport = httpx.MockTransport(handle_request)
        client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
        return client, calls

    def test_calls_client_set_specific_value(self):
        db = _make_tt_db()
        client, calls = self._make_recording_client()
        writer = OnlineWriter(client, db)
        writer.set_specific_value_of_card(12345, keys=["ivl", "factor"], new_values=["10", "2500"])
        assert len(calls) == 1
        action, params = calls[0]
        assert action == "setSpecificValueOfCard"
        assert params["card"] == 12345
        assert params["keys"] == ["ivl", "factor"]
        assert params["newValues"] == ["10", "2500"]


# ── TestOfflineWriterSetSpecificValue ───────────────────────────────────────────


class TestOfflineWriterSetSpecificValue:
    def test_noop_does_not_raise(self):
        import sqlite3

        conn = sqlite3.connect(":memory:")
        writer = OfflineWriter(conn)
        # Should not raise; S3.7 will implement offline FSRS write
        writer.set_specific_value_of_card(12345, keys=["ivl", "factor"], new_values=["10", "2500"])


# ── TestSyncPushForceFsrs ────────────────────────────────────────────────────────


class TestSyncPushForceFsrs:
    def test_force_fsrs_false_does_not_call_set_specific_value(self):
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, Direction.RECOGNITION, anki_card_id=rec_cid)
        writer = FakeWriter()
        sync = AnkiSync(db=db, _reader=FakeReader(), _writer=writer)
        sync.sync_push(force_fsrs=False)
        assert "set_specific_value_of_card" not in writer.action_names()

    def test_force_fsrs_true_calls_set_specific_value(self):
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, Direction.RECOGNITION, stability=10.5, difficulty=4.8, anki_card_id=rec_cid)
        writer = FakeWriter()
        sync = AnkiSync(db=db, _reader=FakeReader(), _writer=writer)
        sync.sync_push(force_fsrs=True)
        assert "set_specific_value_of_card" in writer.action_names()

    def test_force_fsrs_payload_contains_stability_and_difficulty(self):
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, Direction.RECOGNITION, stability=10.5, difficulty=4.8, anki_card_id=rec_cid)
        writer = FakeWriter()
        sync = AnkiSync(db=db, _reader=FakeReader(), _writer=writer)
        sync.sync_push(force_fsrs=True)
        fsrs_calls = [c for c in writer.calls if c[0] == "set_specific_value_of_card"]
        assert len(fsrs_calls) == 1
        _, card_id, keys, new_values = fsrs_calls[0]
        assert card_id == rec_cid
        data_idx = keys.index("data")
        data = json.loads(new_values[data_idx])
        assert data["s"] == pytest.approx(10.5)
        assert data["d"] == pytest.approx(4.8)

    def test_force_fsrs_ivl_is_rounded_stability(self):
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, Direction.RECOGNITION, stability=10.5, anki_card_id=rec_cid)
        writer = FakeWriter()
        sync = AnkiSync(db=db, _reader=FakeReader(), _writer=writer)
        sync.sync_push(force_fsrs=True)
        fsrs_call = next(c for c in writer.calls if c[0] == "set_specific_value_of_card")
        _, _, keys, new_values = fsrs_call
        ivl_idx = keys.index("ivl")
        assert new_values[ivl_idx] == str(max(1, round(10.5)))

    def test_force_fsrs_skipped_when_schema_ver_too_high(self):
        """Schema version guard: if col.ver > KNOWN_ANKI_SCHEMA_VER, skip FSRS writes."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, Direction.RECOGNITION, anki_card_id=rec_cid)
        writer = FakeWriter()
        sync = AnkiSync(db=db, _reader=FakeReader(), _writer=writer, _anki_col_ver=KNOWN_ANKI_SCHEMA_VER + 1)
        sync.sync_push(force_fsrs=True)
        # Other writes (suspend/set_due_date) still proceed
        assert "set_due_date" in writer.action_names()
        # But FSRS write is skipped
        assert "set_specific_value_of_card" not in writer.action_names()

    def test_force_fsrs_proceeds_when_schema_ver_matches(self):
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, Direction.RECOGNITION, anki_card_id=rec_cid)
        writer = FakeWriter()
        sync = AnkiSync(db=db, _reader=FakeReader(), _writer=writer, _anki_col_ver=KNOWN_ANKI_SCHEMA_VER)
        sync.sync_push(force_fsrs=True)
        assert "set_specific_value_of_card" in writer.action_names()

    def test_force_fsrs_skipped_when_reps_zero(self):
        """FSRS write should still happen even if reps=0 (we write stability/difficulty regardless)."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, Direction.RECOGNITION, reps=0, stability=1.0, anki_card_id=rec_cid)
        writer = FakeWriter()
        sync = AnkiSync(db=db, _reader=FakeReader(), _writer=writer)
        sync.sync_push(force_fsrs=True)
        # force_fsrs writes regardless of reps (reps guard is on revlog only)
        assert "set_specific_value_of_card" in writer.action_names()

    def test_force_fsrs_dry_run_does_not_call_writer(self):
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, Direction.RECOGNITION, anki_card_id=rec_cid)
        writer = FakeWriter()
        sync = AnkiSync(db=db, _reader=FakeReader(), _writer=writer)
        sync.sync_push(force_fsrs=True, dry_run=True)
        assert writer.calls == []

    def test_force_fsrs_no_anki_col_ver_proceeds(self):
        """When _anki_col_ver is None (online mode), no schema guard applies."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, Direction.RECOGNITION, anki_card_id=rec_cid)
        writer = FakeWriter()
        sync = AnkiSync(db=db, _reader=FakeReader(), _writer=writer, _anki_col_ver=None)
        sync.sync_push(force_fsrs=True)
        assert "set_specific_value_of_card" in writer.action_names()

    def test_force_fsrs_factor_derived_from_difficulty(self):
        """set_specific_value_of_card must write difficulty-derived factor, not 2500."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, Direction.RECOGNITION, stability=10.5, difficulty=6.0, anki_card_id=rec_cid)
        writer = FakeWriter()
        sync = AnkiSync(db=db, _reader=FakeReader(), _writer=writer)
        sync.sync_push(force_fsrs=True)
        fsrs_call = next(c for c in writer.calls if c[0] == "set_specific_value_of_card")
        _, _, keys, new_values = fsrs_call
        factor_idx = keys.index("factor")
        assert new_values[factor_idx] == "6000"
