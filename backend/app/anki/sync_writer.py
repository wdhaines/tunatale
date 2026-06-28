"""OfflineWriter — write notes/cards/config/media into collection.anki2 via raw sqlite3.

Moved verbatim out of ``app/anki/sync.py`` (Phase 9 mechanical split).
``app.anki.sync`` re-exports ``OfflineWriter``, so existing imports keep working.
"""

from __future__ import annotations

import json as _json
import re
import sqlite3
import time as _time
from pathlib import Path

from app.anki.protobuf_wire import (
    find_varint_field,
    pb_remove_field,
    pb_replace_or_insert_varint,
)
from app.anki.sqlite_reader import find_deck_id
from app.anki.sync_common import DuplicateNoteError
from app.common.guid import compute_guid


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
        # Bump col.mod (so Anki sees the collection changed) but DO NOT touch col.usn.
        # col.usn is the sync ANCHOR — the server's last USN — not a per-row dirty flag.
        # Clobbering it to -1 made AnkiWeb demand a full sync whenever another device
        # (e.g. the phone) advanced the server's USN (Layer 61; reproduced 2026-05-29).
        # The content rows we touch (cards/notes/revlog/decks) carry their own usn=-1,
        # which is what actually pushes on the next incremental sync.
        self._conn.execute("UPDATE col SET mod = ?", (ts,))

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

    def forget_card(self, card_id: int) -> None:
        """Reset a card to NEW — Anki's "Forget".

        Clears the schedule and FSRS memory so the card is genuinely new in
        Anki, mirroring TT's ``reset_collocation``. sync_push calls this when a
        TT reset marks a NEW-state direction dirty, so a reset in TunaTale
        forgets the card in Anki too instead of silently diverging (Anki keeping
        the graduated review while TT shows a fresh NEW card; 2026-06-04).

        Places the card at the tail of the new queue (``MAX(due)+1`` over
        existing new cards) and drops ``data`` to ``{}`` so it carries no FSRS
        ``s``/``d`` — NULL-R, like any never-graded card.
        """
        ts = int(_time.time())
        row = self._conn.execute("SELECT IFNULL(MAX(due), 0) FROM cards WHERE type = 0").fetchone()
        new_due = int(row[0] or 0) + 1
        self._conn.execute(
            """
            UPDATE cards
            SET type = 0, queue = 0, due = ?, ivl = 0, factor = 0,
                reps = 0, lapses = 0, odue = 0, odid = 0, data = '{}',
                mod = ?, usn = -1
            WHERE id = ?
            """,
            (new_due, ts, card_id),
        )
        self._bump_col(ts)
        self._conn.commit()

    def get_current_card_state(self, card_id: int) -> dict | None:
        """Return Anki's current `queue`/`type`/`left`/`mod` for the card, or None
        if the card doesn't exist. Used by sync_push (Fix 3) to skip writes when Anki
        has more progress than TT; `mod` (epoch secs of Anki's last change) lets the
        recency guard avoid discarding a NEWER TT grade (Layer 69).
        """
        row = self._conn.execute(
            "SELECT queue, type, IFNULL(left, 0), mod FROM cards WHERE id = ?",
            (card_id,),
        ).fetchone()
        if row is None:
            return None
        return {"queue": row[0], "type": row[1], "left": row[2], "mod": row[3]}

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

    def bury_siblings(
        self,
        *,
        graded_card_id: int,
        graded_queue: int,
        bury_new: bool = False,
        bury_reviews: bool = False,
        bury_interday_learning: bool = False,
    ) -> int:
        """Replicate Anki's grade-time sibling-bury for a TT-graded card.

        Anki's ``answer_card`` calls ``maybe_bury_siblings`` → ``bury_siblings`` →
        ``all_siblings_for_bury`` (rslib/.../bury_and_suspend.rs:132 +
        siblings_for_bury.sql). TT's ``sync_push`` writes the grade directly into
        the cards/revlog tables and never hits ``answer_card``, so without this
        method TT-graded notes leave their siblings unburied on the Anki side —
        Anki then keeps the sibling in today's review pool, diverging from TT
        which excludes the whole note via the ``last_review=today`` filter.

        ``graded_queue`` is the card's queue AFTER the grade. The
        ``exclude_earlier_gathered_queues`` rule then drops bury flags whose
        target queue has a higher ``gather_ord`` than the graded card:
        Learn/PreviewRepeat=0, DayLearn=1, Review=2, New=3. Concretely:

        - graded Review (gather_ord=2): bury_interday_learning (queue=3 has
          gather_ord=1) gets dropped — wait, 1 ≤ 2 so it's KEPT under the
          Anki rule ``self.bury_X &= queue.gather_ord() <= X.gather_ord()``.

        Re-check: the Anki rule is keep-if `queue.gather_ord() <= X.gather_ord()`.
        So ``bury_interday_learning`` (X=DayLearn=1) is kept only when graded
        gather_ord ≤ 1. Review-graded has gather_ord=2, so interday is dropped.
        ``bury_reviews`` (X=Review=2) is kept when graded gather_ord ≤ 2 (all
        Learn/DayLearn/Review grades). ``bury_new`` (X=New=3) is kept when
        graded gather_ord ≤ 3 — always true for any of these queues.

        Only siblings at queue ∈ {0 (New), 2 (Review), 3 (DayLearn)} are
        eligible; queue=1 (intra-day Learn) and queue=-1/-2/-3 (suspended /
        already-buried) are never touched.

        Writes queue=-2 (sched-buried) + ``mod``/``usn=-1`` on each affected
        sibling, then bumps ``col``. Returns the count of buried siblings.
        """
        queue_to_gather_ord = {0: 3, 1: 0, 2: 2, 3: 1, 4: 0}
        graded_gather_ord = queue_to_gather_ord.get(graded_queue, 255)
        if graded_gather_ord > 1:
            bury_interday_learning = False
        if graded_gather_ord > 2:
            bury_reviews = False
        if graded_gather_ord > 3:
            bury_new = False

        allowed_queues: list[int] = []
        if bury_new:
            allowed_queues.append(0)
        if bury_reviews:
            allowed_queues.append(2)
        if bury_interday_learning:
            allowed_queues.append(3)
        if not allowed_queues:
            return 0

        row = self._conn.execute("SELECT nid FROM cards WHERE id = ?", (graded_card_id,)).fetchone()
        if row is None:
            return 0
        nid = row[0] if isinstance(row, (tuple, list)) else row["nid"]

        ts = int(_time.time())
        placeholders = ",".join("?" * len(allowed_queues))
        cursor = self._conn.execute(
            f"""
            UPDATE cards
            SET queue = -2, mod = ?, usn = -1
            WHERE nid = ?
              AND id != ?
              AND queue IN ({placeholders})
            """,
            (ts, nid, graded_card_id, *allowed_queues),
        )
        count = cursor.rowcount
        if count > 0:
            self._bump_col(ts)
        self._conn.commit()
        return count

    # Card columns the force_fsrs path is allowed to set directly. Restricting the
    # set guards the dynamic column-name interpolation below against typos/injection.
    _SETTABLE_CARD_COLS = frozenset({"data", "ivl", "factor", "due", "queue", "type", "reps", "lapses", "left"})

    def set_specific_value_of_card(self, card_id: int, keys: list[str], new_values: list[str]) -> None:
        """Write arbitrary card columns (used by the force_fsrs path to persist data/ivl/factor).

        Mirrors set_due_date's write contract: stamps ``usn=-1`` and ``mod=now`` on the
        row and bumps ``col.mod`` (never ``col.usn`` — Layer 61). Numeric-looking values
        are coerced to int so INTEGER columns (ivl/factor/…) store integers; the JSON
        ``data`` blob (non-numeric) stays text.
        """
        if not keys:
            return
        unknown = set(keys) - self._SETTABLE_CARD_COLS
        if unknown:
            raise ValueError(f"set_specific_value_of_card: disallowed card column(s) {sorted(unknown)}")

        coerced: list[object] = []
        for v in new_values:
            try:
                coerced.append(int(v))
            except ValueError, TypeError:
                coerced.append(v)

        ts = int(_time.time())
        set_clause = ", ".join(f"{k} = ?" for k in keys) + ", mod = ?, usn = -1"
        self._conn.execute(
            f"UPDATE cards SET {set_clause} WHERE id = ?",
            (*coerced, ts, card_id),
        )
        self._bump_col(ts)
        self._conn.commit()

    def update_card_memory_state(
        self,
        card_id: int,
        *,
        stability: float,
        difficulty: float,
        last_review_secs: int | None = None,
        desired_retention: float | None = None,
    ) -> None:
        """Merge FSRS memory state into ``cards.data`` (Layer 70).

        Every TT grade push must carry the post-grade memory state, like Anki's
        own sync would — otherwise Anki's stored s/d/lrt go stale for TT-graded
        cards and the next pull's take-Anki reverts the grade (the cid=428
        lapse-arc loss, 2026-06-10). Merge, never replace: ``data`` also holds
        ``pos`` (new-card position), ``decay``, and ``dr``, which this write
        must not drop. ``dr`` is set only when absent (Anki's own grade-time
        value wins); ``lrt`` only when the grade has a timestamp. Same dirty
        contract as every card write: ``usn=-1``, ``mod=now``, ``col.mod`` bump.
        """
        row = self._conn.execute("SELECT data FROM cards WHERE id = ?", (card_id,)).fetchone()
        if row is None:
            return
        raw = row[0] or ""
        try:
            data = _json.loads(raw) if raw.strip() else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        data["s"] = float(stability)
        data["d"] = float(difficulty)
        if last_review_secs is not None:
            data["lrt"] = int(last_review_secs)
        if desired_retention is not None and "dr" not in data:
            data["dr"] = float(desired_retention)
        ts = int(_time.time())
        self._conn.execute(
            "UPDATE cards SET data = ?, mod = ?, usn = -1 WHERE id = ?",
            (_json.dumps(data), ts, card_id),
        )
        self._bump_col(ts)
        self._conn.commit()

    def create_note(self, deck_name: str, model_name: str, fields: dict, tags: list) -> int:
        """Insert a new note + cards into the collection.

        Raises DuplicateNoteError if the computed GUID already exists.
        No col.scm change — data-only insert against an existing notetype.
        """
        import hashlib

        from app.anki.notetype import SLOVENE_VOCAB_FIELD_NAMES

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

    def bump_deck_new_today(self, deck_id: int, today_day_index: int) -> None:
        """Increment the "new studied today" counter for *deck_id*.

        Mirrors Anki's ``update_counters_after_answering_card``: reads/writes
        ``decks.common`` protobuf blob.

        review_today is recomputed alongside new_today on the recompute path
        (`set_deck_studied_today`, Layer 73); seconds_today still follows the
        same protobuf path and is dropped on rollover — extend if it ever drifts.
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

        Used by `AnkiSync._recompute_anki_studied_today_all_decks` to know which
        decks need their newToday/revToday counters rewritten.
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

    def count_reviews_today_for_deck(self, deck_id: int, today_4am_ms: int) -> int:
        """Count review answers in *deck_id* since *today_4am_ms*, mirroring Anki's
        per-deck ``review_today`` studied counter.

        Anki increments ``review_today`` from the card's **pre-answer queue**, not
        the revlog type: ``CardQueue::Review | CardQueue::DayLearn => review_delta += 1``
        (``rslib/src/scheduler/answering/mod.rs`` ``update_deck_stats_from_answer``,
        verified against the user's Anki **25.09**). ``DayLearn`` is the *interday*
        (re)learning queue — interday learning and interday relearning both count;
        *intraday* learning/relearning (``CardQueue::Learn``) does not.

        A revlog-only reconstruction keys on the pre-answer interval sign. ``lastIvl``
        stores the pre-answer interval encoded days-positive / seconds-negative
        (``states/interval_kind.rs`` ``as_revlog_interval``), so ``lastIvl >= 1`` ⟺
        the card was on interday footing (Review or DayLearn). ``type IN (0, 1, 2)``
        keeps review (1), relearn (2) and interday-learn (0) while excluding
        filtered/cram (3) and manual (4), which are never answer-driven counter
        events. Counts **rows** (per-answer), matching Anki's per-button increment,
        not distinct cards.
        """
        try:
            row = self._conn.execute(
                """
                SELECT COUNT(*) FROM revlog r
                JOIN cards c ON c.id = r.cid AND c.did = ?
                WHERE r.id >= ? AND r.type IN (0, 1, 2) AND r.lastIvl >= 1
                """,
                (deck_id, today_4am_ms),
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
        return row[0] if row else 0

    def set_deck_studied_today(self, deck_id: int, today_day_index: int, new_today: int, review_today: int) -> None:
        """Set ``deck.common`` ``new_today`` **and** ``review_today`` to explicit
        values (recompute path).

        Unlike `bump_deck_new_today`, this writes known counts rather than
        incrementing. Used by `_recompute_anki_studied_today_all_decks` to align
        both deck counters with revlog reality on every sync, eliminating
        per-push counting drift.

        Layer 73: the old `set_deck_new_today` rewrote only ``new_today`` and, on
        the rollover branch (``last_day_studied`` older than today), *removed*
        ``review_today`` without recomputing it — writing ``review_today=0`` with
        ``usn=-1``. That pushed to AnkiWeb and reset the reviews-done counter on
        other devices (AnkiDroid's "reviews due" jumping back up after a TT sync).
        Both branches now write the recomputed ``review_today``. (``seconds_today``
        is still dropped on rollover — it drives only the time-studied stat, no due
        badge, and revlog-reconstructing it is out of scope.)
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
        blob = pb_replace_or_insert_varint(blob, self._DECKS_COMMON_REVIEW_TODAY, review_today)
        now_ts = int(_time.time())
        self._conn.execute(
            "UPDATE decks SET common = ?, mtime_secs = ?, usn = -1 WHERE id = ?",
            (blob, now_ts, deck_id),
        )
        self._bump_col(now_ts)
        self._conn.commit()
