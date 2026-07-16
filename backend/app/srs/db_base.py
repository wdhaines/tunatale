"""Shared infrastructure for the SRSDatabase mixin family.

Extracted verbatim from app/srs/database.py (god-module split, stage 3).
Holds connection/transaction management, the v0 bootstrap DDL, the shared
column/state constants, and the row->model mappers every concern mixin uses.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.models.srs_item import Direction, DirectionState, SRSItem, SRSState
from app.models.syntactic_unit import SyntacticUnit, deserialize_extras
from app.srs.anki_mirror.rollover import anki_day_bounds_utc
from app.srs.direction_fields import DIRECTION_FIELDS
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


# Single-sourced in app.srs.anki_mirror.rollover; the legacy name stays importable here
# for existing call sites and tests.
_anki_day_bounds_utc = anki_day_bounds_utc


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

_CREATE_LEMMA_ANALYSIS_CACHE = """
CREATE TABLE IF NOT EXISTS lemma_analysis_cache (
    sentence       TEXT NOT NULL,
    language_code  TEXT NOT NULL,
    model_version  TEXT NOT NULL,
    analyses_json  TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (sentence, language_code, model_version)
)
"""

_CREATE_IMAGE_QUERY_CACHE = """
CREATE TABLE IF NOT EXISTS image_query_cache (
    word           TEXT NOT NULL,
    english        TEXT NOT NULL,
    model_version  TEXT NOT NULL,
    query          TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (word, english, model_version)
)
"""

_CREATE_LESSON_LISTENS = """
CREATE TABLE IF NOT EXISTS lesson_listens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_id TEXT NOT NULL,
    listened_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'listen' CHECK(source IN ('listen','import'))
)
"""

# Columns on `collocation_directions` mapped onto a DirectionState.
# Derived from the field registry (app/srs/direction_fields.py) — register new
# columns there, never append here.
_DIR_COLUMNS = tuple(f.column for f in DIRECTION_FIELDS)

# States that should never surface in the due queue regardless of due_date.
_NON_REVIEWABLE_STATES = ("new", "suspended", "known", "buried")

# Column assignments that return a direction to a pristine NEW card (no FSRS
# history). Shared by `reset_collocation` and `set_state_by_id`'s NEW branch so
# the "what NEW means" definition lives in one place. `due_at = ?` is bound to
# today's 4 AM rollover by the caller (so the card reads due-today, not stuck).
_NEW_RESET_SET = (
    "state = 'new', stability = 1.0, fsrs_difficulty = 5.0, reps = 0, lapses = 0, due_at = ?, last_review = NULL"
)
# States that count as "in the learning bucket" (Anki queue=1 / queue=3).
# Shared by get_learning_items (review queue) and count_learning (badge).
_LEARNING_STATES = ("learning", "relearning")


def _configure_connection(conn: sqlite3.Connection) -> None:
    """Apply the standard pragmas to a fresh SQLite connection.

    - ``foreign_keys`` — enforce FK constraints (SQLite defaults them off).
    - ``busy_timeout`` — wait up to 5s for a lock instead of raising
      ``database is locked`` immediately. The live read endpoints
      (``/queue-stats``, ``/review-queue``) otherwise fail the instant a
      peer-sync holds a write transaction.
    - ``journal_mode = WAL`` — readers proceed against the last committed
      snapshot without blocking on the writer, so a slow sync (a big first
      import plus the optimize's divergence writes) doesn't lock out the UI.
      A no-op on ``:memory:`` connections (stays ``memory``).
    """
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")


class SRSDatabaseBase:
    """SQLite-backed SRS repository.

    Use `:memory:` as db_path for in-memory test databases.
    """

    def close(self) -> None:
        """Explicitly close the in-memory connection."""
        if self._in_memory and self._conn is not None:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> SRSDatabaseBase:
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def __init__(self, db_path: str = ":memory:") -> None:
        self._in_memory = db_path == ":memory:"
        if self._in_memory:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            _configure_connection(self._conn)
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
        conn.execute(_CREATE_LEMMA_ANALYSIS_CACHE)
        conn.execute(_CREATE_IMAGE_QUERY_CACHE)
        conn.execute(_CREATE_LESSON_LISTENS)
        conn.commit()

    @contextmanager
    def _file_conn(self):
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _configure_connection(conn)
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
            _configure_connection(conn)
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

    def _load_directions(self, conn: sqlite3.Connection, collocation_id: int) -> dict[Direction, DirectionState]:
        rows = conn.execute(
            f"SELECT direction, {', '.join(_DIR_COLUMNS)} FROM collocation_directions WHERE collocation_id = ?",
            (collocation_id,),
        ).fetchall()
        directions: dict[Direction, DirectionState] = {}
        for row in rows:
            d = Direction(row["direction"])
            due_at = (
                datetime.fromisoformat(row["due_at"])
                if row["due_at"]
                else datetime.fromisoformat("2026-01-01T04:00:00+00:00")
            )
            prior_state_raw = row["prior_state"]
            introduced_at_raw = row["introduced_at"]
            directions[d] = DirectionState(
                direction=d,
                due_at=due_at,
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
                prior_state=SRSState(prior_state_raw) if prior_state_raw else None,
                prior_left=row["prior_left"],
                prior_stability=row["prior_stability"],
                introduced_at=datetime.fromisoformat(introduced_at_raw) if introduced_at_raw else None,
                bury_kind=row["bury_kind"] if "bury_kind" in row.keys() else None,  # noqa: SIM118
                fsrs_force_next=bool(row["fsrs_force_next"]),
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
            article=row["article"] if "article" in row.keys() else "",  # noqa: SIM118
            extras=deserialize_extras(row["extras"] if "extras" in row.keys() else None),  # noqa: SIM118
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
