"""SQLite repository for SRS collocations and violations.

Supports ":memory:" for in-memory test databases.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
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
                     source, corpus_frequency, due_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )
            if not self._in_memory:
                conn.commit()
            elif self._in_memory:
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

    def get_due_collocations(self, as_of: date) -> list[SRSItem]:
        """Return all collocations due for review on or before as_of."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM collocations WHERE due_date <= ? AND state != 'new'",
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
                "SELECT COUNT(*) FROM collocations WHERE due_date <= ? AND state != 'new'",
                (as_of.isoformat(),),
            ).fetchone()[0]

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> SRSItem:
        unit = SyntacticUnit(
            text=row["text"],
            translation=row["translation"],
            word_count=row["word_count"],
            difficulty=row["unit_difficulty"],
            source=row["source"],
            frequency=row["corpus_frequency"],
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
