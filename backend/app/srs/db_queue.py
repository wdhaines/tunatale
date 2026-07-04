"""Queue-gather mixin for SRSDatabase.

Extracted verbatim from app/srs/database.py (god-module split, stage 5).
Queue gather queries (due/new/learning pools, graded-today listers) and
the daily unbury sweep. Anki-parity danger zone — get_new_items carries
the Layer 25/65 ORDER BY + Phase-3 introduction gate; see
.claude/rules/anki-queue-parity.md before changing anything here.
"""

from datetime import date, datetime, time

from app.models.srs_item import Direction, SRSItem
from app.srs.db_base import (
    _LEARNING_STATES,
    _NON_REVIEWABLE_STATES,
    _anki_day_bounds_utc,
)


class DbQueueMixin:
    """Queue gather + unbury sweep. Mixed into SRSDatabase; relies on SRSDatabaseBase infra."""

    def get_due_collocations(
        self,
        as_of: date,
        direction: Direction = Direction.RECOGNITION,
    ) -> list[SRSItem]:
        """Return all collocations whose `direction` is due on or before `as_of`."""
        placeholders = ",".join("?" * len(_NON_REVIEWABLE_STATES))
        end_of_day = datetime.combine(as_of, time.max).isoformat()
        with self._get_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT c.* FROM collocations c
                JOIN collocation_directions d ON d.collocation_id = c.id
                WHERE d.direction = ?
                  AND d.due_at <= ?
                  AND d.state NOT IN ({placeholders})
                ORDER BY d.due_at ASC, d.stability ASC NULLS LAST, d.anki_card_id ASC NULLS LAST, c.id ASC
                """,
                (direction.value, end_of_day, *_NON_REVIEWABLE_STATES),
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
        end_of_day = datetime.combine(as_of, time.max).isoformat()
        with self._get_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT c.* FROM collocations c
                JOIN collocation_directions d ON d.collocation_id = c.id
                WHERE d.direction = ?
                  AND d.due_at <= ?
                  AND d.state NOT IN ({placeholders})
                ORDER BY d.due_at ASC, d.stability ASC NULLS LAST, d.anki_card_id ASC NULLS LAST, c.id ASC
                """,
                (direction.value, end_of_day, *_NON_REVIEWABLE_STATES),
            ).fetchall()
            return [(r["id"], self._row_to_item(conn, r), r["language_code"]) for r in rows]

    def get_learning_items(
        self,
        direction: Direction = Direction.RECOGNITION,
    ) -> list[tuple[int, SRSItem, str]]:
        """Return all rows in LEARNING/RELEARNING state for the given direction.

        Unlike get_due_items, this does NOT filter by due_date — Anki's queue=1
        dispatcher operates on per-card due_at (sub-day) and surfaces every
        learning card regardless of which calendar day its due_date lands on.
        Important when the FSRS engine schedules a 10-min step that crosses UTC
        midnight: due_date jumps to tomorrow but the user is still on today.
        """
        placeholders = ",".join("?" * len(_LEARNING_STATES))
        with self._get_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT c.* FROM collocations c
                JOIN collocation_directions d ON d.collocation_id = c.id
                WHERE d.direction = ?
                  AND d.state IN ({placeholders})
                ORDER BY d.due_at ASC NULLS LAST, d.anki_due ASC NULLS LAST,
                         d.stability ASC NULLS LAST, d.anki_card_id ASC NULLS LAST, c.id ASC
                """,
                (direction.value, *_LEARNING_STATES),
            ).fetchall()
            return [(r["id"], self._row_to_item(conn, r), r["language_code"]) for r in rows]

    def get_new_items(
        self,
        limit: int = 10,
        direction: Direction = Direction.RECOGNITION,
    ) -> list[tuple[int, SRSItem, str]]:
        """Return new-state cards in Anki-parity order under HighestPosition gather.

        Sort order mirrors Anki's deck setting "New card gather order: Descending
        position" (`NewCardGatherPriority::HighestPosition`, emits `due DESC, ord ASC`
        in `rslib/src/storage/card/mod.rs:923`):

        1. `d.anki_due DESC NULLS FIRST` — unsynced rows (anki_due NULL) sit above
           every synced row so /listen auto-adds surface immediately, before they're
           pushed to Anki. After `sync_create_new` allocates `MAX(due)+1` per Phase C,
           they re-anchor at the top of the synced pool with the highest anki_due.
        2. `c.created_at DESC NULLS LAST` — within the unsynced batch, newer wins.
        3. `d.anki_card_id ASC NULLS LAST`, `c.id ASC` — deterministic tiebreakers.

        Layer 25 (this commit) replaces Layer 24's `created_at DESC` lead key with
        `anki_due DESC` so both apps order the synced pool identically while still
        keeping fresh TT-only rows up front. See `.claude/rules/anki-queue-parity.md`.
        """
        # Phase 3 introduction gate (TT-only): a PRODUCTION new card is not
        # introducible until its recognition sibling has graduated past the
        # learning arc (recognition state not in new/learning/relearning). This
        # makes TT introduce recognition before production — which is what Anki
        # does too: Anki is direction-agnostic and orders new cards by deck
        # position, and `create_note` places the recognition card (ord 0) at a
        # lower position than production (ord 1), so recognition surfaces first
        # (empirically 604/36 across the user's paired notes — the prior
        # "production-first" parity assumption was wrong). A cloze note has no
        # recognition direction, so NOT EXISTS is true and it stays introducible.
        # The recognition direction is never gated. See
        # ~/.claude/plans/word-learning-state-machine.md Phase 3 and
        # docs/anki-parity-layers.md.
        gate = (
            """
                  AND NOT EXISTS (
                    SELECT 1 FROM collocation_directions r
                    WHERE r.collocation_id = c.id
                      AND r.direction = 'recognition'
                      AND r.state IN ('new', 'learning', 'relearning')
                  )"""
            if direction == Direction.PRODUCTION
            else ""
        )
        with self._get_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT c.* FROM collocations c
                JOIN collocation_directions d ON d.collocation_id = c.id
                WHERE d.direction = ? AND d.state = 'new'{gate}
                 ORDER BY d.anki_due DESC NULLS FIRST,
                          c.created_at DESC NULLS LAST,
                          d.anki_card_id ASC NULLS LAST,
                          c.id ASC
                 LIMIT ?
                """,
                (direction.value, limit),
            ).fetchall()
            return [(r["id"], self._row_to_item(conn, r), r["language_code"]) for r in rows]

    def list_anki_cards_graded_today(self, today: date) -> list[tuple[int, str]]:
        """Return (anki_card_id, state) for every direction with last_review today.

        Used by sync_push (Layer 47) to backfill sibling-bury writes into Anki.
        Returns directions regardless of dirty_fsrs — covers cases where a
        previous sync_push cleaned the direction without firing bury.

        Filter mirrors ``list_collocations_reviewed_today``: date-aware on
        local-day bounds, tolerant of both full-ISO and legacy date-only
        timestamps.
        """
        start_iso, end_iso = _anki_day_bounds_utc(today)
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT anki_card_id, state FROM collocation_directions
                WHERE anki_card_id IS NOT NULL
                  AND last_review IS NOT NULL
                  AND ((length(last_review) > 10 AND last_review >= ? AND last_review < ?)
                       OR (length(last_review) = 10 AND last_review = ?))
                """,
                (start_iso, end_iso, today.isoformat()),
            ).fetchall()
            return [(int(r[0]), r[1]) for r in rows]

    def list_collocations_reviewed_today(self, today: date) -> set[int]:
        """Return set of collocation IDs reviewed during the local day `today`.

        last_review is stored as a tz-aware UTC ISO datetime by FSRS write
        paths, but legacy migrations preserved date-only strings ('YYYY-MM-DD')
        from the pre-direction schema. We bucket by the *local* day:

        - Datetimes: range-compare against UTC bounds of the local day.
          (`date(last_review)` returns the UTC date, which mis-buckets reviews
          near midnight whenever local and UTC dates differ — e.g., 23:30 PDT =
          06:30 UTC next day.)
        - Legacy date-only: direct equality with the local-day ISO date.
        """
        start_iso, end_iso = _anki_day_bounds_utc(today)
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT collocation_id FROM collocation_directions
                WHERE (length(last_review) > 10 AND last_review >= ? AND last_review < ?)
                   OR (length(last_review) = 10 AND last_review = ?)
                """,
                (start_iso, end_iso, today.isoformat()),
            ).fetchall()
            return {r[0] for r in rows}

    def unbury_if_needed(self, today: date) -> int:
        """Anki-parity daily unbury sweep — restores stale sched-buried rows.

        Anki distinguishes two bury kinds: ``queue=-3`` (sched/sibling, auto-
        released at next rollover) and ``queue=-2`` (user/manual, stays buried
        until manually unburied). TT mirrors this via ``bury_kind``:
        only rows where ``bury_kind = 'sched'`` get released here. Manually-
        buried rows (``bury_kind = 'user'``) survive the sweep, matching
        Anki's ``unbury_if_needed`` behavior in ``rslib/.../queue/builder/``.

        Tracked via ``anki_state_cache['last_unbury_day']``. Idempotent within a
        local day — subsequent calls today return 0 without touching anything,
        which is important because sync_pull within the same day may land new
        ``state='buried'`` rows for today's sibling-buries that must stick.

        Returns the number of rows unburied.
        """
        cached = self.get_anki_state_cache("last_unbury_day")
        today_iso = today.isoformat()
        if cached and cached[0] == today_iso:
            return 0
        with self._get_conn() as conn:
            cursor = conn.execute(
                # Layer 35: filter on bury_kind='sched' so user buries (queue=-2) survive.
                """
                UPDATE collocation_directions
                SET state = CASE WHEN reps > 0 THEN 'review' ELSE 'new' END,
                    bury_kind = NULL
                WHERE state = 'buried' AND bury_kind = 'sched'
                """
            )
            rowcount = cursor.rowcount
            self._commit(conn)
        self.set_anki_state_cache("last_unbury_day", today_iso)
        return rowcount
