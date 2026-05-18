"""Bidirectional sync between TunaTale and Anki.

S3.4: sync_pull (Anki → TunaTale).
S3.5: sync_push (TunaTale → Anki).
S3.6: --force-fsrs gate + setSpecificValueOfCard.
"""

from __future__ import annotations

import json as _json
import logging
import re
import sqlite3
import time as _time
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from app.anki.anki_connect import AnkiConnectClient
from app.anki.protobuf_wire import (
    compute_anki_day_index,
    find_varint_field,
    pb_remove_field,
    pb_replace_or_insert_varint,
)
from app.anki.sqlite_reader import (
    extract_disambig_from_fields,
    extract_l2_from_fields,
    extract_translation,
    fetch_cards_for_notes,
    fetch_notes_for_deck,
    find_deck_id,
)
from app.common.guid import compute_guid
from app.models.srs_item import Direction, DirectionState, Rating, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.srs.queue_stats import resolve_learning_steps, resolve_relearning_steps

_log = logging.getLogger(__name__)

KNOWN_ANKI_SCHEMA_VER = 18

_MEDIA_DIR = Path(__file__).parent.parent.parent / "media"


def _safe_stem(word: str, prefix: str) -> str:
    """Sanitize word for use as a media filename stem: keep letters/digits/underscores."""
    sanitized = re.sub(r"[^\w\s]", "", word).replace(" ", "_")
    return f"{prefix}_{sanitized}"


def _copy_tt_media_to_anki(writer: OfflineWriter, filename: str) -> None:
    """Copy a media file from TT's media dir into Anki's collection.media via the writer.

    Silently skips if the file doesn't exist on disk (logs a warning).
    """
    src = _MEDIA_DIR / filename
    if not src.exists():
        _log.warning("Media file not found, skipping copy to Anki: %s", src)
        return
    writer.store_media_file(filename, src.read_bytes())


class DuplicateNoteError(Exception):
    """Raised by OfflineWriter.create_note when the note guid already exists."""

    def __init__(self, note_id: int) -> None:
        super().__init__(f"duplicate note: note_id={note_id}")
        self.note_id = note_id


class OrphanThresholdExceededError(Exception):
    """Refuse to reset Anki ids when too many TT rows look orphaned.

    Trips when >25% of linked directions reference card_ids that are not in
    the live Anki collection — usually a sign the configured deck path is
    pointing at the wrong file, in which case wholesale ID reset would erase
    the user's actual sync state.
    """


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
    anki_card_mod: int | None = None
    last_review: datetime | None = None
    last_review_ms: int | None = None
    # MIN(revlog.id) for this card. Used by sync_pull to detect the
    # NEW→graded transition when local_dir.prior_state is None (a record
    # written before prior_state was set during sync; self-heal on re-sync).
    first_review_ms: int | None = None
    # False when the source (e.g. AnkiConnect cardsInfo) does not reliably expose
    # FSRS stability/difficulty/due_date — sync_pull then preserves local FSRS
    # state instead of overwriting it with the placeholder values above.
    fsrs_known: bool = True
    card_type: int = 0  # Anki's cards.type (0=New, 1=Learn, 2=Review, 3=Relearn)
    # Required to mirror Anki's queue=1 learning state. Without these, a graded
    # card resumes through the FSRS REVIEW branch and graduates prematurely.
    left: int | None = None
    due_at: datetime | None = None


@dataclass
class NoteRecord:
    anki_note_id: int
    anki_guid: str
    l2_text: str
    translation: str
    note: str
    disambig_key: str
    mod: int
    cards: list[CardRecord]
    sentence_translation: str = ""
    is_cloze: bool = False


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
    notes_created_from_anki: int = 0


_BACK_EXTRA_TRANS = re.compile(r"^\s*<i>([^<]+)</i>\s*<br\s*/?>\s*<br\s*/?>\s*(.*)", re.DOTALL)
_BACK_EXTRA_SENT = re.compile(
    r"^\s*<i>([^<]+)</i>\s*<br\s*/?>\s*<br\s*/?>\s*<span class=\"st\">([^<]*)</span>\s*(.*)", re.DOTALL
)
_SOUND_TAG = re.compile(r"\s*\[sound:[^\]]+\]\s*")


def _strip_sound_tags(back_extra: str) -> str:
    """Remove trailing [sound:...] tags + trailing <br> from a Back Extra string."""
    stripped = _SOUND_TAG.sub("", back_extra)
    stripped = re.sub(r"(?:<br\s*/?>)*\s*$", "", stripped)
    return stripped.rstrip()


def extract_cloze_translation(back_extra: str) -> str:
    """Extract word-level translation from a Cloze note's back_extra (<i>…) field."""
    back_extra = _strip_sound_tags(back_extra)
    m = _BACK_EXTRA_SENT.match(back_extra) or _BACK_EXTRA_TRANS.match(back_extra)
    if m:
        return m.group(1).strip()
    return extract_translation(back_extra)


def extract_cloze_sentence_translation(back_extra: str) -> str:
    """Extract sentence-level translation from a Cloze note's back_extra (<span class="st">…)."""
    back_extra = _strip_sound_tags(back_extra)
    m = _BACK_EXTRA_SENT.match(back_extra)
    if m:
        return m.group(2).strip()
    return ""


def build_cloze_back_extra(
    translation: str, sentence_translation: str, note: str = "", sentence_audio_filename: str | None = None
) -> str:
    """Compose a Cloze note's `Back Extra` field from its parts.

    Format: `<i>WORD</i><br><br><span class="st">SENTENCE</span><br><br>NOTE<br><br>[sound:filename]`,
    skipping any empty part. Single source of truth for both card creation
    (sync_create_new) and edit-push (sync_push).
    """
    parts: list[str] = []
    if translation:
        parts.append(f"<i>{translation}</i>")
    if sentence_translation:
        parts.append(f'<span class="st">{sentence_translation}</span>')
    if note:
        parts.append(note)
    if sentence_audio_filename:
        parts.append(f"[sound:{sentence_audio_filename}]")
    return "<br><br>".join(parts)


def extract_cloze_note(back_extra: str) -> str:
    """Extract note body from a Cloze note's back_extra (after translation/sentence spans)."""
    back_extra = _strip_sound_tags(back_extra)
    m = _BACK_EXTRA_SENT.match(back_extra)
    if m:
        return re.sub(r"^(?:<br\s*/?>)+", "", m.group(3).strip()).strip()
    m = _BACK_EXTRA_TRANS.match(back_extra)
    if m:
        return m.group(2).strip()
    return ""


def _local_today_4am(now: datetime | None = None) -> datetime:
    """Return the datetime of today's 4 AM rollover in local timezone.

    Mirrors Anki's day-cutoff concept — entries with a revlog.id before this
    timestamp are "before today" for the purpose of counting introductions.
    Returns the most recent 4 AM (yesterday if before 4 AM today).
    Accepts an optional *now* override for testability.
    """
    now = now or datetime.now()
    if now.tzinfo is None:
        now = now.astimezone()
    local_tz = now.tzinfo
    today_4am = datetime.combine(now.date(), time(4), tzinfo=local_tz)
    if now < today_4am:
        today_4am = datetime.combine(now.date() - timedelta(days=1), time(4), tzinfo=local_tz)
    return today_4am


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

        # Fetch last review timestamp from revlog for each card
        cid_list = [c.id for c in cards]
        last_revlog_ms: dict[int, int] = {}
        first_revlog_ms: dict[int, int] = {}
        if cid_list:  # pragma: no branch
            placeholders = ",".join("?" * len(cid_list))
            rows = self._conn.execute(
                f"SELECT cid, MIN(id), MAX(id) FROM revlog WHERE cid IN ({placeholders}) GROUP BY cid",
                cid_list,
            ).fetchall()
            for cid, min_ms, max_ms in rows:
                first_revlog_ms[cid] = min_ms
                last_revlog_ms[cid] = max_ms

        cards_by_note: dict[int, list] = {}
        for c in cards:
            cards_by_note.setdefault(c.note_id, []).append(c)

        # Detect Cloze notetype — its back_extra field (fields[1]) is HTML, not plain text
        cloze_mid = None
        try:
            cloze_mid_row = self._conn.execute("SELECT id FROM notetypes WHERE name = 'Cloze'").fetchone()
            cloze_mid = cloze_mid_row[0] if cloze_mid_row else None
        except sqlite3.OperationalError:
            pass  # notetypes table may not exist in test/minimal collections

        records = []
        for note in notes:
            is_cloze = cloze_mid is not None and note.mid == cloze_mid
            if is_cloze:
                back_extra = note.fields[1] if len(note.fields) > 1 else ""
                translation = extract_cloze_translation(back_extra)
                sentence_translation = extract_cloze_sentence_translation(back_extra)
                note_text = extract_cloze_note(back_extra)
                l2_text = extract_l2_from_fields(note.fields)
                disambig_key = ""
            else:
                l2_text = extract_l2_from_fields(note.fields)
                translation = extract_translation(note.fields[1]) if len(note.fields) > 1 else ""
                disambig_key = extract_disambig_from_fields(note.fields)
                note_text = ""
            card_records = [
                CardRecord(
                    anki_card_id=c.id,
                    ord=c.ord,
                    queue=c.queue,
                    reps=c.reps,
                    lapses=c.lapses,
                    card_type=c.card_type,
                    stability=c.fsrs_state.stability,
                    difficulty=c.fsrs_state.difficulty,
                    due_date=c.fsrs_state.due_date,
                    anki_due=c.fsrs_state.anki_due,
                    anki_card_mod=c.mod,
                    last_review=c.fsrs_state.last_review,
                    last_review_ms=last_revlog_ms.get(c.id),
                    first_review_ms=first_revlog_ms.get(c.id),
                    left=c.fsrs_state.left,
                    due_at=c.fsrs_state.due_at,
                )
                for c in cards_by_note.get(note.id, [])
            ]
            records.append(
                NoteRecord(
                    anki_note_id=note.id,
                    anki_guid=note.anki_guid,
                    l2_text=l2_text,
                    translation=translation,
                    sentence_translation=sentence_translation if is_cloze else "",
                    note=note_text,
                    disambig_key=disambig_key,
                    mod=note.mod,
                    cards=card_records,
                    is_cloze=is_cloze,
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

        row = self._conn.execute("SELECT flds, mid FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row is None:
            return
        # Detect notetype: Cloze notes have fields ["Text", "Back Extra"];
        # everything else falls through to SLOVENE_VOCAB_FIELD_NAMES. The
        # notetypes table is absent in some unit-test fixtures — treat that
        # as "use the legacy Slovene Vocabulary mapping."
        nt_name = ""
        try:
            nt_row = self._conn.execute("SELECT name FROM notetypes WHERE id = ?", (row["mid"],)).fetchone()
            if nt_row is not None:
                nt_name = nt_row["name"] or ""
        except sqlite3.OperationalError:
            nt_name = ""
        if nt_name == "Cloze":
            field_names: list[str] = ["Text", "Back Extra"]
        else:
            field_names = list(SLOVENE_VOCAB_FIELD_NAMES)
        parts = row["flds"].split("\x1f")
        name_to_idx = {name: i for i, name in enumerate(field_names)}
        for name, value in fields.items():
            idx = name_to_idx.get(name)
            if idx is None:
                raise ValueError(f"Unknown field name for {nt_name or 'Slovene Vocabulary'} notetype: {name!r}")
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
        col_crt = int(col_row[0] if isinstance(col_row, (tuple, list)) else col_row["crt"] or 0)
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
                left  = CASE WHEN queue = -1 THEN left ELSE 0 END,
                mod = ?,
                usn = -1
            WHERE id IN ({placeholders})
            """,
            (new_due, new_ivl, ts, *card_ids),
        )
        self._bump_col(ts)
        self._conn.commit()

    def get_current_card_state(self, card_id: int) -> dict | None:
        """Return Anki's current `queue`/`type`/`left` for the card, or None
        if the card doesn't exist. Used by sync_push (Fix 3) to skip writes
        when Anki has more progress than TT for the same card.
        """
        row = self._conn.execute(
            "SELECT queue, type, IFNULL(left, 0) FROM cards WHERE id = ?",
            (card_id,),
        ).fetchone()
        if row is None:
            return None
        return {"queue": row[0], "type": row[1], "left": row[2]}

    def set_learning_state(self, card_id: int, left: int, due_at: int, *, type_: int = 1) -> None:
        """Update a learning/relearning card's left, due, queue, type.

        ``type_``: 1 for LEARNING (new card walking through learn_steps),
        3 for RELEARNING (review card lapsed and walking through relearn_steps).
        ``queue`` is always 1 (intra-day learning queue) — Anki uses queue=3 only
        when the next step is ≥1 day, which TunaTale doesn't currently emit.

        Suspended cards (queue=-1) keep their suspension; left/due/type/mod still
        update so the card resumes correctly when later unsuspended.
        """
        ts = int(_time.time())
        self._conn.execute(
            """
            UPDATE cards
            SET left = ?,
                due = ?,
                queue = CASE WHEN queue = -1 THEN queue ELSE 1 END,
                type = ?,
                mod = ?,
                usn = -1
            WHERE id = ?
            """,
            (left, due_at, type_, ts, card_id),
        )
        self._bump_col(ts)
        self._conn.commit()

    def write_revlog(
        self,
        *,
        cid: int,
        ease: int,
        ivl: int,
        last_ivl: int,
        factor: int,
        time_ms: int,
        type_,
        preferred_id=None,
        is_lapse: bool = False,
        ds_reps: int | None = None,
        ds_lapses: int | None = None,
    ) -> None:
        max_row = self._conn.execute("SELECT MAX(id) FROM revlog").fetchone()
        max_id = (max_row[0] or 0) if max_row else 0
        base = preferred_id if preferred_id is not None else int(_time.time() * 1000)
        rid = max(base, max_id + 1)
        self._conn.execute(
            "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, cid, -1, ease, ivl, last_ivl, factor, time_ms, type_),
        )
        ts = int(_time.time())
        lapse_inc = 1 if is_lapse else 0
        self._conn.execute(
            "UPDATE cards SET reps = MAX(reps + 1, ?), lapses = MAX(lapses + ?, ?), mod = ?, usn = -1 WHERE id = ?",
            (ds_reps or 0, lapse_inc, ds_lapses or 0, ts, cid),
        )
        self._bump_col(ts)
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

    def create_cloze_note(
        self,
        deck_name: str,
        cloze_text: str,
        back_extra: str = "",
        tags: list[str] | None = None,
    ) -> int:
        """Insert a new Cloze note + cards into the collection.

        Cloze notetype is Anki built-in: fields are Text + Back Extra. One card
        per c1, c2, ... cloze number. For Phase F we use c1 only → one card per note.

        Raises DuplicateNoteError if the computed GUID already exists.
        Raises ValueError if the Cloze notetype is not found.
        No col.scm change.
        """
        import hashlib

        from app.anki.sqlite_reader import find_deck_id
        from app.common.guid import compute_guid

        mid_row = self._conn.execute("SELECT id FROM notetypes WHERE name = 'Cloze'").fetchone()
        if mid_row is None:
            raise ValueError("Cloze notetype not found in collection")
        mid = mid_row[0]

        did = find_deck_id(self._conn, deck_name)
        if did is None:
            raise ValueError(f"Deck {deck_name!r} not found")

        sfld = cloze_text
        anki_guid = compute_guid(cloze_text, "sl", "")

        existing = self._conn.execute("SELECT id FROM notes WHERE guid = ?", (anki_guid,)).fetchone()
        if existing:
            raise DuplicateNoteError(existing[0])

        ts_ms = int(_time.time() * 1000)
        max_row = self._conn.execute("SELECT MAX(id) FROM notes").fetchone()
        note_id = max(ts_ms, (max_row[0] or 0) + 1)

        flds = f"{cloze_text}\x1f{back_extra}"
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

    # ── Protobuf field numbers in decks.common ──────────────────────────────
    _DECKS_COMMON_LAST_DAY_STUDIED = 3
    _DECKS_COMMON_NEW_TODAY = 4
    _DECKS_COMMON_REVIEW_TODAY = 5
    _DECKS_COMMON_SECONDS_TODAY = 7

    def count_revlog_before(self, cid: int, ts_ms: int) -> int:
        """Count revlog entries for *cid* whose ID < *ts_ms*."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM revlog WHERE cid = ? AND id < ?",
            (cid, ts_ms),
        ).fetchone()
        return row[0] if row else 0

    def get_deck_id_for_card(self, cid: int) -> int | None:
        """Return the ``did`` (deck id) for *cid*, or None if not found."""
        row = self._conn.execute("SELECT did FROM cards WHERE id = ?", (cid,)).fetchone()
        return row[0] if row else None

    def bump_deck_new_today(self, deck_id: int, today_day_index: int) -> None:
        """Increment the "new studied today" counter for *deck_id*.

        Mirrors Anki's ``update_counters_after_answering_card``: reads/writes
        ``decks.common`` protobuf blob.

        TODO: review_today and seconds_today follow the same protobuf
        path. Extend if those counters ever drift.
        """
        row = self._conn.execute("SELECT common FROM decks WHERE id = ?", (deck_id,)).fetchone()
        if row is None:
            return
        blob = bytes(row[0]) if row[0] else b""

        last_day = find_varint_field(blob, self._DECKS_COMMON_LAST_DAY_STUDIED) or 0
        current_new = find_varint_field(blob, self._DECKS_COMMON_NEW_TODAY) or 0
        if last_day < today_day_index:
            blob = pb_remove_field(blob, self._DECKS_COMMON_NEW_TODAY)
            blob = pb_remove_field(blob, self._DECKS_COMMON_REVIEW_TODAY)
            blob = pb_remove_field(blob, self._DECKS_COMMON_SECONDS_TODAY)
            blob = pb_replace_or_insert_varint(blob, self._DECKS_COMMON_LAST_DAY_STUDIED, today_day_index)
            current_new = 0
        blob = pb_replace_or_insert_varint(blob, self._DECKS_COMMON_NEW_TODAY, current_new + 1)
        now_ts = int(_time.time())
        self._conn.execute(
            "UPDATE decks SET common = ?, mtime_secs = ?, usn = -1 WHERE id = ?",
            (blob, now_ts, deck_id),
        )
        self._bump_col(now_ts)
        self._conn.commit()

    def list_decks_with_revlog_today(self, today_4am_ms: int) -> list[int]:
        """Return distinct deck IDs that have at least one revlog entry since *today_4am_ms*.

        Used by `AnkiSync._recompute_anki_new_today_all_decks` to know which
        decks need their newToday counter rewritten.
        """
        try:
            rows = self._conn.execute(
                "SELECT DISTINCT c.did FROM revlog r JOIN cards c ON c.id = r.cid WHERE r.id >= ?",
                (today_4am_ms,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [r[0] for r in rows]

    def count_first_grades_today_for_deck(self, deck_id: int, today_4am_ms: int) -> int:
        """Count distinct cards in *deck_id* whose first-ever revlog id >= *today_4am_ms*.

        Mirrors Anki's "newToday" semantic: a card transitions NEW→non-NEW on
        its first revlog entry, and that's the moment newToday increments.
        Subsequent grades of the same card do not bump it.
        """
        try:
            row = self._conn.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT r.cid FROM revlog r JOIN cards c ON c.id = r.cid AND c.did = ?
                    GROUP BY r.cid HAVING MIN(r.id) >= ?
                )
                """,
                (deck_id, today_4am_ms),
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
        return row[0] if row else 0

    def set_deck_new_today(self, deck_id: int, today_day_index: int, new_today: int) -> None:
        """Set ``deck.common.new_today`` to an explicit value (recompute path).

        Unlike `bump_deck_new_today`, this writes a known count rather than
        incrementing. Used by `_recompute_anki_new_today_all_decks` to align
        the deck counter with revlog reality on every sync, eliminating any
        per-push counting drift. Rollover handling (clear today fields when
        last_day_studied is older) matches `bump_deck_new_today`.
        """
        row = self._conn.execute("SELECT common FROM decks WHERE id = ?", (deck_id,)).fetchone()
        if row is None:
            return
        blob = bytes(row[0]) if row[0] else b""

        last_day = find_varint_field(blob, self._DECKS_COMMON_LAST_DAY_STUDIED) or 0
        if last_day < today_day_index:
            blob = pb_remove_field(blob, self._DECKS_COMMON_NEW_TODAY)
            blob = pb_remove_field(blob, self._DECKS_COMMON_REVIEW_TODAY)
            blob = pb_remove_field(blob, self._DECKS_COMMON_SECONDS_TODAY)
            blob = pb_replace_or_insert_varint(blob, self._DECKS_COMMON_LAST_DAY_STUDIED, today_day_index)
        blob = pb_replace_or_insert_varint(blob, self._DECKS_COMMON_NEW_TODAY, new_today)
        now_ts = int(_time.time())
        self._conn.execute(
            "UPDATE decks SET common = ?, mtime_secs = ?, usn = -1 WHERE id = ?",
            (blob, now_ts, deck_id),
        )
        self._bump_col(now_ts)
        self._conn.commit()


def _direction_differs(local: DirectionState, candidate: DirectionState) -> bool:
    """Return True only if a sync-relevant field changed between local and candidate.

    Excludes last_synced_at and last_rating from the comparison so benign
    timestamp updates don't trigger a spurious write. Includes `left` and
    `due_at` so step-state advances on learning cards aren't silently skipped
    when the merge picked up Anki's value but other fields happened to match.
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
        or local.left != candidate.left
        or local.due_at != candidate.due_at
        or local.prior_state != candidate.prior_state
        # Without bury_kind in the diff, a state-matched / kind-only flip
        # (e.g. migration's pessimistic 'user' default vs candidate 'sched')
        # is silently no-op'd, locking the row in the wrong kind forever.
        or local.bury_kind != candidate.bury_kind
        # anki_card_mod feeds the FNV tiebreaker in
        # _merge_by_retrievability_ascending (Anki's `fnvhash(id, mod)`).
        # When Anki bumps cards.mod for any reason that doesn't change other
        # FSRS fields (housekeeping, server sync, etc.), the tiebreak input
        # drifts and TT serves a different card than Anki from R-tied pools.
        or local.anki_card_mod != candidate.anki_card_mod
    )


def _resolve_prior_state(
    local_dir: DirectionState,
    new_state: SRSState,
    *,
    first_review_ms: int | None = None,
    today_start_ms: int | None = None,
) -> SRSState | None:
    """Return the `prior_state` to write on a sync-merged direction.

    On a state-class transition (e.g. NEW → LEARNING after Anki graded a fresh
    card), `prior_state` captures the local-side state before the transition so
    later queries can identify the event — most importantly
    `count_new_introduced_today`, which filters by `prior_state='new'` to mirror
    Anki's `newToday` counter. When state is unchanged (a no-op sync, or
    within-state grade), preserve `local_dir.prior_state` so earlier transition
    bookkeeping isn't clobbered.

    Self-heal: if Anki's first revlog for this card is today AND the card
    isn't currently in NEW state, force `prior_state='new'` regardless of
    the current value. This covers two cases:
      1. Pre-fix data where sync_pull didn't write prior_state at all.
      2. Cards introduced today that later graduated to REVIEW the same day —
         the LEARNING→REVIEW transition can clobber 'new' in the grade
         endpoint; this restores it. Matches Anki's `newToday` counter
         (sticky for the day, never decremented).
    """
    if new_state != local_dir.state:
        return local_dir.state

    if (
        new_state != SRSState.NEW
        and first_review_ms is not None
        and today_start_ms is not None
        and first_review_ms >= today_start_ms
    ):
        return SRSState.NEW

    return local_dir.prior_state


def _resolve_introduced_at(
    local_dir: DirectionState,
    new_state: SRSState,
    *,
    first_review_ms: int | None,
) -> datetime | None:
    """Return the `introduced_at` to write on a sync-merged direction.

    Layer 26: introduced_at is stamped exactly once per card's intro arc — on
    the first NEW→non-NEW transition observed in EITHER app. Preserves an
    already-set value (sticky for the card's lifetime). Else, if Anki shows
    the card has been graded (new_state != NEW) and we know when Anki's first
    revlog row landed, anchor to that timestamp so `count_new_introduced_today`
    reflects Anki-side introductions after sync.
    """
    if local_dir.introduced_at is not None:
        return local_dir.introduced_at
    if new_state == SRSState.NEW:
        return None
    if first_review_ms is None:
        return None
    return datetime.fromtimestamp(first_review_ms / 1000, tz=UTC)


def _anki_step_ahead(anki_left: int | None, local_left: int | None) -> bool:
    """Return True iff Anki's `total_remaining` is strictly less than TT's.

    Anki encodes `left = today_left * 1000 + total_remaining`; only the low 3
    digits drive the state machine (rslib/.../card/mod.rs:218). A smaller
    `total_remaining` in Anki means Anki has graded the card more times — it's
    further along the learning steps than TT. Used by sync_pull (Fix 2) and
    sync_push (Fix 3) to defer to whichever app has more progress.

    Returns False when either value is missing or zero — there's no "ahead"
    relationship to compare against.
    """
    anki_tr = (anki_left or 0) % 1000
    local_tr = (local_left or 0) % 1000
    return anki_tr > 0 and local_tr > 0 and anki_tr < local_tr


# Layer 35: bury_kind split (sched/user/None).
# Layer 39 (2026-05-17): queue=-2 now maps to 'sched', not 'user'.
def _bury_kind_from_queue(queue: int) -> str | None:
    """Return the bury kind for an Anki queue value, or None when not buried.

    Both ``queue=-2`` and ``queue=-3`` map to ``'sched'`` so the daily
    unbury sweep releases them at TT's rollover, matching Anki's own
    behavior (``unbury_on_day_rollover`` releases both, see
    ``rslib/storage/card/sqlwriter.rs:471-476``).

    The Anki *source* claims grade-time sibling-bury writes ``queue=-3``
    (sched) and only explicit UI actions write ``queue=-2`` (user). The
    Anki *binary* contradicts that: grading a card via
    ``col.sched.answerCard`` places the sibling at ``queue=-2``,
    verified 2026-05-17 against a copy of the user's collection. Per
    rule 13 (``.claude/rules/anki-queue-parity.md``), trust the binary.

    The previous mapping (``queue=-2 → 'user'``) left TT hoarding every
    sibling-bury indefinitely while Anki auto-released them at rollover —
    the 19-card cohort observed on 2026-05-17 and the earlier 140-row
    incident on 2026-05-16 (see ``docs/bury-kind-investigation-*``).
    """
    if queue in (-2, -3):
        return "sched"
    return None


def _queue_to_state(queue: int, card_type: int, reps: int) -> SRSState:
    """Map Anki's (queue, type, reps) tuple to TT's SRSState.

    `queue` is the authoritative signal for Anki's current placement — TT
    must mirror it directly. Layer 30: the previous `if reps == 0: NEW`
    fallback wrongly mapped `(queue=2, reps=0)` cards to NEW, surfacing
    already-graduated cards (e.g. via Anki's "Forget" action or a manual
    `cards.due` edit, which clears `reps` but leaves `queue=2`) as fresh
    new cards in TT.
    """
    if queue == -1:
        return SRSState.SUSPENDED
    if queue in (-2, -3):
        return SRSState.BURIED
    if queue == 1:
        return SRSState.RELEARNING if card_type == 3 else SRSState.LEARNING
    if queue == 3:
        return SRSState.RELEARNING
    if queue == 2:
        return SRSState.REVIEW
    if queue == 0:
        return SRSState.NEW
    # Fallback for unknown queue values (shouldn't happen against modern Anki).
    return SRSState.NEW if reps == 0 else SRSState.REVIEW


def _step_minutes_from_left(left: int | None, steps: list[float]) -> float | None:
    """Decode Anki's `cards.left` to the current step's duration in minutes.

    Anki encodes `left = today_left * 1000 + total_remaining`; the low 3 digits
    drive state. Step index = `len(steps) - total_remaining` (matches
    rslib/.../states/steps.rs:23 `get_index`). Returns None when `left`/`steps`
    is missing or out of range.
    """
    if not left or not steps:
        return None
    total_remaining = left % 1000
    if total_remaining <= 0 or total_remaining > len(steps):
        return None
    step_index = len(steps) - total_remaining
    return steps[step_index]


def _derive_revlog_shape(
    ds: DirectionState,
    learn_steps: list[float],
    relearn_steps: list[float],
) -> tuple[int, int, int]:
    """Compute (type_, ivl, last_ivl) for a revlog row reflecting the actual
    transition. Anki encodes sub-day intervals as negative seconds (e.g. -60
    for 1 min, -600 for 10 min) and day-scale intervals as positive ints.

    `revlog.type`: 0=Learning, 1=Review, 2=Relearning. The type recorded is
    determined by the queue the card was *in* at rating time — i.e. the prior
    state, not the new state.
    """
    stability_days = max(1, round(ds.stability))

    if ds.prior_state is None:
        # Pre-migration row: keep the legacy positive-ivl shape so the rating
        # at least lands in revlog. Future grades populate prior_state and use
        # the precise transition mapping below.
        if ds.state == SRSState.LEARNING:
            type_ = 0
        elif ds.state == SRSState.RELEARNING:
            type_ = 2
        else:
            type_ = 1
        return (type_, stability_days, stability_days)

    if ds.prior_state in (SRSState.NEW, SRSState.LEARNING):
        type_ = 0
    elif ds.prior_state == SRSState.RELEARNING:
        type_ = 2
    else:
        type_ = 1

    if ds.state in (SRSState.LEARNING, SRSState.RELEARNING):
        # Anki's revlog records the **unfuzzed** step (e.g. -60 for a 1m step,
        # -330 for Hard-on-first-step's 5.5m avg) — not `due_at - last_review`,
        # which would include the up-to-25%-of-step fuzz applied at scheduling
        # time. Decode the base step from `left` + steps; override for
        # Hard-on-first-step where Anki uses `(steps[0] + steps[1]) / 2`.
        steps = learn_steps if ds.state == SRSState.LEARNING else relearn_steps
        step_min = _step_minutes_from_left(ds.left, steps)
        if step_min is None and ds.state == SRSState.RELEARNING and relearn_steps:
            step_min = relearn_steps[0]
        if (
            step_min is not None
            and ds.last_rating == Rating.HARD.value
            and ds.left is not None
            and (ds.left % 1000) == len(steps)
            and len(steps) > 1
        ):
            step_min = (steps[0] + steps[1]) / 2
        new_ivl = -int(round(step_min * 60)) if step_min is not None else stability_days
    else:
        new_ivl = stability_days

    if ds.prior_state == SRSState.NEW:
        last_ivl = 0
    elif ds.prior_state == SRSState.LEARNING:
        prior_step_min = _step_minutes_from_left(ds.prior_left, learn_steps)
        last_ivl = -int(round(prior_step_min * 60)) if prior_step_min is not None else 0
    elif ds.prior_state == SRSState.RELEARNING:
        prior_step_min = _step_minutes_from_left(ds.prior_left, relearn_steps)
        last_ivl = -int(round(prior_step_min * 60)) if prior_step_min is not None else 0
    elif ds.prior_state == SRSState.REVIEW:
        last_ivl = max(1, round(ds.prior_stability)) if ds.prior_stability is not None else stability_days
    else:
        last_ivl = stability_days

    return (type_, new_ivl, last_ivl)


class AnkiSync:
    """Orchestrate bidirectional sync between TunaTale and Anki."""

    def __init__(
        self,
        *,
        db: SRSDatabase,
        _reader=None,
        _writer=None,
        _anki_col_ver: int | None = None,
        _anki_col_crt: int | None = None,
    ) -> None:
        self._db = db
        self._anki_col_ver = _anki_col_ver
        self._anki_col_crt = _anki_col_crt
        if _reader is not None:
            self._reader = _reader
        else:
            raise ValueError("_reader is required")

        if _writer is not None:
            self._writer = _writer
        else:
            raise ValueError("_writer is required")

        # Populated by detect_and_reset_orphans; consumed by sync_push to force
        # FSRS state onto cards that were just recreated.
        self._recovered_directions: set[tuple[str, str]] = set()

    def detect_and_reset_orphans(self) -> tuple[int, int]:
        """Reset TT pointers to Anki cards/notes that no longer exist.

        Runs at the top of a sync (before sync_create_new). Diffs the TT mirror
        against the live Anki collection — if a TT direction's `anki_card_id`
        is not in `live_card_ids`, the card was deleted ("Empty Cards", manual
        delete, or wiped by a force-full-download from AnkiWeb). Reset clears
        the dead pointer and (if `reps > 0`) flips `dirty_fsrs=1` so the next
        push writes a fresh revlog and force-FSRS into the recreated card.

        Aborts with `OrphanThresholdExceededError` when the orphan ratio
        exceeds 25% — usually a sign of a misconfigured `anki_collection_path`.

        Returns (direction_resets, note_resets) counts.
        """
        records = self._reader.get_note_records()
        live_note_ids = {r.anki_note_id for r in records}
        live_card_ids = {c.anki_card_id for r in records for c in r.cards}

        tt_card_ids = self._db.list_anki_card_ids()
        if tt_card_ids:
            orphan_count = len(tt_card_ids - live_card_ids)
            if orphan_count / len(tt_card_ids) > 0.25:
                raise OrphanThresholdExceededError(
                    f"Refusing to reset {orphan_count} orphaned anki_card_ids "
                    f"({orphan_count / len(tt_card_ids):.0%} of {len(tt_card_ids)}). "
                    f"Check that anki_collection_path points at the right deck."
                )

        dir_resets, note_resets = self._db.reset_orphaned_anki_ids(
            live_card_ids=live_card_ids,
            live_note_ids=live_note_ids,
        )
        self._recovered_directions = {(guid, direction) for guid, direction in dir_resets}
        return len(dir_resets), len(note_resets)

    def _record_conflict(
        self,
        report: PullReport,
        *,
        guid: str,
        direction: str | None,
        field: str,
        local: str,
        remote: str,
        resolution: str,
        dry_run: bool,
    ) -> None:
        conflict = SyncConflict(
            guid=guid,
            direction=direction,
            field=field,
            local_value=local,
            remote_value=remote,
            resolution=resolution,
        )
        report.conflicts.append(conflict)
        if not dry_run:
            self._db.record_sync_conflict(
                guid=guid,
                direction=direction,
                field=field,
                local=local,
                remote=remote,
                resolution=resolution,
            )

    def _pull_advance_learning_cutoff(self, max_revlog_ms: int, dry_run: bool) -> None:
        """Advance the learning cutoff to the most recent Anki revlog timestamp ingested.

        Anki-parity: without this, an Anki-only grading session would leave
        TT's cutoff frozen at the last *TT* grade, and intraday-learning cards that
        ticked past-due during the Anki session would never become eligible.
        """
        if not dry_run and max_revlog_ms > 0:
            from app.srs.queue_stats import advance_learning_cutoff

            advance_learning_cutoff(self._db, datetime.fromtimestamp(max_revlog_ms / 1000, UTC))

    def _pull_rebuild_session_main_queue(self, dry_run: bool) -> None:
        """Invalidate and eagerly rebuild the frozen session_main_queue on sync completion.

        Anki's ``requires_study_queue_rebuild`` (rslib scheduler/queue/mod.rs:211-215)
        forces a queue rebuild after sync round-trip; mirroring it lazily (clear-only,
        rebuild-on-next-request) means TT freezes at a different moment than Anki's
        session-open rebuild, leading to off-by-N drift on the first-new-card position.
        The eager rebuild aligns the freeze moments. Layer 29.
        """
        if not dry_run:
            from app.api.srs import build_and_freeze_main_queue
            from app.srs.queue_stats import clear_session_main_queue

            clear_session_main_queue(self._db)
            build_and_freeze_main_queue(self._db)

    def sync_pull(self, dry_run: bool = False) -> PullReport:
        """Pull Anki → TunaTale. Returns a PullReport summarising changes."""
        report = PullReport()
        max_revlog_ms = 0  # tracked to advance the learning cutoff after Anki-side grades
        bury_stats: dict[str, int] = {
            "anki_queue_minus2_seen": 0,  # Anki shows user-bury at sync time
            "anki_queue_minus3_seen": 0,  # Anki shows sched-bury at sync time
            "buried_to_released_writes": 0,  # TT BURIED → REVIEW/NEW
            "released_to_buried_writes": 0,  # TT non-BURIED → BURIED
            "kind_only_flips_written": 0,  # state matched but kind differed (was a no-op pre-fix)
            "buried_state_match_no_write": 0,  # both BURIED, all fields incl. kind match
        }

        # Anki-parity daily unbury sweep. Run BEFORE processing Anki records so
        # that any state='buried' rows that this pull lands (today's sibling-
        # buries from Anki) stick — the idempotency guard prevents re-sweep
        # later within the same day.
        if not dry_run:
            self._db.unbury_if_needed(date.today())

        # Local-today's UTC start, used to infer `prior_state='new'` for cards
        # whose first revlog is today but TT lost the transition (synced before
        # sync_pull learned to write prior_state).
        today_start_ms = int(
            datetime.combine(date.today(), time(0), tzinfo=datetime.now().astimezone().tzinfo)
            .astimezone(UTC)
            .timestamp()
            * 1000
        )

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
            local_sent_trans = local_item.syntactic_unit.source_sentence_translation
            local_note = local_item.syntactic_unit.note
            note_changed = False
            new_dirty_fields = dirty_set.copy()

            if rec.translation != local_translation:
                note_changed = True
                if "translation" in dirty_set:
                    self._record_conflict(
                        report,
                        guid=guid,
                        direction=None,
                        field="translation",
                        local=local_translation,
                        remote=rec.translation,
                        resolution="anki_wins",
                        dry_run=dry_run,
                    )
                    new_dirty_fields.discard("translation")

            if rec.sentence_translation != local_sent_trans:
                note_changed = True

            if rec.note != local_note:
                note_changed = True

            if note_changed:
                if not dry_run:
                    self._db.update_collocation_for_sync(
                        guid,
                        translation=rec.translation,
                        note=rec.note,
                        sentence_translation=rec.sentence_translation,
                        dirty_fields_str=",".join(sorted(new_dirty_fields)),
                    )
                report.notes_updated += 1

            for card_rec in rec.cards:
                if local_item.syntactic_unit.card_type == "cloze":
                    direction = Direction.PRODUCTION
                else:
                    direction = Direction.RECOGNITION if card_rec.ord == 0 else Direction.PRODUCTION
                local_dir = local_item.directions.get(direction)
                if local_dir is None:
                    continue

                def _prior(
                    local_dir: DirectionState,
                    new_state: SRSState,
                    _first_review_ms: int = card_rec.first_review_ms,
                    _today_start_ms: int = today_start_ms,
                ) -> SRSState | None:
                    return _resolve_prior_state(
                        local_dir,
                        new_state,
                        first_review_ms=_first_review_ms,
                        today_start_ms=_today_start_ms,
                    )

                def _intro_at(
                    local_dir: DirectionState,
                    new_state: SRSState,
                    _first_review_ms: int = card_rec.first_review_ms,
                ) -> datetime | None:
                    return _resolve_introduced_at(
                        local_dir,
                        new_state,
                        first_review_ms=_first_review_ms,
                    )

                # Compute timestamps for conflict resolution
                local_last_ms = int(local_dir.last_review.timestamp() * 1000) if local_dir.last_review else 0
                anki_last_ms = card_rec.last_review_ms or 0
                if anki_last_ms > max_revlog_ms:
                    max_revlog_ms = anki_last_ms

                # `card_rec.last_review` is the FSRS-scheduler-effective timestamp
                # — populated from cards.data.lrt by `parse_fsrs_data` when
                # available, else day-level via `_compute_last_review`. This is
                # what Anki's `extract_fsrs_retrievability` SQL uses for R, so
                # mirroring it gives R-asc parity. The earlier Layer-10
                # preference for MAX(revlog.id) per card was wrong: for cards
                # graded multiple times in one session (Again → relearning step
                # → Hard), revlog-max advances on every step while lrt sticks
                # to the FSRS-touched grade, producing shorter elapsed → higher
                # R → wrong R-asc position.
                resolved_last_review = card_rec.last_review

                if local_dir.dirty_fsrs and anki_last_ms > local_last_ms:
                    # Anki's review is newer than TunaTale's pending grade.
                    # Anki wins for cards.due/ivl/factor. TunaTale's grade still
                    # becomes a revlog row in Anki (push will handle it).
                    new_state = _queue_to_state(card_rec.queue, card_rec.card_type, card_rec.reps)
                    new_dir_state = DirectionState(
                        direction=direction,
                        due_date=card_rec.due_date,
                        stability=card_rec.stability,
                        difficulty=card_rec.difficulty,
                        reps=card_rec.reps,
                        lapses=card_rec.lapses,
                        state=new_state,
                        prior_state=_prior(local_dir, new_state),
                        introduced_at=_intro_at(local_dir, new_state),
                        dirty_fsrs=False,  # cleared so push won't overwrite Anki
                        anki_card_id=card_rec.anki_card_id,
                        anki_card_mod=card_rec.anki_card_mod,
                        anki_due=card_rec.anki_due,
                        last_review=local_dir.last_review,  # preserve TunaTale's timestamp for revlog ID
                        last_review_time_ms=local_dir.last_review_time_ms,  # preserve duration
                        last_synced_at=datetime.now(UTC).isoformat(),
                        last_rating=local_dir.last_rating,  # preserve for push revlog
                        left=card_rec.left,
                        due_at=card_rec.due_at,
                        bury_kind=_bury_kind_from_queue(card_rec.queue),
                    )
                    self._record_conflict(
                        report,
                        guid=guid,
                        direction=direction.value,
                        field="schedule",
                        local=str(local_dir.last_review),
                        remote=str(card_rec.last_review),
                        resolution="anki_wins_by_timestamp",
                        dry_run=dry_run,
                    )
                elif local_dir.dirty_fsrs:
                    # TunaTale's grade is the latest event. Preserve local FSRS,
                    # let push flush. (anki_card_id / anki_due / last_synced_at refresh.)
                    # Still apply Anki's bury/suspend state if present — these are
                    # manual user actions that must win regardless of timestamp.
                    anki_in_learning = card_rec.queue in (1, 3)
                    local_in_learning = local_dir.state in (SRSState.LEARNING, SRSState.RELEARNING)

                    if card_rec.queue == -1:
                        new_dir_state = replace(
                            local_dir,
                            state=SRSState.SUSPENDED,
                            prior_state=_prior(local_dir, SRSState.SUSPENDED),
                            introduced_at=_intro_at(local_dir, SRSState.SUSPENDED),
                            anki_card_id=card_rec.anki_card_id,
                            anki_card_mod=card_rec.anki_card_mod,
                            anki_due=card_rec.anki_due,
                            last_synced_at=datetime.now(UTC).isoformat(),
                            bury_kind=None,
                        )
                    elif card_rec.queue in (-2, -3):
                        new_dir_state = replace(
                            local_dir,
                            state=SRSState.BURIED,
                            prior_state=_prior(local_dir, SRSState.BURIED),
                            introduced_at=_intro_at(local_dir, SRSState.BURIED),
                            anki_card_id=card_rec.anki_card_id,
                            anki_card_mod=card_rec.anki_card_mod,
                            anki_due=card_rec.anki_due,
                            last_synced_at=datetime.now(UTC).isoformat(),
                            bury_kind=_bury_kind_from_queue(card_rec.queue),
                        )
                    elif anki_in_learning and not local_in_learning:
                        # State-class divergence: Anki has the card mid-learning but
                        # the local grade graduated it. The local grade was applied
                        # against a stale prior state (the missing-left/due_at bug);
                        # preserving and pushing it would erase Anki's learning step.
                        # Defer to Anki, drop the local grade, surface a conflict.
                        new_state = (
                            SRSState.RELEARNING
                            if (card_rec.queue == 3 or card_rec.card_type == 3)
                            else SRSState.LEARNING
                        )
                        new_dir_state = DirectionState(
                            direction=direction,
                            due_date=card_rec.due_date,
                            stability=card_rec.stability,
                            difficulty=card_rec.difficulty,
                            reps=card_rec.reps,
                            lapses=card_rec.lapses,
                            state=new_state,
                            prior_state=_prior(local_dir, new_state),
                            introduced_at=_intro_at(local_dir, new_state),
                            dirty_fsrs=False,
                            anki_card_id=card_rec.anki_card_id,
                            anki_card_mod=card_rec.anki_card_mod,
                            anki_due=card_rec.anki_due,
                            last_review=resolved_last_review,
                            last_synced_at=datetime.now(UTC).isoformat(),
                            left=card_rec.left,
                            due_at=card_rec.due_at,
                            bury_kind=_bury_kind_from_queue(card_rec.queue),
                        )
                        self._record_conflict(
                            report,
                            guid=guid,
                            direction=direction.value,
                            field="state_class",
                            local=local_dir.state.value,
                            remote=new_state.value,
                            resolution="anki_wins_state_class_divergence",
                            dry_run=dry_run,
                        )
                    elif local_in_learning and card_rec.queue == 2:
                        # Inverse state-class divergence: local thinks LEARNING but
                        # Anki has already graduated (queue=2). Anki has more
                        # progress; TT's pending grade is stale. Same shape as
                        # the previous branch — Anki wins, drop dirty, surface a
                        # conflict so the divergence is visible.
                        new_dir_state = DirectionState(
                            direction=direction,
                            due_date=card_rec.due_date,
                            stability=card_rec.stability,
                            difficulty=card_rec.difficulty,
                            reps=card_rec.reps,
                            lapses=card_rec.lapses,
                            state=SRSState.REVIEW,
                            prior_state=_prior(local_dir, SRSState.REVIEW),
                            introduced_at=_intro_at(local_dir, SRSState.REVIEW),
                            dirty_fsrs=False,
                            anki_card_id=card_rec.anki_card_id,
                            anki_card_mod=card_rec.anki_card_mod,
                            anki_due=card_rec.anki_due,
                            last_review=resolved_last_review,
                            last_synced_at=datetime.now(UTC).isoformat(),
                            left=card_rec.left,
                            due_at=card_rec.due_at,
                            bury_kind=None,
                        )
                        self._record_conflict(
                            report,
                            guid=guid,
                            direction=direction.value,
                            field="state_class",
                            local=local_dir.state.value,
                            remote=SRSState.REVIEW.value,
                            resolution="anki_wins_state_class_divergence",
                            dry_run=dry_run,
                        )
                    elif anki_in_learning and local_in_learning and _anki_step_ahead(card_rec.left, local_dir.left):
                        # Both in learning, but Anki has graded the card more
                        # times than TT (smaller total_remaining). Take Anki's
                        # left/due_at + FSRS state; clear dirty_fsrs so push
                        # doesn't write TT's stale view back over Anki's
                        # progress. Surface as a "step_progress" conflict.
                        new_dir_state = replace(
                            local_dir,
                            stability=card_rec.stability,
                            difficulty=card_rec.difficulty,
                            reps=card_rec.reps,
                            lapses=card_rec.lapses,
                            left=card_rec.left,
                            due_at=card_rec.due_at,
                            prior_state=_prior(local_dir, local_dir.state),
                            introduced_at=_intro_at(local_dir, local_dir.state),
                            dirty_fsrs=False,
                            anki_card_id=card_rec.anki_card_id,
                            anki_card_mod=card_rec.anki_card_mod,
                            anki_due=card_rec.anki_due,
                            last_review=resolved_last_review,
                            last_synced_at=datetime.now(UTC).isoformat(),
                        )
                        self._record_conflict(
                            report,
                            guid=guid,
                            direction=direction.value,
                            field="step_progress",
                            local=str(local_dir.left),
                            remote=str(card_rec.left),
                            resolution="anki_wins_step_progress",
                            dry_run=dry_run,
                        )
                    else:
                        new_dir_state = replace(
                            local_dir,
                            state=local_dir.state,
                            prior_state=_prior(local_dir, local_dir.state),
                            introduced_at=_intro_at(local_dir, local_dir.state),
                            anki_card_id=card_rec.anki_card_id,
                            anki_card_mod=card_rec.anki_card_mod,
                            anki_due=card_rec.anki_due,
                            last_synced_at=datetime.now(UTC).isoformat(),
                        )
                elif card_rec.fsrs_known:
                    new_state = _queue_to_state(card_rec.queue, card_rec.card_type, card_rec.reps)
                    new_dir_state = DirectionState(
                        direction=direction,
                        due_date=card_rec.due_date,
                        stability=card_rec.stability,
                        difficulty=card_rec.difficulty,
                        reps=card_rec.reps,
                        lapses=card_rec.lapses,
                        state=new_state,
                        prior_state=_prior(local_dir, new_state),
                        introduced_at=_intro_at(local_dir, new_state),
                        dirty_fsrs=False,
                        anki_card_id=card_rec.anki_card_id,
                        anki_card_mod=card_rec.anki_card_mod,
                        anki_due=card_rec.anki_due,
                        last_review=resolved_last_review,
                        last_synced_at=datetime.now(UTC).isoformat(),
                        left=card_rec.left,
                        due_at=card_rec.due_at,
                        bury_kind=_bury_kind_from_queue(card_rec.queue),
                    )
                else:
                    new_state = _queue_to_state(card_rec.queue, card_rec.card_type, card_rec.reps)
                    new_dir_state = DirectionState(
                        direction=direction,
                        due_date=card_rec.due_date,
                        stability=local_dir.stability,
                        difficulty=local_dir.difficulty,
                        reps=card_rec.reps,
                        lapses=card_rec.lapses,
                        state=new_state,
                        prior_state=_prior(local_dir, new_state),
                        introduced_at=_intro_at(local_dir, new_state),
                        dirty_fsrs=False,
                        anki_card_id=card_rec.anki_card_id,
                        anki_card_mod=card_rec.anki_card_mod,
                        anki_due=card_rec.anki_due,
                        last_review=resolved_last_review,
                        last_synced_at=datetime.now(UTC).isoformat(),
                        left=card_rec.left,
                        due_at=card_rec.due_at,
                        bury_kind=_bury_kind_from_queue(card_rec.queue),
                    )
                differs = _direction_differs(local_dir, new_dir_state)
                # Forensic trace for any direction whose Anki state OR TT state
                # touches BURIED. Lets future investigators reconstruct exactly
                # which queue value Anki returned (sched vs user vs released),
                # what TT had locally, and whether the diff actually fired.
                # Grep server stderr for "BURY_TRACE".
                bury_relevant = (
                    card_rec.queue in (-2, -3)
                    or local_dir.state == SRSState.BURIED
                    or new_dir_state.state == SRSState.BURIED
                )
                if bury_relevant:
                    _log.info(
                        "BURY_TRACE cid=%s text=%r dir=%s anki_queue=%d anki_mod=%s "
                        "local=(state=%s kind=%s last_review=%s) "
                        "candidate=(state=%s kind=%s last_review=%s) "
                        "diff=%s write=%s",
                        card_rec.anki_card_id,
                        local_item.syntactic_unit.text,
                        direction.value,
                        card_rec.queue,
                        card_rec.anki_card_mod,
                        local_dir.state.value,
                        local_dir.bury_kind,
                        local_dir.last_review.isoformat() if local_dir.last_review else None,
                        new_dir_state.state.value,
                        new_dir_state.bury_kind,
                        new_dir_state.last_review.isoformat() if new_dir_state.last_review else None,
                        differs,
                        differs and not dry_run,
                    )
                    if card_rec.queue == -2:
                        bury_stats["anki_queue_minus2_seen"] += 1
                    elif card_rec.queue == -3:
                        bury_stats["anki_queue_minus3_seen"] += 1
                    was_buried = local_dir.state == SRSState.BURIED
                    will_be_buried = new_dir_state.state == SRSState.BURIED
                    if differs and was_buried and not will_be_buried:
                        bury_stats["buried_to_released_writes"] += 1
                    if differs and not was_buried and will_be_buried:
                        bury_stats["released_to_buried_writes"] += 1
                    if (
                        differs
                        and local_dir.state == new_dir_state.state
                        and local_dir.bury_kind != new_dir_state.bury_kind
                    ):
                        bury_stats["kind_only_flips_written"] += 1
                    if not differs and was_buried and will_be_buried:
                        bury_stats["buried_state_match_no_write"] += 1
                if differs:
                    if not dry_run:
                        self._db.update_direction(guid, direction, new_dir_state)
                    report.directions_updated += 1

        self._pull_advance_learning_cutoff(max_revlog_ms, dry_run)
        self._pull_rebuild_session_main_queue(dry_run)

        _log.info("BURY_TRACE summary dry_run=%s %s", dry_run, bury_stats)
        return report

    def _capture_anki_card_state(self, card_id: int) -> dict | None:
        """Snapshot ``cards.{queue, type, left}`` for *card_id* before mutating it.

        Retained for the anki_ahead conflict-resolution check in sync_push.
        """
        if hasattr(self._writer, "get_current_card_state"):
            return self._writer.get_current_card_state(card_id)
        return None

    def _recompute_anki_new_today_all_decks(self) -> None:
        """Set every revlog-touched deck's ``new_today`` counter from revlog reality.

        Replaces per-push increment. Walks ``SELECT DISTINCT did`` for cards
        with any revlog ``id >= today_4am_ms``, then for each deck counts
        distinct cards whose *first* revlog id falls today (mirroring Anki's
        newToday semantic) and writes that count back to ``deck.common.new_today``.

        Idempotent — running it twice in a row produces the same result.
        Eliminates the per-push double-count drift that the older increment
        approach was prone to (Anki grades a card → Anki bumps; TT pushes the
        same card → push bumped again).

        No-op when the writer doesn't support the three methods (e.g., legacy
        AnkiConnect path, FakeReader-only tests). Also no-op when col.crt is
        unknown (can't compute today_day_index).
        """
        if self._anki_col_crt is None:
            return
        required = ("list_decks_with_revlog_today", "count_first_grades_today_for_deck", "set_deck_new_today")
        if not all(hasattr(self._writer, m) for m in required):
            return
        today_4am_ms = int(_local_today_4am().timestamp() * 1000)
        day_index = compute_anki_day_index(self._anki_col_crt)
        for deck_id in self._writer.list_decks_with_revlog_today(today_4am_ms):
            count = self._writer.count_first_grades_today_for_deck(deck_id, today_4am_ms)
            self._writer.set_deck_new_today(deck_id, day_index, count)

    def sync_push(self, dry_run: bool = False, force_fsrs: bool = False) -> PushReport:
        """Push TunaTale → Anki. Returns a PushReport summarising changes."""
        report = PushReport()

        for guid, anki_note_id, dirty_fields_str, item, coll_id in self._db.list_dirty_field_edits():
            if anki_note_id is None:
                continue
            dirty_set = {f for f in dirty_fields_str.split(",") if f}
            fields: dict[str, str] = {}
            if item.syntactic_unit.card_type == "cloze":
                # Cloze notes: any of {translation, sentence_translation, note, audio}
                # dirty → rebuild Back Extra. Cloze has no separate "English" field.
                if dirty_set & {"translation", "sentence_translation", "note", "audio"}:
                    sentence_audio = self._db.get_sentence_audio_filename(coll_id)
                    fields["Back Extra"] = build_cloze_back_extra(
                        item.syntactic_unit.translation,
                        item.syntactic_unit.source_sentence_translation,
                        item.syntactic_unit.note,
                        sentence_audio_filename=sentence_audio,
                    )
                    if sentence_audio and not dry_run:
                        _copy_tt_media_to_anki(self._writer, sentence_audio)
            else:
                if "translation" in dirty_set:
                    fields["English"] = item.syntactic_unit.translation
            if not fields:
                continue
            if not dry_run:
                self._writer.update_note_fields(anki_note_id, fields)
                self._db.set_dirty_fields(guid, "")
            report.notes_pushed += 1

        # First loop: dirty directions (TunaTale's grade is latest)
        recovered = self._recovered_directions
        for guid, direction, ds in self._db.list_dirty():
            if ds.anki_card_id is None:
                continue
            # Recovery: when detect_and_reset_orphans cleared this direction's
            # anki_card_id earlier in the run and sync_create_new just minted a
            # fresh one, force_fsrs writes the TT-side stability/difficulty into
            # the new card's data JSON regardless of the global flag.
            row_force_fsrs = force_fsrs or (guid, direction.value) in recovered
            days_str = str(max(0, (ds.due_date - date.today()).days))
            if not dry_run:
                # Snapshot Anki's pre-push card state for the anki_ahead
                # conflict-resolution check. Must be captured BEFORE
                # set_learning_state / set_due_date, which mutate cards.queue.
                anki_state_before = self._capture_anki_card_state(ds.anki_card_id)

                if ds.state == SRSState.SUSPENDED:
                    self._writer.suspend([ds.anki_card_id])
                else:
                    self._writer.unsuspend([ds.anki_card_id])

                # Handle learning/relearning cards differently: update left and due (absolute timestamp)
                if (
                    ds.state in (SRSState.LEARNING, SRSState.RELEARNING)
                    and ds.left is not None
                    and ds.due_at is not None
                ):
                    # Fix 3: defer to Anki if Anki is further along. Push runs
                    # before pull in the sync flow, so without this guard a
                    # stale TT view would clobber Anki's correct step state /
                    # graduation. The matching pull-side defense (Fix 2) then
                    # carries Anki's view into TT and clears dirty_fsrs.
                    anki_now = anki_state_before
                    anki_ahead = False
                    if anki_now is not None:
                        if anki_now["queue"] == 2:
                            anki_ahead = True  # graduated
                        elif anki_now["queue"] in (1, 3) and _anki_step_ahead(anki_now["left"], ds.left):
                            anki_ahead = True

                    if not anki_ahead:
                        # queue=1 for both; type=1 for LEARNING, type=3 for RELEARNING (lapse)
                        due_timestamp = int(ds.due_at.timestamp())
                        type_ = 3 if ds.state == SRSState.RELEARNING else 1
                        self._writer.set_learning_state(ds.anki_card_id, ds.left, due_timestamp, type_=type_)
                    else:
                        # Skip both the card update and the revlog: TT's grade
                        # is being discarded in favour of Anki's. mark_direction_clean
                        # at end of loop drops the dirty flag so we don't keep
                        # retrying.
                        self._db.mark_direction_clean(guid, direction)
                        report.directions_pushed += 1
                        continue
                else:
                    # Review/new cards: use set_due_date (days since col_crt)
                    self._writer.set_due_date([ds.anki_card_id], days_str)

                if ds.reps > 0:
                    learn_steps, _ = resolve_learning_steps(self._db)
                    relearn_steps, _ = resolve_relearning_steps(self._db)
                    type_, ivl, last_ivl = _derive_revlog_shape(ds, learn_steps, relearn_steps)
                    ease = ds.last_rating if ds.last_rating is not None else 3
                    factor = max(1300, min(13000, round(ds.difficulty * 1000)))
                    # Use last_review timestamp for revlog ID
                    preferred_id = int(ds.last_review.timestamp() * 1000) if ds.last_review else None
                    is_lapse = ds.prior_state == SRSState.REVIEW and ds.last_rating == Rating.AGAIN.value
                    self._writer.write_revlog(
                        cid=ds.anki_card_id,
                        ease=ease,
                        ivl=ivl,
                        last_ivl=last_ivl,
                        factor=factor,
                        time_ms=ds.last_review_time_ms,
                        type_=type_,
                        preferred_id=preferred_id,
                        is_lapse=is_lapse,
                        ds_reps=ds.reps,
                        ds_lapses=ds.lapses,
                    )
                if row_force_fsrs:
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

        # Second loop: clean directions that need revlog (Anki won earlier by timestamp)
        for guid, direction, ds in self._db.list_recently_graded_clean():
            if ds.anki_card_id is None:
                continue
            if not dry_run:
                if ds.reps > 0:
                    learn_steps, _ = resolve_learning_steps(self._db)
                    relearn_steps, _ = resolve_relearning_steps(self._db)
                    type_, ivl, last_ivl = _derive_revlog_shape(ds, learn_steps, relearn_steps)
                    ease = ds.last_rating if ds.last_rating is not None else 3
                    factor = max(1300, min(13000, round(ds.difficulty * 1000)))
                    preferred_id = int(ds.last_review.timestamp() * 1000) if ds.last_review else None
                    is_lapse = ds.prior_state == SRSState.REVIEW and ds.last_rating == Rating.AGAIN.value
                    self._writer.write_revlog(
                        cid=ds.anki_card_id,
                        ease=ease,
                        ivl=ivl,
                        last_ivl=last_ivl,
                        factor=factor,
                        time_ms=ds.last_review_time_ms,
                        type_=type_,
                        preferred_id=preferred_id,
                        is_lapse=is_lapse,
                        ds_reps=ds.reps,
                        ds_lapses=ds.lapses,
                    )
                # Clear last_rating so it doesn't re-fire next sync
                self._db.mark_direction_clean(guid, direction)
            report.directions_pushed += 1

        # Recompute Anki's deck.newToday from revlog reality. Runs after both
        # push loops so it sees every revlog this push just wrote. Idempotent.
        if not dry_run:
            self._recompute_anki_new_today_all_decks()

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
        items = list(self._db.list_items_without_anki_note())
        if dry_run:
            return CreateNewReport(count=len(items))

        # Sort oldest-first so the MAX(due)+1 allocator in create_note gives newer
        # items higher cards.due. Under Anki's "New card gather order: Descending
        # position" deck setting (rslib/src/storage/card/mod.rs:923 — emits
        # "due DESC, ord ASC"), the freshest TT auto-add surfaces first in the
        # user's next review. See docs/anki-parity-layers.md Layer 24.
        items.sort(key=lambda gi: self._db.get_created_at_by_guid(gi[0]) or "")

        used_image_urls: set[str] = set()
        created = 0
        linked = 0
        skipped = 0

        for guid, item, coll_id in items:
            from app.srs.function_words import make_cloze_text

            if item.syntactic_unit.card_type == "cloze":
                cloze_text = make_cloze_text(
                    item.syntactic_unit.text,
                    item.syntactic_unit.source_sentence or "",
                )
                sentence_audio = self._db.get_sentence_audio_filename(coll_id)
                back_extra = build_cloze_back_extra(
                    item.syntactic_unit.translation,
                    item.syntactic_unit.source_sentence_translation,
                    sentence_audio_filename=sentence_audio,
                )
                try:
                    note_id = self._writer.create_cloze_note(
                        deck_name,
                        cloze_text,
                        back_extra=back_extra,
                        tags=["tunatale", "cloze"],
                    )
                    created += 1
                except DuplicateNoteError as exc:
                    note_id = exc.note_id
                    linked += 1

                if sentence_audio and not dry_run:
                    _copy_tt_media_to_anki(self._writer, sentence_audio)

                cards_by_ord = self._writer.get_cards_for_note(note_id)
                # Cloze notetype has exactly one template (ord=0)
                card_ids = {Direction.PRODUCTION: cards_by_ord[0]}
                self._db.set_anki_ids(guid, note_id, card_ids)
                continue

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

        # Reverse-import pass: mint new TT rows from Anki-only notes (Layer 22)
        records = self._reader.get_note_records()
        linked_anki_ids = self._db.list_linked_anki_note_ids()
        notes_created_from_anki = 0

        for rec in records:
            if rec.anki_note_id in linked_anki_ids:
                continue

            card_type = "cloze" if rec.is_cloze else "vocab"
            word_count = max(1, len(rec.l2_text.split()))
            unit = SyntacticUnit(
                text=rec.l2_text,
                translation=rec.translation,
                word_count=word_count,
                difficulty=1,
                source="anki",
                frequency=0,
                disambig_key=rec.disambig_key,
                lemma=rec.l2_text.lower() if word_count == 1 else None,
                source_sentence=rec.note,
                source_sentence_translation=rec.sentence_translation,
                card_type=card_type,
            )

            directions: dict[Direction, DirectionState] = {}
            cards_to_import = rec.cards[:1] if rec.is_cloze else rec.cards
            for card in cards_to_import:
                if rec.is_cloze:
                    direction = Direction.PRODUCTION
                else:
                    direction = Direction.RECOGNITION if card.ord == 0 else Direction.PRODUCTION

                state = _queue_to_state(card.queue, card.card_type, card.reps)

                directions[direction] = DirectionState(
                    direction=direction,
                    due_date=card.due_date,
                    stability=card.stability,
                    difficulty=card.difficulty,
                    reps=card.reps,
                    lapses=card.lapses,
                    state=state,
                    last_review=card.last_review,
                    anki_card_id=card.anki_card_id,
                    anki_due=card.anki_due or 0,
                    anki_card_mod=card.anki_card_mod,
                    left=card.left,
                    due_at=card.due_at,
                    dirty_fsrs=False,
                    last_synced_at=datetime.now(UTC).isoformat(),
                    prior_state=None,
                    introduced_at=_resolve_introduced_at(
                        DirectionState(direction=direction, due_date=card.due_date),
                        state,
                        first_review_ms=card.first_review_ms,
                    ),
                )

            if not directions:
                continue

            self._db.upsert_by_guid(unit, "sl", directions, anki_note_id=rec.anki_note_id)
            notes_created_from_anki += 1

        return CreateNewReport(
            count=count,
            created=created,
            linked=linked,
            skipped=skipped,
            notes_created_from_anki=notes_created_from_anki,
        )


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
            col_row = ctx.conn.execute("SELECT ver, crt FROM col").fetchone()
            col_ver = col_row[0]
            col_crt = col_row[1]
            reader = OfflineReader(ctx.conn, _s.anki_deck_name)
            writer = OfflineWriter(ctx.conn)
            sync = AnkiSync(
                db=db,
                _reader=reader,
                _writer=writer,
                _anki_col_ver=col_ver,
                _anki_col_crt=col_crt,
            )
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
