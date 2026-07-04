"""Load-balancer histogram read mixin for SRSDatabase.

Extracted verbatim from app/srs/database.py (god-module split, stage 4).
Read-only feeds for the live FSRS load balancer (Layer 55); the balancer
logic itself lives in app/srs/load_balancer.py, untouched.
"""


class DbHistogramMixin:
    """Load-balancer histogram reads. Mixed into SRSDatabase; relies on SRSDatabaseBase infra."""

    def get_load_balancer_histogram(self, today: int, days: int) -> list[tuple[int, int | None, int]]:
        """Return ``(anki_card_id, anki_note_id, anki_due)`` for every direction
        whose ``anki_due`` falls in the col-day window ``[today, today + days)``.

        Mirrors Anki's ``get_all_cards_due_in_range(today, today + LOAD_BALANCE_DAYS)``
        (load_balancer.rs): NO queue filter, so suspended review cards in range are
        included. Learning/new cards fall out naturally — their ``anki_due`` is NULL
        (unsynced) or a (re)learning timestamp far outside ``[today, today+days)``.
        Unsynced rows (no ``anki_card_id``) are skipped: they aren't Anki cards yet.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT cd.anki_card_id, c.anki_note_id, cd.anki_due
                FROM collocation_directions cd
                JOIN collocations c ON cd.collocation_id = c.id
                WHERE cd.anki_due IS NOT NULL
                  AND cd.anki_due >= ? AND cd.anki_due < ?
                  AND cd.anki_card_id IS NOT NULL
                """,
                (today, today + days),
            ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def get_load_balancer_session_replay(self) -> list[tuple[int, int | None, int]]:
        """Return ``(anki_card_id, anki_note_id, interval)`` for each direction graded
        in TT since the last sync (``dirty_fsrs=1``), using its most recent tt_revlog
        ``interval``.

        These grades moved ``due_at`` but NOT ``anki_due`` (which stays frozen at the
        last sync), so they're absent from the ``anki_due`` histogram and must be
        ``add_card``'d explicitly to mirror Anki's per-answer histogram mutation
        (never-remove). ``interval`` is days-from-grade, which equals days-from-today
        for the common intraday session; a cross-day-unsynced grade is at most ±1 day
        stale and self-heals at the next sync (bounded drift, queue-parity rule 1).
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT cd.anki_card_id, c.anki_note_id, r.interval
                FROM collocation_directions cd
                JOIN collocations c ON cd.collocation_id = c.id
                JOIN tt_revlog r ON r.id = (
                    SELECT MAX(id) FROM tt_revlog
                    WHERE collocation_id = cd.collocation_id AND direction = cd.direction
                )
                WHERE cd.dirty_fsrs = 1 AND cd.anki_card_id IS NOT NULL
                """,
            ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]
