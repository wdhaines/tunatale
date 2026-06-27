"""SQLite repository for SRS collocations and violations.

Schema is managed by `app.srs.migrations`. Fresh DBs bootstrap the v0 base
tables (matching the pre-migration shape) and then `migrate()` runs every
pending step up to `CURRENT_VERSION`.

Supports ":memory:" for in-memory test databases.
"""

from __future__ import annotations

import sqlite3
import time as _time
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from app.common.guid import compute_guid
from app.config import ANKI_ROLLOVER_HOUR
from app.models.srs_item import (
    Direction,
    DirectionState,
    RevlogRow,
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


def _anki_day_bounds_utc(today: date, now: datetime | None = None) -> tuple[str, str]:
    """Return the UTC [start, end) ISO bounds of the Anki day anchored on `today`.

    The window runs from `ANKI_ROLLOVER_HOUR` local on `today` to the same hour
    the next day. When the wall-clock `now` is *before* today's rollover, the
    active Anki day is still yesterday's, so the anchor shifts back one day —
    matching what `_local_today_4am` does for sync-side counts. Counting on the
    local-midnight boundary instead silently sibling-buries cards graded in the
    `[midnight, rollover)` window that Anki still treats as graded yesterday
    (the 66-vs-73 review-badge divergence, 2026-06-02).
    """
    local_tz = datetime.now().astimezone().tzinfo
    now = (now or datetime.now(local_tz)).astimezone(local_tz)
    day_start = datetime.combine(today, time(ANKI_ROLLOVER_HOUR), tzinfo=local_tz)
    if now < day_start:
        day_start -= timedelta(days=1)
    return day_start.astimezone(UTC).isoformat(), (day_start + timedelta(days=1)).astimezone(UTC).isoformat()


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

# Columns on `collocation_directions` mapped onto a DirectionState.
# due_date dropped in v25 — due_at is the single source of truth.
_DIR_COLUMNS = (
    "stability",
    "fsrs_difficulty",
    "due_at",
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
    "prior_state",
    "prior_left",
    "prior_stability",
    "introduced_at",
    "bury_kind",
    "fsrs_force_next",
)

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

    # ── Write operations ───────────────────────────────────────────────

    def add_collocation(self, unit: SyntacticUnit, language_code: str = "sl") -> bool:
        """Insert a new collocation; if it already exists, backfill an empty translation.

        New rows get both recognition and production direction rows (defaults).
        Single-word units without an explicit lemma get lemma = casefolded text
        so that get_collocation_by_lemma_with_id lookups succeed. Empty strings
        count as missing — pre-Phase-F sync paths sometimes wrote empties.

        Returns True if a new row was inserted, False if it already existed.
        """
        if not unit.lemma and unit.word_count == 1:
            unit.lemma = unit.text.casefold()
        disambig = unit.disambig_key
        guid = compute_guid(unit.text, language_code, disambig)
        is_new = False
        with self._get_conn() as conn:
            # Identity is the case-normalized guid; legacy rows may carry a
            # stale guid that no longer matches the current compute_guid output,
            # so check guid first, then fall back to (text, language_code,
            # disambig_key) which is the actual UNIQUE constraint enforced by
            # the schema. Heal a stale guid in place when the fallback matches.
            existing = conn.execute(
                "SELECT id, guid, translation FROM collocations WHERE guid = ?",
                (guid,),
            ).fetchone()
            if existing is None:
                existing = conn.execute(
                    """
                    SELECT id, guid, translation FROM collocations
                    WHERE text = ? AND language_code = ? AND disambig_key = ?
                    """,
                    (unit.text, language_code, disambig),
                ).fetchone()
            if existing is None:
                is_new = True
                conn.execute(
                    """
                    INSERT INTO collocations
                        (text, translation, language_code, word_count, unit_difficulty,
                         source, corpus_frequency, lemma, guid, disambig_key, grammar, note,
                         source_sentence, sentence_translation, source_lesson_id, source_line_index, card_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                coll_id = conn.execute(
                    "SELECT id FROM collocations WHERE guid = ?",
                    (guid,),
                ).fetchone()["id"]
            else:
                coll_id = existing["id"]
                if existing["translation"] == "" and unit.translation:
                    conn.execute(
                        "UPDATE collocations SET translation = ? WHERE id = ?",
                        (unit.translation, coll_id),
                    )
                if existing["guid"] != guid:
                    conn.execute(
                        "UPDATE collocations SET guid = ? WHERE id = ?",
                        (guid, coll_id),
                    )
            if unit.card_type == "cloze":
                directions = [Direction.PRODUCTION]
            else:
                directions = [Direction.RECOGNITION, Direction.PRODUCTION]
            today_due_at = datetime.combine(date.today(), time(4, 0), tzinfo=UTC).isoformat()
            for direction in directions:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO collocation_directions
                        (collocation_id, direction, due_at)
                    VALUES (?, ?, ?)
                    """,
                    (coll_id, direction.value, today_due_at),
                )
            self._commit(conn)
        return is_new

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

    # ── Read operations ────────────────────────────────────────────────

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

    def get_collocation_id_by_guid(self, guid: str) -> int | None:
        """Return the collocation row id for a guid, or None."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT id FROM collocations WHERE guid = ?", (guid,)).fetchone()
            return row[0] if row else None

    def get_collocation_by_anki_note_id(self, anki_note_id: int) -> SRSItem | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM collocations WHERE anki_note_id = ? LIMIT 1", (anki_note_id,)).fetchone()
            if row is None:
                return None
            return self._row_to_item(conn, row)

    def list_linked_anki_note_ids(self) -> dict[int, int]:
        """Return {anki_note_id: collocation_id} for all linked notes.

        Used by sync_create_new to determine which Anki notes already have
        a TT row (reverse-import skips already-linked notes).
        """
        with self._get_conn() as conn:
            rows = conn.execute("SELECT id, anki_note_id FROM collocations WHERE anki_note_id IS NOT NULL").fetchall()
            return {row["anki_note_id"]: row["id"] for row in rows}

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

    def get_inflection_clozes_for_lemma(self, lemma: str) -> list[tuple[int, SRSItem]]:
        """All morphology (inflection) clozes for a lemma, hydrated with directions.

        Inflection clozes are card_type='cloze' with a disambig_key like 'morph:%'
        (set by the /listen morphology path and POST /inflection-clozes). This
        deliberately EXCLUDES the lemma's plain function-word base cloze, which
        has disambig_key NULL/empty.
        Returns (collocation_id, SRSItem) per row; empty list if none.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM collocations WHERE lemma = ? AND card_type = 'cloze' AND disambig_key LIKE 'morph:%'",
                (lemma,),
            ).fetchall()
            return [(row["id"], self._row_to_item(conn, row)) for row in rows]

    def get_collocations_with_lemma_key(
        self,
        language_code: str,
        min_word_count: int = 2,
    ) -> list[tuple[int, str, str | None]]:
        """Return (id, text, lemma_key) for collocations of at least min_word_count words.

        lemma_key is the space-joined lemma tuple for multi-word span matching
        (NULL until first computed). Read by transcript._build_collocation_index,
        which lazily fills any NULL via set_lemma_key.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, text, lemma_key FROM collocations WHERE language_code = ? AND word_count >= ?",
                (language_code, min_word_count),
            ).fetchall()
        return [(row["id"], row["text"], row["lemma_key"]) for row in rows]

    def set_lemma_key(self, row_id: int, lemma_key: str) -> None:
        """Persist the precomputed lemma_key for a collocation (span-match cache)."""
        with self._get_conn() as conn:
            conn.execute("UPDATE collocations SET lemma_key = ? WHERE id = ?", (lemma_key, row_id))
            self._commit(conn)

    def get_sentence_analysis(self, sentence: str, language_code: str, model_version: str) -> str | None:
        """Return cached analyses_json for a sentence, or None on miss."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT analyses_json FROM lemma_analysis_cache WHERE sentence = ? AND language_code = ? AND model_version = ?",
                (sentence, language_code, model_version),
            ).fetchone()
        return row["analyses_json"] if row else None

    def set_sentence_analysis(self, sentence: str, language_code: str, model_version: str, analyses_json: str) -> None:
        """Upsert a sentence analysis into the persistent cache."""
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO lemma_analysis_cache (sentence, language_code, model_version, analyses_json, updated_at)"
                " VALUES (?, ?, ?, ?, datetime('now'))",
                (sentence, language_code, model_version, analyses_json),
            )
            self._commit(conn)

    def get_image_query(self, word: str, english: str, model_version: str) -> str | None:
        """Return the cached image-search query for a card, or None on miss.

        An empty-string result is a *hit*, not a miss: it is the sentinel for
        "this word is abstract, don't fetch an image". Callers must check
        ``is not None`` rather than truthiness.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT query FROM image_query_cache WHERE word = ? AND english = ? AND model_version = ?",
                (word, english, model_version),
            ).fetchone()
        return row["query"] if row else None

    def set_image_query(self, word: str, english: str, model_version: str, query: str) -> None:
        """Upsert an image-search query (possibly the empty-string skip sentinel)."""
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO image_query_cache (word, english, model_version, query, updated_at)"
                " VALUES (?, ?, ?, ?, datetime('now'))",
                (word, english, model_version, query),
            )
            self._commit(conn)

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

    def update_direction_by_id(self, row_id: int, direction: Direction, state: DirectionState) -> None:
        """Persist direction state for a collocation identified by row id."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT guid FROM collocations WHERE id = ?", (row_id,)).fetchone()
            if row is None:
                return
        self.update_direction(row["guid"], direction, state)

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

    def get_sentence_audio_filename(self, collocation_id: int) -> str | None:
        """Return filename of the audio_tts_sentence media row, or None."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT filename FROM media WHERE collocation_id = ? AND kind = 'audio_tts_sentence' LIMIT 1",
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
        """Reset FSRS scheduling for one or both directions of a collocation.

        ``dirty_fsrs = 1`` so the reset propagates to Anki on the next
        ``sync_push`` (which forgets the card). Writing ``dirty_fsrs = 0`` left
        the reset TT-local: Anki kept the graduated review while TT showed a
        fresh NEW card — a permanent new-vs-review badge divergence that the
        next pull silently clobbered (2026-06-04). Mirrors
        ``set_state_by_id(NEW)``, which already marks dirty.
        """
        today_due_at = datetime.combine(date.today(), time(4, 0), tzinfo=UTC).isoformat()
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
            today_due_at = datetime.combine(date.today(), time(4, 0), tzinfo=UTC).isoformat()
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
        today_due_at = datetime.combine(date.today(), time(4, 0), tzinfo=UTC).isoformat()
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

    def add_ignored_lemma(self, language_code: str, lemma: str) -> None:
        """Add a lemma to the card-less ignore list (idempotent)."""
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO ignored_lemmas (language_code, lemma) VALUES (?, ?)",
                (language_code, lemma.lower()),
            )
            self._commit(conn)

    def remove_ignored_lemma(self, language_code: str, lemma: str) -> None:
        """Remove a lemma from the card-less ignore list (idempotent)."""
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM ignored_lemmas WHERE language_code = ? AND lemma = ?",
                (language_code, lemma.lower()),
            )
            self._commit(conn)

    def get_ignored_lemmas(self, language_code: str) -> set[str]:
        """Return the set of ignored lemmas for a language."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT lemma FROM ignored_lemmas WHERE language_code = ?",
                (language_code,),
            ).fetchall()
            return {r["lemma"] for r in rows}

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
            "due_date": "due_at",
            "due_at": "due_at",
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
                         source, corpus_frequency, lemma, guid, anki_note_id, disambig_key,
                         grammar, note, source_sentence, sentence_translation,
                         source_lesson_id, source_line_index, card_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        unit.grammar,
                        unit.note,
                        unit.source_sentence,
                        unit.source_sentence_translation,
                        unit.source_lesson_id,
                        unit.source_line_index,
                        unit.card_type,
                    ),
                )
                coll_id = cursor.lastrowid
                for direction, state in directions.items():
                    conn.execute(
                        """
                        INSERT INTO collocation_directions
                            (collocation_id, direction, stability, fsrs_difficulty, due_at,
                             reps, lapses, state, last_review, anki_card_id, anki_due, left)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            coll_id,
                            direction.value,
                            state.stability,
                            state.difficulty,
                            state.due_at.isoformat(),
                            state.reps,
                            state.lapses,
                            state.state.value,
                            state.last_review.isoformat() if state.last_review else None,
                            state.anki_card_id,
                            state.anki_due,
                            state.left,
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
                                (collocation_id, direction, stability, fsrs_difficulty, due_at,
                                 reps, lapses, state, last_review, anki_card_id, anki_due, left)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                coll_id,
                                direction.value,
                                state.stability,
                                state.difficulty,
                                state.due_at.isoformat(),
                                state.reps,
                                state.lapses,
                                state.state.value,
                                state.last_review.isoformat() if state.last_review else None,
                                state.anki_card_id,
                                state.anki_due,
                                state.left,
                            ),
                        )
                    elif dir_row["reps"] > 0:
                        conn.execute(
                            """
                            UPDATE collocation_directions SET
                                state = ?, due_at = ?, anki_card_id = ?,
                                anki_due = ?, left = ?
                            WHERE collocation_id = ? AND direction = ?
                            """,
                            (
                                state.state.value,
                                state.due_at.isoformat(),
                                state.anki_card_id,
                                state.anki_due,
                                state.left,
                                coll_id,
                                direction.value,
                            ),
                        )
                    else:
                        conn.execute(
                            """
                            UPDATE collocation_directions SET
                                stability = ?, fsrs_difficulty = ?, due_at = ?,
                                reps = ?, lapses = ?, state = ?, last_review = ?,
                                anki_card_id = ?, anki_due = ?, left = ?
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
                                state.anki_card_id,
                                state.anki_due,
                                state.left,
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

    def delete_collocations_for_graves(self, *, grave_note_ids: set[int]) -> list[str]:
        """Hard-delete collocations whose Anki note is in the graves table.

        A note grave means the user deleted the note in Anki on purpose; honor
        it by removing the TT collocation (FK cascade also drops its directions
        and media) rather than resurrecting it on the next push. Counterpart to
        the recovery path in ``reset_orphaned_anki_ids`` — the caller routes a
        missing note here only when it carries a grave. Returns deleted guids.
        """
        if not grave_note_ids:
            return []
        placeholders = ",".join("?" * len(grave_note_ids))
        with self._get_conn() as conn:
            rows = conn.execute(
                f"SELECT id, guid, text FROM collocations WHERE anki_note_id IN ({placeholders})",
                list(grave_note_ids),
            ).fetchall()
            if not rows:
                return []
            row_ids = [r["id"] for r in rows]
            texts = [r["text"] for r in rows]
            text_ph = ",".join("?" * len(texts))
            conn.execute(f"DELETE FROM violations WHERE collocation_text IN ({text_ph})", texts)
            id_ph = ",".join("?" * len(row_ids))
            conn.execute(f"DELETE FROM collocations WHERE id IN ({id_ph})", row_ids)
            self._commit(conn)
            return [r["guid"] for r in rows]

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

        Empty keep set is treated as a no-op — defense against accidentally
        nuking all rows when the caller's per-pass tracking failed to record
        anything. Use ``delete_all_media_for_kind`` for the intentional
        "kind vanished from the note" case.
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

    def delete_all_media_for_kind(self, collocation_id: int, kind: str) -> int:
        """Delete every media row of ``kind`` on this collocation. Returns
        the number of rows deleted.

        Distinct from ``delete_stale_media_for_kind(..., set())`` (which is a
        defensive no-op): this method is the explicit collapse path used when
        a note no longer references any media of a given kind. The canonical
        case is a note whose image field switched from ``<img src="paste-…">``
        to ``<img src="data:…">`` (per RFC 2397 the latter has no file in
        ``collection.media/``); the prior file row must collapse so the UI
        stops serving the old picture.
        """
        with self._get_conn() as conn:
            cur = conn.execute(
                "DELETE FROM media WHERE collocation_id = ? AND kind = ?",
                (collocation_id, kind),
            )
            self._commit(conn)
            return cur.rowcount

    def list_media_kinds_for_collocation(self, collocation_id: int) -> set[str]:
        """Return the set of distinct media kinds currently recorded on this
        collocation. Used by the refresh-media path to decide which kinds need
        a cleanup pass — including kinds that have vanished from the Anki note
        (otherwise their stale rows would persist forever).
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT kind FROM media WHERE collocation_id = ?",
                (collocation_id,),
            ).fetchall()
        return {r[0] for r in rows}

    def find_media_by_sha256(self, collocation_id: int, kind: str, sha256: str) -> dict[str, Any] | None:
        """Return the media row matching ``(collocation_id, kind, sha256)``, or None.

        Used by the refresh-media path to recognize inline (``data:`` URI)
        images on re-import: those have no Anki filename to dedupe against, so
        we identify them content-wise instead.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM media WHERE collocation_id = ? AND kind = ? AND sha256 = ?",
                (collocation_id, kind, sha256),
            ).fetchone()
        return dict(row) if row is not None else None

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
                due_at = datetime.fromisoformat(row["due_at"])
                prior_state_raw = row["prior_state"]
                ds = DirectionState(
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
                    dirty_fsrs=bool(row["dirty_fsrs"]),
                    last_synced_at=row["last_synced_at"],
                    last_rating=row["last_rating"],
                    left=row["left"],
                    prior_state=SRSState(prior_state_raw) if prior_state_raw else None,
                    prior_left=row["prior_left"],
                    prior_stability=row["prior_stability"],
                    bury_kind=row["bury_kind"],
                    # Load-bearing for the push loop's row_force_fsrs decision —
                    # without it a restored direction never force-writes its
                    # stability to Anki (the same silent-False trap as bury_kind).
                    fsrs_force_next=bool(row["fsrs_force_next"]),
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
                due_at = datetime.fromisoformat(row["due_at"])
                prior_state_raw = row["prior_state"]
                ds = DirectionState(
                    direction=d,
                    due_at=due_at,
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
                    prior_state=SRSState(prior_state_raw) if prior_state_raw else None,
                    prior_left=row["prior_left"],
                    prior_stability=row["prior_stability"],
                    bury_kind=row["bury_kind"],
                    # fsrs_force_next is load-bearing here: the push loop reads it
                    # off the list_recently_graded_clean DirectionState for the
                    # row_force_fsrs decision. Same silent-False trap as bury_kind.
                    fsrs_force_next=bool(row["fsrs_force_next"]),
                )
                result.append((row["guid"], d, ds))
        return result

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
                    fsrs_force_next = 0,
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

    def count_new_available(self) -> int:
        """Count all collocation_directions rows in the NEW state (both directions).

        Raw, bury-unaware total. Used as the upper bound for the per-direction
        new-pool overfetch in ``_compute_live_main``. The badge uses the
        bury-aware ``count_new_available_collocations`` instead.
        """
        with self._get_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM collocation_directions WHERE state = 'new'").fetchone()[0]

    def count_new_available_collocations(self, today: date) -> int:
        """Count distinct collocations with a NEW direction Anki would NOT bury
        out of today's new queue. Mirror image of ``count_review_due_collocations``.

        Anki buries a new card at queue-build when ``bury_new`` is set and a
        sibling was already gathered into today's queue. Gather order is
        learning → review → new (`builder/gathering.rs:14-21`), so a new card is
        buried whenever a sibling is:

        1. **Graded today** — grading any sibling buries the new card with
           ``queue=-2`` at grade time; that bury persists until the day
           rollover, so it still applies even when the graded sibling's review
           was pushed to a *future* due date (the ``last_review today`` clause).
        2. **In learning/relearning** — learning cards are gathered first
           (`add_new_card` then sees the note as already-seen and buries it).
        3. **A review due today** — gathered in the review phase, so the new
           sibling is buried. A *future*-due review sibling is NOT gathered and
           does NOT bury (verified against the Anki binary): pushing the
           sibling's ``due`` forward flips Anki's ``counts.new`` 0 → 1.

        ``COUNT(DISTINCT collocation_id)`` collapses a both-new note to one,
        mirroring Anki burying the second new sibling. Only meaningful when
        ``bury_new`` is set — the caller falls back to ``count_new_available``
        otherwise. (`_compute_live_main` already applies the same bury to the
        served queue; this keeps the badge consistent with it.)
        """
        start_iso, end_iso = _anki_day_bounds_utc(today)
        end_of_day_utc = datetime.combine(today, time.max).isoformat()
        with self._get_conn() as conn:
            return conn.execute(
                """
                SELECT COUNT(DISTINCT cd.collocation_id) FROM collocation_directions cd
                WHERE cd.state = 'new'
                  AND cd.collocation_id NOT IN (
                    SELECT collocation_id FROM collocation_directions
                    WHERE (length(last_review) > 10 AND last_review >= ? AND last_review < ?)
                       OR (length(last_review) = 10 AND last_review = ?)
                       OR state IN ('learning', 'relearning')
                       OR (state = 'review' AND due_at <= ?)
                  )
                """,
                (start_iso, end_iso, today.isoformat(), end_of_day_utc),
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

    def count_review_due_collocations(self, today: date) -> int:
        """Count distinct collocations with at least one review-state direction
        due today, excluding those Anki would bury out of today's review pool.

        Anki's `bury_reviews=true` removes a note from today's review pool when
        any sibling is active. Two triggers mirror that:

        1. **Graded today** — once any direction is graded, the un-graded
           sibling goes to queue=-2 until tomorrow. Exclude collocations whose
           `last_review` for any direction falls within today's local day, so
           the badge decrements by 1 (not 2) when one direction of a dual
           note is graded.
        2. **Sibling in the learning queue** — Anki also buries the review
           card whenever its sibling sits in learning/relearning (queue=1/3),
           *including interday learning steps graded on a prior day*. The
           "graded today" filter alone misses those, over-counting the badge
           (the observed 214→208 gap was exactly the notes with a learning
           sibling). Exclude collocations with any direction in
           learning/relearning regardless of when it was last graded.

        Together these match Anki's deck-overview review count when both apps
        share the same data.
        """
        start_iso, end_iso = _anki_day_bounds_utc(today)
        end_of_day_utc = datetime.combine(today, time.max).isoformat()
        with self._get_conn() as conn:
            return conn.execute(
                """
                SELECT COUNT(DISTINCT cd.collocation_id) FROM collocation_directions cd
                WHERE cd.due_at <= ? AND cd.state = 'review'
                  AND cd.collocation_id NOT IN (
                    SELECT collocation_id FROM collocation_directions
                    WHERE (length(last_review) > 10 AND last_review >= ? AND last_review < ?)
                       OR (length(last_review) = 10 AND last_review = ?)
                       OR state IN ('learning', 'relearning')
                  )
                """,
                (end_of_day_utc, start_iso, end_iso, today.isoformat()),
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
        start_iso, end_iso = _anki_day_bounds_utc(today)
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(DISTINCT collocation_id) FROM collocation_directions
                WHERE introduced_at IS NOT NULL
                  AND introduced_at >= ?
                  AND introduced_at < ?
                """,
                (start_iso, end_iso),
            ).fetchone()
            return row[0] if row else 0

    def count_reviews_completed_today(self, today: date) -> int:
        """Count distinct collocation_directions with a review completed today.

        Filters on state IN ('review', 'relearning') with a last_review that
        falls within today's local-day window and a non-null last_rating
        (filters out new-card introductions that happened to land today).
        Mirrors Anki's 'reviews done today' derived from revlog.
        """
        start_iso, end_iso = _anki_day_bounds_utc(today)
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(DISTINCT collocation_id || '-' || direction)
                FROM collocation_directions
                WHERE state IN ('review', 'relearning')
                  AND last_review IS NOT NULL
                  AND last_review >= ?
                  AND last_review < ?
                  AND last_rating IS NOT NULL
                """,
                (start_iso, end_iso),
            ).fetchone()
            return row[0] if row else 0

    def count_due_collocations(
        self,
        as_of: date,
        direction: Direction = Direction.RECOGNITION,
    ) -> int:
        placeholders = ",".join("?" * len(_NON_REVIEWABLE_STATES))
        # End-of-day cutoff: any due_at strictly before (as_of + 1 day) midnight UTC counts.
        cutoff = datetime.combine(as_of + timedelta(days=1), time(0, 0), tzinfo=UTC).isoformat()
        with self._get_conn() as conn:
            return conn.execute(
                f"""
                SELECT COUNT(DISTINCT c.id) FROM collocations c
                JOIN collocation_directions d ON d.collocation_id = c.id
                WHERE d.direction = ?
                  AND d.due_at < ?
                  AND d.state NOT IN ({placeholders})
                """,
                (direction.value, cutoff, *_NON_REVIEWABLE_STATES),
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

    def set_dirty_fields(self, guid: str, fields_str: str) -> None:
        """Set dirty_fields for the collocation identified by guid."""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE collocations SET dirty_fields = ? WHERE guid = ?",
                (fields_str, guid),
            )
            self._commit(conn)

    def add_dirty_field(self, guid: str, field: str) -> None:
        """Append *field* to the comma-separated dirty_fields set (no dupes)."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT dirty_fields FROM collocations WHERE guid = ?", (guid,)).fetchone()
            if row is None:
                return
            existing = {f for f in (row["dirty_fields"] or "").split(",") if f}
            existing.add(field)
            conn.execute(
                "UPDATE collocations SET dirty_fields = ? WHERE guid = ?",
                (",".join(sorted(existing)), guid),
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

    def set_sentence_translation_dirty(self, guid: str, sentence_translation: str) -> None:
        """Update sentence_translation for `guid` and append `sentence_translation` to dirty_fields.

        Called by the /listen backfill path and the one-shot backfill script.
        Marks dirty so sync_push will rewrite the cloze note's Back Extra field
        on the next push.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT dirty_fields FROM collocations WHERE guid = ?",
                (guid,),
            ).fetchone()
            if row is None:
                return
            existing = {f for f in (row["dirty_fields"] or "").split(",") if f}
            existing.add("sentence_translation")
            conn.execute(
                "UPDATE collocations SET sentence_translation = ?, dirty_fields = ?, "
                "updated_at = datetime('now') WHERE guid = ?",
                (sentence_translation, ",".join(sorted(existing)), guid),
            )
            self._commit(conn)

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

    def list_items_without_anki_note(self) -> list[tuple[str, SRSItem, int]]:
        """Return (guid, SRSItem, id) for collocations with no anki_note_id set."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM collocations WHERE anki_note_id IS NULL").fetchall()
            return [(row["guid"], self._row_to_item(conn, row), row["id"]) for row in rows]

    def list_dirty_field_edits(self) -> list[tuple[str, int | None, str, SRSItem, int]]:
        """Return (guid, anki_note_id, dirty_fields_str, SRSItem, id) for rows with non-empty dirty_fields."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM collocations WHERE dirty_fields IS NOT NULL AND dirty_fields != ''"
            ).fetchall()
            return [
                (row["guid"], row["anki_note_id"], row["dirty_fields"], self._row_to_item(conn, row), row["id"])
                for row in rows
            ]

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
                     last_interval, factor, taken_millis, review_kind, anki_card_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            )

        guid = coll["guid"] if coll else None
        anki_note_id = coll["anki_note_id"] if coll else None
        card_type = coll["card_type"] or "vocab" if coll else "vocab"

        other_dir = Direction.PRODUCTION if direction == Direction.RECOGNITION else Direction.RECOGNITION
        now_4am = datetime.combine(date.today(), time(ANKI_ROLLOVER_HOUR, 0), tzinfo=UTC)
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
