"""SQLite repository for SRS collocations and violations.

Schema is managed by `app.srs.migrations`. Fresh DBs bootstrap the v0 base
tables (matching the pre-migration shape) and then `migrate()` runs every
pending step up to `CURRENT_VERSION`.

Supports ":memory:" for in-memory test databases.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from app.common.guid import compute_guid
from app.models.srs_item import (
    Direction,
    DirectionState,
    SRSItem,
    SRSState,
)
from app.models.syntactic_unit import SyntacticUnit
from app.srs.migrations import migrate


def _parse_last_review(value: str | None) -> datetime | None:
    """Parse last_review from DB. Handles both date and datetime strings."""
    if value is None:
        return None
    dt = datetime.fromisoformat(value)
    # Promote naive to UTC for safe comparisons
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


# v0 base schema. Fresh DBs go through v0 → v1 → v2 via migrations so every
# deployment converges on the same path.
_CREATE_COLLOCATIONS_V0 = """
CREATE TABLE IF NOT EXISTS collocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT UNIQUE NOT NULL,
    translation TEXT NOT NULL DEFAULT '',
    language_code TEXT NOT NULL DEFAULT 'sl',
    word_count INTEGER NOT NULL DEFAULT 1,
    unit_difficulty INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'corpus',
    corpus_frequency INTEGER NOT NULL DEFAULT 0,
    stability REAL NOT NULL DEFAULT 1.0,
    fsrs_difficulty REAL NOT NULL DEFAULT 5.0,
    due_date TEXT NOT NULL,
    reps INTEGER NOT NULL DEFAULT 0,
    lapses INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'new',
    last_review TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
)
"""

_CREATE_VIOLATIONS = """
CREATE TABLE IF NOT EXISTS violations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collocation_text TEXT NOT NULL,
    day_number INTEGER NOT NULL,
    violation_type TEXT NOT NULL,
    details TEXT,
    created_at TEXT DEFAULT (datetime('now'))
)
"""

_CREATE_SYNC_CONFLICTS = """
CREATE TABLE IF NOT EXISTS sync_conflicts (
    id INTEGER PRIMARY KEY,
    guid TEXT NOT NULL,
    direction TEXT,
    field TEXT NOT NULL,
    local_value TEXT,
    remote_value TEXT,
    resolution TEXT NOT NULL,
    resolved_at TEXT NOT NULL
)
"""

_CREATE_ANKI_STATE_CACHE = """
CREATE TABLE IF NOT EXISTS anki_state_cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

# Columns on `collocation_directions` mapped onto a DirectionState.
_DIR_COLUMNS = (
    "stability",
    "fsrs_difficulty",
    "due_date",
    "reps",
    "lapses",
    "state",
    "last_review",
    "last_review_time_ms",
    "anki_card_id",
    "anki_card_mod",
    "anki_due",
    "dirty_fsrs",
    "last_synced_at",
    "last_rating",
    "left",
    "due_at",
    "prior_state",
    "prior_left",
    "prior_stability",
    "introduced_at",
)

# States that should never surface in the due queue regardless of due_date.
_NON_REVIEWABLE_STATES = ("new", "suspended", "known", "buried")
# States that count as "in the learning bucket" (Anki queue=1 / queue=3).
# Shared by get_learning_items (review queue) and count_learning (badge).
_LEARNING_STATES = ("learning", "relearning")


class SRSDatabase:
    """SQLite-backed SRS repository.

    Use `:memory:` as db_path for in-memory test databases.
    """

    def close(self) -> None:
        """Explicitly close the in-memory connection."""
        if self._in_memory and self._conn is not None:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> SRSDatabase:
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def __init__(self, db_path: str = ":memory:") -> None:
        self._in_memory = db_path == ":memory:"
        if self._in_memory:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._init_schema(self._conn)
        else:
            # Handle sqlite:// URL format
            if db_path.startswith("sqlite:///"):
                db_path = db_path[10:]  # Remove "sqlite:///"
            path = Path(db_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._path = str(path)
            self._conn = None
            with self._file_conn() as conn:
                self._init_schema(conn)

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(_CREATE_COLLOCATIONS_V0)
        conn.execute(_CREATE_VIOLATIONS)
        conn.commit()
        migrate(conn)
        conn.execute(_CREATE_SYNC_CONFLICTS)
        conn.execute(_CREATE_ANKI_STATE_CACHE)
        conn.commit()

    @contextmanager
    def _file_conn(self):
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def _get_conn(self):
        txn = getattr(self, "_txn_conn", None)
        if txn is not None:
            yield txn
        elif self._in_memory:
            yield self._conn
        else:
            with self._file_conn() as conn:
                yield conn

    def _commit(self, conn: sqlite3.Connection) -> None:
        if getattr(self, "_txn_conn", None) is not None:
            return  # transaction context handles commits
        if self._in_memory:
            conn.commit()
        # file-backed contexts commit on exit

    @contextmanager
    def begin_transaction(self, dry_run: bool = False):
        """Wrap subsequent DB calls in a single transaction.

        COMMIT on success unless dry_run=True; ROLLBACK on any exception.
        """
        assert not hasattr(self, "_txn_conn"), "Nested transactions not supported"
        if self._in_memory:
            conn = self._conn
            prev_iso = conn.isolation_level
            conn.isolation_level = None
            conn.execute("BEGIN")
        else:
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.isolation_level = None
            conn.execute("BEGIN")
        self._txn_conn = conn
        try:
            yield self
            if dry_run:
                conn.execute("ROLLBACK")
            else:
                conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            del self._txn_conn
            if self._in_memory:
                conn.isolation_level = prev_iso
            else:
                conn.close()

    # ── Write operations ───────────────────────────────────────────────

    def add_collocation(self, unit: SyntacticUnit, language_code: str = "sl") -> None:
        """Insert a new collocation; if it already exists, backfill an empty translation.

        New rows get both recognition and production direction rows (defaults).
        Single-word units without an explicit lemma get lemma = casefolded text
        so that get_collocation_by_lemma_with_id lookups succeed. Empty strings
        count as missing — pre-Phase-F sync paths sometimes wrote empties.
        """
        if not unit.lemma and unit.word_count == 1:
            unit.lemma = unit.text.casefold()
        disambig = unit.disambig_key
        guid = compute_guid(unit.text, language_code, disambig)
        today = date.today().isoformat()
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO collocations
                    (text, translation, language_code, word_count, unit_difficulty,
                     source, corpus_frequency, lemma, guid, disambig_key, grammar, note,
                     source_sentence, sentence_translation, source_lesson_id, source_line_index, card_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guid) DO UPDATE SET
                    translation = CASE
                        WHEN excluded.translation != '' AND collocations.translation = ''
                        THEN excluded.translation
                        ELSE collocations.translation
                    END
                """,
                (
                    unit.text,
                    unit.translation,
                    language_code,
                    unit.word_count,
                    unit.difficulty,
                    unit.source,
                    unit.frequency,
                    unit.lemma,
                    guid,
                    disambig,
                    unit.grammar,
                    unit.note,
                    unit.source_sentence,
                    unit.source_sentence_translation,
                    unit.source_lesson_id,
                    unit.source_line_index,
                    unit.card_type,
                ),
            )
            row = conn.execute(
                "SELECT id FROM collocations WHERE guid = ?",
                (guid,),
            ).fetchone()
            coll_id = row["id"]
            if unit.card_type == "cloze":
                directions = [Direction.PRODUCTION]
            else:
                directions = [Direction.RECOGNITION, Direction.PRODUCTION]
            for direction in directions:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO collocation_directions
                        (collocation_id, direction, due_date)
                    VALUES (?, ?, ?)
                    """,
                    (coll_id, direction.value, today),
                )
            self._commit(conn)

    def get_untranslated_collocations(self) -> list[tuple[str, str]]:
        """Return (text, language_code) for all rows with an empty translation."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT text, language_code FROM collocations WHERE translation = ''",
            ).fetchall()
        return [(row["text"], row["language_code"]) for row in rows]

    def backfill_translations(self, glosses: dict[str, str]) -> int:
        """Update rows with empty translations using the provided gloss map."""
        if not glosses:
            return 0
        updated = 0
        with self._get_conn() as conn:
            for text, translation in glosses.items():
                if not translation:
                    continue
                cursor = conn.execute(
                    "UPDATE collocations SET translation = ?, updated_at = datetime('now') "
                    "WHERE text = ? AND translation = ''",
                    (translation, text),
                )
                updated += cursor.rowcount
            self._commit(conn)
        return updated

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
                    due_date = ?,
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
                    due_at = ?,
                    prior_state = ?,
                    prior_left = ?,
                    prior_stability = ?,
                    introduced_at = ?,
                    bury_kind = ?
                WHERE collocation_id = ? AND direction = ?
                """,
                (
                    state.stability,
                    state.difficulty,
                    state.due_date.isoformat(),
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
                    state.due_at.isoformat() if state.due_at else None,
                    state.prior_state.value if state.prior_state is not None else None,
                    state.prior_left,
                    state.prior_stability,
                    state.introduced_at.isoformat() if state.introduced_at else None,
                    state.bury_kind,
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

    def record_violation(
        self, collocation_text: str, day_number: int, violation_type: str, details: str | None = None
    ) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO violations (collocation_text, day_number, violation_type, details) VALUES (?, ?, ?, ?)",
                (collocation_text, day_number, violation_type, details),
            )
            self._commit(conn)

    # ── Read operations ────────────────────────────────────────────────

    def _load_directions(self, conn: sqlite3.Connection, collocation_id: int) -> dict[Direction, DirectionState]:
        rows = conn.execute(
            f"SELECT direction, {', '.join(_DIR_COLUMNS)} FROM collocation_directions WHERE collocation_id = ?",
            (collocation_id,),
        ).fetchall()
        directions: dict[Direction, DirectionState] = {}
        for row in rows:
            d = Direction(row["direction"])
            # Parse due_at if present
            due_at = None
            if row["due_at"] is not None:
                due_at = datetime.fromisoformat(row["due_at"])
            prior_state_raw = row["prior_state"]
            introduced_at_raw = row["introduced_at"]
            directions[d] = DirectionState(
                direction=d,
                due_date=date.fromisoformat(row["due_date"]),
                stability=row["stability"],
                difficulty=row["fsrs_difficulty"],
                reps=row["reps"],
                lapses=row["lapses"],
                state=SRSState(row["state"]),
                last_review=_parse_last_review(row["last_review"]),
                last_review_time_ms=row["last_review_time_ms"] or 0,
                anki_card_id=row["anki_card_id"],
                anki_card_mod=row["anki_card_mod"],
                anki_due=row["anki_due"],
                dirty_fsrs=bool(row["dirty_fsrs"]),
                last_synced_at=row["last_synced_at"],
                last_rating=row["last_rating"],
                left=row["left"],
                due_at=due_at,
                prior_state=SRSState(prior_state_raw) if prior_state_raw else None,
                prior_left=row["prior_left"],
                prior_stability=row["prior_stability"],
                introduced_at=datetime.fromisoformat(introduced_at_raw) if introduced_at_raw else None,
                bury_kind=row["bury_kind"] if "bury_kind" in row.keys() else None,  # noqa: SIM118
            )
        return directions

    def _row_to_item(self, conn: sqlite3.Connection, row: sqlite3.Row) -> SRSItem:
        unit = SyntacticUnit(
            text=row["text"],
            translation=row["translation"],
            word_count=row["word_count"],
            difficulty=row["unit_difficulty"],
            source=row["source"],
            frequency=row["corpus_frequency"],
            lemma=row["lemma"],
            guid=row["guid"],
            disambig_key=row["disambig_key"],
            grammar=row["grammar"],
            note=row["note"],
            source_sentence=row["source_sentence"],
            source_sentence_translation=row["sentence_translation"] if "sentence_translation" in row.keys() else "",  # noqa: SIM118
            source_lesson_id=row["source_lesson_id"],
            source_line_index=row["source_line_index"],
            card_type=row["card_type"] if "card_type" in row.keys() else "vocab",  # noqa: SIM118
        )
        directions = self._load_directions(conn, row["id"])
        return SRSItem(
            syntactic_unit=unit,
            directions=directions,
            guid=row["guid"],
            anki_note_id=row["anki_note_id"],
        )

    def get_collocation(self, text: str) -> SRSItem | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM collocations WHERE text = ?", (text,)).fetchone()
            if row is None:
                return None
            return self._row_to_item(conn, row)

    def get_collocation_by_guid(self, guid: str) -> SRSItem | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM collocations WHERE guid = ?", (guid,)).fetchone()
            if row is None:
                return None
            return self._row_to_item(conn, row)

    def get_collocation_by_anki_note_id(self, anki_note_id: int) -> SRSItem | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM collocations WHERE anki_note_id = ? LIMIT 1", (anki_note_id,)).fetchone()
            if row is None:
                return None
            return self._row_to_item(conn, row)

    def get_collocation_by_lemma(self, lemma: str) -> SRSItem | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM collocations WHERE lemma = ? LIMIT 1", (lemma,)).fetchone()
            if row is None:
                return None
            return self._row_to_item(conn, row)

    def get_collocation_by_lemma_with_id(self, lemma: str) -> tuple[int, SRSItem] | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM collocations WHERE lemma = ? LIMIT 1", (lemma,)).fetchone()
            if row is None:
                return None
            return (row["id"], self._row_to_item(conn, row))

    def get_collocations_for_language(
        self,
        language_code: str,
        min_word_count: int = 2,
    ) -> list[tuple[int, str]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, text FROM collocations WHERE language_code = ? AND word_count >= ?",
                (language_code, min_word_count),
            ).fetchall()
        return [(row["id"], row["text"]) for row in rows]

    def get_due_collocations(
        self,
        as_of: date,
        direction: Direction = Direction.RECOGNITION,
    ) -> list[SRSItem]:
        """Return all collocations whose `direction` is due on or before `as_of`."""
        placeholders = ",".join("?" * len(_NON_REVIEWABLE_STATES))
        with self._get_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT c.* FROM collocations c
                JOIN collocation_directions d ON d.collocation_id = c.id
                WHERE d.direction = ?
                  AND d.due_date <= ?
                  AND d.state NOT IN ({placeholders})
                ORDER BY d.due_date ASC, d.stability ASC NULLS LAST, d.anki_card_id ASC NULLS LAST, c.id ASC
                """,
                (direction.value, as_of.isoformat(), *_NON_REVIEWABLE_STATES),
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
        with self._get_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT c.* FROM collocations c
                JOIN collocation_directions d ON d.collocation_id = c.id
                WHERE d.direction = ?
                  AND d.due_date <= ?
                  AND d.state NOT IN ({placeholders})
                ORDER BY d.due_date ASC, d.stability ASC NULLS LAST, d.anki_card_id ASC NULLS LAST, c.id ASC
                """,
                (direction.value, as_of.isoformat(), *_NON_REVIEWABLE_STATES),
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
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT c.* FROM collocations c
                JOIN collocation_directions d ON d.collocation_id = c.id
                WHERE d.direction = ? AND d.state = 'new'
                 ORDER BY d.anki_due DESC NULLS FIRST,
                          c.created_at DESC NULLS LAST,
                          d.anki_card_id ASC NULLS LAST,
                          c.id ASC
                 LIMIT ?
                """,
                (direction.value, limit),
            ).fetchall()
            return [(r["id"], self._row_to_item(conn, r), r["language_code"]) for r in rows]

    def update_direction_by_id(self, row_id: int, direction: Direction, state: DirectionState) -> None:
        """Persist direction state for a collocation identified by row id."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT guid FROM collocations WHERE id = ?", (row_id,)).fetchone()
            if row is None:
                return
        self.update_direction(row["guid"], direction, state)

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
        local_tz = datetime.now().astimezone().tzinfo
        start_utc = datetime.combine(today, time(0), tzinfo=local_tz).astimezone(UTC)
        end_utc = datetime.combine(today + timedelta(days=1), time(0), tzinfo=local_tz).astimezone(UTC)
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT collocation_id FROM collocation_directions
                WHERE (length(last_review) > 10 AND last_review >= ? AND last_review < ?)
                   OR (length(last_review) = 10 AND last_review = ?)
                """,
                (start_utc.isoformat(), end_utc.isoformat(), today.isoformat()),
            ).fetchall()
            return {r[0] for r in rows}

    def get_image_filename(self, collocation_id: int) -> str | None:
        """Return the filename of the first image media row for a collocation, or None."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT filename FROM media WHERE collocation_id = ? AND kind = 'image' ORDER BY id DESC LIMIT 1",
                (collocation_id,),
            ).fetchone()
        return row["filename"] if row is not None else None

    def get_audio_filename(self, collocation_id: int) -> str | None:
        """Return the filename of the preferred audio media row (forvo > tts), or None."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT filename FROM media WHERE collocation_id = ? AND kind IN ('audio_forvo','audio_tts') "
                "ORDER BY CASE kind WHEN 'audio_forvo' THEN 0 ELSE 1 END LIMIT 1",
                (collocation_id,),
            ).fetchone()
        return row["filename"] if row is not None else None

    def get_created_at_by_guid(self, guid: str) -> str | None:
        """Return the ISO timestamp from collocations.created_at for the given guid,
        or None if no row matches. Used by sync_create_new to sort items so newer
        cards get higher cards.due under Anki's Descending position gather order.
        """
        with self._get_conn() as conn:
            row = conn.execute("SELECT created_at FROM collocations WHERE guid = ?", (guid,)).fetchone()
            return row["created_at"] if row else None

    def get_collocation_by_id(self, row_id: int) -> tuple[int, SRSItem, str] | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM collocations WHERE id = ?", (row_id,)).fetchone()
            if row is None:
                return None
            return (row["id"], self._row_to_item(conn, row), row["language_code"])

    def update_collocation_fields(self, row_id: int, *, text: str, translation: str) -> None:
        """Update text and translation for a collocation by id.

        When `text` changes, the computed guid updates accordingly.
        Changed fields are appended to dirty_fields for sync tracking.
        """
        try:
            with self._get_conn() as conn:
                cur = conn.execute(
                    "SELECT language_code, text, translation, dirty_fields, disambig_key FROM collocations WHERE id = ?",
                    (row_id,),
                ).fetchone()
                if cur is None:
                    return
                disambig = cur["disambig_key"] if cur["disambig_key"] is not None else ""
                new_guid = compute_guid(text, cur["language_code"], disambig)
                changed: set[str] = set()
                if text != cur["text"]:
                    changed.add("text")
                if translation != cur["translation"]:
                    changed.add("translation")
                existing = {f for f in (cur["dirty_fields"] or "").split(",") if f}
                merged = ",".join(sorted(existing | changed))
                conn.execute(
                    "UPDATE collocations SET text = ?, translation = ?, guid = ?, "
                    "dirty_fields = ?, updated_at = datetime('now') WHERE id = ?",
                    (text, translation, new_guid, merged, row_id),
                )
                self._commit(conn)
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"text already exists: {text!r}") from exc

    def delete_collocation(self, row_id: int) -> None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT text FROM collocations WHERE id = ?", (row_id,)).fetchone()
            if row is not None:
                conn.execute("DELETE FROM violations WHERE collocation_text = ?", (row["text"],))
                conn.execute("DELETE FROM collocations WHERE id = ?", (row_id,))
                self._commit(conn)

    def delete_collocations(self, row_ids: list[int]) -> int:
        if not row_ids:
            return 0
        placeholders = ",".join("?" * len(row_ids))
        with self._get_conn() as conn:
            rows = conn.execute(f"SELECT text FROM collocations WHERE id IN ({placeholders})", row_ids).fetchall()
            texts = [r["text"] for r in rows]
            if texts:
                text_ph = ",".join("?" * len(texts))
                conn.execute(f"DELETE FROM violations WHERE collocation_text IN ({text_ph})", texts)
            conn.execute(f"DELETE FROM collocations WHERE id IN ({placeholders})", row_ids)
            self._commit(conn)
        return len(texts)

    def untrack_collocation(self, row_id: int) -> dict[str, str]:
        """Remove a collocation from the user's learning queue.

        If the row was never pushed to Anki (anki_note_id IS NULL), delete it
        outright (cascade deletes both direction rows). Otherwise suspend both
        directions and mark dirty_fsrs=1 so the next Anki push suspends the card.

        Returns {"action": "deleted"} or {"action": "suspended"}.
        """
        with self._get_conn() as conn:
            row = conn.execute("SELECT anki_note_id FROM collocations WHERE id = ?", (row_id,)).fetchone()
            if row is None:
                return {"action": "deleted"}
            if row["anki_note_id"] is None:
                conn.execute(
                    "DELETE FROM violations WHERE collocation_text = (SELECT text FROM collocations WHERE id = ?)",
                    (row_id,),
                )
                conn.execute("DELETE FROM collocations WHERE id = ?", (row_id,))
                self._commit(conn)
                return {"action": "deleted"}
            conn.execute(
                "UPDATE collocation_directions SET state = 'suspended', dirty_fsrs = 1 WHERE collocation_id = ?",
                (row_id,),
            )
            conn.execute(
                "UPDATE collocations SET updated_at = datetime('now') WHERE id = ?",
                (row_id,),
            )
            self._commit(conn)
            return {"action": "suspended"}

    def reset_collocation(self, row_id: int, direction: Direction | None = None) -> None:
        """Reset FSRS scheduling for one or both directions of a collocation."""
        today = date.today().isoformat()
        params: tuple
        if direction is None:
            sql = (
                "UPDATE collocation_directions SET "
                "state = 'new', stability = 1.0, fsrs_difficulty = 5.0, "
                "reps = 0, lapses = 0, due_date = ?, last_review = NULL, "
                "dirty_fsrs = 0 "
                "WHERE collocation_id = ?"
            )
            params = (today, row_id)
        else:
            sql = (
                "UPDATE collocation_directions SET "
                "state = 'new', stability = 1.0, fsrs_difficulty = 5.0, "
                "reps = 0, lapses = 0, due_date = ?, last_review = NULL, "
                "dirty_fsrs = 0 "
                "WHERE collocation_id = ? AND direction = ?"
            )
            params = (today, row_id, direction.value)
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
        """Set the state of a collocation directly, bypassing FSRS scheduling."""
        dirty_clause = ", dirty_fsrs = 1" if mark_dirty else ""
        with self._get_conn() as conn:
            if direction is None:
                conn.execute(
                    f"UPDATE collocation_directions SET state = ?{dirty_clause} WHERE collocation_id = ?",
                    (state.value, row_id),
                )
            else:
                conn.execute(
                    f"UPDATE collocation_directions SET state = ?{dirty_clause} WHERE collocation_id = ? AND direction = ?",
                    (state.value, row_id, direction.value),
                )
            conn.execute(
                "UPDATE collocations SET updated_at = datetime('now') WHERE id = ?",
                (row_id,),
            )
            self._commit(conn)

    def promote_to_learning(
        self,
        row_id: int,
        direction: Direction | None = None,
    ) -> None:
        """Set state to LEARNING with today's due_date and a fresh last_review.

        This is NOT an FSRS grade event — no revlog row is written. The caller
        is responsible for ensuring the collocation exists.

        Note: `left` and `due_at` are left as NULL, so sync_push routes to
        set_due_date (the new/review branch at sync.py:1219), not to
        set_learning_state. Anki receives "due today" without learning-step
        metadata — TunaTale shows LEARNING, Anki treats it as effectively new.
        This matches the "no FSRS grade" intent but creates a silent asymmetry
        between TT and Anki views.
        """
        today = date.today().isoformat()
        now = datetime.now(UTC)
        now_ms = int(now.timestamp() * 1000)
        now_iso = now.isoformat()
        with self._get_conn() as conn:
            if direction is None:
                conn.execute(
                    "UPDATE collocation_directions SET state = 'learning',"
                    " due_date = ?, last_review = ?, last_review_time_ms = ?,"
                    " dirty_fsrs = 1 WHERE collocation_id = ?",
                    (today, now_iso, now_ms, row_id),
                )
            else:
                conn.execute(
                    "UPDATE collocation_directions SET state = 'learning',"
                    " due_date = ?, last_review = ?, last_review_time_ms = ?,"
                    " dirty_fsrs = 1 WHERE collocation_id = ? AND direction = ?",
                    (today, now_iso, now_ms, row_id, direction.value),
                )
            conn.execute(
                "UPDATE collocations SET updated_at = datetime('now') WHERE id = ?",
                (row_id,),
            )
            self._commit(conn)

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

    def list_collocations(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        state: SRSState | None = None,
        order_by: str = "text",
        order_dir: str = "asc",
        order_direction: Direction = Direction.RECOGNITION,
    ) -> tuple[list[tuple[int, SRSItem, str]], int]:
        """Paginated browse for the admin UI. Returns (rows, total_count)."""
        parent_columns = {"text", "translation"}
        direction_columns = {
            "state": "state",
            "due_date": "due_date",
            "fsrs_difficulty": "fsrs_difficulty",
            "reps": "reps",
            "lapses": "lapses",
            "last_review": "last_review",
        }
        _VALID_ORDER_DIR = {"asc", "desc"}

        if order_by not in parent_columns and order_by not in direction_columns:
            raise ValueError(f"Invalid order_by: {order_by!r}")
        if order_dir not in _VALID_ORDER_DIR:
            raise ValueError(f"Invalid order_dir: {order_dir!r}")

        conditions: list[str] = []
        params: list = []

        if search:
            conditions.append("(c.text LIKE ? OR c.translation LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        if state is not None:
            conditions.append("d_filter.state = ?")
            params.append(state.value)

        # d_filter is a direction row used for state filter + ordering by direction columns.
        # It is always joined on the requested order_direction.
        join = "LEFT JOIN collocation_directions d_filter ON d_filter.collocation_id = c.id AND d_filter.direction = ?"
        join_params = [order_direction.value]

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        order_expr = f"c.{order_by}" if order_by in parent_columns else f"d_filter.{direction_columns[order_by]}"

        count_sql = f"SELECT COUNT(*) FROM collocations c {join} {where}"
        rows_sql = f"SELECT c.* FROM collocations c {join} {where} ORDER BY {order_expr} {order_dir} LIMIT ? OFFSET ?"

        with self._get_conn() as conn:
            total = conn.execute(count_sql, join_params + params).fetchone()[0]
            rows = conn.execute(rows_sql, join_params + params + [limit, offset]).fetchall()
            result = [(r["id"], self._row_to_item(conn, r), r["language_code"]) for r in rows]
        return result, total

    def get_violations(self, collocation_text: str) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM violations WHERE collocation_text = ?",
                (collocation_text,),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_collocations(self) -> int:
        with self._get_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM collocations").fetchone()[0]

    # ── Anki-surface methods ───────────────────────────────────────────

    def upsert_by_guid(
        self,
        unit: SyntacticUnit,
        language_code: str,
        directions: dict[Direction, DirectionState],
        anki_note_id: int | None = None,
    ) -> int:
        """Insert parent row if new, else update parent scalar fields.

        Per-direction idempotency: if an existing direction has reps > 0,
        only update anki_card_id (preserve TunaTale-local review progress).
        If reps == 0, refresh all FSRS fields from the supplied state.
        Returns the collocation id.
        """
        guid = compute_guid(unit.text, language_code, unit.disambig_key)
        # Backfill missing single-word lemma so by-lemma lookups keep working;
        # mirrors add_collocation. Empty strings count as missing.
        if not unit.lemma and unit.word_count == 1:
            unit.lemma = unit.text.casefold()
        with self._get_conn() as conn:
            row = conn.execute("SELECT id, lemma FROM collocations WHERE guid = ?", (guid,)).fetchone()
            if row is None:
                cursor = conn.execute(
                    """
                    INSERT INTO collocations
                        (text, translation, language_code, word_count, unit_difficulty,
                         source, corpus_frequency, lemma, guid, anki_note_id, disambig_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        unit.text,
                        unit.translation,
                        language_code,
                        unit.word_count,
                        unit.difficulty,
                        unit.source,
                        unit.frequency,
                        unit.lemma,
                        guid,
                        anki_note_id,
                        unit.disambig_key,
                    ),
                )
                coll_id = cursor.lastrowid
                for direction, state in directions.items():
                    conn.execute(
                        """
                        INSERT INTO collocation_directions
                            (collocation_id, direction, stability, fsrs_difficulty, due_date,
                             reps, lapses, state, last_review, anki_card_id, anki_due, left, due_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            coll_id,
                            direction.value,
                            state.stability,
                            state.difficulty,
                            state.due_date.isoformat(),
                            state.reps,
                            state.lapses,
                            state.state.value,
                            state.last_review.isoformat() if state.last_review else None,
                            state.anki_card_id,
                            state.anki_due,
                            state.left,
                            state.due_at.isoformat() if state.due_at else None,
                        ),
                    )
            else:
                coll_id = row["id"]
                # Preserve an existing non-empty lemma if the incoming row is empty.
                resolved_lemma = unit.lemma if unit.lemma else row["lemma"]
                conn.execute(
                    """
                    UPDATE collocations SET
                        translation = ?, word_count = ?, unit_difficulty = ?,
                        source = ?, corpus_frequency = ?, lemma = ?,
                        anki_note_id = COALESCE(?, anki_note_id),
                        updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (
                        unit.translation,
                        unit.word_count,
                        unit.difficulty,
                        unit.source,
                        unit.frequency,
                        resolved_lemma,
                        anki_note_id,
                        coll_id,
                    ),
                )
                for direction, state in directions.items():
                    dir_row = conn.execute(
                        "SELECT reps FROM collocation_directions WHERE collocation_id = ? AND direction = ?",
                        (coll_id, direction.value),
                    ).fetchone()
                    if dir_row is None:
                        conn.execute(
                            """
                            INSERT INTO collocation_directions
                                (collocation_id, direction, stability, fsrs_difficulty, due_date,
                                 reps, lapses, state, last_review, anki_card_id, anki_due, left, due_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                coll_id,
                                direction.value,
                                state.stability,
                                state.difficulty,
                                state.due_date.isoformat(),
                                state.reps,
                                state.lapses,
                                state.state.value,
                                state.last_review.isoformat() if state.last_review else None,
                                state.anki_card_id,
                                state.anki_due,
                                state.left,
                                state.due_at.isoformat() if state.due_at else None,
                            ),
                        )
                    elif dir_row["reps"] > 0:
                        # Preserve TunaTale-local FSRS progress; refresh Anki-bookkeeping fields
                        # (state, anki_card_id, anki_due, left, due_at) from the current sync.
                        conn.execute(
                            """
                            UPDATE collocation_directions SET
                                state = ?, anki_card_id = ?, anki_due = ?, left = ?, due_at = ?
                            WHERE collocation_id = ? AND direction = ?
                            """,
                            (
                                state.state.value,
                                state.anki_card_id,
                                state.anki_due,
                                state.left,
                                state.due_at.isoformat() if state.due_at else None,
                                coll_id,
                                direction.value,
                            ),
                        )
                    else:
                        conn.execute(
                            """
                            UPDATE collocation_directions SET
                                stability = ?, fsrs_difficulty = ?, due_date = ?,
                                reps = ?, lapses = ?, state = ?, last_review = ?,
                                anki_card_id = ?, anki_due = ?, left = ?, due_at = ?
                            WHERE collocation_id = ? AND direction = ?
                            """,
                            (
                                state.stability,
                                state.difficulty,
                                state.due_date.isoformat(),
                                state.reps,
                                state.lapses,
                                state.state.value,
                                state.last_review.isoformat() if state.last_review else None,
                                state.anki_card_id,
                                state.anki_due,
                                state.left,
                                state.due_at.isoformat() if state.due_at else None,
                                coll_id,
                                direction.value,
                            ),
                        )
            self._commit(conn)
        return coll_id

    def set_anki_ids(
        self,
        guid: str,
        note_id: int,
        card_ids: dict[Direction, int],
    ) -> None:
        """Set anki_note_id on the parent and anki_card_id on each direction row."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT id FROM collocations WHERE guid = ?", (guid,)).fetchone()
            if row is None:
                return
            coll_id = row["id"]
            conn.execute(
                "UPDATE collocations SET anki_note_id = ? WHERE id = ?",
                (note_id, coll_id),
            )
            for direction, card_id in card_ids.items():
                conn.execute(
                    "UPDATE collocation_directions SET anki_card_id = ? WHERE collocation_id = ? AND direction = ?",
                    (card_id, coll_id, direction.value),
                )
            self._commit(conn)

    def list_anki_card_ids(self) -> set[int]:
        """Return all anki_card_ids currently linked on directions.

        Used by sync to diff against the Anki collection's live cards and detect
        orphans (TT rows pointing at cards that no longer exist in Anki).
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT anki_card_id FROM collocation_directions WHERE anki_card_id IS NOT NULL"
            ).fetchall()
            return {row["anki_card_id"] for row in rows}

    def reset_orphaned_anki_ids(
        self,
        *,
        live_card_ids: set[int],
        live_note_ids: set[int],
    ) -> tuple[list[tuple[str, str]], list[str]]:
        """Clear anki_card_id / anki_note_id on rows whose Anki target is gone.

        Behaviour:
        - For each direction with `anki_card_id NOT IN live_card_ids`: set
          `anki_card_id`, `anki_card_mod`, `anki_due`, `last_synced_at` to NULL.
          If the row has `reps > 0`, also set `dirty_fsrs = 1` so the next
          `sync_push` rewrites the FSRS state (force_fsrs) and a revlog entry
          onto the freshly-created Anki card.
        - For each collocation with `anki_note_id NOT IN live_note_ids`: clear
          `anki_note_id` and `last_synced_at`. The next `sync_create_new` then
          creates a fresh Anki note.

        Returns (direction_resets, note_resets) where direction_resets is a list
        of (guid, direction_str) and note_resets is a list of guid.
        """
        direction_resets: list[tuple[str, str]] = []
        note_resets: list[str] = []
        with self._get_conn() as conn:
            dir_rows = conn.execute(
                """
                SELECT c.guid, d.direction, d.anki_card_id, d.collocation_id, d.reps
                FROM collocation_directions d
                JOIN collocations c ON c.id = d.collocation_id
                WHERE d.anki_card_id IS NOT NULL
                """,
            ).fetchall()
            for row in dir_rows:
                if row["anki_card_id"] in live_card_ids:
                    continue
                make_dirty = row["reps"] > 0
                conn.execute(
                    """
                    UPDATE collocation_directions SET
                      anki_card_id = NULL,
                      anki_card_mod = NULL,
                      anki_due = NULL,
                      last_synced_at = NULL,
                      dirty_fsrs = CASE WHEN ? THEN 1 ELSE dirty_fsrs END
                    WHERE collocation_id = ? AND direction = ?
                    """,
                    (1 if make_dirty else 0, row["collocation_id"], row["direction"]),
                )
                direction_resets.append((row["guid"], row["direction"]))

            note_rows = conn.execute(
                "SELECT id, guid, anki_note_id FROM collocations WHERE anki_note_id IS NOT NULL"
            ).fetchall()
            for row in note_rows:
                if row["anki_note_id"] in live_note_ids:
                    continue
                conn.execute(
                    "UPDATE collocations SET anki_note_id = NULL, last_synced_at = NULL WHERE id = ?",
                    (row["id"],),
                )
                note_resets.append(row["guid"])

            self._commit(conn)
        return direction_resets, note_resets

    def add_media(
        self,
        collocation_id: int,
        kind: str,
        filename: str,
        path: str,
        anki_filename: str,
        sha256: str,
        size_bytes: int,
    ) -> int:
        """Insert a media row. Returns the new media id."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO media (collocation_id, kind, filename, path, anki_filename, sha256, bytes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (collocation_id, kind, filename, path, anki_filename, sha256, size_bytes),
            )
            self._commit(conn)
            return cursor.lastrowid

    def find_media_by_anki_filename(self, anki_filename: str, *, collocation_id: int) -> dict[str, Any] | None:
        """Return the media row for the given Anki filename on a specific collocation, or None.

        Scoped by ``collocation_id`` so that two collocations referencing the
        same filename (e.g. ``img_yes.jpg`` shared between ``ja`` and ``da``)
        don't cross-contaminate during sync.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM media WHERE anki_filename = ? AND collocation_id = ?",
                (anki_filename, collocation_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def delete_stale_media_for_kind(self, collocation_id: int, kind: str, keep_anki_filenames: set[str]) -> int:
        """Delete media rows on this collocation/kind whose anki_filename isn't in
        ``keep_anki_filenames``. Used by import_seed to collapse the row set down
        to what Anki currently references. Returns the number of rows deleted.
        """
        if not keep_anki_filenames:
            return 0
        placeholders = ",".join("?" * len(keep_anki_filenames))
        with self._get_conn() as conn:
            cur = conn.execute(
                f"DELETE FROM media WHERE collocation_id = ? AND kind = ? AND anki_filename NOT IN ({placeholders})",
                (collocation_id, kind, *keep_anki_filenames),
            )
            self._commit(conn)
            return cur.rowcount

    def update_media_file(self, row_id: int, sha256: str, size_bytes: int) -> None:
        """Update sha256 and size_bytes for an existing media row (used by refresh-media)."""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE media SET sha256 = ?, bytes = ? WHERE id = ?",
                (sha256, size_bytes, row_id),
            )
            self._commit(conn)

    def list_dirty(
        self,
        direction: Direction | None = None,
    ) -> list[tuple[str, Direction, DirectionState]]:
        """Return (guid, direction, DirectionState) tuples for dirty FSRS rows.

        A row is dirty when dirty_fsrs=1 (set by schedule() after a review).
        Pass direction to restrict to one direction; omit for both.
        """
        with self._get_conn() as conn:
            if direction is None:
                rows = conn.execute(
                    f"""
                    SELECT c.guid, d.direction, {", ".join(f"d.{col}" for col in _DIR_COLUMNS)}
                    FROM collocations c
                    JOIN collocation_directions d ON d.collocation_id = c.id
                    WHERE d.dirty_fsrs = 1
                    """,
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    SELECT c.guid, d.direction, {", ".join(f"d.{col}" for col in _DIR_COLUMNS)}
                    FROM collocations c
                    JOIN collocation_directions d ON d.collocation_id = c.id
                    WHERE d.dirty_fsrs = 1 AND d.direction = ?
                    """,
                    (direction.value,),
                ).fetchall()
            result = []
            for row in rows:
                d = Direction(row["direction"])
                due_at = None
                if row["due_at"] is not None:
                    due_at = datetime.fromisoformat(row["due_at"])
                prior_state_raw = row["prior_state"]
                ds = DirectionState(
                    direction=d,
                    due_date=date.fromisoformat(row["due_date"]),
                    stability=row["stability"],
                    difficulty=row["fsrs_difficulty"],
                    reps=row["reps"],
                    lapses=row["lapses"],
                    state=SRSState(row["state"]),
                    last_review=_parse_last_review(row["last_review"]),
                    last_review_time_ms=row["last_review_time_ms"] or 0,
                    anki_card_id=row["anki_card_id"],
                    dirty_fsrs=bool(row["dirty_fsrs"]),
                    last_synced_at=row["last_synced_at"],
                    last_rating=row["last_rating"],
                    left=row["left"],
                    due_at=due_at,
                    prior_state=SRSState(prior_state_raw) if prior_state_raw else None,
                    prior_left=row["prior_left"],
                    prior_stability=row["prior_stability"],
                )
                result.append((row["guid"], d, ds))
        return result

    def list_recently_graded_clean(
        self,
        direction: Direction | None = None,
    ) -> list[tuple[str, Direction, DirectionState]]:
        """Return (guid, direction, DirectionState) for clean rows that need revlog.

        These are directions where:
        - last_review > last_synced_at (a grade happened after last sync)
        - dirty_fsrs = 0 (already synced schedule)
        - last_rating IS NOT NULL (there is a grade to write)
        """
        with self._get_conn() as conn:
            if direction is None:
                rows = conn.execute(
                    f"""
                    SELECT c.guid, d.direction, {", ".join(f"d.{col}" for col in _DIR_COLUMNS)}
                    FROM collocations c
                    JOIN collocation_directions d ON d.collocation_id = c.id
                    WHERE d.dirty_fsrs = 0
                      AND d.last_rating IS NOT NULL
                    """,
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    SELECT c.guid, d.direction, {", ".join(f"d.{col}" for col in _DIR_COLUMNS)}
                    FROM collocations c
                    JOIN collocation_directions d ON d.collocation_id = c.id
                    WHERE d.dirty_fsrs = 0
                      AND d.last_rating IS NOT NULL
                      AND d.direction = ?
                    """,
                    (direction.value,),
                ).fetchall()
            result = []
            for row in rows:
                d = Direction(row["direction"])
                # SQL already filters: dirty_fsrs = 0 AND last_rating IS NOT NULL
                # No need for last_review > last_synced_at check since last_rating
                # being non-NULL indicates a pending revlog write.
                last_review_dt = _parse_last_review(row["last_review"])
                last_synced_at = row["last_synced_at"]
                due_at = None
                if row["due_at"] is not None:
                    due_at = datetime.fromisoformat(row["due_at"])
                prior_state_raw = row["prior_state"]
                ds = DirectionState(
                    direction=d,
                    due_date=date.fromisoformat(row["due_date"]),
                    stability=row["stability"],
                    difficulty=row["fsrs_difficulty"],
                    reps=row["reps"],
                    lapses=row["lapses"],
                    state=SRSState(row["state"]),
                    last_review=last_review_dt,
                    last_review_time_ms=row["last_review_time_ms"] or 0,
                    anki_card_id=row["anki_card_id"],
                    dirty_fsrs=bool(row["dirty_fsrs"]),
                    last_synced_at=last_synced_at,
                    last_rating=row["last_rating"],
                    left=row["left"],
                    due_at=due_at,
                    prior_state=SRSState(prior_state_raw) if prior_state_raw else None,
                    prior_left=row["prior_left"],
                    prior_stability=row["prior_stability"],
                )
                result.append((row["guid"], d, ds))
        return result

    def touch_last_synced_at(self, guid: str, direction: Direction) -> None:
        """Update last_synced_at to now for one direction without clearing dirty_fsrs."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT id FROM collocations WHERE guid = ?", (guid,)).fetchone()
            if row is None:
                return
            conn.execute(
                """
                UPDATE collocation_directions SET
                    last_synced_at = ?
                WHERE collocation_id = ? AND direction = ?
                """,
                (datetime.now(UTC).isoformat(), row["id"], direction.value),
            )
            self._commit(conn)

    def mark_direction_clean(self, guid: str, direction: Direction) -> None:
        """Clear dirty_fsrs and set last_synced_at to now for one direction."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT id FROM collocations WHERE guid = ?", (guid,)).fetchone()
            if row is None:
                return
            conn.execute(
                """
                UPDATE collocation_directions SET
                    dirty_fsrs = 0,
                    last_rating = NULL,
                    last_synced_at = ?,
                    prior_state = NULL,
                    prior_left = NULL,
                    prior_stability = NULL
                WHERE collocation_id = ? AND direction = ?
                """,
                (datetime.now(UTC).isoformat(), row["id"], direction.value),
            )
            self._commit(conn)

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

    def count_new_available(self) -> int:
        """Count all collocation_directions rows in the NEW state (both directions)."""
        with self._get_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM collocation_directions WHERE state = 'new'").fetchone()[0]

    def count_due_today_total(self, today: date) -> int:
        """Count all collocation_directions rows due on or before today, excluding non-reviewable states."""
        placeholders = ",".join("?" * len(_NON_REVIEWABLE_STATES))
        with self._get_conn() as conn:
            return conn.execute(
                f"""
                SELECT COUNT(*) FROM collocation_directions
                WHERE due_date <= ?
                  AND state NOT IN ({placeholders})
                """,
                (today.isoformat(), *_NON_REVIEWABLE_STATES),
            ).fetchone()[0]

    def count_learning(self) -> int:
        """Count every learning/relearning direction (Anki red badge).

        Matches Anki deck-browser semantics exactly: every queue=1 card is
        counted, regardless of due_date or whether the next step has elapsed.
        This is the same filter as `get_learning_items` — the count and the
        list must agree.
        """
        placeholders = ",".join("?" * len(_LEARNING_STATES))
        with self._get_conn() as conn:
            return conn.execute(
                f"SELECT COUNT(*) FROM collocation_directions WHERE state IN ({placeholders})",
                _LEARNING_STATES,
            ).fetchone()[0]

    def count_review_due(self, today: date) -> int:
        """Count review directions due today (Anki green bucket)."""
        with self._get_conn() as conn:
            return conn.execute(
                """
                SELECT COUNT(*) FROM collocation_directions
                WHERE due_date <= ? AND state = 'review'
                """,
                (today.isoformat(),),
            ).fetchone()[0]

    def count_review_due_collocations(self, today: date) -> int:
        """Count distinct collocations with at least one review-state direction
        due today and not yet graded today (in any direction).

        Anki's `bury_reviews=true` removes a note from today's review pool as
        soon as any sibling is graded — the un-graded sibling goes to queue=-2
        until tomorrow. Mirror that: exclude collocations whose `last_review`
        for any direction falls within today's local day. This way the badge
        decrements by 1 when *any* direction of a dual-template note is graded
        (not 2), matching Anki's deck-overview count exactly when both apps
        share the same data.
        """
        local_tz = datetime.now().astimezone().tzinfo
        start_utc = datetime.combine(today, time(0), tzinfo=local_tz).astimezone(UTC)
        end_utc = datetime.combine(today + timedelta(days=1), time(0), tzinfo=local_tz).astimezone(UTC)
        with self._get_conn() as conn:
            return conn.execute(
                """
                SELECT COUNT(DISTINCT cd.collocation_id) FROM collocation_directions cd
                WHERE cd.due_date <= ? AND cd.state = 'review'
                  AND cd.collocation_id NOT IN (
                    SELECT collocation_id FROM collocation_directions
                    WHERE (length(last_review) > 10 AND last_review >= ? AND last_review < ?)
                       OR (length(last_review) = 10 AND last_review = ?)
                  )
                """,
                (today.isoformat(), start_utc.isoformat(), end_utc.isoformat(), today.isoformat()),
            ).fetchone()[0]

    def count_new_introduced_today(self, today: date) -> int:
        """Count distinct collocations whose first NEW→non-NEW transition fell today.

        Filters on the explicit `introduced_at` column written once by the grade
        endpoint (`app.srs.fsrs.schedule`) and by `sync_pull` on the first
        introduction event. Mirrors Anki's `newToday` counter, which increments
        only on that first grade — subsequent reviews of the same card on later
        days do NOT bump it.

        Pre-Layer-26 rows that were introduced before `introduced_at` existed
        have NULL and naturally fall out of the count. Going forward, every new
        grade populates the column.
        """
        local_tz = datetime.now().astimezone().tzinfo
        start_utc = datetime.combine(today, time(0), tzinfo=local_tz).astimezone(UTC)
        end_utc = datetime.combine(today + timedelta(days=1), time(0), tzinfo=local_tz).astimezone(UTC)
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(DISTINCT collocation_id) FROM collocation_directions
                WHERE introduced_at IS NOT NULL
                  AND introduced_at >= ?
                  AND introduced_at < ?
                """,
                (start_utc.isoformat(), end_utc.isoformat()),
            ).fetchone()
            return row[0] if row else 0

    def count_due_collocations(
        self,
        as_of: date,
        direction: Direction = Direction.RECOGNITION,
    ) -> int:
        placeholders = ",".join("?" * len(_NON_REVIEWABLE_STATES))
        with self._get_conn() as conn:
            return conn.execute(
                f"""
                SELECT COUNT(DISTINCT c.id) FROM collocations c
                JOIN collocation_directions d ON d.collocation_id = c.id
                WHERE d.direction = ?
                  AND d.due_date <= ?
                  AND d.state NOT IN ({placeholders})
                """,
                (direction.value, as_of.isoformat(), *_NON_REVIEWABLE_STATES),
            ).fetchone()[0]

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

    def set_anki_state_cache(self, key: str, value: str) -> None:
        """Upsert a key/value pair in the Anki state cache with the current UTC timestamp."""
        from datetime import datetime

        updated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO anki_state_cache (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, updated_at),
            )
            self._commit(conn)

    def set_anki_state_cache_raw(self, key: str, value: str, updated_at: str) -> None:
        """Test helper: upsert a cache row with caller-specified updated_at.

        Production code uses set_anki_state_cache (stamps current UTC time).
        This variant is for tests that need to simulate stale or corrupt
        timestamps without reaching into the SQLite connection.
        """
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO anki_state_cache (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, updated_at),
            )
            self._commit(conn)

    def get_anki_state_cache(self, key: str) -> tuple[str, str] | None:
        """Return (value, updated_at) for the given key, or None if absent."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT value, updated_at FROM anki_state_cache WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return (row["value"], row["updated_at"])

    def delete_anki_state_cache(self, key: str) -> None:
        """Remove the cache row for `key` (idempotent — no-op when absent)."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM anki_state_cache WHERE key = ?", (key,))
            self._commit(conn)

    def get_enable_cloze_cards(self) -> bool:
        """Return the current cloze-cards flag (DB-backed, default False)."""
        row = self.get_anki_state_cache("enable_cloze_cards")
        if row is None:
            return False
        return row[0].lower() == "true"

    def set_enable_cloze_cards(self, enabled: bool) -> None:
        self.set_anki_state_cache("enable_cloze_cards", "true" if enabled else "false")

    def set_dirty_fields(self, guid: str, fields_str: str) -> None:
        """Set dirty_fields for the collocation identified by guid."""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE collocations SET dirty_fields = ? WHERE guid = ?",
                (fields_str, guid),
            )
            self._commit(conn)

    def get_dirty_fields(self, guid: str) -> str:
        """Return dirty_fields for the collocation identified by guid, or ''."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT dirty_fields FROM collocations WHERE guid = ?",
                (guid,),
            ).fetchone()
        return (row["dirty_fields"] or "") if row else ""

    def update_collocation_for_sync(
        self,
        guid: str,
        *,
        translation: str,
        note: str,
        sentence_translation: str = "",
        dirty_fields_str: str,
    ) -> None:
        """Update translation, note, sentence_translation, and dirty_fields after a sync pull."""
        now_iso = datetime.now(UTC).isoformat()
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE collocations SET translation = ?, note = ?, sentence_translation = ?, "
                "dirty_fields = ?, last_synced_at = ?, updated_at = ? WHERE guid = ?",
                (translation, note, sentence_translation, dirty_fields_str, now_iso, now_iso, guid),
            )
            self._commit(conn)

    def list_items_without_anki_note(self) -> list[tuple[str, SRSItem]]:
        """Return (guid, SRSItem) for collocations with no anki_note_id set."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM collocations WHERE anki_note_id IS NULL").fetchall()
            return [(row["guid"], self._row_to_item(conn, row)) for row in rows]

    def list_dirty_field_edits(self) -> list[tuple[str, int | None, str, SRSItem]]:
        """Return (guid, anki_note_id, dirty_fields_str, SRSItem) for rows with non-empty dirty_fields."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM collocations WHERE dirty_fields IS NOT NULL AND dirty_fields != ''"
            ).fetchall()
            return [
                (row["guid"], row["anki_note_id"], row["dirty_fields"], self._row_to_item(conn, row)) for row in rows
            ]
