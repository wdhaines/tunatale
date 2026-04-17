"""Versioned SRS schema migrations keyed on PRAGMA user_version.

Each migration is a function taking a sqlite3.Connection and running inside
a single transaction. Migrations must be idempotent (safe to re-run).
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, timedelta

from app.common.guid import compute_guid

CURRENT_VERSION = 2


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return column in cols


def _get_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"PRAGMA user_version = {int(version)}")


def migrate_v0_to_v1(conn: sqlite3.Connection) -> None:
    """Add the lemma column + index to v0 collocations."""
    if _table_exists(conn, "collocations") and not _column_exists(conn, "collocations", "lemma"):
        conn.execute("ALTER TABLE collocations ADD COLUMN lemma TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_collocations_lemma ON collocations(lemma)")
    _set_version(conn, 1)


def _spread_production_due_date(text: str, today: date) -> date:
    """Deterministic spread across the next 30 days based on text hash."""
    h = int(hashlib.sha1(text.encode("utf-8")).hexdigest(), 16)
    return today + timedelta(days=h % 30)


def migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Split FSRS state into a child table; add guid + media + tags."""
    # Idempotency guard: if already migrated, just bump version.
    if _table_exists(conn, "collocation_directions"):
        _set_version(conn, 2)
        return

    # Drop indexes attached to the v1 table before renaming so fresh names
    # on the v2 table don't collide with their v1 counterparts.
    for idx in ("idx_collocations_due_date", "idx_collocations_state", "idx_collocations_lemma"):
        conn.execute(f"DROP INDEX IF EXISTS {idx}")

    conn.execute("ALTER TABLE collocations RENAME TO _collocations_v1")

    conn.execute("""
        CREATE TABLE collocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT UNIQUE NOT NULL,
            translation TEXT NOT NULL DEFAULT '',
            language_code TEXT NOT NULL DEFAULT 'sl',
            word_count INTEGER NOT NULL DEFAULT 1,
            unit_difficulty INTEGER NOT NULL DEFAULT 1,
            source TEXT NOT NULL DEFAULT 'corpus',
            corpus_frequency INTEGER NOT NULL DEFAULT 0,
            lemma TEXT,
            guid TEXT UNIQUE,
            anki_note_id INTEGER,
            dirty_fields TEXT NOT NULL DEFAULT '',
            last_synced_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX idx_collocations_lemma ON collocations(lemma)")
    conn.execute("CREATE INDEX idx_collocations_guid ON collocations(guid)")

    conn.execute("""
        CREATE TABLE collocation_directions (
            collocation_id INTEGER NOT NULL REFERENCES collocations(id) ON DELETE CASCADE,
            direction TEXT NOT NULL CHECK(direction IN ('recognition','production')),
            stability REAL NOT NULL DEFAULT 1.0,
            fsrs_difficulty REAL NOT NULL DEFAULT 5.0,
            due_date TEXT NOT NULL,
            reps INTEGER NOT NULL DEFAULT 0,
            lapses INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT 'new',
            last_review TEXT,
            anki_card_id INTEGER,
            dirty_fsrs INTEGER NOT NULL DEFAULT 0,
            last_synced_at TEXT,
            PRIMARY KEY (collocation_id, direction)
        )
    """)
    conn.execute("CREATE INDEX idx_directions_due_date ON collocation_directions(due_date)")
    conn.execute("CREATE INDEX idx_directions_state ON collocation_directions(state)")
    conn.execute("CREATE INDEX idx_directions_anki_card_id ON collocation_directions(anki_card_id)")

    conn.execute("""
        CREATE TABLE media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collocation_id INTEGER REFERENCES collocations(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK(kind IN ('image','audio_forvo','audio_tts')),
            filename TEXT NOT NULL,
            path TEXT,
            anki_filename TEXT,
            sha256 TEXT,
            bytes INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX idx_media_collocation ON media(collocation_id)")
    conn.execute("CREATE INDEX idx_media_anki_filename ON media(anki_filename)")

    conn.execute("""
        CREATE TABLE collocation_tags (
            collocation_id INTEGER NOT NULL REFERENCES collocations(id) ON DELETE CASCADE,
            tag TEXT NOT NULL,
            PRIMARY KEY (collocation_id, tag)
        )
    """)

    today = date.today()
    old_rows = conn.execute("SELECT * FROM _collocations_v1").fetchall()
    for row in old_rows:
        row_d = dict(row)
        guid = compute_guid(row_d["text"], row_d["language_code"])
        cursor = conn.execute(
            """
            INSERT INTO collocations
                (text, translation, language_code, word_count, unit_difficulty,
                 source, corpus_frequency, lemma, guid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_d["text"],
                row_d["translation"],
                row_d["language_code"],
                row_d["word_count"],
                row_d["unit_difficulty"],
                row_d["source"],
                row_d["corpus_frequency"],
                row_d.get("lemma"),
                guid,
            ),
        )
        new_id = cursor.lastrowid

        # Recognition direction: copy verbatim from v1 FSRS fields.
        conn.execute(
            """
            INSERT INTO collocation_directions
                (collocation_id, direction, stability, fsrs_difficulty, due_date,
                 reps, lapses, state, last_review)
            VALUES (?, 'recognition', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id,
                row_d["stability"],
                row_d["fsrs_difficulty"],
                row_d["due_date"],
                row_d["reps"],
                row_d["lapses"],
                row_d["state"],
                row_d["last_review"],
            ),
        )

        # Production direction: seeded new, due date spread across 30 days.
        prod_due = _spread_production_due_date(row_d["text"], today).isoformat()
        conn.execute(
            """
            INSERT INTO collocation_directions
                (collocation_id, direction, stability, fsrs_difficulty, due_date,
                 reps, lapses, state, last_review)
            VALUES (?, 'production', 1.0, 5.0, ?, 0, 0, 'new', NULL)
            """,
            (new_id, prod_due),
        )

    conn.execute("DROP TABLE _collocations_v1")
    _set_version(conn, 2)


_MIGRATIONS = {
    0: migrate_v0_to_v1,
    1: migrate_v1_to_v2,
}


def migrate(conn: sqlite3.Connection) -> None:
    """Run every pending migration in order, each inside its own transaction."""
    while True:
        version = _get_version(conn)
        if version >= CURRENT_VERSION:
            return
        migration = _MIGRATIONS.get(version)
        if migration is None:
            raise RuntimeError(f"No migration registered for version {version}")
        try:
            migration(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
