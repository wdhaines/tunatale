"""OfflineReader — read NoteRecords from a raw sqlite3 connection to collection.anki2.

Moved verbatim out of ``app/anki/sync.py`` (Phase 9 mechanical split).
``app.anki.sync`` re-exports ``OfflineReader``, so existing imports keep working.
"""

from __future__ import annotations

import sqlite3

from app.anki.sqlite_reader import (
    extract_disambig_from_fields,
    extract_l2_from_fields,
    extract_translation,
    extract_via_profile,
    fetch_cards_for_notes,
    fetch_notes_for_deck,
    find_deck_id,
)
from app.anki.sync_common import (
    CardRecord,
    NoteRecord,
    _ms_to_datetime,
    extract_cloze_note,
    extract_cloze_sentence_translation,
    extract_cloze_translation,
)
from app.models.syntactic_unit import BackField


class OfflineReader:
    """Read NoteRecords from a raw sqlite3.Connection to collection.anki2."""

    def __init__(self, conn: sqlite3.Connection, deck_name: str) -> None:
        self._conn = conn
        self._deck_name = deck_name

    def get_revlog_for_card(self, card_id: int, after_ms: int = 0) -> list[sqlite3.Row]:
        """Return revlog rows for *card_id* with id > *after_ms*.

        Used by Stage 0 to ingest Anki revlog into tt_revlog during sync_pull.
        """
        return self._conn.execute(
            "SELECT id, ease, ivl, lastIvl, factor, time, type FROM revlog WHERE cid = ? AND id > ? ORDER BY id",
            (card_id, after_ms),
        ).fetchall()

    def get_grave_note_ids(self) -> set[int]:
        """Return the note ids in Anki's ``graves`` table (``type=1``).

        A grave is Anki's tombstone for a deleted row (``type``: 0=card,
        1=note, 2=deck). `detect_and_reset_orphans` uses note graves to tell an
        *intentional* delete (honor it — hard-delete the TT collocation) from a
        card merely missing after a wipe (recover it). Returns an empty set when
        the table is absent (minimal/synthetic collections).
        """
        try:
            rows = self._conn.execute("SELECT oid FROM graves WHERE type = 1").fetchall()
        except sqlite3.OperationalError:
            return set()
        return {int(r[0]) for r in rows}

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
                article = ""
                extras: tuple[BackField, ...] = ()
            else:
                profile_result = extract_via_profile(note)
                if profile_result is not None:
                    l2_text, translation, disambig_key, article, extras = profile_result
                else:
                    l2_text = extract_l2_from_fields(note.fields)
                    translation = extract_translation(note.fields[1]) if len(note.fields) > 1 else ""
                    disambig_key = extract_disambig_from_fields(note.fields)
                    article = ""
                    extras = ()
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
                    due_at=c.fsrs_state.due_at,
                    anki_due=c.fsrs_state.anki_due,
                    anki_card_mod=c.mod,
                    # Learning/relearning cards (queue=1) have no day-level FSRS
                    # last_review, and a `data={}`/no-`lrt` card (the biti-cloze
                    # cohort) has none from data either — fall back to the latest
                    # revlog timestamp so a just-graded card is never left NULL.
                    last_review=c.fsrs_state.last_review or _ms_to_datetime(last_revlog_ms.get(c.id)),
                    last_review_ms=last_revlog_ms.get(c.id),
                    first_review_ms=first_revlog_ms.get(c.id),
                    left=c.fsrs_state.left,
                    # When Anki's data has no real FSRS state (lrt-only / empty),
                    # the stability/difficulty above are placeholder defaults —
                    # mark fsrs_known=False so sync_pull preserves TT's values
                    # instead of clobbering them (the 'stuck at 1.0' bug).
                    fsrs_known=c.fsrs_known,
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
                    article=article,
                    extras=extras,
                    mod=note.mod,
                    cards=card_records,
                    is_cloze=is_cloze,
                )
            )
        return records
