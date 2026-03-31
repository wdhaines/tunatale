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
    created_at TEXT DEFAULT (datetime('now'))
)
"""


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
            rows = conn.execute("SELECT id, data_json FROM curricula ORDER BY created_at DESC").fetchall()
        result = []
        for row in rows:
            c = Curriculum.from_json(row["data_json"])
            result.append({"id": row["id"], "topic": c.topic})
        return result

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

    # ── Audio files ───────────────────────────────────────────────────────

    def save_audio_file(self, audio_id: str, lesson_id: str, file_path: str) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO audio_files (id, lesson_id, file_path) VALUES (?, ?, ?)",
                (audio_id, lesson_id, file_path),
            )
            if self._in_memory:
                conn.commit()

    def get_audio_file(self, audio_id: str) -> str | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT file_path FROM audio_files WHERE id = ?", (audio_id,)).fetchone()
        if row is None:
            return None
        return row["file_path"]
