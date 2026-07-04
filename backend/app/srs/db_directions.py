"""Direction/FSRS state-machine mixin for SRSDatabase.

Extracted verbatim from app/srs/database.py (god-module split, stage 5).
Per-direction state transitions: FSRS state persistence, reset, known
mark/restore, promote-to-learning, suspend. Anki-parity danger zone —
see .claude/rules/anki-queue-parity.md before changing anything here.
"""

from datetime import UTC, date, datetime

from app.anki.rollover import due_at_rollover_utc
from app.models.srs_item import Direction, DirectionState, SRSItem, SRSState
from app.srs.db_base import _NEW_RESET_SET


class DbDirectionsMixin:
    """Direction/FSRS state machine. Mixed into SRSDatabase; relies on SRSDatabaseBase infra."""

    # ── Write operations ───────────────────────────────────────────────

    def update_direction(
        self,
        guid: str,
        direction: Direction,
        state: DirectionState,
    ) -> None:
        """Persist the FSRS state for one direction of a collocation."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT id FROM collocations WHERE guid = ?", (guid,)).fetchone()
            if row is None:
                return
            conn.execute(
                """
                UPDATE collocation_directions SET
                    stability = ?,
                    fsrs_difficulty = ?,
                    due_at = ?,
                    reps = ?,
                    lapses = ?,
                    state = ?,
                    last_review = ?,
                    last_review_time_ms = ?,
                    anki_card_id = ?,
                    anki_card_mod = ?,
                    anki_due = ?,
                    dirty_fsrs = ?,
                    last_synced_at = ?,
                    last_rating = ?,
                    left = ?,
                    prior_state = ?,
                    prior_left = ?,
                    prior_stability = ?,
                    introduced_at = ?,
                    bury_kind = ?,
                    fsrs_force_next = ?
                WHERE collocation_id = ? AND direction = ?
                """,
                (
                    state.stability,
                    state.difficulty,
                    state.due_at.isoformat(),
                    state.reps,
                    state.lapses,
                    state.state.value,
                    state.last_review.isoformat() if state.last_review else None,
                    state.last_review_time_ms,
                    state.anki_card_id,
                    state.anki_card_mod,
                    state.anki_due,
                    1 if state.dirty_fsrs else 0,
                    state.last_synced_at,
                    state.last_rating,
                    state.left,
                    state.prior_state.value if state.prior_state is not None else None,
                    state.prior_left,
                    state.prior_stability,
                    state.introduced_at.isoformat() if state.introduced_at else None,
                    state.bury_kind,
                    1 if state.fsrs_force_next else 0,
                    row["id"],
                    direction.value,
                ),
            )
            self._commit(conn)

    def update_collocation(self, item: SRSItem) -> None:
        """Persist the first available direction of `item`.

        Cloze items only have PRODUCTION; vocab items have RECOGNITION
        as the primary direction used by back-compat callers.
        """
        if item.guid is None:
            # Fall back to looking up the guid by text for legacy flows.
            with self._get_conn() as conn:
                row = conn.execute(
                    "SELECT guid FROM collocations WHERE text = ?",
                    (item.syntactic_unit.text,),
                ).fetchone()
                if row is None:
                    return
                guid = row["guid"]
        else:
            guid = item.guid
        direction = Direction.RECOGNITION if Direction.RECOGNITION in item.directions else Direction.PRODUCTION
        self.update_direction(guid, direction, item.directions[direction])

    def update_direction_by_id(self, row_id: int, direction: Direction, state: DirectionState) -> None:
        """Persist direction state for a collocation identified by row id."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT guid FROM collocations WHERE id = ?", (row_id,)).fetchone()
            if row is None:
                return
        self.update_direction(row["guid"], direction, state)

    def reset_collocation(self, row_id: int, direction: Direction | None = None) -> None:
        """Reset FSRS scheduling for one or both directions of a collocation.

        ``dirty_fsrs = 1`` so the reset propagates to Anki on the next
        ``sync_push`` (which forgets the card). Writing ``dirty_fsrs = 0`` left
        the reset TT-local: Anki kept the graduated review while TT showed a
        fresh NEW card — a permanent new-vs-review badge divergence that the
        next pull silently clobbered (2026-06-04). Mirrors
        ``set_state_by_id(NEW)``, which already marks dirty.
        """
        today_due_at = due_at_rollover_utc(date.today()).isoformat()
        if direction is None:
            sql = f"UPDATE collocation_directions SET {_NEW_RESET_SET}, dirty_fsrs = 1 WHERE collocation_id = ?"
            params = (today_due_at, row_id)
        else:
            sql = (
                f"UPDATE collocation_directions SET {_NEW_RESET_SET}, dirty_fsrs = 1 "
                "WHERE collocation_id = ? AND direction = ?"
            )
            params = (today_due_at, row_id, direction.value)
        with self._get_conn() as conn:
            conn.execute(sql, params)
            conn.execute(
                "UPDATE collocations SET updated_at = datetime('now') WHERE id = ?",
                (row_id,),
            )
            self._commit(conn)

    def set_state_by_id(
        self,
        row_id: int,
        state: SRSState,
        direction: Direction | None = None,
        *,
        mark_dirty: bool = True,
    ) -> None:
        """Set the state of a collocation directly, bypassing FSRS scheduling.

        For non-NEW states this is label-only: ``stability`` / ``difficulty`` /
        ``due_at`` / ``reps`` are preserved, so cycling a card to ``review`` /
        ``known`` restores its real schedule rather than fabricating one. When the
        target state enters the review/learning flow (review / learning / relearning
        / known) and the card was never introduced, ``introduced_at`` is stamped
        (one-shot via ``COALESCE``, Layer 26) so ``count_new_introduced_today`` stays
        consistent — a card leaving NEW must decrement the new quota. ``suspended``
        is *not* an introduction, so it does not stamp.

        ``state == NEW`` is a **full reset** (mirrors ``reset_collocation``): a NEW
        card has no schedule, so leaving a graduated ``due_at`` / ``last_review`` /
        ``reps`` / ``stability`` stamped makes the transcript render it red (mastery
        keys off ``state == NEW``) yet read *not* due (``is_due`` keys off
        ``due_at``) — the plain click then no-ops ("stuck reset"). Resetting the
        schedule makes the card due today and re-learnable. NEW also clears
        ``introduced_at`` / ``prior_state`` so ``count_new_introduced_today`` isn't
        inflated.
        """
        dirty_clause = ", dirty_fsrs = 1" if mark_dirty else ""
        if state == SRSState.NEW:
            today_due_at = due_at_rollover_utc(date.today()).isoformat()
            set_clause = f"{_NEW_RESET_SET}{dirty_clause}, introduced_at = NULL, prior_state = NULL"
            params_head: tuple[object, ...] = (today_due_at,)
        elif state in (SRSState.LEARNING, SRSState.RELEARNING, SRSState.REVIEW, SRSState.KNOWN):
            # Entering the review/learning flow: stamp introduced_at if unset so the
            # new-introduced quota decrements (COALESCE keeps any prior stamp).
            now_iso = datetime.now(UTC).isoformat()
            set_clause = f"state = ?{dirty_clause}, introduced_at = COALESCE(introduced_at, ?)"
            params_head = (state.value, now_iso)
        else:
            set_clause = f"state = ?{dirty_clause}"
            params_head = (state.value,)
        with self._get_conn() as conn:
            if direction is None:
                conn.execute(
                    f"UPDATE collocation_directions SET {set_clause} WHERE collocation_id = ?",
                    (*params_head, row_id),
                )
            else:
                conn.execute(
                    f"UPDATE collocation_directions SET {set_clause} WHERE collocation_id = ? AND direction = ?",
                    (*params_head, row_id, direction.value),
                )
            conn.execute(
                "UPDATE collocations SET updated_at = datetime('now') WHERE id = ?",
                (row_id,),
            )
            self._commit(conn)

    def mark_known(
        self,
        row_id: int,
        due_at: datetime,
        stability: float,
        direction: Direction | None = None,
    ) -> None:
        """Set state to KNOWN with a far-future due_at and matched stability.

        Sets dirty_fsrs=1 so the direction is picked up by sync_push.
        Stamps introduced_at (COALESCE) if unset, preserving any prior stamp.

        Snapshots the pre-known ``state``/``stability``/``due_at`` into the
        ``known_prior_*`` columns so ``restore_known`` can exactly reverse the
        mark. The CASE guards capture the *old* row values and only on entry
        (``state != 'known'``), so a double-mark keeps the first (real)
        snapshot rather than clobbering it with the inflated KNOWN values.
        SQLite evaluates every SET RHS against the pre-update row, so reading
        the old ``state``/``stability``/``due_at`` in the same statement is safe.

        ``introduced_at`` is COALESCE-stamped here but NOT un-stamped by
        ``restore_known``: a rare new→known→restore path leaves the word
        "introduced". Accepted — restore targets review/known words in practice.
        """
        now_iso = datetime.now(UTC).isoformat()
        due_at_iso = due_at.isoformat()
        snapshot_sql = (
            " known_prior_state = CASE WHEN state != 'known' THEN state ELSE known_prior_state END,"
            " known_prior_stability = CASE WHEN state != 'known' THEN stability ELSE known_prior_stability END,"
            " known_prior_due_at = CASE WHEN state != 'known' THEN due_at ELSE known_prior_due_at END,"
        )
        with self._get_conn() as conn:
            if direction is None:
                conn.execute(
                    "UPDATE collocation_directions SET"
                    f"{snapshot_sql}"
                    " state = 'known', due_at = ?,"
                    " stability = ?, dirty_fsrs = 1,"
                    " introduced_at = COALESCE(introduced_at, ?)"
                    " WHERE collocation_id = ?",
                    (due_at_iso, stability, now_iso, row_id),
                )
            else:
                conn.execute(
                    "UPDATE collocation_directions SET"
                    f"{snapshot_sql}"
                    " state = 'known', due_at = ?,"
                    " stability = ?, dirty_fsrs = 1,"
                    " introduced_at = COALESCE(introduced_at, ?)"
                    " WHERE collocation_id = ? AND direction = ?",
                    (due_at_iso, stability, now_iso, row_id, direction.value),
                )
            conn.execute(
                "UPDATE collocations SET updated_at = datetime('now') WHERE id = ?",
                (row_id,),
            )
            self._commit(conn)

    def restore_known(self, row_id: int, direction: Direction | None = None) -> None:
        """Reverse ``mark_known``: restore the snapshotted pre-known schedule.

        Writes ``known_prior_*`` back to the live ``state``/``stability``/
        ``due_at`` columns, clears the snapshot, and sets ``dirty_fsrs=1`` +
        ``fsrs_force_next=1``. The force flag makes the next sync_push
        force-write the restored stability into Anki's ``cards.data`` — a
        restored card is ``review``, which otherwise has no TT→Anki
        stability-write signal and would be re-clobbered by the next
        take-Anki-verbatim pull. Push runs before pull, so Anki is corrected
        before the pull reads it (mirrors how KNOWN forces via ``state==KNOWN``).

        No-op for any direction without a snapshot (``known_prior_state IS NULL``),
        so calling it on a card that was never marked known leaves it untouched.
        Does NOT un-stamp ``introduced_at`` (see ``mark_known``).
        """
        where_dir = "" if direction is None else " AND direction = ?"
        params: list = [row_id]
        if direction is not None:
            params.append(direction.value)
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE collocation_directions SET"
                " state = known_prior_state,"
                " stability = known_prior_stability,"
                " due_at = known_prior_due_at,"
                " dirty_fsrs = 1,"
                " fsrs_force_next = 1,"
                " known_prior_state = NULL,"
                " known_prior_stability = NULL,"
                " known_prior_due_at = NULL"
                " WHERE collocation_id = ? AND known_prior_state IS NOT NULL" + where_dir,
                params,
            )
            conn.execute(
                "UPDATE collocations SET updated_at = datetime('now') WHERE id = ?",
                (row_id,),
            )
            self._commit(conn)

    def is_known_marked(self, row_id: int) -> bool:
        """True if any direction of this collocation has a known snapshot pending.

        A snapshot is present iff the word is currently marked known (and thus
        reversible via ``restore_known``). Drives the transcript's
        ``known_marked`` flag and the popover's Mark/Un-mark toggle.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT EXISTS(SELECT 1 FROM collocation_directions"
                " WHERE collocation_id = ? AND known_prior_state IS NOT NULL)",
                (row_id,),
            ).fetchone()
        return bool(row[0])

    def promote_to_learning(
        self,
        row_id: int,
        direction: Direction | None = None,
    ) -> None:
        """Set state to LEARNING with today's due_at and a fresh last_review.

        The caller is responsible for ensuring the collocation exists.

        Note: `left` is left as NULL, so sync_push routes to
        set_due_date (the new/review branch at sync.py:1219), not to
        set_learning_state. Anki receives "due today" without learning-step
        metadata — TunaTale shows LEARNING, Anki treats it as effectively new.
        This matches the "no FSRS grade" intent but creates a silent asymmetry
        between TT and Anki views.
        """
        today_due_at = due_at_rollover_utc(date.today()).isoformat()
        now = datetime.now(UTC)
        now_ms = int(now.timestamp() * 1000)
        now_iso = now.isoformat()
        with self._get_conn() as conn:
            if direction is None:
                conn.execute(
                    "UPDATE collocation_directions SET state = 'learning',"
                    " due_at = ?, last_review = ?, last_review_time_ms = ?,"
                    " dirty_fsrs = 1 WHERE collocation_id = ?",
                    (today_due_at, now_iso, now_ms, row_id),
                )
            else:
                conn.execute(
                    "UPDATE collocation_directions SET state = 'learning',"
                    " due_at = ?, last_review = ?, last_review_time_ms = ?,"
                    " dirty_fsrs = 1 WHERE collocation_id = ? AND direction = ?",
                    (today_due_at, now_iso, now_ms, row_id, direction.value),
                )
            conn.execute(
                "UPDATE collocations SET updated_at = datetime('now') WHERE id = ?",
                (row_id,),
            )
            self._commit(conn)
        # Stage 0: write Manual revlog row. Only iterate directions that actually
        # exist: a production-only (cloze) collocation has no recognition row, and
        # tt_revlog's (collocation_id, direction) FK rejects a revlog for a
        # nonexistent direction (would 500 the promote-to-learning request).
        anki_id = None
        if direction is None:
            for d in self._existing_directions(row_id):
                row = self._get_anki_card_id_for_direction(row_id, d)
                self.append_manual_revlog(row_id, d, anki_card_id=row)
        else:
            anki_id = self._get_anki_card_id_for_direction(row_id, direction)
            self.append_manual_revlog(row_id, direction, anki_card_id=anki_id)

    def _existing_directions(self, collocation_id: int) -> list[Direction]:
        """Return the directions with a collocation_directions row, in canonical
        (recognition, production) order. Cloze collocations have production only.
        """
        with self._get_conn() as conn:
            present = {
                r["direction"]
                for r in conn.execute(
                    "SELECT direction FROM collocation_directions WHERE collocation_id = ?",
                    (collocation_id,),
                )
            }
        return [d for d in Direction if d.value in present]

    def _get_anki_card_id_for_direction(self, collocation_id: int, direction: Direction) -> int | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT anki_card_id FROM collocation_directions WHERE collocation_id = ? AND direction = ?",
                (collocation_id, direction.value),
            ).fetchone()
            return row["anki_card_id"] if row else None

    def set_suspended(
        self,
        row_id: int,
        suspended: bool,
        direction: Direction | None = None,
    ) -> None:
        """Suspend or unsuspend a collocation.

        Suspending sets SUSPENDED. Unsuspending restores REVIEW for directions
        with reps>0 and marks dirty_fsrs=1 so the next push syncs to Anki.
        """
        if suspended:
            self.set_state_by_id(row_id, SRSState.SUSPENDED, direction=direction)
            return

        dirs_to_restore = [direction] if direction is not None else list(Direction)
        with self._get_conn() as conn:
            for d in dirs_to_restore:
                row = conn.execute(
                    "SELECT reps FROM collocation_directions WHERE collocation_id = ? AND direction = ?",
                    (row_id, d.value),
                ).fetchone()
                if row is None:
                    continue
                restored = SRSState.REVIEW if row["reps"] > 0 else SRSState.NEW
                conn.execute(
                    "UPDATE collocation_directions SET state = ?, dirty_fsrs = 1"
                    " WHERE collocation_id = ? AND direction = ?",
                    (restored.value, row_id, d.value),
                )
            conn.execute(
                "UPDATE collocations SET updated_at = datetime('now') WHERE id = ?",
                (row_id,),
            )
            self._commit(conn)
