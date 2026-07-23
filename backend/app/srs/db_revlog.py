"""tt_revlog mixin for SRSDatabase.

Extracted verbatim from app/srs/database.py (god-module split, stage 5).
The TT-side revlog: append/ingest helpers, the Stage-3b incremental
FSRS replay (rebuild_from_revlog), and the Layer 60/71 dedupe/anchor
helpers. Anki-parity danger zone — see .claude/rules/anki-queue-parity.md
before changing anything here.
"""

import time as _time
from datetime import UTC, date, datetime

from app.models.srs_item import Direction, DirectionState, RevlogRow, SRSItem
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.rollover import due_at_rollover_utc


class DbRevlogMixin:
    """tt_revlog helpers. Mixed into SRSDatabase; relies on SRSDatabaseBase infra."""

    # ── tt_revlog helpers (Stage 0: event-sync migration) ──────────────

    def has_revision_near(
        self,
        collocation_id: int,
        direction: str,
        timestamp_ms: int,
        button_chosen: int,
        window_ms: int = 5000,
        exclude_id: int | None = None,
        ignore_ids: set[int] | None = None,
    ) -> bool:
        """Return True if a tt_revlog row exists within *window_ms* of *timestamp_ms* with the same *button_chosen*.

        Used at Anki-import time to avoid double-recording the same grade event
        when TT wrote its own row (Stage 0) before the Anki-side copy arrives.

        ``exclude_id`` skips the candidate's own id (the Anki row may already be
        in tt_revlog at its exact id from a prior sync, and ``INSERT OR IGNORE``
        handles PK dupes — that's not a "near match" worth suppressing).

        ``ignore_ids`` removes those tt_revlog rows from the near-match entirely.
        The ingest passes the card's *Anki revlog ids* here so an already-ingested
        Anki row never suppresses a *distinct* Anki grade a few seconds later
        (Layer 60). The guard then only fires against genuine TT-*written* rows —
        whose ids are never in the card's Anki revlog, because ``write_revlog``
        may bump the pushed id off the TT grade time.
        """
        sql = (
            "SELECT 1 FROM tt_revlog WHERE collocation_id = ? AND direction = ? "
            "AND button_chosen = ? AND abs(id - ?) < ?"
        )
        params: list[object] = [collocation_id, direction, button_chosen, timestamp_ms, window_ms]
        if exclude_id is not None:
            sql += " AND id != ?"
            params.append(exclude_id)
        if ignore_ids:
            sql += f" AND id NOT IN ({','.join('?' * len(ignore_ids))})"
            params.extend(ignore_ids)
        sql += " LIMIT 1"
        with self._get_conn() as conn:
            return conn.execute(sql, params).fetchone() is not None

    def get_tt_revlog_ids(self, collocation_id: int, direction: Direction) -> set[int]:
        """Return the set of tt_revlog ids already held for (collocation_id, direction).

        Lets sync_pull's gap-proof ingest reconcile against the card's full Anki
        revlog while skipping a per-row query/write for grades it already holds.
        """
        with self._get_conn() as conn:
            return {
                r[0]
                for r in conn.execute(
                    "SELECT id FROM tt_revlog WHERE collocation_id = ? AND direction = ?",
                    (collocation_id, direction.value),
                )
            }

    def append_revlog(self, row: RevlogRow) -> None:
        """Insert a tt_revlog row (idempotent via INSERT OR IGNORE)."""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO tt_revlog
                    (id, collocation_id, direction, button_chosen, interval,
                     last_interval, factor, taken_millis, review_kind, anki_card_id,
                     budget_neutral)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.id,
                    row.collocation_id,
                    row.direction.value,
                    row.button_chosen,
                    row.interval,
                    row.last_interval,
                    row.factor,
                    row.taken_millis,
                    row.review_kind,
                    row.anki_card_id,
                    int(row.budget_neutral),
                ),
            )
            self._commit(conn)

    def delete_revlog_row(self, revlog_id: int) -> None:
        """Delete a single tt_revlog row by id (grade-undo unwinds its own row)."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM tt_revlog WHERE id = ?", (revlog_id,))
            self._commit(conn)

    def rebuild_from_revlog(
        self,
        collocation_id: int,
        direction: Direction,
        params=None,
        col_crt: int | None = None,
        exclude_review_kinds: frozenset[int] = frozenset({4}),
        anki_card_id: int | None = None,
        starting_state: DirectionState | None = None,
        since_id: int | None = None,
    ) -> DirectionState:
        """Replay tt_revlog rows through FSRS schedule() to derive DirectionState.

        Reads non-excluded revlog rows for ``(collocation_id, direction)`` ordered
        by ``id`` ASC and replays them through ``app.srs.fsrs.schedule``.

        Pass *anki_card_id* to ensure the FSRS interval-fuzz seed matches the
        real Anki card id; omit or pass ``None`` for TT-only directions.

        **Incremental replay (Stage 3b).** By default the walk starts from a fresh
        NEW state over every row. Pass *starting_state* to begin from a stored
        ``DirectionState`` instead, and *since_id* to walk only rows with
        ``id > since_id``. Together these turn the helper into a forward-step from
        the last-synced state over just the new revlog rows — the composition
        invariant ``replay(prefix) ∘ replay(suffix) == replay(all)`` holds because
        ``schedule`` is a pure function of ``(prev_state, rating, timing)``. When
        *starting_state* is given and no rows remain after the filter, it is
        returned unchanged (the "no new grades since last sync" case).

        Returns the replayed ``DirectionState``.  The caller is responsible for
        writing it back (and merging non-FSRS fields).
        """
        from app.srs.fsrs import DEFAULT_FSRS5_PARAMS, Rating, schedule

        if params is None:
            params = DEFAULT_FSRS5_PARAMS

        sql = """
            SELECT id, button_chosen, taken_millis, review_kind, factor
            FROM tt_revlog
            WHERE collocation_id = ? AND direction = ?
        """
        sql_params: list = [collocation_id, direction.value]
        if since_id is not None:
            sql += " AND id > ?"
            sql_params.append(since_id)
        sql += " ORDER BY id ASC"

        with self._get_conn() as conn:
            rows = conn.execute(sql, sql_params).fetchall()
            coll = conn.execute(
                """
                SELECT guid, anki_note_id, text, card_type FROM collocations WHERE id = ?
            """,
                (collocation_id,),
            ).fetchone()

        rows = [r for r in rows if r["review_kind"] not in exclude_review_kinds]

        if not rows:
            if starting_state is not None:
                return starting_state
            return DirectionState(
                direction=direction,
                due_at=due_at_rollover_utc(date.today()),
            )

        guid = coll["guid"] if coll else None
        anki_note_id = coll["anki_note_id"] if coll else None
        card_type = coll["card_type"] or "vocab" if coll else "vocab"

        other_dir = Direction.PRODUCTION if direction == Direction.RECOGNITION else Direction.RECOGNITION
        now_4am = due_at_rollover_utc(date.today())
        # Incremental: forward-step from the stored state. Otherwise: from NEW.
        start_state = (
            starting_state
            if starting_state is not None
            else DirectionState(direction=direction, due_at=now_4am, anki_card_id=anki_card_id)
        )
        other_state = DirectionState(direction=other_dir, due_at=now_4am)
        unit = SyntacticUnit(
            text=coll["text"] if coll else "replay",
            translation="",
            word_count=1,
            difficulty=1,
            source="replay",
            card_type=card_type,
        )
        item = SRSItem(
            syntactic_unit=unit,
            directions={direction: start_state, other_dir: other_state},
            guid=guid or "replay",
            anki_note_id=anki_note_id,
        )

        for row in rows:
            if row["button_chosen"] not in (1, 2, 3, 4):
                continue
            now_dt = datetime.fromtimestamp(row["id"] / 1000, tz=UTC)
            review_date = now_dt.date()
            item = schedule(
                item,
                Rating(row["button_chosen"]),
                review_date=review_date,
                direction=direction,
                params=params,
                time_ms=row["id"],
                now=now_dt,
                col_crt=col_crt,
            )

        return item.directions[direction]

    def get_unpushed_revlog_rows(self, collocation_id: int, direction: Direction, after_id: int) -> list[RevlogRow]:
        """Return tt_revlog rows for (collocation_id, direction) with id > after_id, ordered by id.

        ``after_id`` is typically ``max_revlog_id_for_card(anki_card_id)`` —
        the highest id already present in Anki's revlog for that card. Any
        tt_revlog row with a higher id represents a grade that hasn't been
        pushed yet.

        Pre-Layer-78 rows are naturally excluded: a pushed collapsed row
        at the latest grade id sets the watermark above them.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, collocation_id, direction, button_chosen, interval,
                       last_interval, factor, taken_millis, review_kind, anki_card_id
                FROM tt_revlog
                WHERE collocation_id = ? AND direction = ? AND id > ?
                ORDER BY id
                """,
                (collocation_id, direction.value, after_id),
            ).fetchall()
            return [
                RevlogRow(
                    id=r["id"],
                    collocation_id=r["collocation_id"],
                    direction=Direction(r["direction"]),
                    button_chosen=r["button_chosen"],
                    interval=r["interval"],
                    last_interval=r["last_interval"],
                    factor=r["factor"],
                    taken_millis=r["taken_millis"],
                    review_kind=r["review_kind"],
                    anki_card_id=r["anki_card_id"],
                )
                for r in rows
            ]

    def latest_revlog_id_for_direction(self, collocation_id: int, direction: Direction) -> int | None:
        """Return MAX(id) from tt_revlog for the given direction, or None.

        The Stage-3b incremental-replay anchor (Layer 71). Keyed by
        (collocation_id, direction) — the same domain ``rebuild_from_revlog``
        walks — NOT by ``anki_card_id``: TT-native rows graded before
        ``sync_create_new`` mints the card carry ``anki_card_id=NULL`` (and a
        re-minted card changes ids), so a card-keyed anchor misses them,
        ``since_id`` resolves to None, and the replay re-walks the full
        history on top of the already-evolved stored state on every sync.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT MAX(id) FROM tt_revlog WHERE collocation_id = ? AND direction = ?",
                (collocation_id, direction.value),
            ).fetchone()
            return row[0] if row and row[0] is not None else None

    def append_manual_revlog(
        self,
        collocation_id: int,
        direction: Direction | None = None,
        *,
        anki_card_id: int | None = None,
    ) -> None:
        """Write one or two review_kind=4 (Manual) tt_revlog rows.

        Used by promote_to_learning and similar admin operations that mutate
        state without going through ``schedule()``.
        """
        now_ms = int(_time.time() * 1000)
        dirs = [direction] if direction is not None else list(Direction)
        for d in dirs:
            self.append_revlog(
                RevlogRow(
                    id=now_ms,
                    collocation_id=collocation_id,
                    direction=d,
                    button_chosen=0,
                    interval=0,
                    last_interval=0,
                    factor=0,
                    taken_millis=0,
                    review_kind=4,
                    anki_card_id=anki_card_id,
                )
            )
