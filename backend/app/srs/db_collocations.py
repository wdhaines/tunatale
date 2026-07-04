"""Collocation CRUD mixin for SRSDatabase.

Extracted verbatim from app/srs/database.py (god-module split, stage 4).
Row-level create/read/update/delete/list for collocations, including
upsert_by_guid — the card-adding contract entry point (see
.claude/rules/anki-sync.md "When building a new UI that adds cards").
"""

import sqlite3
from datetime import date

from app.anki.rollover import due_at_rollover_utc
from app.common.guid import compute_guid
from app.models.srs_item import Direction, DirectionState, SRSItem, SRSState
from app.models.syntactic_unit import SyntacticUnit, serialize_extras


class DbCollocationsMixin:
    """Collocation CRUD. Mixed into SRSDatabase; relies on SRSDatabaseBase infra."""

    def add_collocation(self, unit: SyntacticUnit, language_code: str = "sl") -> bool:
        """Insert a new collocation; if it already exists, backfill an empty translation.

        New rows get both recognition and production direction rows (defaults).
        Single-word units without an explicit lemma get lemma = casefolded text
        so that get_collocation_by_lemma_with_id lookups succeed. Empty strings
        count as missing — pre-Phase-F sync paths sometimes wrote empties.

        Returns True if a new row was inserted, False if it already existed.
        """
        if not unit.lemma and unit.word_count == 1:
            unit.lemma = unit.text.casefold()
        disambig = unit.disambig_key
        guid = compute_guid(unit.text, language_code, disambig)
        is_new = False
        with self._get_conn() as conn:
            # Identity is the case-normalized guid; legacy rows may carry a
            # stale guid that no longer matches the current compute_guid output,
            # so check guid first, then fall back to (text, language_code,
            # disambig_key) which is the actual UNIQUE constraint enforced by
            # the schema. Heal a stale guid in place when the fallback matches.
            existing = conn.execute(
                "SELECT id, guid, translation FROM collocations WHERE guid = ?",
                (guid,),
            ).fetchone()
            if existing is None:
                existing = conn.execute(
                    """
                    SELECT id, guid, translation FROM collocations
                    WHERE text = ? AND language_code = ? AND disambig_key = ?
                    """,
                    (unit.text, language_code, disambig),
                ).fetchone()
            if existing is None:
                is_new = True
                conn.execute(
                    """
                    INSERT INTO collocations
                        (text, translation, language_code, word_count, unit_difficulty,
                         source, corpus_frequency, lemma, guid, disambig_key, article, extras, grammar, note,
                         source_sentence, sentence_translation, source_lesson_id, source_line_index, card_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        unit.text,
                        unit.translation,
                        language_code,
                        unit.word_count,
                        unit.difficulty,
                        unit.source,
                        unit.frequency,
                        unit.lemma,
                        guid,
                        disambig,
                        unit.article,
                        serialize_extras(unit.extras),
                        unit.grammar,
                        unit.note,
                        unit.source_sentence,
                        unit.source_sentence_translation,
                        unit.source_lesson_id,
                        unit.source_line_index,
                        unit.card_type,
                    ),
                )
                coll_id = conn.execute(
                    "SELECT id FROM collocations WHERE guid = ?",
                    (guid,),
                ).fetchone()["id"]
            else:
                coll_id = existing["id"]
                if existing["translation"] == "" and unit.translation:
                    conn.execute(
                        "UPDATE collocations SET translation = ? WHERE id = ?",
                        (unit.translation, coll_id),
                    )
                if existing["guid"] != guid:
                    conn.execute(
                        "UPDATE collocations SET guid = ? WHERE id = ?",
                        (guid, coll_id),
                    )
            if unit.card_type == "cloze":
                directions = [Direction.PRODUCTION]
            else:
                directions = [Direction.RECOGNITION, Direction.PRODUCTION]
            today_due_at = due_at_rollover_utc(date.today()).isoformat()
            for direction in directions:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO collocation_directions
                        (collocation_id, direction, due_at)
                    VALUES (?, ?, ?)
                    """,
                    (coll_id, direction.value, today_due_at),
                )
            self._commit(conn)
        return is_new

    def get_untranslated_collocations(self) -> list[tuple[str, str]]:
        """Return (text, language_code) for all rows with an empty translation."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT text, language_code FROM collocations WHERE translation = ''",
            ).fetchall()
        return [(row["text"], row["language_code"]) for row in rows]

    def backfill_translations(self, glosses: dict[str, str]) -> int:
        """Update rows with empty translations using the provided gloss map."""
        if not glosses:
            return 0
        updated = 0
        with self._get_conn() as conn:
            for text, translation in glosses.items():
                if not translation:
                    continue
                cursor = conn.execute(
                    "UPDATE collocations SET translation = ?, updated_at = datetime('now') "
                    "WHERE text = ? AND translation = ''",
                    (translation, text),
                )
                updated += cursor.rowcount
            self._commit(conn)
        return updated

    def get_collocation(self, text: str) -> SRSItem | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM collocations WHERE text = ?", (text,)).fetchone()
            if row is None:
                return None
            return self._row_to_item(conn, row)

    def get_collocation_by_guid(self, guid: str) -> SRSItem | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM collocations WHERE guid = ?", (guid,)).fetchone()
            if row is None:
                return None
            return self._row_to_item(conn, row)

    def get_collocation_id_by_guid(self, guid: str) -> int | None:
        """Return the collocation row id for a guid, or None."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT id FROM collocations WHERE guid = ?", (guid,)).fetchone()
            return row[0] if row else None

    def get_guid_by_collocation_id(self, collocation_id: int) -> str | None:
        """Return the guid for a collocation row id, or None."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT guid FROM collocations WHERE id = ?", (collocation_id,)).fetchone()
            return row["guid"] if row else None

    def get_collocation_by_anki_note_id(self, anki_note_id: int) -> SRSItem | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM collocations WHERE anki_note_id = ? LIMIT 1", (anki_note_id,)).fetchone()
            if row is None:
                return None
            return self._row_to_item(conn, row)

    def get_created_at_by_guid(self, guid: str) -> str | None:
        """Return the ISO timestamp from collocations.created_at for the given guid,
        or None if no row matches. Used by sync_create_new to sort items so newer
        cards get higher cards.due under Anki's Descending position gather order.
        """
        with self._get_conn() as conn:
            row = conn.execute("SELECT created_at FROM collocations WHERE guid = ?", (guid,)).fetchone()
            return row["created_at"] if row else None

    def get_collocation_by_id(self, row_id: int) -> tuple[int, SRSItem, str] | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM collocations WHERE id = ?", (row_id,)).fetchone()
            if row is None:
                return None
            return (row["id"], self._row_to_item(conn, row), row["language_code"])

    def update_collocation_fields(self, row_id: int, *, text: str, translation: str) -> None:
        """Update text and translation for a collocation by id.

        When `text` changes, the computed guid updates accordingly.
        Changed fields are appended to dirty_fields for sync tracking.
        """
        try:
            with self._get_conn() as conn:
                cur = conn.execute(
                    "SELECT language_code, text, translation, dirty_fields, disambig_key FROM collocations WHERE id = ?",
                    (row_id,),
                ).fetchone()
                if cur is None:
                    return
                disambig = cur["disambig_key"] if cur["disambig_key"] is not None else ""
                new_guid = compute_guid(text, cur["language_code"], disambig)
                changed: set[str] = set()
                if text != cur["text"]:
                    changed.add("text")
                if translation != cur["translation"]:
                    changed.add("translation")
                existing = {f for f in (cur["dirty_fields"] or "").split(",") if f}
                merged = ",".join(sorted(existing | changed))
                conn.execute(
                    "UPDATE collocations SET text = ?, translation = ?, guid = ?, "
                    "dirty_fields = ?, updated_at = datetime('now') WHERE id = ?",
                    (text, translation, new_guid, merged, row_id),
                )
                self._commit(conn)
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"text already exists: {text!r}") from exc

    def delete_collocation(self, row_id: int) -> None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT text FROM collocations WHERE id = ?", (row_id,)).fetchone()
            if row is not None:
                conn.execute("DELETE FROM violations WHERE collocation_text = ?", (row["text"],))
                conn.execute("DELETE FROM collocations WHERE id = ?", (row_id,))
                self._commit(conn)

    def delete_collocations(self, row_ids: list[int]) -> int:
        if not row_ids:
            return 0
        placeholders = ",".join("?" * len(row_ids))
        with self._get_conn() as conn:
            rows = conn.execute(f"SELECT text FROM collocations WHERE id IN ({placeholders})", row_ids).fetchall()
            texts = [r["text"] for r in rows]
            if texts:
                text_ph = ",".join("?" * len(texts))
                conn.execute(f"DELETE FROM violations WHERE collocation_text IN ({text_ph})", texts)
            conn.execute(f"DELETE FROM collocations WHERE id IN ({placeholders})", row_ids)
            self._commit(conn)
        return len(texts)

    def untrack_collocation(self, row_id: int) -> dict[str, str]:
        """Remove a collocation from the user's learning queue.

        If the row was never pushed to Anki (anki_note_id IS NULL), delete it
        outright (cascade deletes both direction rows). Otherwise suspend both
        directions and mark dirty_fsrs=1 so the next Anki push suspends the card.

        Returns {"action": "deleted"} or {"action": "suspended"}.
        """
        with self._get_conn() as conn:
            row = conn.execute("SELECT anki_note_id FROM collocations WHERE id = ?", (row_id,)).fetchone()
            if row is None:
                return {"action": "deleted"}
            if row["anki_note_id"] is None:
                conn.execute(
                    "DELETE FROM violations WHERE collocation_text = (SELECT text FROM collocations WHERE id = ?)",
                    (row_id,),
                )
                conn.execute("DELETE FROM collocations WHERE id = ?", (row_id,))
                self._commit(conn)
                return {"action": "deleted"}
            conn.execute(
                "UPDATE collocation_directions SET state = 'suspended', dirty_fsrs = 1 WHERE collocation_id = ?",
                (row_id,),
            )
            conn.execute(
                "UPDATE collocations SET updated_at = datetime('now') WHERE id = ?",
                (row_id,),
            )
            self._commit(conn)
            return {"action": "suspended"}

    def list_collocations(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        state: SRSState | None = None,
        order_by: str = "text",
        order_dir: str = "asc",
        order_direction: Direction = Direction.RECOGNITION,
    ) -> tuple[list[tuple[int, SRSItem, str]], int]:
        """Paginated browse for the admin UI. Returns (rows, total_count)."""
        parent_columns = {"text", "translation"}
        direction_columns = {
            "state": "state",
            "due_date": "due_at",
            "due_at": "due_at",
            "fsrs_difficulty": "fsrs_difficulty",
            "reps": "reps",
            "lapses": "lapses",
            "last_review": "last_review",
        }
        _VALID_ORDER_DIR = {"asc", "desc"}

        if order_by not in parent_columns and order_by not in direction_columns:
            raise ValueError(f"Invalid order_by: {order_by!r}")
        if order_dir not in _VALID_ORDER_DIR:
            raise ValueError(f"Invalid order_dir: {order_dir!r}")

        conditions: list[str] = []
        params: list = []

        if search:
            conditions.append("(c.text LIKE ? OR c.translation LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        if state is not None:
            conditions.append("d_filter.state = ?")
            params.append(state.value)

        # d_filter is a direction row used for state filter + ordering by direction columns.
        # It is always joined on the requested order_direction.
        join = "LEFT JOIN collocation_directions d_filter ON d_filter.collocation_id = c.id AND d_filter.direction = ?"
        join_params = [order_direction.value]

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        order_expr = f"c.{order_by}" if order_by in parent_columns else f"d_filter.{direction_columns[order_by]}"

        count_sql = f"SELECT COUNT(*) FROM collocations c {join} {where}"
        # Content-based tie-breakers: without them, which tied rows survive the
        # LIMIT depends on rowid (insertion) order — nondeterministic for callers
        # like build_learner_snapshot that need a pure function of DB contents.
        rows_sql = (
            f"SELECT c.* FROM collocations c {join} {where} "
            f"ORDER BY {order_expr} {order_dir}, c.text ASC, c.id ASC LIMIT ? OFFSET ?"
        )

        with self._get_conn() as conn:
            total = conn.execute(count_sql, join_params + params).fetchone()[0]
            rows = conn.execute(rows_sql, join_params + params + [limit, offset]).fetchall()
            result = [(r["id"], self._row_to_item(conn, r), r["language_code"]) for r in rows]
        return result, total

    def count_collocations(self) -> int:
        with self._get_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM collocations").fetchone()[0]

    # ── Anki-surface methods ───────────────────────────────────────────

    def upsert_by_guid(
        self,
        unit: SyntacticUnit,
        language_code: str,
        directions: dict[Direction, DirectionState],
        anki_note_id: int | None = None,
    ) -> int:
        """Insert parent row if new, else update parent scalar fields.

        Per-direction idempotency: if an existing direction has reps > 0,
        only update anki_card_id (preserve TunaTale-local review progress).
        If reps == 0, refresh all FSRS fields from the supplied state.
        Returns the collocation id.
        """
        guid = compute_guid(unit.text, language_code, unit.disambig_key)
        # Backfill missing single-word lemma so by-lemma lookups keep working;
        # mirrors add_collocation. Empty strings count as missing.
        if not unit.lemma and unit.word_count == 1:
            unit.lemma = unit.text.casefold()
        with self._get_conn() as conn:
            row = conn.execute("SELECT id, lemma FROM collocations WHERE guid = ?", (guid,)).fetchone()
            if row is None:
                cursor = conn.execute(
                    """
                    INSERT INTO collocations
                        (text, translation, language_code, word_count, unit_difficulty,
                         source, corpus_frequency, lemma, guid, anki_note_id, disambig_key,
                         article, extras, grammar, note, source_sentence, sentence_translation,
                         source_lesson_id, source_line_index, card_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        unit.text,
                        unit.translation,
                        language_code,
                        unit.word_count,
                        unit.difficulty,
                        unit.source,
                        unit.frequency,
                        unit.lemma,
                        guid,
                        anki_note_id,
                        unit.disambig_key,
                        unit.article,
                        serialize_extras(unit.extras),
                        unit.grammar,
                        unit.note,
                        unit.source_sentence,
                        unit.source_sentence_translation,
                        unit.source_lesson_id,
                        unit.source_line_index,
                        unit.card_type,
                    ),
                )
                coll_id = cursor.lastrowid
                for direction, state in directions.items():
                    conn.execute(
                        """
                        INSERT INTO collocation_directions
                            (collocation_id, direction, stability, fsrs_difficulty, due_at,
                             reps, lapses, state, last_review, anki_card_id, anki_due, left)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            coll_id,
                            direction.value,
                            state.stability,
                            state.difficulty,
                            state.due_at.isoformat(),
                            state.reps,
                            state.lapses,
                            state.state.value,
                            state.last_review.isoformat() if state.last_review else None,
                            state.anki_card_id,
                            state.anki_due,
                            state.left,
                        ),
                    )
            else:
                coll_id = row["id"]
                # Preserve an existing non-empty lemma if the incoming row is empty.
                resolved_lemma = unit.lemma if unit.lemma else row["lemma"]
                conn.execute(
                    """
                    UPDATE collocations SET
                        translation = ?, word_count = ?, unit_difficulty = ?,
                        source = ?, corpus_frequency = ?, lemma = ?,
                        anki_note_id = COALESCE(?, anki_note_id),
                        updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (
                        unit.translation,
                        unit.word_count,
                        unit.difficulty,
                        unit.source,
                        unit.frequency,
                        resolved_lemma,
                        anki_note_id,
                        coll_id,
                    ),
                )
                for direction, state in directions.items():
                    dir_row = conn.execute(
                        "SELECT reps FROM collocation_directions WHERE collocation_id = ? AND direction = ?",
                        (coll_id, direction.value),
                    ).fetchone()
                    if dir_row is None:
                        conn.execute(
                            """
                            INSERT INTO collocation_directions
                                (collocation_id, direction, stability, fsrs_difficulty, due_at,
                                 reps, lapses, state, last_review, anki_card_id, anki_due, left)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                coll_id,
                                direction.value,
                                state.stability,
                                state.difficulty,
                                state.due_at.isoformat(),
                                state.reps,
                                state.lapses,
                                state.state.value,
                                state.last_review.isoformat() if state.last_review else None,
                                state.anki_card_id,
                                state.anki_due,
                                state.left,
                            ),
                        )
                    elif dir_row["reps"] > 0:
                        conn.execute(
                            """
                            UPDATE collocation_directions SET
                                state = ?, due_at = ?, anki_card_id = ?,
                                anki_due = ?, left = ?
                            WHERE collocation_id = ? AND direction = ?
                            """,
                            (
                                state.state.value,
                                state.due_at.isoformat(),
                                state.anki_card_id,
                                state.anki_due,
                                state.left,
                                coll_id,
                                direction.value,
                            ),
                        )
                    else:
                        conn.execute(
                            """
                            UPDATE collocation_directions SET
                                stability = ?, fsrs_difficulty = ?, due_at = ?,
                                reps = ?, lapses = ?, state = ?, last_review = ?,
                                anki_card_id = ?, anki_due = ?, left = ?
                            WHERE collocation_id = ? AND direction = ?
                            """,
                            (
                                state.stability,
                                state.difficulty,
                                state.due_at.isoformat(),
                                state.reps,
                                state.lapses,
                                state.state.value,
                                state.last_review.isoformat() if state.last_review else None,
                                state.anki_card_id,
                                state.anki_due,
                                state.left,
                                coll_id,
                                direction.value,
                            ),
                        )
            self._commit(conn)
        return coll_id
