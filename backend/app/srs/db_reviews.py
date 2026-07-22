"""Lesson-reviews mixin for SRSDatabase.

TT-only state: per-lesson 'check your work reviewed' tracking. Not involved
in sync, FSRS, or queue assembly.
"""

from datetime import UTC, datetime


class DbReviewsMixin:
    """lesson_reviews accessors. Mixed into SRSDatabase; relies on SRSDatabaseBase infra."""

    def record_review(self, lesson_id: str) -> None:
        """Append one row to lesson_reviews with the current UTC timestamp."""
        reviewed_at = datetime.now(UTC).isoformat()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO lesson_reviews (lesson_id, reviewed_at) VALUES (?, ?)",
                (lesson_id, reviewed_at),
            )
            self._commit(conn)

    def latest_review_at(self, lesson_id: str) -> str | None:
        """Most recent reviewed_at ISO timestamp, or None if never reviewed."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT MAX(reviewed_at) AS latest FROM lesson_reviews WHERE lesson_id = ?",
                (lesson_id,),
            ).fetchone()
        return row["latest"]
