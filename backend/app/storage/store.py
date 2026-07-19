"""SQLite repository for curricula, lessons, and audio file mappings.

Supports ":memory:" for in-memory test databases.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.models.curriculum import Curriculum
from app.models.lesson import Lesson

_CREATE_CURRICULA = """
CREATE TABLE IF NOT EXISTS curricula (
    id TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
)
"""

_CREATE_LESSONS = """
CREATE TABLE IF NOT EXISTS lessons (
    id TEXT PRIMARY KEY,
    curriculum_id TEXT NOT NULL,
    day INTEGER NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
)
"""

_CREATE_AUDIO_FILES = """
CREATE TABLE IF NOT EXISTS audio_files (
    id TEXT PRIMARY KEY,
    lesson_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    section_index INTEGER,
    section_type TEXT,
    created_at TEXT DEFAULT (datetime('now'))
)
"""

# Columns added after initial schema — applied via migration in _init_schema
_AUDIO_FILES_MIGRATION_COLUMNS = [
    ("section_index", "INTEGER"),
    ("section_type", "TEXT"),
    ("cues_json", "TEXT"),
]


class ContentStore:
    """SQLite-backed store for curricula, lessons, and audio files.

    Use `:memory:` as db_path for in-memory test databases.
    """

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
        conn.execute(_CREATE_CURRICULA)
        conn.execute(_CREATE_LESSONS)
        conn.execute(_CREATE_AUDIO_FILES)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lessons_curriculum_id ON lessons(curriculum_id)")
        self._migrate_audio_files(conn)
        conn.commit()

    def _migrate_audio_files(self, conn: sqlite3.Connection) -> None:
        """Add any missing columns to audio_files (idempotent)."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(audio_files)").fetchall()}
        for col_name, col_type in _AUDIO_FILES_MIGRATION_COLUMNS:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE audio_files ADD COLUMN {col_name} {col_type}")

    @contextmanager
    def _file_conn(self):
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
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

    def close(self) -> None:
        if self._in_memory and self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> ContentStore:
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ── Curricula ─────────────────────────────────────────────────────────

    def save_curriculum(self, curriculum_id: str, curriculum: Curriculum) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO curricula (id, data_json) VALUES (?, ?)",
                (curriculum_id, curriculum.to_json()),
            )
            if self._in_memory:
                conn.commit()

    def get_curriculum(self, curriculum_id: str) -> Curriculum | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT data_json FROM curricula WHERE id = ?", (curriculum_id,)).fetchone()
        if row is None:
            return None
        return Curriculum.from_json(row["data_json"])

    def list_curricula(self) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT id, data_json, created_at FROM curricula ORDER BY created_at DESC").fetchall()
        result = []
        for row in rows:
            c = Curriculum.from_json(row["data_json"])
            result.append({"id": row["id"], "topic": c.topic, "created_at": row["created_at"]})
        return result

    def delete_curriculum(self, curriculum_id: str) -> bool:
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM audio_files WHERE lesson_id IN (SELECT id FROM lessons WHERE curriculum_id = ?)",
                (curriculum_id,),
            )
            conn.execute("DELETE FROM lessons WHERE curriculum_id = ?", (curriculum_id,))
            deleted = conn.execute("DELETE FROM curricula WHERE id = ?", (curriculum_id,)).rowcount > 0
            conn.commit()
        return deleted

    # ── Lessons ───────────────────────────────────────────────────────────

    def save_lesson(self, lesson_id: str, curriculum_id: str, day: int, lesson: Lesson) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO lessons (id, curriculum_id, day, data_json) VALUES (?, ?, ?, ?)",
                (lesson_id, curriculum_id, day, lesson.to_json()),
            )
            if self._in_memory:
                conn.commit()

    def get_lesson(self, lesson_id: str) -> Lesson | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT data_json FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
        if row is None:
            return None
        return Lesson.from_json(row["data_json"])

    def get_lesson_row(self, lesson_id: str) -> dict | None:
        """Return the raw lesson row as a dict (id, curriculum_id, day, data_json), or None."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT id, curriculum_id, day, data_json FROM lessons WHERE id = ?",
                (lesson_id,),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def get_latest_lesson_by_day(self, curriculum_id: str, day: int) -> tuple[str, Lesson] | None:
        """Return the most recent (lesson_id, Lesson) for a given curriculum day, or None."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT id, data_json FROM lessons"
                " WHERE curriculum_id = ? AND day = ?"
                " ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (curriculum_id, day),
            ).fetchone()
        if row is None:
            return None
        return row["id"], Lesson.from_json(row["data_json"])

    def get_lesson_days(self, curriculum_id: str) -> list[dict]:
        """Return [{day, lesson_id}, ...] for each day with a lesson (latest per day)."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT l.day, l.id AS lesson_id"
                " FROM lessons l"
                " INNER JOIN ("
                "   SELECT day, MAX(rowid) AS max_rowid"
                "   FROM lessons WHERE curriculum_id = ?"
                "   GROUP BY day"
                " ) latest ON l.rowid = latest.max_rowid"
                " ORDER BY l.day ASC",
                (curriculum_id,),
            ).fetchall()
        return [{"day": row["day"], "lesson_id": row["lesson_id"]} for row in rows]

    def list_lessons(self) -> list[tuple[str, str, int, Lesson]]:
        """Every lesson as ``(lesson_id, curriculum_id, day, Lesson)``, oldest first.

        Used by one-shot migrations that need to walk and rewrite all lessons.
        """
        with self._get_conn() as conn:
            rows = conn.execute("SELECT id, curriculum_id, day, data_json FROM lessons ORDER BY created_at").fetchall()
        return [(r["id"], r["curriculum_id"], r["day"], Lesson.from_json(r["data_json"])) for r in rows]

    def get_all_token_glosses(self) -> dict[str, str]:
        """Merge token_glosses from all stored lessons into a single dict.

        Later lessons (higher rowid) win on duplicate lemmas.
        """
        with self._get_conn() as conn:
            rows = conn.execute("SELECT data_json FROM lessons ORDER BY rowid ASC").fetchall()
        glosses: dict[str, str] = {}
        for row in rows:
            lesson = Lesson.from_json(row["data_json"])
            glosses.update(lesson.generation_metadata.get("token_glosses", {}))
        return glosses

    # ── Audio files ───────────────────────────────────────────────────────

    def save_audio_file(
        self,
        audio_id: str,
        lesson_id: str,
        file_path: str,
        *,
        section_index: int | None = None,
        section_type: str | None = None,
        cues_json: str | None = None,
    ) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO audio_files (id, lesson_id, file_path, section_index, section_type, cues_json)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (audio_id, lesson_id, file_path, section_index, section_type, cues_json),
            )
            if self._in_memory:
                conn.commit()

    def get_audio_file_row(self, audio_id: str) -> dict | None:
        """Return all fields for an audio_files row, or None if not found."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT id, lesson_id, file_path, section_index, section_type, cues_json FROM audio_files WHERE id = ?",
                (audio_id,),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def list_audio_files_for_lesson(self, lesson_id: str) -> list[dict]:
        """Return all audio file rows for a lesson.

        Ordering: full-lesson row first (section_index IS NULL), then sections
        in ascending section_index order.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, lesson_id, file_path, section_index, section_type, cues_json FROM audio_files"
                " WHERE lesson_id = ?"
                " ORDER BY section_index IS NOT NULL, section_index ASC",
                (lesson_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_audio_files_for_lesson(self, lesson_id: str) -> None:
        """Delete all audio file rows for a lesson so re-render replaces, not appends."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM audio_files WHERE lesson_id = ?", (lesson_id,))
            conn.commit()

    def delete_lessons_for_day(self, curriculum_id: str, day: int) -> None:
        """Delete all lesson rows (and their audio) for a given curriculum day.

        There can be multiple lesson versions per day; every one is removed.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id FROM lessons WHERE curriculum_id = ? AND day = ?",
                (curriculum_id, day),
            ).fetchall()
            lesson_ids = [row["id"] for row in rows]
            for lesson_id in lesson_ids:
                conn.execute("DELETE FROM audio_files WHERE lesson_id = ?", (lesson_id,))
            conn.execute("DELETE FROM lessons WHERE curriculum_id = ? AND day = ?", (curriculum_id, day))
            conn.commit()
