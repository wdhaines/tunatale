"""SQLite repository for SRS collocations and violations.

Schema is managed by `app.srs.migrations`. Fresh DBs bootstrap the v0 base
tables (matching the pre-migration shape) and then `migrate()` runs every
pending step up to `CURRENT_VERSION`.

Supports ":memory:" for in-memory test databases.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from app.common.guid import compute_guid
from app.models.srs_item import (
    Direction,
    DirectionState,
    SRSItem,
    SRSState,
)
from app.models.syntactic_unit import SyntacticUnit
from app.srs.migrations import migrate

# v0 base schema. Fresh DBs go through v0 → v1 → v2 via migrations so every
# deployment converges on the same path.
_CREATE_COLLOCATIONS_V0 = """
CREATE TABLE IF NOT EXISTS collocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT UNIQUE NOT NULL,
    translation TEXT NOT NULL DEFAULT '',
    language_code TEXT NOT NULL DEFAULT 'sl',
    word_count INTEGER NOT NULL DEFAULT 1,
    unit_difficulty INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'corpus',
    corpus_frequency INTEGER NOT NULL DEFAULT 0,
    stability REAL NOT NULL DEFAULT 1.0,
    fsrs_difficulty REAL NOT NULL DEFAULT 5.0,
    due_date TEXT NOT NULL,
    reps INTEGER NOT NULL DEFAULT 0,
    lapses INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'new',
    last_review TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
)
"""

_CREATE_VIOLATIONS = """
CREATE TABLE IF NOT EXISTS violations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collocation_text TEXT NOT NULL,
    day_number INTEGER NOT NULL,
    violation_type TEXT NOT NULL,
    details TEXT,
    created_at TEXT DEFAULT (datetime('now'))
)
"""

_CREATE_SYNC_CONFLICTS = """
CREATE TABLE IF NOT EXISTS sync_conflicts (
    id INTEGER PRIMARY KEY,
    guid TEXT NOT NULL,
    direction TEXT,
    field TEXT NOT NULL,
    local_value TEXT,
    remote_value TEXT,
    resolution TEXT NOT NULL,
    resolved_at TEXT NOT NULL
)
"""

_CREATE_PENDING_REVLOG = """
CREATE TABLE IF NOT EXISTS pending_revlog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cid INTEGER NOT NULL,
    ease INTEGER NOT NULL,
    ivl INTEGER NOT NULL,
    last_ivl INTEGER NOT NULL,
    factor INTEGER NOT NULL,
    time_ms INTEGER NOT NULL,
    type INTEGER NOT NULL,
    created_at TEXT NOT NULL
)
"""

_CREATE_PENDING_REVLOG_IDX = """
CREATE INDEX IF NOT EXISTS idx_pending_revlog_cid ON pending_revlog(cid)
"""

# Columns on `collocation_directions` mapped onto a DirectionState.
_DIR_COLUMNS = (
    "stability",
    "fsrs_difficulty",
    "due_date",
    "reps",
    "lapses",
    "state",
    "last_review",
    "anki_card_id",
    "dirty_fsrs",
    "last_synced_at",
)

# States that should never surface in the due queue regardless of due_date.
_NON_REVIEWABLE_STATES = ("new", "suspended", "known")


class SRSDatabase:
    """SQLite-backed SRS repository.

    Use `:memory:` as db_path for in-memory test databases.
    """

    def close(self) -> None:
        """Explicitly close the in-memory connection."""
        if self._in_memory and self._conn is not None:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> SRSDatabase:
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def __init__(self, db_path: str = ":memory:") -> None:
        self._in_memory = db_path == ":memory:"
        if self._in_memory:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._init_schema(self._conn)
        else:
            path = Path(db_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._path = str(path)
            self._conn = None
            with self._file_conn() as conn:
                self._init_schema(conn)

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(_CREATE_COLLOCATIONS_V0)
        conn.execute(_CREATE_VIOLATIONS)
        conn.commit()
        migrate(conn)
        conn.execute(_CREATE_SYNC_CONFLICTS)
        conn.execute(_CREATE_PENDING_REVLOG)
        conn.execute(_CREATE_PENDING_REVLOG_IDX)
        conn.commit()

    @contextmanager
    def _file_conn(self):
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def _get_conn(self):
        txn = getattr(self, "_txn_conn", None)
        if txn is not None:
            yield txn
        elif self._in_memory:
            yield self._conn
        else:
            with self._file_conn() as conn:
                yield conn

    def _commit(self, conn: sqlite3.Connection) -> None:
        if getattr(self, "_txn_conn", None) is not None:
            return  # transaction context handles commits
        if self._in_memory:
            conn.commit()
        # file-backed contexts commit on exit

    @contextmanager
    def begin_transaction(self, dry_run: bool = False):
        """Wrap subsequent DB calls in a single transaction.

        COMMIT on success unless dry_run=True; ROLLBACK on any exception.
        """
        assert not hasattr(self, "_txn_conn"), "Nested transactions not supported"
        if self._in_memory:
            conn = self._conn
            prev_iso = conn.isolation_level
            conn.isolation_level = None
            conn.execute("BEGIN")
        else:
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.isolation_level = None
            conn.execute("BEGIN")
        self._txn_conn = conn
        try:
            yield self
            if dry_run:
                conn.execute("ROLLBACK")
            else:
                conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            del self._txn_conn
            if self._in_memory:
                conn.isolation_level = prev_iso
            else:
                conn.close()

    # ── Write operations ───────────────────────────────────────────────

    def add_collocation(self, unit: SyntacticUnit, language_code: str = "sl") -> None:
        """Insert a new collocation; if it already exists, backfill an empty translation.

        New rows get both recognition and production direction rows (defaults).
        """
        disambig = unit.disambig_key
        guid = compute_guid(unit.text, language_code, disambig)
        today = date.today().isoformat()
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO collocations
                    (text, translation, language_code, word_count, unit_difficulty,
                     source, corpus_frequency, lemma, guid, disambig_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(text, disambig_key) DO UPDATE SET
                    translation = CASE
                        WHEN excluded.translation != '' AND collocations.translation = ''
                        THEN excluded.translation
                        ELSE collocations.translation
                    END
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
                ),
            )
            row = conn.execute(
                "SELECT id FROM collocations WHERE text = ? AND disambig_key = ?",
                (unit.text, disambig),
            ).fetchone()
            coll_id = row["id"]
            for direction in (Direction.RECOGNITION, Direction.PRODUCTION):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO collocation_directions
                        (collocation_id, direction, due_date)
                    VALUES (?, ?, ?)
                    """,
                    (coll_id, direction.value, today),
                )
            self._commit(conn)

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

    def update_direction(
        self,
        guid: str,
        direction: Direction,
        state: DirectionState,
    ) -> None:
        """Persist the FSRS state for one direction of a collocation."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT id FROM collocations WHERE guid = ?", (guid,)).fetchone()
            if row is None:
                return
            conn.execute(
                """
                UPDATE collocation_directions SET
                    stability = ?,
                    fsrs_difficulty = ?,
                    due_date = ?,
                    reps = ?,
                    lapses = ?,
                    state = ?,
                    last_review = ?,
                    anki_card_id = ?,
                    dirty_fsrs = ?,
                    last_synced_at = ?
                WHERE collocation_id = ? AND direction = ?
                """,
                (
                    state.stability,
                    state.difficulty,
                    state.due_date.isoformat(),
                    state.reps,
                    state.lapses,
                    state.state.value,
                    state.last_review.isoformat() if state.last_review else None,
                    state.anki_card_id,
                    1 if state.dirty_fsrs else 0,
                    state.last_synced_at,
                    row["id"],
                    direction.value,
                ),
            )
            self._commit(conn)

    def update_collocation(self, item: SRSItem) -> None:
        """Back-compat shim: persist the recognition direction of `item`.

        Callers still constructing flat SRSItems (via property shims) go
        through this path. Stage 3.5 removes it.
        """
        if item.guid is None:
            # Fall back to looking up the guid by text for legacy flows.
            with self._get_conn() as conn:
                row = conn.execute(
                    "SELECT guid FROM collocations WHERE text = ?",
                    (item.syntactic_unit.text,),
                ).fetchone()
                if row is None:
                    return
                guid = row["guid"]
        else:
            guid = item.guid
        self.update_direction(guid, Direction.RECOGNITION, item.directions[Direction.RECOGNITION])

    def record_violation(
        self, collocation_text: str, day_number: int, violation_type: str, details: str | None = None
    ) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO violations (collocation_text, day_number, violation_type, details) VALUES (?, ?, ?, ?)",
                (collocation_text, day_number, violation_type, details),
            )
            self._commit(conn)

    # ── Read operations ────────────────────────────────────────────────

    def _load_directions(self, conn: sqlite3.Connection, collocation_id: int) -> dict[Direction, DirectionState]:
        rows = conn.execute(
            f"SELECT direction, {', '.join(_DIR_COLUMNS)} FROM collocation_directions WHERE collocation_id = ?",
            (collocation_id,),
        ).fetchall()
        directions: dict[Direction, DirectionState] = {}
        for row in rows:
            d = Direction(row["direction"])
            directions[d] = DirectionState(
                direction=d,
                due_date=date.fromisoformat(row["due_date"]),
                stability=row["stability"],
                difficulty=row["fsrs_difficulty"],
                reps=row["reps"],
                lapses=row["lapses"],
                state=SRSState(row["state"]),
                last_review=date.fromisoformat(row["last_review"]) if row["last_review"] else None,
                anki_card_id=row["anki_card_id"],
                dirty_fsrs=bool(row["dirty_fsrs"]),
                last_synced_at=row["last_synced_at"],
            )
        return directions

    def _row_to_item(self, conn: sqlite3.Connection, row: sqlite3.Row) -> SRSItem:
        unit = SyntacticUnit(
            text=row["text"],
            translation=row["translation"],
            word_count=row["word_count"],
            difficulty=row["unit_difficulty"],
            source=row["source"],
            frequency=row["corpus_frequency"],
            lemma=row["lemma"],
            guid=row["guid"],
            disambig_key=row["disambig_key"],
        )
        directions = self._load_directions(conn, row["id"])
        return SRSItem(
            syntactic_unit=unit,
            directions=directions,
            guid=row["guid"],
            anki_note_id=row["anki_note_id"],
        )

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

    def get_collocation_by_anki_note_id(self, anki_note_id: int) -> SRSItem | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM collocations WHERE anki_note_id = ? LIMIT 1", (anki_note_id,)).fetchone()
            if row is None:
                return None
            return self._row_to_item(conn, row)

    def get_collocation_by_lemma(self, lemma: str) -> SRSItem | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM collocations WHERE lemma = ? LIMIT 1", (lemma,)).fetchone()
            if row is None:
                return None
            return self._row_to_item(conn, row)

    def get_collocation_by_lemma_with_id(self, lemma: str) -> tuple[int, SRSItem] | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM collocations WHERE lemma = ? LIMIT 1", (lemma,)).fetchone()
            if row is None:
                return None
            return (row["id"], self._row_to_item(conn, row))

    def get_collocations_for_language(
        self,
        language_code: str,
        min_word_count: int = 2,
    ) -> list[tuple[int, str]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, text FROM collocations WHERE language_code = ? AND word_count >= ?",
                (language_code, min_word_count),
            ).fetchall()
        return [(row["id"], row["text"]) for row in rows]

    def get_due_collocations(
        self,
        as_of: date,
        direction: Direction = Direction.RECOGNITION,
    ) -> list[SRSItem]:
        """Return all collocations whose `direction` is due on or before `as_of`."""
        placeholders = ",".join("?" * len(_NON_REVIEWABLE_STATES))
        with self._get_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT c.* FROM collocations c
                JOIN collocation_directions d ON d.collocation_id = c.id
                WHERE d.direction = ?
                  AND d.due_date <= ?
                  AND d.state NOT IN ({placeholders})
                """,
                (direction.value, as_of.isoformat(), *_NON_REVIEWABLE_STATES),
            ).fetchall()
            return [self._row_to_item(conn, r) for r in rows]

    def get_new_collocations(
        self,
        limit: int = 10,
        direction: Direction = Direction.RECOGNITION,
    ) -> list[SRSItem]:
        """Return collocations whose `direction` state is NEW."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT c.* FROM collocations c
                JOIN collocation_directions d ON d.collocation_id = c.id
                WHERE d.direction = ? AND d.state = 'new'
                LIMIT ?
                """,
                (direction.value, limit),
            ).fetchall()
            return [self._row_to_item(conn, r) for r in rows]

    def get_due_items(
        self,
        as_of: date,
        direction: Direction = Direction.RECOGNITION,
    ) -> list[tuple[int, SRSItem, str]]:
        """Like get_due_collocations but returns (id, SRSItem, language_code) tuples."""
        placeholders = ",".join("?" * len(_NON_REVIEWABLE_STATES))
        with self._get_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT c.* FROM collocations c
                JOIN collocation_directions d ON d.collocation_id = c.id
                WHERE d.direction = ?
                  AND d.due_date <= ?
                  AND d.state NOT IN ({placeholders})
                """,
                (direction.value, as_of.isoformat(), *_NON_REVIEWABLE_STATES),
            ).fetchall()
            return [(r["id"], self._row_to_item(conn, r), r["language_code"]) for r in rows]

    def get_new_items(
        self,
        limit: int = 10,
        direction: Direction = Direction.RECOGNITION,
    ) -> list[tuple[int, SRSItem, str]]:
        """Like get_new_collocations but returns (id, SRSItem, language_code) tuples."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT c.* FROM collocations c
                JOIN collocation_directions d ON d.collocation_id = c.id
                WHERE d.direction = ? AND d.state = 'new'
                LIMIT ?
                """,
                (direction.value, limit),
            ).fetchall()
            return [(r["id"], self._row_to_item(conn, r), r["language_code"]) for r in rows]

    def update_direction_by_id(self, row_id: int, direction: Direction, state: DirectionState) -> None:
        """Persist direction state for a collocation identified by row id."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT guid FROM collocations WHERE id = ?", (row_id,)).fetchone()
            if row is None:
                return
        self.update_direction(row["guid"], direction, state)

    def get_image_filename(self, collocation_id: int) -> str | None:
        """Return the filename of the first image media row for a collocation, or None."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT filename FROM media WHERE collocation_id = ? AND kind = 'image' LIMIT 1",
                (collocation_id,),
            ).fetchone()
        return row["filename"] if row is not None else None

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

    def reset_collocation(self, row_id: int, direction: Direction | None = None) -> None:
        """Reset FSRS scheduling for one or both directions of a collocation."""
        today = date.today().isoformat()
        params: tuple
        if direction is None:
            sql = (
                "UPDATE collocation_directions SET "
                "state = 'new', stability = 1.0, fsrs_difficulty = 5.0, "
                "reps = 0, lapses = 0, due_date = ?, last_review = NULL, "
                "dirty_fsrs = 0 "
                "WHERE collocation_id = ?"
            )
            params = (today, row_id)
        else:
            sql = (
                "UPDATE collocation_directions SET "
                "state = 'new', stability = 1.0, fsrs_difficulty = 5.0, "
                "reps = 0, lapses = 0, due_date = ?, last_review = NULL, "
                "dirty_fsrs = 0 "
                "WHERE collocation_id = ? AND direction = ?"
            )
            params = (today, row_id, direction.value)
        with self._get_conn() as conn:
            conn.execute(sql, params)
            conn.execute(
                "UPDATE collocations SET updated_at = datetime('now') WHERE id = ?",
                (row_id,),
            )
            self._commit(conn)

    def set_state_by_id(
        self,
        row_id: int,
        state: SRSState,
        direction: Direction | None = None,
    ) -> None:
        """Set the state of a collocation directly, bypassing FSRS scheduling."""
        with self._get_conn() as conn:
            if direction is None:
                conn.execute(
                    "UPDATE collocation_directions SET state = ? WHERE collocation_id = ?",
                    (state.value, row_id),
                )
            else:
                conn.execute(
                    "UPDATE collocation_directions SET state = ? WHERE collocation_id = ? AND direction = ?",
                    (state.value, row_id, direction.value),
                )
            conn.execute(
                "UPDATE collocations SET updated_at = datetime('now') WHERE id = ?",
                (row_id,),
            )
            self._commit(conn)

    def set_suspended(
        self,
        row_id: int,
        suspended: bool,
        direction: Direction | None = None,
    ) -> None:
        """Suspend or unsuspend a collocation. Unsuspending resets state to 'new'."""
        new_state = SRSState.SUSPENDED if suspended else SRSState.NEW
        self.set_state_by_id(row_id, new_state, direction=direction)

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
            "due_date": "due_date",
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
        join = "JOIN collocation_directions d_filter ON d_filter.collocation_id = c.id AND d_filter.direction = ?"
        join_params = [order_direction.value]

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        order_expr = f"c.{order_by}" if order_by in parent_columns else f"d_filter.{direction_columns[order_by]}"

        count_sql = f"SELECT COUNT(*) FROM collocations c {join} {where}"
        rows_sql = f"SELECT c.* FROM collocations c {join} {where} ORDER BY {order_expr} {order_dir} LIMIT ? OFFSET ?"

        with self._get_conn() as conn:
            total = conn.execute(count_sql, join_params + params).fetchone()[0]
            rows = conn.execute(rows_sql, join_params + params + [limit, offset]).fetchall()
            result = [(r["id"], self._row_to_item(conn, r), r["language_code"]) for r in rows]
        return result, total

    def get_violations(self, collocation_text: str) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM violations WHERE collocation_text = ?",
                (collocation_text,),
            ).fetchall()
        return [dict(r) for r in rows]

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
        with self._get_conn() as conn:
            row = conn.execute("SELECT id FROM collocations WHERE guid = ?", (guid,)).fetchone()
            if row is None:
                cursor = conn.execute(
                    """
                    INSERT INTO collocations
                        (text, translation, language_code, word_count, unit_difficulty,
                         source, corpus_frequency, lemma, guid, anki_note_id, disambig_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ),
                )
                coll_id = cursor.lastrowid
                for direction, state in directions.items():
                    conn.execute(
                        """
                        INSERT INTO collocation_directions
                            (collocation_id, direction, stability, fsrs_difficulty, due_date,
                             reps, lapses, state, last_review, anki_card_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            coll_id,
                            direction.value,
                            state.stability,
                            state.difficulty,
                            state.due_date.isoformat(),
                            state.reps,
                            state.lapses,
                            state.state.value,
                            state.last_review.isoformat() if state.last_review else None,
                            state.anki_card_id,
                        ),
                    )
            else:
                coll_id = row["id"]
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
                        unit.lemma,
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
                                (collocation_id, direction, stability, fsrs_difficulty, due_date,
                                 reps, lapses, state, last_review, anki_card_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                coll_id,
                                direction.value,
                                state.stability,
                                state.difficulty,
                                state.due_date.isoformat(),
                                state.reps,
                                state.lapses,
                                state.state.value,
                                state.last_review.isoformat() if state.last_review else None,
                                state.anki_card_id,
                            ),
                        )
                    elif dir_row["reps"] > 0:
                        conn.execute(
                            "UPDATE collocation_directions SET anki_card_id = ? WHERE collocation_id = ? AND direction = ?",
                            (state.anki_card_id, coll_id, direction.value),
                        )
                    else:
                        conn.execute(
                            """
                            UPDATE collocation_directions SET
                                stability = ?, fsrs_difficulty = ?, due_date = ?,
                                reps = ?, lapses = ?, state = ?, last_review = ?, anki_card_id = ?
                            WHERE collocation_id = ? AND direction = ?
                            """,
                            (
                                state.stability,
                                state.difficulty,
                                state.due_date.isoformat(),
                                state.reps,
                                state.lapses,
                                state.state.value,
                                state.last_review.isoformat() if state.last_review else None,
                                state.anki_card_id,
                                coll_id,
                                direction.value,
                            ),
                        )
            self._commit(conn)
        return coll_id

    def set_anki_ids(
        self,
        guid: str,
        note_id: int,
        card_ids: dict[Direction, int],
    ) -> None:
        """Set anki_note_id on the parent and anki_card_id on each direction row."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT id FROM collocations WHERE guid = ?", (guid,)).fetchone()
            if row is None:
                return
            coll_id = row["id"]
            conn.execute(
                "UPDATE collocations SET anki_note_id = ? WHERE id = ?",
                (note_id, coll_id),
            )
            for direction, card_id in card_ids.items():
                conn.execute(
                    "UPDATE collocation_directions SET anki_card_id = ? WHERE collocation_id = ? AND direction = ?",
                    (card_id, coll_id, direction.value),
                )
            self._commit(conn)

    def add_media(
        self,
        collocation_id: int,
        kind: str,
        filename: str,
        path: str,
        anki_filename: str,
        sha256: str,
        size_bytes: int,
    ) -> int:
        """Insert a media row. Returns the new media id."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO media (collocation_id, kind, filename, path, anki_filename, sha256, bytes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (collocation_id, kind, filename, path, anki_filename, sha256, size_bytes),
            )
            self._commit(conn)
            return cursor.lastrowid

    def find_media_by_anki_filename(self, anki_filename: str) -> dict[str, Any] | None:
        """Return the media row for the given Anki filename, or None."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM media WHERE anki_filename = ?",
                (anki_filename,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_dirty(
        self,
        direction: Direction | None = None,
    ) -> list[tuple[str, Direction, DirectionState]]:
        """Return (guid, direction, DirectionState) tuples for dirty FSRS rows.

        A row is dirty when dirty_fsrs=1 (set by schedule() after a review).
        Pass direction to restrict to one direction; omit for both.
        """
        with self._get_conn() as conn:
            if direction is None:
                rows = conn.execute(
                    f"""
                    SELECT c.guid, d.direction, {", ".join(f"d.{col}" for col in _DIR_COLUMNS)}
                    FROM collocations c
                    JOIN collocation_directions d ON d.collocation_id = c.id
                    WHERE d.dirty_fsrs = 1
                    """,
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    SELECT c.guid, d.direction, {", ".join(f"d.{col}" for col in _DIR_COLUMNS)}
                    FROM collocations c
                    JOIN collocation_directions d ON d.collocation_id = c.id
                    WHERE d.dirty_fsrs = 1 AND d.direction = ?
                    """,
                    (direction.value,),
                ).fetchall()
            result = []
            for row in rows:
                d = Direction(row["direction"])
                ds = DirectionState(
                    direction=d,
                    due_date=date.fromisoformat(row["due_date"]),
                    stability=row["stability"],
                    difficulty=row["fsrs_difficulty"],
                    reps=row["reps"],
                    lapses=row["lapses"],
                    state=SRSState(row["state"]),
                    last_review=date.fromisoformat(row["last_review"]) if row["last_review"] else None,
                    anki_card_id=row["anki_card_id"],
                    dirty_fsrs=bool(row["dirty_fsrs"]),
                    last_synced_at=row["last_synced_at"],
                )
                result.append((row["guid"], d, ds))
        return result

    def mark_direction_clean(self, guid: str, direction: Direction) -> None:
        """Clear dirty_fsrs and set last_synced_at to now for one direction."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT id FROM collocations WHERE guid = ?", (guid,)).fetchone()
            if row is None:
                return
            conn.execute(
                """
                UPDATE collocation_directions SET
                    dirty_fsrs = 0,
                    last_synced_at = datetime('now')
                WHERE collocation_id = ? AND direction = ?
                """,
                (row["id"], direction.value),
            )
            self._commit(conn)

    def count_due_collocations(
        self,
        as_of: date,
        direction: Direction = Direction.RECOGNITION,
    ) -> int:
        placeholders = ",".join("?" * len(_NON_REVIEWABLE_STATES))
        with self._get_conn() as conn:
            return conn.execute(
                f"""
                SELECT COUNT(DISTINCT c.id) FROM collocations c
                JOIN collocation_directions d ON d.collocation_id = c.id
                WHERE d.direction = ?
                  AND d.due_date <= ?
                  AND d.state NOT IN ({placeholders})
                """,
                (direction.value, as_of.isoformat(), *_NON_REVIEWABLE_STATES),
            ).fetchone()[0]

    def record_sync_conflict(
        self,
        *,
        guid: str,
        direction: str | None,
        field: str,
        local: str | None,
        remote: str | None,
        resolution: str,
    ) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO sync_conflicts
                    (guid, direction, field, local_value, remote_value, resolution, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (guid, direction, field, local, remote, resolution),
            )
            self._commit(conn)

    def list_sync_conflicts(self) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM sync_conflicts ORDER BY id").fetchall()
            return [dict(r) for r in rows]

    def enqueue_pending_revlog(
        self,
        *,
        cid: int,
        ease: int,
        ivl: int,
        last_ivl: int,
        factor: int,
        time_ms: int,
        type_: int,
    ) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO pending_revlog
                    (cid, ease, ivl, last_ivl, factor, time_ms, type, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (cid, ease, ivl, last_ivl, factor, time_ms, type_),
            )
            self._commit(conn)

    def drain_pending_revlog(self) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM pending_revlog ORDER BY id").fetchall()
            result = [dict(r) for r in rows]
            conn.execute("DELETE FROM pending_revlog")
            self._commit(conn)
            return result

    def set_dirty_fields(self, guid: str, fields_str: str) -> None:
        """Set dirty_fields for the collocation identified by guid."""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE collocations SET dirty_fields = ? WHERE guid = ?",
                (fields_str, guid),
            )
            self._commit(conn)

    def get_dirty_fields(self, guid: str) -> str:
        """Return dirty_fields for the collocation identified by guid, or ''."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT dirty_fields FROM collocations WHERE guid = ?",
                (guid,),
            ).fetchone()
        return (row["dirty_fields"] or "") if row else ""

    def update_collocation_for_sync(
        self,
        guid: str,
        *,
        translation: str,
        dirty_fields_str: str,
    ) -> None:
        """Update translation and dirty_fields after a sync pull."""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE collocations SET translation = ?, dirty_fields = ?, "
                "last_synced_at = datetime('now'), updated_at = datetime('now') WHERE guid = ?",
                (translation, dirty_fields_str, guid),
            )
            self._commit(conn)

    def list_items_without_anki_note(self) -> list[tuple[str, SRSItem]]:
        """Return (guid, SRSItem) for collocations with no anki_note_id set."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM collocations WHERE anki_note_id IS NULL").fetchall()
            return [(row["guid"], self._row_to_item(conn, row)) for row in rows]

    def list_dirty_field_edits(self) -> list[tuple[str, int | None, str, SRSItem]]:
        """Return (guid, anki_note_id, dirty_fields_str, SRSItem) for rows with non-empty dirty_fields."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM collocations WHERE dirty_fields IS NOT NULL AND dirty_fields != ''"
            ).fetchall()
            return [
                (row["guid"], row["anki_note_id"], row["dirty_fields"], self._row_to_item(conn, row)) for row in rows
            ]
