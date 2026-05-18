"""Versioned SRS schema migrations keyed on PRAGMA user_version.

Each migration is a function taking a sqlite3.Connection and running inside
a single transaction. Migrations must be idempotent (safe to re-run).
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date

from app.common.guid import compute_guid

CURRENT_VERSION = 23

_SUFFIX_RE = re.compile(r"^(.+?)\s\((.+)\)$")


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

        conn.execute(
            """
            INSERT INTO collocation_directions
                (collocation_id, direction, stability, fsrs_difficulty, due_date,
                 reps, lapses, state, last_review)
            VALUES (?, 'production', 1.0, 5.0, ?, 0, 0, 'new', NULL)
            """,
            (new_id, today.isoformat()),
        )

    conn.execute("DROP TABLE _collocations_v1")
    _set_version(conn, 2)


def migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Add disambig_key; change UNIQUE(text) → UNIQUE(text, disambig_key); recompute all guids."""
    if _column_exists(conn, "collocations", "disambig_key"):
        _set_version(conn, 3)
        return

    # SQLite 3.26+ rewrites FK references on RENAME TABLE. Renaming "collocations"
    # to a temp name would corrupt FKs in collocation_directions/media/collocation_tags.
    # Workaround: create a new table, copy data, DROP the old table (no FK rewrite on
    # DROP), then RENAME the new table back to "collocations" (no FK rewrite since no
    # child table references the temp name).
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for idx in ("idx_collocations_lemma", "idx_collocations_guid"):
            conn.execute(f"DROP INDEX IF EXISTS {idx}")

        conn.execute("""
            CREATE TABLE _collocations_v3 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                translation TEXT NOT NULL DEFAULT '',
                language_code TEXT NOT NULL DEFAULT 'sl',
                word_count INTEGER NOT NULL DEFAULT 1,
                unit_difficulty INTEGER NOT NULL DEFAULT 1,
                source TEXT NOT NULL DEFAULT 'corpus',
                corpus_frequency INTEGER NOT NULL DEFAULT 0,
                lemma TEXT,
                guid TEXT UNIQUE,
                disambig_key TEXT NOT NULL DEFAULT '',
                anki_note_id INTEGER,
                dirty_fields TEXT NOT NULL DEFAULT '',
                last_synced_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(text, disambig_key)
            )
        """)

        old_rows = conn.execute("SELECT * FROM collocations").fetchall()
        for row in old_rows:
            row_d = dict(row)
            m = _SUFFIX_RE.match(row_d["text"])
            if m:
                bare_text = m.group(1)
                disambig = m.group(2)
            else:
                bare_text = row_d["text"]
                disambig = ""
            new_guid = compute_guid(bare_text, row_d["language_code"], disambig)
            conn.execute(
                """
                INSERT INTO _collocations_v3
                    (id, text, translation, language_code, word_count, unit_difficulty,
                     source, corpus_frequency, lemma, guid, disambig_key, anki_note_id,
                     dirty_fields, last_synced_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_d["id"],
                    bare_text,
                    row_d["translation"],
                    row_d["language_code"],
                    row_d["word_count"],
                    row_d["unit_difficulty"],
                    row_d["source"],
                    row_d["corpus_frequency"],
                    row_d.get("lemma"),
                    new_guid,
                    disambig,
                    row_d.get("anki_note_id"),
                    row_d.get("dirty_fields", ""),
                    row_d.get("last_synced_at"),
                    row_d.get("created_at"),
                    row_d.get("updated_at"),
                ),
            )

        conn.execute("DROP TABLE collocations")
        conn.execute("ALTER TABLE _collocations_v3 RENAME TO collocations")
        conn.execute("CREATE INDEX idx_collocations_lemma ON collocations(lemma)")
        conn.execute("CREATE INDEX idx_collocations_guid ON collocations(guid)")

        _set_version(conn, 3)
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    """Repair child-table FK references corrupted by the pre-H2 v2→v3 migration.

    An earlier version of migrate_v2_to_v3 did ALTER TABLE collocations RENAME TO
    _collocations_v2 as its first step. SQLite 3.26+ auto-rewrites FK references in
    child tables on RENAME, leaving them pointing to the now-dropped _collocations_v2.
    Fresh DBs migrated with the fixed code are unaffected; only the live DB needs repair.
    """
    broken = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE '%_collocations_v2%'"
    ).fetchone()
    if broken is None:
        _set_version(conn, 4)
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("""
            CREATE TABLE _cd_fix (
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
        conn.execute("""
            INSERT INTO _cd_fix
                (collocation_id, direction, stability, fsrs_difficulty, due_date,
                 reps, lapses, state, last_review, anki_card_id, dirty_fsrs, last_synced_at)
            SELECT collocation_id, direction, stability, fsrs_difficulty, due_date,
                   reps, lapses, state, last_review, anki_card_id, dirty_fsrs, last_synced_at
            FROM collocation_directions
        """)
        for idx in ("idx_directions_due_date", "idx_directions_state", "idx_directions_anki_card_id"):
            conn.execute(f"DROP INDEX IF EXISTS {idx}")
        conn.execute("DROP TABLE collocation_directions")
        conn.execute("ALTER TABLE _cd_fix RENAME TO collocation_directions")
        conn.execute("CREATE INDEX idx_directions_due_date ON collocation_directions(due_date)")
        conn.execute("CREATE INDEX idx_directions_state ON collocation_directions(state)")
        conn.execute("CREATE INDEX idx_directions_anki_card_id ON collocation_directions(anki_card_id)")

        conn.execute("""
            CREATE TABLE _media_fix (
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
        conn.execute("""
            INSERT INTO _media_fix
                (id, collocation_id, kind, filename, path, anki_filename, sha256, bytes, created_at)
            SELECT id, collocation_id, kind, filename, path, anki_filename, sha256, bytes, created_at
            FROM media
        """)
        for idx in ("idx_media_collocation", "idx_media_anki_filename"):
            conn.execute(f"DROP INDEX IF EXISTS {idx}")
        conn.execute("DROP TABLE media")
        conn.execute("ALTER TABLE _media_fix RENAME TO media")
        conn.execute("CREATE INDEX idx_media_collocation ON media(collocation_id)")
        conn.execute("CREATE INDEX idx_media_anki_filename ON media(anki_filename)")

        conn.execute("""
            CREATE TABLE _ct_fix (
                collocation_id INTEGER NOT NULL REFERENCES collocations(id) ON DELETE CASCADE,
                tag TEXT NOT NULL,
                PRIMARY KEY (collocation_id, tag)
            )
        """)
        conn.execute("""
            INSERT INTO _ct_fix (collocation_id, tag)
            SELECT collocation_id, tag FROM collocation_tags
        """)
        conn.execute("DROP TABLE collocation_tags")
        conn.execute("ALTER TABLE _ct_fix RENAME TO collocation_tags")

        fk_issues = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_issues:
            raise RuntimeError(f"FK check failed after v3→v4 repair: {fk_issues}")

        _set_version(conn, 4)
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    """Add last_rating INTEGER to collocation_directions (nullable, default NULL)."""
    if not _column_exists(conn, "collocation_directions", "last_rating"):
        conn.execute("ALTER TABLE collocation_directions ADD COLUMN last_rating INTEGER")
    _set_version(conn, 5)


def migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    """Add anki_due INTEGER (nullable) to collocation_directions for new-card ordering."""
    if not _column_exists(conn, "collocation_directions", "anki_due"):
        conn.execute("ALTER TABLE collocation_directions ADD COLUMN anki_due INTEGER")
    _set_version(conn, 6)


def migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    """Add grammar and note TEXT columns to collocations (default '')."""
    for col in ("grammar", "note"):
        if not _column_exists(conn, "collocations", col):
            conn.execute(f"ALTER TABLE collocations ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    _set_version(conn, 7)


def migrate_v7_to_v8(conn: sqlite3.Connection) -> None:
    """Add source context columns to collocations for LingQ-style capture."""
    if not _column_exists(conn, "collocations", "source_sentence"):
        conn.execute("ALTER TABLE collocations ADD COLUMN source_sentence TEXT NOT NULL DEFAULT ''")
    if not _column_exists(conn, "collocations", "source_lesson_id"):
        conn.execute("ALTER TABLE collocations ADD COLUMN source_lesson_id TEXT")
    if not _column_exists(conn, "collocations", "source_line_index"):
        conn.execute("ALTER TABLE collocations ADD COLUMN source_line_index INTEGER")
    _set_version(conn, 8)


def migrate_v8_to_v9(conn: sqlite3.Connection) -> None:
    """Drop pending_revlog table (no longer used after removing online mode)."""
    conn.execute("DROP TABLE IF EXISTS pending_revlog")
    conn.execute("DROP INDEX IF EXISTS idx_pending_revlog_cid")
    _set_version(conn, 9)


def migrate_v9_to_v10(conn: sqlite3.Connection) -> None:
    """Add last_review_time_ms INTEGER column to collocation_directions (default 0)."""
    if not _column_exists(conn, "collocation_directions", "last_review_time_ms"):
        conn.execute("ALTER TABLE collocation_directions ADD COLUMN last_review_time_ms INTEGER NOT NULL DEFAULT 0")
    _set_version(conn, 10)


def migrate_v10_to_v11(conn: sqlite3.Connection) -> None:
    """Add left INTEGER and due_at TEXT columns to collocation_directions for learning steps."""
    if not _column_exists(conn, "collocation_directions", "left"):
        conn.execute("ALTER TABLE collocation_directions ADD COLUMN left INTEGER")
    if not _column_exists(conn, "collocation_directions", "due_at"):
        conn.execute("ALTER TABLE collocation_directions ADD COLUMN due_at TEXT")
    _set_version(conn, 11)


def migrate_v11_to_v12(conn: sqlite3.Connection) -> None:
    """Repair invariant: state='new' implies last_review IS NULL.

    Companion fix to ``parse_fsrs_data`` (sqlite_reader.py) which previously
    synthesized ``last_review`` from due/ivl even when reps=0. Anki cards in
    queue=2 with reps=0 (e.g. from FSRS imports or raw uploads) leaked through
    that path and produced rows with state='new' AND last_review set, which
    then displayed wrong review-count widgets.

    The Python-side fix prevents new occurrences. This migration repairs any
    existing rows. Idempotent: matches 0 rows on the second run.
    """
    conn.execute(
        "UPDATE collocation_directions "
        "SET last_review = NULL "
        "WHERE state = 'new' AND last_review IS NOT NULL AND reps = 0"
    )
    _set_version(conn, 12)


def migrate_v12_to_v13(conn: sqlite3.Connection) -> None:
    """Add prior_state, prior_left, prior_stability to collocation_directions.

    These columns snapshot the pre-grade direction state so the Anki sync push
    can emit a revlog row whose (type, ivl, lastIvl) reflect the actual
    transition (e.g. REVIEW + Again → RELEARNING with a 10-min step). Without
    them, push falls back to a hardcoded type=2/positive-ivl shape that leaves
    Anki unable to reconstruct the user's prior step on the next UI rating.
    """
    if not _column_exists(conn, "collocation_directions", "prior_state"):
        conn.execute("ALTER TABLE collocation_directions ADD COLUMN prior_state TEXT")
    if not _column_exists(conn, "collocation_directions", "prior_left"):
        conn.execute("ALTER TABLE collocation_directions ADD COLUMN prior_left INTEGER")
    if not _column_exists(conn, "collocation_directions", "prior_stability"):
        conn.execute("ALTER TABLE collocation_directions ADD COLUMN prior_stability REAL")
    _set_version(conn, 13)


def migrate_v14_to_v15(conn: sqlite3.Connection) -> None:
    """Fill lemma for single-word rows that lack it.

    Existing rows imported before lemma was tracked have lemma=NULL, which
    breaks get_collocation_by_lemma_with_id lookups in transcript extraction.
    The casefold() normalization matches compute_guid() and add_collocation().
    """
    conn.execute(
        "UPDATE collocations SET lemma = CASE WHEN word_count = 1 THEN LOWER(text) ELSE lemma END WHERE lemma IS NULL",
    )
    _set_version(conn, 15)


def migrate_v13_to_v14(conn: sqlite3.Connection) -> None:
    """Add `anki_card_mod` to collocation_directions.

    Mirrors Anki's `cards.mod` so the review-queue sort can match Anki's
    secondary tiebreak under RetrievabilityAscending: `fnvhash(id, mod)`.
    Without it, two cards with identical FSRS state diverge in head-of-queue
    order between TT and Anki. Populated by sync_pull from cards.mod.
    """
    if not _column_exists(conn, "collocation_directions", "anki_card_mod"):
        conn.execute("ALTER TABLE collocation_directions ADD COLUMN anki_card_mod INTEGER")
    _set_version(conn, 14)


def migrate_v15_to_v16(conn: sqlite3.Connection) -> None:
    """Delete phantom direction rows left over from the auto-fill bug.

    Pre-fix `_build_directions` invented a default DirectionState for any
    direction whose Anki notetype had no template at that ord — most visibly
    for phonics on the "Basic" notetype, which produced production-side
    rows with `anki_card_id IS NULL` per import. The fix removes the auto-
    fill; this migration sweeps up the residue.

    Safe-to-delete criteria (intersection of all):
      - direction has `anki_card_id IS NULL` (no Anki card backs this row)
      - parent collocation has `anki_note_id IS NOT NULL` (synced from Anki,
        so the missing card is not "pending creation")
      - `reps = 0` and `dirty_fsrs = 0` (no review history, no pending push)

    User-added rows awaiting their first sync_create_new (`anki_note_id IS
    NULL`) are preserved.
    """
    conn.execute(
        """
        DELETE FROM collocation_directions
        WHERE anki_card_id IS NULL
          AND reps = 0
          AND dirty_fsrs = 0
          AND collocation_id IN (
            SELECT id FROM collocations WHERE anki_note_id IS NOT NULL
          )
        """,
    )
    _set_version(conn, 16)


def migrate_v16_to_v17(conn: sqlite3.Connection) -> None:
    """Index collocations.created_at for the Phase C recency-prioritized new queue.

    get_new_items ORDER BY now leads with c.created_at DESC so freshly auto-added
    cards from /listen surface ahead of the imported Anki backlog. Without an index
    on created_at, every queue rebuild does a full sort. See docs/anki-parity-layers.md
    Layer 24 for the rationale (intentional divergence from Anki's due-position
    ordering).
    """
    conn.execute("CREATE INDEX IF NOT EXISTS idx_collocations_created_at ON collocations(created_at)")
    _set_version(conn, 17)


def migrate_v17_to_v18(conn: sqlite3.Connection) -> None:
    """Add collocation_directions.introduced_at for the Layer 26 first-grade marker.

    `count_new_introduced_today` previously filtered on `prior_state='new' AND
    last_review today` — over-counting sticky-NEW cards whose introduction was
    on a prior day but happened to be reviewed again today. With `introduced_at`
    set only on the first NEW→non-NEW transition, the count now mirrors Anki's
    `newToday` counter exactly. Existing rows get NULL (no backfill); they
    naturally fall out of the count, restoring the historical "introduced 0
    today" state until grades populate the column going forward.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(collocation_directions)")}
    if "introduced_at" not in cols:
        conn.execute("ALTER TABLE collocation_directions ADD COLUMN introduced_at TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_directions_introduced_at ON collocation_directions(introduced_at)")
    _set_version(conn, 18)


def migrate_v18_to_v19(conn: sqlite3.Connection) -> None:
    """Add collocations.card_type to support Phase F cloze cards.

    'vocab' (default): existing Slovene Vocabulary notetype path.
    'cloze': Anki built-in Cloze notetype with {{c1::surface}} in source_sentence.
    """
    if not _column_exists(conn, "collocations", "card_type"):
        conn.execute("ALTER TABLE collocations ADD COLUMN card_type TEXT DEFAULT 'vocab'")
    _set_version(conn, 19)


def migrate_v19_to_v20(conn: sqlite3.Connection) -> None:
    """Add collocation_directions.bury_kind to distinguish sched vs user bury.

    Anki has two bury kinds: ``queue=-3`` (sched/sibling bury, auto-unburied at
    rollover) and ``queue=-2`` (user/manual bury, sticks until manually
    unburied). Before this migration TT collapsed both into ``state='buried'``
    and the daily ``unbury_if_needed`` sweep wiped them all — surfacing
    manually-buried Anki cards as reviewable in TT.

    Backfill: every existing ``state='buried'`` row gets ``bury_kind='user'``.
    Pessimistic but safe — sibling-buried rows from the current day would
    survive until the next sync_pull rewrites the kind from Anki's queue value.
    """
    if not _column_exists(conn, "collocation_directions", "bury_kind"):
        conn.execute("ALTER TABLE collocation_directions ADD COLUMN bury_kind TEXT")
    conn.execute("UPDATE collocation_directions SET bury_kind = 'user' WHERE state = 'buried' AND bury_kind IS NULL")
    _set_version(conn, 20)


def migrate_v20_to_v21(conn: sqlite3.Connection) -> None:
    """Add sentence_translation column to collocations for cloze sentence-level English.

    Stores the English translation of the full source sentence (e.g. "It's open
    every day" for source_sentence "Odprto je vsak dan"). Populated at cloze
    creation time from lesson generation_metadata. Default empty string for
    existing rows.
    """
    if not _column_exists(conn, "collocations", "sentence_translation"):
        conn.execute("ALTER TABLE collocations ADD COLUMN sentence_translation TEXT NOT NULL DEFAULT ''")
    _set_version(conn, 21)


def migrate_v21_to_v22(conn: sqlite3.Connection) -> None:
    """Expand media.kind CHECK constraint to allow 'audio_tts_sentence'.

    The v1→v2 migration created media with a CHECK constraint restricting kind
    to ('image','audio_forvo','audio_tts'), which rejects 'audio_tts_sentence'.
    This migration recreates the table without the CHECK. No data is moved —
    the schema is the same except for the constraint. See the v3→v4 pattern.
    """
    # Heuristic: if any row already uses the new kind, we've already migrated.
    row = conn.execute(
        "SELECT COUNT(*) FROM media WHERE kind = 'audio_tts_sentence'",
    ).fetchone()
    if row and row[0] > 0:
        _set_version(conn, 22)
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("""
            CREATE TABLE _media_v22 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collocation_id INTEGER REFERENCES collocations(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                filename TEXT NOT NULL,
                path TEXT,
                anki_filename TEXT,
                sha256 TEXT,
                bytes INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            INSERT INTO _media_v22
                (id, collocation_id, kind, filename, path, anki_filename, sha256, bytes, created_at)
            SELECT id, collocation_id, kind, filename, path, anki_filename, sha256, bytes, created_at
            FROM media
        """)
        for idx in ("idx_media_collocation", "idx_media_anki_filename"):
            conn.execute(f"DROP INDEX IF EXISTS {idx}")
        conn.execute("DROP TABLE media")
        conn.execute("ALTER TABLE _media_v22 RENAME TO media")
        conn.execute("CREATE INDEX idx_media_collocation ON media(collocation_id)")
        conn.execute("CREATE INDEX idx_media_anki_filename ON media(anki_filename)")
        _set_version(conn, 22)
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def migrate_v22_to_v23(conn: sqlite3.Connection) -> None:
    r"""Mark `audio` dirty on cloze collocations with sentence audio + anki_note_id.

    One-shot: gets pre-existing cloze rows that were synthesized by the TTS
    backfill primed for the next sync_push, which will attach [sound:...] to
    Back Extra and copy the MP3 into Anki's collection.media/. Idempotent
    for the 'audio' token.
    """
    conn.execute(r"""
        UPDATE collocations
        SET dirty_fields = CASE
            WHEN dirty_fields IS NULL OR dirty_fields = '' THEN 'audio'
            WHEN ',' || dirty_fields || ',' LIKE '%,audio,%' THEN dirty_fields
            ELSE dirty_fields || ',audio'
        END
        WHERE card_type = 'cloze'
          AND anki_note_id IS NOT NULL
          AND id IN (SELECT DISTINCT collocation_id FROM media WHERE kind = 'audio_tts_sentence')
    """)
    _set_version(conn, 23)


_MIGRATIONS = {
    0: migrate_v0_to_v1,
    1: migrate_v1_to_v2,
    2: migrate_v2_to_v3,
    3: migrate_v3_to_v4,
    4: migrate_v4_to_v5,
    5: migrate_v5_to_v6,
    6: migrate_v6_to_v7,
    7: migrate_v7_to_v8,
    8: migrate_v8_to_v9,
    9: migrate_v9_to_v10,
    10: migrate_v10_to_v11,
    11: migrate_v11_to_v12,
    12: migrate_v12_to_v13,
    13: migrate_v13_to_v14,
    14: migrate_v14_to_v15,
    15: migrate_v15_to_v16,
    16: migrate_v16_to_v17,
    17: migrate_v17_to_v18,
    18: migrate_v18_to_v19,
    19: migrate_v19_to_v20,
    20: migrate_v20_to_v21,
    21: migrate_v21_to_v22,
    22: migrate_v22_to_v23,
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
