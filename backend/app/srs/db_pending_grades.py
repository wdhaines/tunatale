"""Pending-listen-grades mixin for SRSDatabase.

TT-only state: provisional grades staged by a listen, not yet applied to FSRS
or sync. Not involved in sync, FSRS, or queue assembly.
"""

from datetime import UTC, datetime


class DbPendingGradesMixin:
    """pending_listen_grades accessors. Mixed into SRSDatabase; relies on SRSDatabaseBase infra."""

    def stage_pending_grade(
        self,
        lesson_id: str,
        collocation_id: int,
        direction: str,
        rating: str,
        grade_class: str | None = None,
    ) -> None:
        """INSERT or UPSERT one pending grade row per (collocation_id, direction).

        Rating: "again"|"hard"|"good"|"easy".
        grade_class: "due"|"ahead"|"learning" at stage time (nullable).
        """
        now = datetime.now(UTC).isoformat()
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO pending_listen_grades
                   (lesson_id, collocation_id, direction, rating, grade_class, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(collocation_id, direction) DO UPDATE SET
                       lesson_id = excluded.lesson_id,
                       rating = excluded.rating,
                       grade_class = excluded.grade_class,
                       created_at = excluded.created_at""",
                (lesson_id, collocation_id, direction, rating, grade_class, now),
            )
            self._commit(conn)

    def get_pending_grades(self, lesson_id: str) -> list[dict]:
        """Return all pending grade rows for a lesson, newest first."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT id, lesson_id, collocation_id, direction, rating, grade_class, created_at
                   FROM pending_listen_grades
                   WHERE lesson_id = ?
                   ORDER BY created_at DESC""",
                (lesson_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_pending_grade(self, collocation_id: int, direction: str) -> dict | None:
        """Return a single pending grade row, or None."""
        with self._get_conn() as conn:
            row = conn.execute(
                """SELECT id, lesson_id, collocation_id, direction, rating, grade_class, created_at
                   FROM pending_listen_grades
                   WHERE collocation_id = ? AND direction = ?""",
                (collocation_id, direction),
            ).fetchone()
        return dict(row) if row else None

    def clear_pending_grade(self, collocation_id: int, direction: str) -> None:
        """Delete a pending grade row."""
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM pending_listen_grades WHERE collocation_id = ? AND direction = ?",
                (collocation_id, direction),
            )
            self._commit(conn)

    def clear_pending_grade_by_guid(self, guid: str, direction: str) -> None:
        """Delete a pending grade row addressed by collocation guid.

        The sync path holds guids, not row ids. Resolving the id there would
        mean a ``get_collocation_id_by_guid`` call plus a None-guard that cannot
        fire (the guid came from a matched note) — an untestable branch. Doing
        the join in SQL makes an unmatched guid a no-op delete instead.
        """
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM pending_listen_grades WHERE direction = ? "
                "AND collocation_id IN (SELECT id FROM collocations WHERE guid = ?)",
                (direction, guid),
            )
            self._commit(conn)

    def clear_pending_grades_for_lesson(self, lesson_id: str) -> None:
        """Drop every pending row this lesson owns.

        Called at the top of a listen so the listen's own staging pass is the
        lesson's current assessment rather than an addition to the last one —
        a row the user just skipped must not survive from the previous listen.
        Lesson-scoped on purpose: another lesson's rows are not this listen's to
        discard.
        """
        with self._get_conn() as conn:
            conn.execute("DELETE FROM pending_listen_grades WHERE lesson_id = ?", (lesson_id,))
            self._commit(conn)

    def count_pending_grades(self, lesson_id: str) -> int:
        """Return the number of pending grade rows for a lesson."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM pending_listen_grades WHERE lesson_id = ?",
                (lesson_id,),
            ).fetchone()
        return row[0]
