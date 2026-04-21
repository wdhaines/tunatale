"""Bidirectional sync between TunaTale and Anki.

S3.4: sync_pull (Anki → TunaTale).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

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
    ) -> None:
        self._db = db
        if _reader is not None:
            self._reader = _reader
        elif mode == "online":
            if client is None or deck_name is None:
                raise ValueError("client and deck_name required for mode='online'")
            self._reader = OnlineReader(client, deck_name)
        else:
            raise NotImplementedError(f"mode={mode!r} not yet implemented")

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
