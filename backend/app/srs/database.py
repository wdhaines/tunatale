"""SQLite repository for SRS collocations and violations.

Supports ":memory:" for in-memory test databases.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager, suppress
from datetime import date
from pathlib import Path

from app.models.srs_item import SRSItem, SRSState
from app.models.syntactic_unit import SyntacticUnit

_CREATE_COLLOCATIONS = """
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
    lemma TEXT,
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
            self._init_schema(self._conn)
        else:
            path = Path(db_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._path = str(path)
            self._conn = None
            with self._file_conn() as conn:
                self._init_schema(conn)

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(_CREATE_COLLOCATIONS)
        conn.execute(_CREATE_VIOLATIONS)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_collocations_due_date ON collocations(due_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_collocations_state ON collocations(state)")
        with suppress(sqlite3.OperationalError):
            conn.execute("ALTER TABLE collocations ADD COLUMN lemma TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_collocations_lemma ON collocations(lemma)")
        conn.commit()

    @contextmanager
    def _file_conn(self):
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def _get_conn(self):
        if self._in_memory:
            yield self._conn
        else:
            with self._file_conn() as conn:
                yield conn

    # ── Write operations ───────────────────────────────────────────────

    def add_collocation(self, unit: SyntacticUnit, language_code: str = "sl") -> None:
        """Insert a new collocation (ignore if already exists)."""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO collocations
                    (text, translation, language_code, word_count, unit_difficulty,
                     source, corpus_frequency, due_date, lemma)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    unit.text,
                    unit.translation,
                    language_code,
                    unit.word_count,
                    unit.difficulty,
                    unit.source,
                    unit.frequency,
                    date.today().isoformat(),
                    unit.lemma,
                ),
            )
            if not self._in_memory:
                conn.commit()
            else:
                self._conn.commit()

    def update_collocation(self, item: SRSItem) -> None:
        """Update FSRS scheduling fields for an existing collocation."""
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE collocations SET
                    stability = ?,
                    fsrs_difficulty = ?,
                    due_date = ?,
                    reps = ?,
                    lapses = ?,
                    state = ?,
                    last_review = ?,
                    updated_at = datetime('now')
                WHERE text = ?
                """,
                (
                    item.stability,
                    item.difficulty,
                    item.due_date.isoformat(),
                    item.reps,
                    item.lapses,
                    item.state.value,
                    item.last_review.isoformat() if item.last_review else None,
                    item.syntactic_unit.text,
                ),
            )
            if self._in_memory:
                self._conn.commit()

    def record_violation(
        self, collocation_text: str, day_number: int, violation_type: str, details: str | None = None
    ) -> None:
        """Record an SRS enforcement violation."""
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO violations (collocation_text, day_number, violation_type, details) VALUES (?, ?, ?, ?)",
                (collocation_text, day_number, violation_type, details),
            )
            if self._in_memory:
                self._conn.commit()

    # ── Read operations ────────────────────────────────────────────────

    def get_collocation(self, text: str) -> SRSItem | None:
        """Retrieve an SRSItem by collocation text."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM collocations WHERE text = ?", (text,)).fetchone()
        if row is None:
            return None
        return self._row_to_item(row)

    def get_collocation_by_lemma(self, lemma: str) -> SRSItem | None:
        """Retrieve an SRSItem by lemma (canonical word form)."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM collocations WHERE lemma = ? LIMIT 1", (lemma,)).fetchone()
        if row is None:
            return None
        return self._row_to_item(row)

    def get_due_collocations(self, as_of: date) -> list[SRSItem]:
        """Return all collocations due for review on or before as_of."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM collocations WHERE due_date <= ? AND state NOT IN ('new', 'suspended')",
                (as_of.isoformat(),),
            ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def get_new_collocations(self, limit: int = 10) -> list[SRSItem]:
        """Return collocations not yet introduced to the learner."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM collocations WHERE state = 'new' LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def get_collocation_by_id(self, row_id: int) -> tuple[int, SRSItem, str] | None:
        """Retrieve a collocation by primary key. Returns (id, SRSItem, language_code) or None."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM collocations WHERE id = ?", (row_id,)).fetchone()
        if row is None:
            return None
        return (row["id"], self._row_to_item(row), row["language_code"])

    def update_collocation_fields(self, row_id: int, *, text: str, translation: str) -> None:
        """Update text and translation for a collocation by id.

        Raises ValueError if the new text collides with an existing row.
        """
        try:
            with self._get_conn() as conn:
                conn.execute(
                    "UPDATE collocations SET text = ?, translation = ?, updated_at = datetime('now') WHERE id = ?",
                    (text, translation, row_id),
                )
                if self._in_memory:
                    self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"text already exists: {text!r}") from exc

    def delete_collocation(self, row_id: int) -> None:
        """Delete a collocation and its associated violations."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT text FROM collocations WHERE id = ?", (row_id,)).fetchone()
            if row is not None:
                conn.execute("DELETE FROM violations WHERE collocation_text = ?", (row["text"],))
                conn.execute("DELETE FROM collocations WHERE id = ?", (row_id,))
                if self._in_memory:
                    self._conn.commit()

    def delete_collocations(self, row_ids: list[int]) -> int:
        """Bulk delete collocations by id. Returns number of rows deleted."""
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
            if self._in_memory:
                self._conn.commit()
        return len(texts)

    def reset_collocation(self, row_id: int) -> None:
        """Reset FSRS scheduling fields to initial values."""
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE collocations SET
                    state = 'new', stability = 1.0, fsrs_difficulty = 5.0,
                    reps = 0, lapses = 0, due_date = ?, last_review = NULL,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (date.today().isoformat(), row_id),
            )
            if self._in_memory:
                self._conn.commit()

    def set_suspended(self, row_id: int, suspended: bool) -> None:
        """Suspend or unsuspend a collocation. Unsuspending resets state to 'new'."""
        new_state = "suspended" if suspended else "new"
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE collocations SET state = ?, updated_at = datetime('now') WHERE id = ?",
                (new_state, row_id),
            )
            if self._in_memory:
                self._conn.commit()

    def list_collocations(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        state: SRSState | None = None,
        order_by: str = "text",
        order_dir: str = "asc",
    ) -> tuple[list[tuple[int, SRSItem, str]], int]:
        """Paginated browse for the admin UI. Returns (rows, total_count).
        Each row is (id, SRSItem, language_code).
        """
        _VALID_ORDER_BY = {
            "text",
            "translation",
            "state",
            "due_date",
            "fsrs_difficulty",
            "reps",
            "lapses",
            "last_review",
        }
        _VALID_ORDER_DIR = {"asc", "desc"}
        if order_by not in _VALID_ORDER_BY:
            raise ValueError(f"Invalid order_by: {order_by!r}")
        if order_dir not in _VALID_ORDER_DIR:
            raise ValueError(f"Invalid order_dir: {order_dir!r}")

        conditions: list[str] = []
        params: list = []

        if search:
            conditions.append("(text LIKE ? OR translation LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        if state is not None:
            conditions.append("state = ?")
            params.append(state.value)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        base_sql = f"FROM collocations {where}"

        with self._get_conn() as conn:
            total = conn.execute(f"SELECT COUNT(*) {base_sql}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT * {base_sql} ORDER BY {order_by} {order_dir} LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()

        result = []
        for row in rows:
            item = self._row_to_item(row)
            result.append((row["id"], item, row["language_code"]))
        return result, total

    def get_violations(self, collocation_text: str) -> list[dict]:
        """Return all violations for a specific collocation."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM violations WHERE collocation_text = ?",
                (collocation_text,),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_collocations(self) -> int:
        with self._get_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM collocations").fetchone()[0]

    def count_due_collocations(self, as_of: date) -> int:
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM collocations WHERE due_date <= ? AND state NOT IN ('new', 'suspended')",
                (as_of.isoformat(),),
            ).fetchone()[0]

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> SRSItem:
        keys = row.keys()
        unit = SyntacticUnit(
            text=row["text"],
            translation=row["translation"],
            word_count=row["word_count"],
            difficulty=row["unit_difficulty"],
            source=row["source"],
            frequency=row["corpus_frequency"],
            lemma=row["lemma"] if "lemma" in keys else None,
        )
        return SRSItem(
            syntactic_unit=unit,
            due_date=date.fromisoformat(row["due_date"]),
            stability=row["stability"],
            difficulty=row["fsrs_difficulty"],
            reps=row["reps"],
            lapses=row["lapses"],
            state=SRSState(row["state"]),
            last_review=date.fromisoformat(row["last_review"]) if row["last_review"] else None,
        )
