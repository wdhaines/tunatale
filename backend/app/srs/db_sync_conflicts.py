"""Sync-conflict audit-log mixin for SRSDatabase.

Extracted verbatim from app/srs/database.py (god-module split, stage 4).
Append/list for the sync_conflicts table written during Anki merges.
"""


class DbSyncConflictsMixin:
    """sync_conflicts accessors. Mixed into SRSDatabase."""

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
