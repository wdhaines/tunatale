"""Lesson-listens mixin for SRSDatabase.

TT-only state: per-lesson 'listened' tracking. Not involved in sync,
FSRS, or queue assembly.
"""

from datetime import UTC, datetime


class DbListensMixin:
    """lesson_listens accessors. Mixed into SRSDatabase; relies on SRSDatabaseBase infra."""

    def record_listen(self, lesson_id: str, source: str = "listen") -> None:
        """Append one row to lesson_listens with the current UTC timestamp."""
        listened_at = datetime.now(UTC).isoformat()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO lesson_listens (lesson_id, listened_at, source) VALUES (?, ?, ?)",
                (lesson_id, listened_at, source),
            )
            self._commit(conn)

    def has_listen(self, lesson_id: str) -> bool:
        """Return True if at least one listen exists for the given lesson."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM lesson_listens WHERE lesson_id = ? LIMIT 1",
                (lesson_id,),
            ).fetchone()
        return row is not None

    def count_listens(self, lesson_id: str) -> int:
        """Return the number of listens recorded for the given lesson."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM lesson_listens WHERE lesson_id = ?",
                (lesson_id,),
            ).fetchone()
        return row[0]

    def get_listened_lessons(self) -> list[dict]:
        """Return one dict per distinct lesson_id, sorted by last_listened_at DESC.

        Each dict: {"lesson_id": str, "listen_count": int, "last_listened_at": str}.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT lesson_id,
                       COUNT(*) AS listen_count,
                       MAX(listened_at) AS last_listened_at
                FROM lesson_listens
                GROUP BY lesson_id
                ORDER BY last_listened_at DESC, lesson_id
                """
            ).fetchall()
        return [
            {"lesson_id": r["lesson_id"], "listen_count": r["listen_count"], "last_listened_at": r["last_listened_at"]}
            for r in rows
        ]

    def latest_listen_at(self, lesson_id: str) -> str | None:
        """Most recent listened_at ISO timestamp, or None if never listened."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT MAX(listened_at) AS latest FROM lesson_listens WHERE lesson_id = ?",
                (lesson_id,),
            ).fetchone()
        return row["latest"]
