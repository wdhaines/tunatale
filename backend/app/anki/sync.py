"""Bidirectional sync between TunaTale and Anki.

S3.4: sync_pull (Anki → TunaTale).
S3.5: sync_push (TunaTale → Anki).
S3.6: --force-fsrs gate + setSpecificValueOfCard.
"""

from __future__ import annotations

import json as _json
import re
import sqlite3
import time as _time
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
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


def _safe_stem(word: str, prefix: str) -> str:
    """Sanitize word for use as a media filename stem: keep letters/digits/underscores."""
    sanitized = re.sub(r"[^\w\s]", "", word).replace(" ", "_")
    return f"{prefix}_{sanitized}"


def _factor_to_fsrs_difficulty(factor: int) -> float:
    """Map Anki ease factor (1300 hardest .. 3500+ easiest) to FSRS difficulty (10 .. 1).

    Linear approximation: factor=1300→10.0; factor=2500 (neutral)→4.5; factor=3500→1.0.
    """
    return max(1.0, min(10.0, (3500 - factor) / 220))


class DuplicateNoteError(Exception):
    """Raised by OfflineWriter.create_note when the note guid already exists."""

    def __init__(self, note_id: int) -> None:
        super().__init__(f"duplicate note: note_id={note_id}")
        self.note_id = note_id


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
    anki_due: int | None = None
    last_review: date | None = None
    # False when the source (e.g. AnkiConnect cardsInfo) does not reliably expose
    # FSRS stability/difficulty/due_date — sync_pull then preserves local FSRS
    # state instead of overwriting it with the placeholder values above.
    fsrs_known: bool = True


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


@dataclass
class CreateNewReport:
    count: int = 0
    created: int = 0
    linked: int = 0
    skipped: int = 0


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
                    anki_due=c.fsrs_state.anki_due,
                    last_review=c.fsrs_state.last_review,
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


class OfflineWriter:
    """Write changes directly into a raw sqlite3.Connection to collection.anki2.

    Every mutation sets ``usn = -1`` and bumps ``mod`` on the touched row plus
    ``col`` so AnkiWeb's next sync sees the change as local-dirty. See
    ``.claude/rules/anki-sync.md`` for the full contract.

    ``media_dir``: path to ``collection.media/`` directory on disk (optional).
        Required for ``store_media_file`` to actually write files.
    ``media_db_path``: explicit path to ``collection.media.db`` (optional).
        Defaults to ``media_dir/../collection.media.db`` when media_dir is set.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        media_dir: Path | None = None,
        media_db_path: Path | None = None,
    ) -> None:
        self._conn = conn
        self._media_dir = media_dir
        self._media_db_path = media_db_path

    def _bump_col(self, ts: int) -> None:
        self._conn.execute("UPDATE col SET mod = ?, usn = -1", (ts,))

    def update_note_fields(self, note_id: int, fields: dict[str, str]) -> None:
        from app.anki.notetype import SLOVENE_VOCAB_FIELD_NAMES

        row = self._conn.execute("SELECT flds FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row is None:
            return
        parts = row["flds"].split("\x1f")
        name_to_idx = {name: i for i, name in enumerate(SLOVENE_VOCAB_FIELD_NAMES)}
        for name, value in fields.items():
            idx = name_to_idx.get(name)
            if idx is None:
                raise ValueError(f"Unknown field name for Slovene Vocabulary notetype: {name!r}")
            parts[idx] = value
        new_flds = "\x1f".join(parts)
        ts = int(_time.time())
        self._conn.execute(
            "UPDATE notes SET flds = ?, mod = ?, usn = -1 WHERE id = ?",
            (new_flds, ts, note_id),
        )
        self._bump_col(ts)
        self._conn.commit()

    def suspend(self, card_ids: list[int]) -> None:
        ts = int(_time.time())
        placeholders = ",".join("?" * len(card_ids))
        self._conn.execute(
            f"UPDATE cards SET queue = -1, mod = ?, usn = -1 WHERE id IN ({placeholders})",
            (ts, *card_ids),
        )
        self._bump_col(ts)
        self._conn.commit()

    def unsuspend(self, card_ids: list[int]) -> None:
        ts = int(_time.time())
        placeholders = ",".join("?" * len(card_ids))
        # Restore queue from type: new→0, learning/relearning→1, review→2.
        self._conn.execute(
            f"""
            UPDATE cards
            SET queue = CASE
                WHEN type = 0 THEN 0
                WHEN type = 1 THEN 1
                WHEN type = 3 THEN 1
                ELSE 2
            END,
            mod = ?, usn = -1
            WHERE id IN ({placeholders}) AND queue = -1
            """,
            (ts, *card_ids),
        )
        self._bump_col(ts)
        self._conn.commit()

    def set_due_date(self, card_ids: list[int], days: str) -> None:
        days_int = int(days)
        col_row = self._conn.execute("SELECT crt FROM col LIMIT 1").fetchone()
        col_crt = int(col_row["crt"] or 0)
        from datetime import date as _date

        days_since_crt = (_date.today() - _date.fromtimestamp(col_crt)).days
        new_due = days_since_crt + days_int
        new_ivl = max(1, days_int)
        ts = int(_time.time())
        placeholders = ",".join("?" * len(card_ids))
        # Preserve suspension (queue=-1): only update due/ivl/mod/usn.
        # For other states, promote to review (queue=2, type=2).
        self._conn.execute(
            f"""
            UPDATE cards
            SET due = ?,
                ivl = ?,
                queue = CASE WHEN queue = -1 THEN queue ELSE 2 END,
                type  = CASE WHEN queue = -1 THEN type  ELSE 2 END,
                mod = ?,
                usn = -1
            WHERE id IN ({placeholders})
            """,
            (new_due, new_ivl, ts, *card_ids),
        )
        self._bump_col(ts)
        self._conn.commit()

    def write_revlog(
        self, *, cid: int, ease: int, ivl: int, last_ivl: int, factor: int, time_ms: int, type_: int
    ) -> None:
        ts = int(_time.time() * 1000)
        max_row = self._conn.execute("SELECT MAX(id) FROM revlog").fetchone()
        max_id = (max_row[0] or 0) if max_row else 0
        rid = max(ts, max_id + 1)
        self._conn.execute(
            "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, cid, -1, ease, ivl, last_ivl, factor, time_ms, type_),
        )
        self._bump_col(int(_time.time()))
        self._conn.commit()

    def set_specific_value_of_card(self, card_id: int, keys: list[str], new_values: list[str]) -> None:
        pass  # deferred to S3.7

    def create_note(self, deck_name: str, model_name: str, fields: dict, tags: list) -> int:
        """Insert a new note + cards into the collection.

        Raises DuplicateNoteError if the computed GUID already exists.
        No col.scm change — data-only insert against an existing notetype.
        """
        import hashlib
        import re

        from app.anki.notetype import SLOVENE_VOCAB_FIELD_NAMES
        from app.anki.sqlite_reader import find_deck_id
        from app.common.guid import compute_guid

        mid_row = self._conn.execute("SELECT id FROM notetypes WHERE name = ?", (model_name,)).fetchone()
        if mid_row is None:
            raise ValueError(f"Notetype {model_name!r} not found in notetypes table")
        mid = mid_row[0]

        did = find_deck_id(self._conn, deck_name)
        if did is None:
            raise ValueError(f"Deck {deck_name!r} not found")

        # "Slovene" is SLOVENE_VOCAB_FIELD_NAMES[0] — the sort field for this single-language project
        sfld = re.sub(r"<[^>]+>", "", fields.get("Slovene", "")).strip()
        disambig = fields.get("DisambigKey", "")
        anki_guid = compute_guid(sfld, "sl", disambig)

        existing = self._conn.execute("SELECT id FROM notes WHERE guid = ?", (anki_guid,)).fetchone()
        if existing:
            raise DuplicateNoteError(existing[0])

        # Anki convention: ms-epoch IDs; bump past existing max in case the clock hasn't caught up
        ts_ms = int(_time.time() * 1000)
        max_row = self._conn.execute("SELECT MAX(id) FROM notes").fetchone()
        note_id = max(ts_ms, (max_row[0] or 0) + 1)

        flds = "\x1f".join(fields.get(name, "") for name in SLOVENE_VOCAB_FIELD_NAMES)
        csum = int(hashlib.sha1(sfld.encode()).hexdigest()[:8], 16)
        ts = int(_time.time())
        tags_str = f" {' '.join(tags)} " if tags else ""

        self._conn.execute(
            "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data) "
            "VALUES (?, ?, ?, ?, -1, ?, ?, ?, ?, 0, '')",
            (note_id, anki_guid, mid, ts, tags_str, flds, sfld, csum),
        )

        tmpl_rows = self._conn.execute("SELECT ord FROM templates WHERE ntid = ? ORDER BY ord", (mid,)).fetchall()
        due_row = self._conn.execute("SELECT MAX(due) + 1 FROM cards WHERE type = 0").fetchone()
        next_due = due_row[0] if due_row and due_row[0] else 1

        for (ord_,) in tmpl_rows:
            card_id = note_id + ord_
            while self._conn.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone():
                card_id += 1
            self._conn.execute(
                "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl, "
                "factor, reps, lapses, left, odue, odid, flags, data) "
                "VALUES (?, ?, ?, ?, ?, -1, 0, 0, ?, 0, 0, 0, 0, 0, 0, 0, 0, '')",
                (card_id, note_id, did, ord_, ts, next_due + ord_),
            )

        self._bump_col(ts)
        self._conn.commit()
        return note_id

    def store_media_file(self, filename: str, data: bytes) -> None:
        """Write media file to collection.media dir and register in collection.media.db."""
        if self._media_dir is None:
            return
        (self._media_dir / filename).write_bytes(data)

        if self._media_db_path:
            media_db = self._media_db_path
        else:
            # Modern Anki (≥2.1.55) renamed the media DB to collection.media.db2
            media_db2 = self._media_dir.parent / "collection.media.db2"
            media_db = media_db2 if media_db2.exists() else (self._media_dir.parent / "collection.media.db")
        if not media_db.exists():
            return
        import hashlib

        csum = hashlib.sha1(data).hexdigest()
        mtime = int(_time.time())
        try:
            mconn = sqlite3.connect(str(media_db))
            mconn.execute(
                "INSERT OR REPLACE INTO media (fname, csum, mtime, dirty) VALUES (?, ?, ?, 1)",
                (filename, csum, mtime),
            )
            mconn.commit()
            mconn.close()
        except sqlite3.Error:
            pass  # non-fatal: Anki's Check Media will register the file on next open

    def get_cards_for_note(self, note_id: int) -> dict[int, int]:
        rows = self._conn.execute("SELECT ord, id FROM cards WHERE nid = ? ORDER BY ord", (note_id,)).fetchall()
        return {row[0]: row[1] for row in rows}


def _direction_differs(local: DirectionState, candidate: DirectionState) -> bool:
    """Return True only if a sync-relevant field changed between local and candidate.

    Excludes last_synced_at and last_rating from the comparison
    so benign timestamp updates don't trigger a spurious write.
    """
    return (
        local.state != candidate.state
        or local.stability != candidate.stability
        or local.difficulty != candidate.difficulty
        or local.due_date != candidate.due_date
        or local.reps != candidate.reps
        or local.lapses != candidate.lapses
        or local.dirty_fsrs != candidate.dirty_fsrs
        or local.anki_card_id != candidate.anki_card_id
        or local.anki_due != candidate.anki_due
        or local.last_review != candidate.last_review
    )


class AnkiSync:
    """Orchestrate bidirectional sync between TunaTale and Anki."""

    def __init__(
        self,
        *,
        db: SRSDatabase,
        _reader=None,
        _writer=None,
        _anki_col_ver: int | None = None,
    ) -> None:
        self._db = db
        self._anki_col_ver = _anki_col_ver
        if _reader is not None:
            self._reader = _reader
        else:
            raise ValueError("_reader is required")

        if _writer is not None:
            self._writer = _writer
        else:
            raise ValueError("_writer is required")

    def sync_pull(self, dry_run: bool = False) -> PullReport:
        """Pull Anki → TunaTale. Returns a PullReport summarising changes."""
        report = PullReport()

        for rec in self._reader.get_note_records():
            # Primary: stable pointer set by sync_create_new. Handles duplicate
            # computed-guid homonyms by ignoring the un-linked orphan Anki notes.
            local_item = self._db.get_collocation_by_anki_note_id(rec.anki_note_id)
            if local_item is None:
                # Fallback: row was never linked (e.g., imported before anki_note_id
                # column was populated). Validate guid before trusting it.
                expected_guid = compute_guid(rec.l2_text, "sl", rec.disambig_key)
                if rec.anki_guid != expected_guid:
                    report.skipped_unknown_guid += 1
                    continue
                local_item = self._db.get_collocation_by_guid(rec.anki_guid)
                if local_item is None:
                    continue
                # If the row is already linked to a different Anki note, this
                # record is an orphan — skip it.
                if local_item.anki_note_id is not None and local_item.anki_note_id != rec.anki_note_id:
                    continue
                guid = rec.anki_guid
            else:
                guid = local_item.guid
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

                if local_dir.dirty_fsrs:
                    # Local has unpushed grades — preserve all FSRS state; sync_push
                    # will flush. Not a conflict: this is queued local work.
                    new_dir_state = replace(
                        local_dir,
                        anki_card_id=card_rec.anki_card_id,
                        anki_due=card_rec.anki_due,
                        last_synced_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
                    )
                elif card_rec.fsrs_known:
                    if card_rec.queue == -1:
                        new_state = SRSState.SUSPENDED
                    elif card_rec.queue in (-2, -3):
                        new_state = SRSState.BURIED
                    elif card_rec.queue == 1:
                        new_state = SRSState.LEARNING
                    elif card_rec.queue == 3:
                        new_state = SRSState.RELEARNING
                    elif card_rec.reps == 0:
                        new_state = SRSState.NEW
                    else:
                        new_state = SRSState.REVIEW
                    new_dir_state = DirectionState(
                        direction=direction,
                        due_date=card_rec.due_date,
                        stability=card_rec.stability,
                        difficulty=card_rec.difficulty,
                        reps=card_rec.reps,
                        lapses=card_rec.lapses,
                        state=new_state,
                        dirty_fsrs=False,
                        anki_card_id=card_rec.anki_card_id,
                        anki_due=card_rec.anki_due,
                        last_review=card_rec.last_review,
                        last_synced_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
                    )
                else:
                    # Online pull: FSRS state not available via cardsInfo. Keep
                    # local stability/difficulty/due_date/reps/lapses and the
                    # existing dirty_fsrs so the next push can still flush.
                    if card_rec.queue == -1:
                        new_state = SRSState.SUSPENDED
                    elif card_rec.queue in (-2, -3):
                        new_state = SRSState.BURIED
                    elif card_rec.queue == 1:
                        new_state = SRSState.LEARNING
                    elif card_rec.queue == 3:
                        new_state = SRSState.RELEARNING
                    elif card_rec.reps == 0:
                        new_state = SRSState.NEW
                    else:
                        new_state = SRSState.REVIEW
                    new_dir_state = replace(
                        local_dir,
                        state=new_state,
                        anki_card_id=card_rec.anki_card_id,
                        anki_due=card_rec.anki_due,
                        last_synced_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
                    )

                if _direction_differs(local_dir, new_dir_state):
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
                fields["English"] = item.syntactic_unit.translation
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
                    ease = ds.last_rating if ds.last_rating is not None else 3
                    factor = max(1300, min(13000, round(ds.difficulty * 1000)))
                    self._writer.write_revlog(
                        cid=ds.anki_card_id,
                        ease=ease,
                        ivl=ivl,
                        last_ivl=ivl,
                        factor=factor,
                        time_ms=0,
                        type_=2,
                    )
                if force_fsrs:
                    schema_ok = self._anki_col_ver is None or self._anki_col_ver <= KNOWN_ANKI_SCHEMA_VER
                    if schema_ok:
                        ivl_val = max(1, round(ds.stability))
                        data_json = _json.dumps({"s": ds.stability, "d": ds.difficulty})
                        factor_val = max(1300, min(13000, round(ds.difficulty * 1000)))
                        self._writer.set_specific_value_of_card(
                            ds.anki_card_id,
                            keys=["data", "ivl", "factor"],
                            new_values=[data_json, str(ivl_val), str(factor_val)],
                        )
                self._db.mark_direction_clean(guid, direction)
            report.directions_pushed += 1

        return report

    async def sync_create_new(
        self,
        *,
        deck_name: str,
        model_name: str,
        dry_run: bool = False,
        _media_fn=None,
    ) -> CreateNewReport:
        """Create Anki notes for SRS items that have no anki_note_id yet.

        Returns a CreateNewReport with created/linked/skipped counters.
        """
        items = self._db.list_items_without_anki_note()
        if dry_run:
            return CreateNewReport(count=len(items))

        used_image_urls: set[str] = set()
        created = 0
        linked = 0
        skipped = 0

        for guid, item in items:
            word = item.syntactic_unit.text
            english = item.syntactic_unit.translation
            audio_tag = ""
            image_tag = ""

            if _media_fn is not None:
                media = await _media_fn(word, english, used_image_urls=used_image_urls)
                if media is not None and media.audio_bytes is not None:
                    prefix = "sl" if media.audio_source == "forvo" else "tts"
                    audio_filename = f"{_safe_stem(word, prefix)}.mp3"
                    self._writer.store_media_file(audio_filename, media.audio_bytes)
                    audio_tag = f"[sound:{audio_filename}]"
                if media is not None and media.image_bytes is not None:
                    ext = media.image_ext or "jpg"
                    img_filename = f"{_safe_stem(english, 'img')}.{ext}"
                    self._writer.store_media_file(img_filename, media.image_bytes)
                    image_tag = f'<img src="{img_filename}">'

            fields = {
                "Slovene": word,
                "English": english,
                "Audio": audio_tag,
                "Image": image_tag,
                "Grammar": item.syntactic_unit.grammar or "",
                "Note": item.syntactic_unit.source_sentence or "",
                "DisambigKey": item.syntactic_unit.disambig_key or "",
            }

            try:
                note_id = self._writer.create_note(deck_name, model_name, fields, ["tunatale"])
                created += 1
            except DuplicateNoteError as exc:
                note_id = exc.note_id
                linked += 1

            cards_by_ord = self._writer.get_cards_for_note(note_id)
            _ORD_TO_DIR = {0: Direction.RECOGNITION, 1: Direction.PRODUCTION}
            card_ids = {_ORD_TO_DIR[ord_]: cid for ord_, cid in cards_by_ord.items() if ord_ in _ORD_TO_DIR}
            self._db.set_anki_ids(guid, note_id, card_ids)

        count = created + linked + skipped
        return CreateNewReport(count=count, created=created, linked=linked, skipped=skipped)


def main(
    argv: list[str] | None = None,
    *,
    _settings=None,
    _safe_open_fn=None,
    _force_fsrs_ack_path: Path | None = None,
    _db=None,
) -> int:
    import argparse
    import sys

    from app.anki.safety import safe_open
    from app.config import settings as _default_settings
    from app.srs.database import SRSDatabase

    _s = _settings if _settings is not None else _default_settings
    _so = _safe_open_fn if _safe_open_fn is not None else safe_open
    _ack_path = (
        _force_fsrs_ack_path
        if _force_fsrs_ack_path is not None
        else Path("~/.tunatale/force_fsrs_ack.txt").expanduser()
    )

    # Get database instance
    db = _db if _db is not None else SRSDatabase(_s.database_url.removeprefix("sqlite:///"))

    parser = argparse.ArgumentParser(description="TunaTale ↔ Anki bidirectional sync")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-fsrs", action="store_true", dest="force_fsrs")
    args = parser.parse_args(argv)

    if args.force_fsrs:
        interactive = sys.stdin.isatty()
        try:
            ensure_force_fsrs_ack(_ack_path, interactive=interactive)
        except ForceFsrsNotAcknowledgedError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    try:
        with _so(_s.anki_collection_path, mode="rw") as ctx:
            col_ver = ctx.conn.execute("SELECT ver FROM col").fetchone()[0]
            reader = OfflineReader(ctx.conn, _s.anki_deck_name)
            writer = OfflineWriter(ctx.conn)
            sync = AnkiSync(db=db, _reader=reader, _writer=writer, _anki_col_ver=col_ver)
            push = sync.sync_push(dry_run=args.dry_run, force_fsrs=args.force_fsrs)
            pull = sync.sync_pull(dry_run=args.dry_run)
            print(
                f"Pull: {pull.notes_updated} notes updated, "
                f"{pull.directions_updated} directions, "
                f"{len(pull.conflicts)} conflicts"
            )
            print(f"Push: {push.notes_pushed} notes, {push.directions_pushed} directions")
            return 0
    except RuntimeError as e:
        print(f"Error opening collection: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(main())
