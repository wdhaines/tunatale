"""Bidirectional sync between TunaTale and Anki.

S3.4: sync_pull (Anki → TunaTale).
S3.5: sync_push (TunaTale → Anki).
S3.6: --force-fsrs gate + setSpecificValueOfCard.
"""

from __future__ import annotations

import json as _json
import sqlite3
import time as _time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.anki.anki_connect import AnkiConnectClient
from app.anki.sqlite_reader import (
    extract_disambig_from_fields,
    extract_l2_from_fields,
    extract_translation,
    fetch_cards_for_notes,
    fetch_notes_for_deck,
    find_deck_id,
)
from app.common.guid import compute_guid
from app.models.srs_item import Direction, DirectionState, SRSState
from app.srs.database import SRSDatabase

KNOWN_ANKI_SCHEMA_VER = 18


class ForceFsrsNotAcknowledgedError(Exception):
    """--force-fsrs requires a one-time acknowledgement file."""


class SetSpecificValueMissingError(Exception):
    """AnkiConnect does not expose setSpecificValueOfCard."""


def ensure_force_fsrs_ack(ack_path: Path, interactive: bool = True) -> None:
    """Verify the user has acknowledged the force-fsrs risk.

    Reads ack_path; if absent or empty, either raises (non-interactive) or
    prompts the user and writes the file on 'y'.
    """
    if ack_path.exists() and ack_path.read_text().strip():
        return
    if not interactive:
        raise ForceFsrsNotAcknowledgedError(
            f"--force-fsrs requires acknowledgement. Run interactively first to create: {ack_path}"
        )
    print(
        "--force-fsrs will overwrite raw FSRS stability/difficulty in Anki's "
        "cards.data JSON. This is officially dangerous (Anki may reject on schema drift). "
        "Acknowledge? [y/N] ",
        end="",
        flush=True,
    )
    answer = input().strip().lower()
    if answer != "y":
        raise ForceFsrsNotAcknowledgedError("User declined force-fsrs acknowledgement.")
    ack_path.parent.mkdir(parents=True, exist_ok=True)
    ack_path.write_text(f"acknowledged at {_time.strftime('%Y-%m-%dT%H:%M:%S')}\n")


def preflight_set_specific_value_of_card(client: AnkiConnectClient) -> None:
    """Raise SetSpecificValueMissingError if AnkiConnect lacks setSpecificValueOfCard."""
    actions = client.api_reflect()
    if "setSpecificValueOfCard" not in actions:
        raise SetSpecificValueMissingError(
            "AnkiConnect does not expose setSpecificValueOfCard. "
            "Add 'setSpecificValueOfCard' to the allowedActions list in your AnkiConnect config."
        )


@dataclass
class CardRecord:
    anki_card_id: int
    ord: int
    queue: int
    reps: int
    lapses: int
    stability: float
    difficulty: float
    due_date: date


@dataclass
class NoteRecord:
    anki_note_id: int
    anki_guid: str
    l2_text: str
    translation: str
    disambig_key: str
    mod: int
    cards: list[CardRecord]


@dataclass
class SyncConflict:
    guid: str
    direction: str | None
    field: str
    local_value: str | None
    remote_value: str | None
    resolution: str


@dataclass
class PullReport:
    notes_updated: int = 0
    directions_updated: int = 0
    conflicts: list[SyncConflict] = field(default_factory=list)
    skipped_unknown_guid: int = 0


@dataclass
class PushReport:
    notes_pushed: int = 0
    directions_pushed: int = 0


class OfflineReader:
    """Read NoteRecords from a raw sqlite3.Connection to collection.anki2."""

    def __init__(self, conn: sqlite3.Connection, deck_name: str) -> None:
        self._conn = conn
        self._deck_name = deck_name

    def get_note_records(self) -> list[NoteRecord]:
        deck_id = find_deck_id(self._conn, self._deck_name)
        if deck_id is None:
            return []
        notes = fetch_notes_for_deck(self._conn, deck_id)
        if not notes:
            return []

        note_ids = [n.id for n in notes]
        cards = fetch_cards_for_notes(self._conn, note_ids)

        cards_by_note: dict[int, list] = {}
        for c in cards:
            cards_by_note.setdefault(c.note_id, []).append(c)

        records = []
        for note in notes:
            l2_text = extract_l2_from_fields(note.fields)
            translation = extract_translation(note.fields[1]) if len(note.fields) > 1 else ""
            disambig_key = extract_disambig_from_fields(note.fields)
            card_records = [
                CardRecord(
                    anki_card_id=c.id,
                    ord=c.ord,
                    queue=c.queue,
                    reps=c.reps,
                    lapses=c.lapses,
                    stability=c.fsrs_state.stability,
                    difficulty=c.fsrs_state.difficulty,
                    due_date=c.fsrs_state.due_date,
                )
                for c in cards_by_note.get(note.id, [])
            ]
            records.append(
                NoteRecord(
                    anki_note_id=note.id,
                    anki_guid=note.anki_guid,
                    l2_text=l2_text,
                    translation=translation,
                    disambig_key=disambig_key,
                    mod=note.mod,
                    cards=card_records,
                )
            )
        return records


class OnlineReader:
    """Read NoteRecords via AnkiConnect."""

    def __init__(self, client: AnkiConnectClient, deck_name: str) -> None:
        self._client = client
        self._deck_name = deck_name

    def get_note_records(self) -> list[NoteRecord]:
        note_ids = self._client.find_notes(f'deck:"{self._deck_name}"')
        if not note_ids:
            return []
        notes_info = self._client.notes_info(note_ids)

        all_card_ids = [cid for ni in notes_info for cid in ni.get("cards", [])]
        if all_card_ids:
            cards_info = self._client.cards_info(all_card_ids)
            cards_by_id = {c["cardId"]: c for c in cards_info}
        else:
            cards_by_id = {}

        records = []
        for ni in notes_info:
            fields_list = [v["value"] for v in sorted(ni["fields"].values(), key=lambda x: x["order"])]
            l2_text = extract_l2_from_fields(fields_list)
            translation = extract_translation(fields_list[1]) if len(fields_list) > 1 else ""
            disambig_key = extract_disambig_from_fields(fields_list)
            anki_guid = compute_guid(l2_text, "sl", disambig_key)

            card_records = []
            for cid in ni.get("cards", []):
                if cid not in cards_by_id:
                    continue
                c = cards_by_id[cid]
                ivl = c.get("ivl", 0)
                card_records.append(
                    CardRecord(
                        anki_card_id=cid,
                        ord=c["ord"],
                        queue=c["queue"],
                        reps=c.get("reps", 0),
                        lapses=c.get("lapses", 0),
                        stability=float(ivl) if ivl > 0 else 1.0,
                        difficulty=5.0,
                        due_date=date.today(),
                    )
                )

            records.append(
                NoteRecord(
                    anki_note_id=ni["noteId"],
                    anki_guid=anki_guid,
                    l2_text=l2_text,
                    translation=translation,
                    disambig_key=disambig_key,
                    mod=ni.get("mod", 0),
                    cards=card_records,
                )
            )
        return records


class OnlineWriter:
    """Write changes back to Anki via AnkiConnect."""

    def __init__(self, client: AnkiConnectClient, db: SRSDatabase) -> None:
        self._client = client
        self._db = db

    def update_note_fields(self, note_id: int, fields: dict[str, str]) -> None:
        self._client.update_note_fields(note_id, fields)

    def suspend(self, card_ids: list[int]) -> None:
        self._client.suspend(card_ids)

    def unsuspend(self, card_ids: list[int]) -> None:
        self._client.unsuspend(card_ids)

    def set_due_date(self, card_ids: list[int], days: str) -> None:
        self._client.set_due_date(card_ids, days)

    def write_revlog(
        self, *, cid: int, ease: int, ivl: int, last_ivl: int, factor: int, time_ms: int, type_: int
    ) -> None:
        self._db.enqueue_pending_revlog(
            cid=cid, ease=ease, ivl=ivl, last_ivl=last_ivl, factor=factor, time_ms=time_ms, type_=type_
        )

    def set_specific_value_of_card(self, card_id: int, keys: list[str], new_values: list[str]) -> None:
        self._client.set_specific_value_of_card(card_id, keys=keys, newValues=new_values)


class OfflineWriter:
    """Write changes directly into a raw sqlite3.Connection to collection.anki2.

    S3.5: only write_revlog() is implemented. Card-state operations (suspend,
    unsuspend, set_due_date, update_note_fields) are deferred to S3.7.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def update_note_fields(self, note_id: int, fields: dict[str, str]) -> None:
        pass  # deferred to S3.7

    def suspend(self, card_ids: list[int]) -> None:
        pass  # deferred to S3.7

    def unsuspend(self, card_ids: list[int]) -> None:
        pass  # deferred to S3.7

    def set_due_date(self, card_ids: list[int], days: str) -> None:
        pass  # deferred to S3.7

    def write_revlog(
        self, *, cid: int, ease: int, ivl: int, last_ivl: int, factor: int, time_ms: int, type_: int
    ) -> None:
        ts = int(_time.time() * 1000)
        self._conn.execute(
            "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, cid, -1, ease, ivl, last_ivl, factor, time_ms, type_),
        )
        self._conn.commit()

    def set_specific_value_of_card(self, card_id: int, keys: list[str], new_values: list[str]) -> None:
        pass  # deferred to S3.7


class AnkiSync:
    """Orchestrate bidirectional sync between TunaTale and Anki."""

    def __init__(
        self,
        *,
        db: SRSDatabase,
        mode: str = "online",
        client: AnkiConnectClient | None = None,
        deck_name: str | None = None,
        _reader=None,
        _writer=None,
        _anki_col_ver: int | None = None,
    ) -> None:
        self._db = db
        self._anki_col_ver = _anki_col_ver
        if _reader is not None:
            self._reader = _reader
        elif mode == "online":
            if client is None or deck_name is None:
                raise ValueError("client and deck_name required for mode='online'")
            self._reader = OnlineReader(client, deck_name)
        else:
            raise NotImplementedError(f"mode={mode!r} not yet implemented")

        if _writer is not None:
            self._writer = _writer
        elif mode == "online" and client is not None:
            self._writer = OnlineWriter(client, db)
        else:
            self._writer = None

    def sync_pull(self, dry_run: bool = False) -> PullReport:
        """Pull Anki → TunaTale. Returns a PullReport summarising changes."""
        report = PullReport()

        for rec in self._reader.get_note_records():
            expected_guid = compute_guid(rec.l2_text, "sl", rec.disambig_key)
            if rec.anki_guid != expected_guid:
                report.skipped_unknown_guid += 1
                continue

            local_item = self._db.get_collocation_by_guid(rec.anki_guid)
            if local_item is None:
                continue

            guid = rec.anki_guid
            local_dirty_fields = self._db.get_dirty_fields(guid)
            dirty_set = {f for f in local_dirty_fields.split(",") if f}

            local_translation = local_item.syntactic_unit.translation
            note_changed = False
            new_dirty_fields = dirty_set.copy()

            if rec.translation != local_translation:
                note_changed = True
                if "translation" in dirty_set:
                    conflict = SyncConflict(
                        guid=guid,
                        direction=None,
                        field="translation",
                        local_value=local_translation,
                        remote_value=rec.translation,
                        resolution="anki_wins",
                    )
                    report.conflicts.append(conflict)
                    if not dry_run:
                        self._db.record_sync_conflict(
                            guid=guid,
                            direction=None,
                            field="translation",
                            local=local_translation,
                            remote=rec.translation,
                            resolution="anki_wins",
                        )
                    new_dirty_fields.discard("translation")

            if note_changed:
                if not dry_run:
                    self._db.update_collocation_for_sync(
                        guid,
                        translation=rec.translation,
                        dirty_fields_str=",".join(sorted(new_dirty_fields)),
                    )
                report.notes_updated += 1

            for card_rec in rec.cards:
                direction = Direction.RECOGNITION if card_rec.ord == 0 else Direction.PRODUCTION
                local_dir = local_item.directions.get(direction)
                if local_dir is None:
                    continue

                new_state = (
                    SRSState.SUSPENDED
                    if card_rec.queue == -1
                    else SRSState.NEW
                    if card_rec.reps == 0
                    else SRSState.REVIEW
                )

                new_dirty_fsrs = local_dir.dirty_fsrs
                if local_dir.dirty_fsrs:
                    conflict = SyncConflict(
                        guid=guid,
                        direction=direction.value,
                        field="fsrs",
                        local_value=None,
                        remote_value=None,
                        resolution="anki_wins",
                    )
                    report.conflicts.append(conflict)
                    if not dry_run:
                        self._db.record_sync_conflict(
                            guid=guid,
                            direction=direction.value,
                            field="fsrs",
                            local=None,
                            remote=None,
                            resolution="anki_wins",
                        )
                    new_dirty_fsrs = False

                new_dir_state = DirectionState(
                    direction=direction,
                    due_date=card_rec.due_date,
                    stability=card_rec.stability,
                    difficulty=card_rec.difficulty,
                    reps=card_rec.reps,
                    lapses=card_rec.lapses,
                    state=new_state,
                    dirty_fsrs=new_dirty_fsrs,
                    anki_card_id=card_rec.anki_card_id,
                )

                if not dry_run:
                    self._db.update_direction(guid, direction, new_dir_state)
                report.directions_updated += 1

        return report

    def sync_push(self, dry_run: bool = False, force_fsrs: bool = False) -> PushReport:
        """Push TunaTale → Anki. Returns a PushReport summarising changes."""
        report = PushReport()

        for guid, anki_note_id, dirty_fields_str, item in self._db.list_dirty_field_edits():
            if anki_note_id is None:
                continue
            dirty_set = {f for f in dirty_fields_str.split(",") if f}
            fields: dict[str, str] = {}
            if "translation" in dirty_set:
                fields["Back"] = item.syntactic_unit.translation
            if not fields:
                continue
            if not dry_run:
                self._writer.update_note_fields(anki_note_id, fields)
                self._db.set_dirty_fields(guid, "")
            report.notes_pushed += 1

        for guid, direction, ds in self._db.list_dirty():
            if ds.anki_card_id is None:
                continue
            days_str = str(max(0, (ds.due_date - date.today()).days))
            if not dry_run:
                if ds.state == SRSState.SUSPENDED:
                    self._writer.suspend([ds.anki_card_id])
                else:
                    self._writer.unsuspend([ds.anki_card_id])
                self._writer.set_due_date([ds.anki_card_id], days_str)
                if ds.reps > 0:
                    ivl = max(1, round(ds.stability))
                    self._writer.write_revlog(
                        cid=ds.anki_card_id,
                        ease=3,
                        ivl=ivl,
                        last_ivl=ivl,
                        factor=2500,
                        time_ms=0,
                        type_=2,
                    )
                if force_fsrs:
                    schema_ok = self._anki_col_ver is None or self._anki_col_ver <= KNOWN_ANKI_SCHEMA_VER
                    if schema_ok:
                        ivl_val = max(1, round(ds.stability))
                        data_json = _json.dumps({"s": ds.stability, "d": ds.difficulty})
                        self._writer.set_specific_value_of_card(
                            ds.anki_card_id,
                            keys=["data", "ivl", "factor"],
                            new_values=[data_json, str(ivl_val), "2500"],
                        )
                self._db.mark_direction_clean(guid, direction)
            report.directions_pushed += 1

        return report
