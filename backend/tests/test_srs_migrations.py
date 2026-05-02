"""Tests for versioned SRS database migrations."""

import sqlite3
from datetime import date

import pytest

from app.srs.migrations import CURRENT_VERSION, migrate


def _make_v1_conn() -> sqlite3.Connection:
    """In-memory DB at schema version 1 (has lemma column, no directions)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
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
            stability REAL NOT NULL DEFAULT 1.0,
            fsrs_difficulty REAL NOT NULL DEFAULT 5.0,
            due_date TEXT NOT NULL,
            reps INTEGER NOT NULL DEFAULT 0,
            lapses INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT 'new',
            last_review TEXT,
            lemma TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE violations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collocation_text TEXT NOT NULL,
            day_number INTEGER NOT NULL,
            violation_type TEXT NOT NULL,
            details TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    return conn


def _insert(
    conn,
    text,
    translation="trans",
    language_code="sl",
    stability=3.5,
    fsrs_difficulty=5.0,
    reps=2,
    lapses=0,
    state="review",
    due_date="2026-01-01",
):
    conn.execute(
        """INSERT INTO collocations
           (text, translation, language_code, stability, fsrs_difficulty, reps,
            lapses, state, due_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (text, translation, language_code, stability, fsrs_difficulty, reps, lapses, state, due_date),
    )
    conn.commit()


class TestMigrations:
    def test_current_version(self):
        assert CURRENT_VERSION == 8

    def test_migrates_from_v1_to_v2(self):
        from app.srs.migrations import migrate_v1_to_v2

        conn = _make_v1_conn()
        _insert(conn, "banka")
        migrate_v1_to_v2(conn)
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2

    def test_two_direction_rows_per_collocation(self):
        conn = _make_v1_conn()
        _insert(conn, "banka")
        migrate(conn)
        rows = conn.execute("SELECT * FROM collocation_directions").fetchall()
        assert len(rows) == 2
        directions = {r["direction"] for r in rows}
        assert directions == {"recognition", "production"}

    def test_recognition_copies_v1_fsrs_state(self):
        conn = _make_v1_conn()
        _insert(conn, "banka", stability=3.5, reps=2, state="review")
        migrate(conn)
        row = conn.execute("SELECT * FROM collocation_directions WHERE direction = 'recognition'").fetchone()
        assert row["stability"] == 3.5
        assert row["reps"] == 2
        assert row["state"] == "review"

    def test_production_seeded_with_new_state(self):
        conn = _make_v1_conn()
        _insert(conn, "banka", stability=3.5, reps=2, state="review")
        migrate(conn)
        row = conn.execute("SELECT * FROM collocation_directions WHERE direction = 'production'").fetchone()
        assert row["state"] == "new"
        assert row["reps"] == 0
        assert row["stability"] == 1.0
        assert row["fsrs_difficulty"] == 5.0

    def test_guid_populated_after_migration(self):
        conn = _make_v1_conn()
        _insert(conn, "banka")
        migrate(conn)
        row = conn.execute("SELECT guid FROM collocations WHERE text = 'banka'").fetchone()
        assert row["guid"] is not None
        assert len(row["guid"]) == 16

    def test_guid_deterministic_matches_compute_guid(self):
        from app.common.guid import compute_guid

        conn = _make_v1_conn()
        _insert(conn, "banka", language_code="sl")
        migrate(conn)
        row = conn.execute("SELECT guid FROM collocations WHERE text = 'banka'").fetchone()
        assert row["guid"] == compute_guid("banka", "sl")

    def test_media_table_created(self):
        conn = _make_v1_conn()
        migrate(conn)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "media" in tables

    def test_collocation_tags_table_created(self):
        conn = _make_v1_conn()
        migrate(conn)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "collocation_tags" in tables

    def test_production_due_dates_seeded_to_today(self):
        conn = _make_v1_conn()
        for i in range(3):
            _insert(conn, f"word{i}", due_date="2026-01-01")
        migrate(conn)
        today = date.today()
        rows = conn.execute("SELECT due_date FROM collocation_directions WHERE direction = 'production'").fetchall()
        assert len(rows) == 3
        for row in rows:
            assert row["due_date"] == today.isoformat(), f"production due_date should be today, got {row['due_date']}"

    def test_idempotent_on_rerun(self):
        conn = _make_v1_conn()
        _insert(conn, "banka")
        migrate(conn)
        migrate(conn)  # should not raise or duplicate rows
        rows = conn.execute("SELECT * FROM collocation_directions").fetchall()
        assert len(rows) == 2

    def test_multiple_collocations(self):
        conn = _make_v1_conn()
        for text in ["banka", "hiša", "avto"]:
            _insert(conn, text)
        migrate(conn)
        n_parents = conn.execute("SELECT COUNT(*) FROM collocations").fetchone()[0]
        n_dirs = conn.execute("SELECT COUNT(*) FROM collocation_directions").fetchone()[0]
        assert n_parents == 3
        assert n_dirs == 6

    def test_v0_to_v1_skips_alter_when_lemma_already_present(self):
        """migrate_v0_to_v1 is a no-op for the ALTER when lemma column exists."""
        from app.srs.migrations import migrate_v0_to_v1

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE collocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT UNIQUE NOT NULL,
                due_date TEXT NOT NULL,
                stability REAL NOT NULL DEFAULT 1.0,
                fsrs_difficulty REAL NOT NULL DEFAULT 5.0,
                reps INTEGER NOT NULL DEFAULT 0,
                lapses INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'new',
                last_review TEXT,
                lemma TEXT
            )
        """)
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
        migrate_v0_to_v1(conn)
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1

    def test_v1_to_v2_short_circuits_if_direction_table_already_exists(self):
        """Defensive branch: collocation_directions exists but user_version < 2."""
        from app.srs.migrations import migrate_v1_to_v2

        conn = _make_v1_conn()
        conn.execute("""
            CREATE TABLE collocation_directions (
                collocation_id INTEGER,
                direction TEXT,
                stability REAL,
                fsrs_difficulty REAL,
                due_date TEXT NOT NULL,
                reps INTEGER,
                lapses INTEGER,
                state TEXT,
                last_review TEXT,
                anki_card_id INTEGER,
                dirty_fsrs INTEGER,
                last_synced_at TEXT,
                PRIMARY KEY (collocation_id, direction)
            )
        """)
        conn.commit()
        migrate_v1_to_v2(conn)
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2

    def test_migrate_rolls_back_on_error(self, monkeypatch):
        """If a migration raises, the transaction is rolled back and error propagates."""
        import app.srs.migrations as migrations

        conn = _make_v1_conn()

        def boom(_conn):
            raise RuntimeError("simulated failure")

        original = dict(migrations._MIGRATIONS)
        monkeypatch.setattr(migrations, "_MIGRATIONS", {**original, 1: boom})
        with pytest.raises(RuntimeError, match="simulated failure"):
            migrate(conn)

    def test_migrate_raises_for_unregistered_version(self, monkeypatch):
        """migrate() raises RuntimeError when no migration is registered for a version."""
        import app.srs.migrations as migrations

        conn = _make_v1_conn()
        monkeypatch.setattr(migrations, "_MIGRATIONS", {})
        with pytest.raises(RuntimeError, match="No migration registered for version 1"):
            migrate(conn)

    def test_v2_to_v3_short_circuits_if_disambig_key_already_present(self):
        """Idempotency guard: if disambig_key exists, just bump version."""
        from app.srs.migrations import migrate_v2_to_v3

        conn = _make_v1_conn()
        _insert(conn, "banka")
        # Run v1→v2 first to get the v2 schema
        from app.srs.migrations import migrate_v1_to_v2

        migrate_v1_to_v2(conn)
        conn.commit()
        # Manually add disambig_key to simulate an already-migrated state
        conn.execute("ALTER TABLE collocations ADD COLUMN disambig_key TEXT NOT NULL DEFAULT ''")
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
        migrate_v2_to_v3(conn)
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3

    def test_v2_to_v3_strips_suffix_from_homonym_text(self):
        """Rows with 'word (disambig)' text get bare text + disambig_key populated."""
        from app.srs.migrations import migrate_v1_to_v2, migrate_v2_to_v3

        conn = _make_v1_conn()
        _insert(conn, "barva (color)")
        migrate_v1_to_v2(conn)
        conn.commit()
        migrate_v2_to_v3(conn)
        conn.commit()
        row = conn.execute("SELECT text, disambig_key FROM collocations").fetchone()
        assert row["text"] == "barva"
        assert row["disambig_key"] == "color"

    def test_v2_to_v3_nested_paren_suffix_is_split(self):
        """'star (old (≠new))' → bare='star', disambig_key='old (≠new)'."""
        from app.srs.migrations import migrate_v1_to_v2, migrate_v2_to_v3

        conn = _make_v1_conn()
        _insert(conn, "star (old (≠new))")
        migrate_v1_to_v2(conn)
        conn.commit()
        migrate_v2_to_v3(conn)
        conn.commit()
        row = conn.execute("SELECT text, disambig_key FROM collocations").fetchone()
        assert row["text"] == "star"
        assert row["disambig_key"] == "old (≠new)"

    def test_v2_to_v3_two_homonym_rows_produce_distinct_pairs(self):
        """Two rows sharing bare text but different disambig survive with distinct (text, disambig_key)."""
        from app.common.guid import compute_guid
        from app.srs.migrations import migrate_v1_to_v2, migrate_v2_to_v3

        conn = _make_v1_conn()
        _insert(conn, "barva (color)", language_code="sl")
        _insert(conn, "barva (paint)", language_code="sl")
        migrate_v1_to_v2(conn)
        conn.commit()
        migrate_v2_to_v3(conn)
        conn.commit()
        rows = conn.execute("SELECT text, disambig_key, guid FROM collocations ORDER BY disambig_key").fetchall()
        assert len(rows) == 2
        assert rows[0]["text"] == "barva"
        assert rows[0]["disambig_key"] == "color"
        assert rows[1]["text"] == "barva"
        assert rows[1]["disambig_key"] == "paint"
        assert rows[0]["guid"] != rows[1]["guid"]
        assert rows[0]["guid"] == compute_guid("barva", "sl", "color")
        assert rows[1]["guid"] == compute_guid("barva", "sl", "paint")


# ---------------------------------------------------------------------------
# Helpers for v3→v4 tests
# ---------------------------------------------------------------------------

_BROKEN_CD_DDL = """\
    CREATE TABLE collocation_directions (
        collocation_id INTEGER NOT NULL REFERENCES "_collocations_v2"(id) ON DELETE CASCADE,
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
    )"""

_BROKEN_MEDIA_DDL = """\
    CREATE TABLE media (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        collocation_id INTEGER REFERENCES "_collocations_v2"(id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK(kind IN ('image','audio_forvo','audio_tts')),
        filename TEXT NOT NULL,
        path TEXT,
        anki_filename TEXT,
        sha256 TEXT,
        bytes INTEGER,
        created_at TEXT DEFAULT (datetime('now'))
    )"""

_BROKEN_CT_DDL = """\
    CREATE TABLE collocation_tags (
        collocation_id INTEGER NOT NULL REFERENCES "_collocations_v2"(id) ON DELETE CASCADE,
        tag TEXT NOT NULL,
        PRIMARY KEY (collocation_id, tag)
    )"""

_CLEAN_CD_DDL = """\
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
    )"""

_CLEAN_MEDIA_DDL = """\
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
    )"""

_CLEAN_CT_DDL = """\
    CREATE TABLE collocation_tags (
        collocation_id INTEGER NOT NULL REFERENCES collocations(id) ON DELETE CASCADE,
        tag TEXT NOT NULL,
        PRIMARY KEY (collocation_id, tag)
    )"""

_V3_COLLOCATIONS_DDL = """\
    CREATE TABLE collocations (
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
    )"""


def _make_v3_broken_fk_conn() -> sqlite3.Connection:
    """v3 DB where all three child tables reference the non-existent _collocations_v2."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(_V3_COLLOCATIONS_DDL)
    conn.execute(_BROKEN_CD_DDL)
    conn.execute(_BROKEN_MEDIA_DDL)
    conn.execute(_BROKEN_CT_DDL)
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    return conn


def _make_v3_partial_broken_fk_conn() -> sqlite3.Connection:
    """v3 DB where only media has the broken FK; cd and ct are correct."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(_V3_COLLOCATIONS_DDL)
    conn.execute(_CLEAN_CD_DDL)
    conn.execute(_BROKEN_MEDIA_DDL)
    conn.execute(_CLEAN_CT_DDL)
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    return conn


def _insert_v3(conn, text="banka", translation="bank", language_code="sl") -> int:
    """Insert a v3-schema collocations row; return its id."""
    from app.common.guid import compute_guid

    guid = compute_guid(text, language_code)
    cursor = conn.execute(
        "INSERT INTO collocations (text, translation, language_code, guid) VALUES (?, ?, ?, ?)",
        (text, translation, language_code, guid),
    )
    conn.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# v3→v4 tests
# ---------------------------------------------------------------------------


class TestMigrateV3ToV4:
    def test_v3_to_v4_detects_broken_fks(self):
        conn = _make_v3_broken_fk_conn()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE '%_collocations_v2%'"
        ).fetchall()
        assert len(rows) == 3

    def test_v3_to_v4_repairs_fk_references(self):
        from app.srs.migrations import migrate_v3_to_v4

        conn = _make_v3_broken_fk_conn()
        migrate_v3_to_v4(conn)
        conn.commit()
        rows = conn.execute("SELECT name FROM sqlite_master WHERE sql LIKE '%_collocations_v2%'").fetchall()
        assert rows == []

    def test_v3_to_v4_bumps_version(self):
        from app.srs.migrations import migrate_v3_to_v4

        conn = _make_v3_broken_fk_conn()
        migrate_v3_to_v4(conn)
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4

    def test_v3_to_v4_insert_works_after_repair(self):
        from app.srs.migrations import migrate_v3_to_v4

        conn = _make_v3_broken_fk_conn()
        migrate_v3_to_v4(conn)
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
        parent_id = _insert_v3(conn)
        conn.execute(
            "INSERT INTO collocation_directions "
            "(collocation_id, direction, due_date) VALUES (?, 'recognition', '2026-01-01')",
            (parent_id,),
        )
        conn.commit()

    def test_v3_to_v4_idempotent_when_fks_already_correct(self):
        from app.srs.migrations import migrate_v1_to_v2, migrate_v2_to_v3, migrate_v3_to_v4

        conn = _make_v1_conn()
        _insert(conn, "banka")
        migrate_v1_to_v2(conn)
        conn.commit()
        migrate_v2_to_v3(conn)
        conn.commit()
        # Clean v3 — no broken FK
        migrate_v3_to_v4(conn)
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert conn.execute("SELECT name FROM sqlite_master WHERE sql LIKE '%_collocations_v2%'").fetchall() == []

    def test_v3_to_v4_data_preserved(self):
        from app.srs.migrations import migrate_v3_to_v4

        conn = _make_v3_broken_fk_conn()
        parent_id = _insert_v3(conn, "hiša")
        # Insert child rows with FK off (conn is still FK-off from helper)
        conn.execute(
            "INSERT INTO collocation_directions "
            "(collocation_id, direction, due_date, stability) VALUES (?, 'recognition', '2026-01-01', 3.5)",
            (parent_id,),
        )
        conn.execute(
            "INSERT INTO collocation_tags (collocation_id, tag) VALUES (?, 'noun')",
            (parent_id,),
        )
        conn.commit()
        migrate_v3_to_v4(conn)
        conn.commit()
        dir_rows = conn.execute("SELECT * FROM collocation_directions").fetchall()
        tag_rows = conn.execute("SELECT * FROM collocation_tags").fetchall()
        assert len(dir_rows) == 1
        assert dir_rows[0]["stability"] == 3.5
        assert len(tag_rows) == 1
        assert tag_rows[0]["tag"] == "noun"

    def test_v3_to_v4_foreign_key_check_passes(self):
        from app.srs.migrations import migrate_v3_to_v4

        conn = _make_v3_broken_fk_conn()
        parent_id = _insert_v3(conn, "miza")
        conn.execute(
            "INSERT INTO collocation_directions "
            "(collocation_id, direction, due_date) VALUES (?, 'production', '2026-02-01')",
            (parent_id,),
        )
        conn.commit()
        migrate_v3_to_v4(conn)
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
        fk_issues = conn.execute("PRAGMA foreign_key_check").fetchall()
        assert fk_issues == []

    def test_v3_to_v4_handles_partial_corruption(self):
        from app.srs.migrations import migrate_v3_to_v4

        conn = _make_v3_partial_broken_fk_conn()
        migrate_v3_to_v4(conn)
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert conn.execute("SELECT name FROM sqlite_master WHERE sql LIKE '%_collocations_v2%'").fetchall() == []

    def test_v3_to_v4_raises_if_fk_check_fails_after_rebuild(self):
        """Defensive RuntimeError fires when rebuilt table has a dangling FK reference."""
        from app.srs.migrations import migrate_v3_to_v4

        conn = _make_v3_broken_fk_conn()
        # Insert a direction row pointing to a non-existent parent (id=999).
        # FK enforcement is off so this succeeds; after rebuild the FK check catches it.
        conn.execute(
            "INSERT INTO collocation_directions "
            "(collocation_id, direction, due_date) VALUES (999, 'recognition', '2026-01-01')"
        )
        conn.commit()
        with pytest.raises(RuntimeError, match="FK check failed"):
            migrate_v3_to_v4(conn)

    def _make_v4_conn(self) -> sqlite3.Connection:
        """In-memory DB at schema version 4 (collocation_directions without last_rating)."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
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
                disambig_key TEXT NOT NULL DEFAULT '',
                anki_note_id INTEGER,
                dirty_fields TEXT NOT NULL DEFAULT '',
                last_synced_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(text, disambig_key)
            );
            CREATE TABLE collocation_directions (
                collocation_id INTEGER NOT NULL REFERENCES collocations(id) ON DELETE CASCADE,
                direction TEXT NOT NULL CHECK(direction IN ('recognition','production')),
                stability REAL NOT NULL DEFAULT 1.0,
                fsrs_difficulty REAL NOT NULL DEFAULT 5.0,
                due_date TEXT NOT NULL DEFAULT (date('now')),
                reps INTEGER NOT NULL DEFAULT 0,
                lapses INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'new',
                last_review TEXT,
                anki_card_id INTEGER,
                dirty_fsrs INTEGER NOT NULL DEFAULT 0,
                last_synced_at TEXT,
                PRIMARY KEY (collocation_id, direction)
            );
            PRAGMA user_version = 4;
        """)
        conn.execute(
            "INSERT INTO collocations (text, translation, guid, disambig_key) VALUES ('banka','bank','abc','');"
        )
        conn.execute(
            "INSERT INTO collocation_directions (collocation_id, direction, due_date) VALUES (1,'recognition',date('now'));"
        )
        conn.execute(
            "INSERT INTO collocation_directions (collocation_id, direction, due_date) VALUES (1,'production',date('now'));"
        )
        conn.commit()
        return conn

    def test_v4_to_v5_adds_last_rating_column(self):
        """migrate_v4_to_v5 adds last_rating INTEGER to collocation_directions."""
        from app.srs.migrations import migrate_v4_to_v5

        conn = self._make_v4_conn()
        migrate_v4_to_v5(conn)
        conn.commit()

        cols = {r[1] for r in conn.execute("PRAGMA table_info(collocation_directions)").fetchall()}
        assert "last_rating" in cols

    def test_v4_to_v5_existing_rows_have_null_last_rating(self):
        """After v4→v5 migration, existing rows have NULL last_rating (nullable column)."""
        from app.srs.migrations import migrate_v4_to_v5

        conn = self._make_v4_conn()
        migrate_v4_to_v5(conn)
        conn.commit()

        rows = conn.execute("SELECT last_rating FROM collocation_directions").fetchall()
        assert len(rows) == 2
        assert all(r["last_rating"] is None for r in rows)

    def test_v4_to_v5_idempotent(self):
        """Running migrate_v4_to_v5 twice does not raise or duplicate data."""
        from app.srs.migrations import migrate_v4_to_v5

        conn = self._make_v4_conn()
        migrate_v4_to_v5(conn)
        conn.commit()
        migrate_v4_to_v5(conn)  # second call — must not raise
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5


class TestMigrateV5ToV6:
    def _make_v5_conn(self) -> sqlite3.Connection:
        """In-memory DB at schema version 5 (collocation_directions with last_rating)."""
        from app.srs.migrations import migrate_v1_to_v2, migrate_v2_to_v3, migrate_v3_to_v4, migrate_v4_to_v5

        conn = _make_v1_conn()
        _insert(conn, "banka")
        migrate_v1_to_v2(conn)
        conn.commit()
        migrate_v2_to_v3(conn)
        conn.commit()
        migrate_v3_to_v4(conn)
        conn.commit()
        migrate_v4_to_v5(conn)
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        return conn

    def test_migrates_from_v5_to_v6_adds_anki_due_column(self):
        """migrate_v5_to_v6 adds anki_due INTEGER column to collocation_directions."""
        from app.srs.migrations import migrate_v5_to_v6

        conn = self._make_v5_conn()
        migrate_v5_to_v6(conn)
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        cols = {r[1] for r in conn.execute("PRAGMA table_info(collocation_directions)").fetchall()}
        assert "anki_due" in cols

    def test_v5_to_v6_idempotent(self):
        """Running migrate_v5_to_v6 twice does not raise or lose data."""
        from app.srs.migrations import migrate_v5_to_v6

        conn = self._make_v5_conn()
        migrate_v5_to_v6(conn)
        conn.commit()
        migrate_v5_to_v6(conn)  # second call — must not raise
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        cols = {r[1] for r in conn.execute("PRAGMA table_info(collocation_directions)").fetchall()}
        assert "anki_due" in cols

    def test_v5_to_v6_preserves_existing_data(self):
        """After v5→v6 migration, existing row data is unchanged (anki_due is NULL)."""
        from app.srs.migrations import migrate_v5_to_v6

        conn = self._make_v5_conn()
        migrate_v5_to_v6(conn)
        conn.commit()
        # Check both directions exist and anki_due is NULL for all
        rows = conn.execute(
            "SELECT last_rating, state, anki_card_id, due_date, anki_due FROM collocation_directions"
        ).fetchall()
        assert len(rows) == 2
        for row in rows:
            assert row["anki_due"] is None
            assert row["due_date"] is not None

    def test_full_migrate_includes_v6(self):
        """migrate() runs all migrations including v5→v6 and ends at CURRENT_VERSION."""
        conn = _make_v1_conn()
        _insert(conn, "banka")
        migrate(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
        cols = {r[1] for r in conn.execute("PRAGMA table_info(collocation_directions)").fetchall()}
        assert "anki_due" in cols


class TestMigrationV6ToV7:
    """Migration v6→v7 adds grammar and note columns to collocations."""

    def test_adds_grammar_column(self):
        from app.srs.migrations import migrate_v6_to_v7

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA user_version = 6")
        conn.commit()
        # Need a minimal collocations table at v6
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
        conn.commit()
        migrate_v6_to_v7(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(collocations)").fetchall()}
        assert "grammar" in cols
        assert "note" in cols
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7

    def test_grammar_defaults_to_empty_string(self):
        from app.srs.migrations import migrate_v6_to_v7

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA user_version = 6")
        conn.execute("""
            CREATE TABLE collocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT UNIQUE NOT NULL,
                translation TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.commit()
        migrate_v6_to_v7(conn)
        conn.execute("INSERT INTO collocations (text, translation) VALUES ('test', 't')")
        row = conn.execute("SELECT grammar, note FROM collocations").fetchone()
        assert row["grammar"] == ""
        assert row["note"] == ""

    def test_full_migrate_includes_v7(self):
        conn = _make_v1_conn()
        _insert(conn, "banka")
        migrate(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8


class TestMigrationV6ToV7Detailed:
    """Detailed tests for v6->v7 migration."""

    def test_adds_grammar_and_note_columns(self, tmp_path):
        """migrate_v6_to_v7 adds grammar and note columns."""
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # Create v6 schema (without grammar/note)
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
        conn.execute("PRAGMA user_version = 6")
        conn.commit()

        from app.srs.migrations import migrate_v6_to_v7

        migrate_v6_to_v7(conn)

        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
        cols = {r[1] for r in conn.execute("PRAGMA table_info(collocations)").fetchall()}
        assert "grammar" in cols
        assert "note" in cols


class TestMigrationV7ToV8:
    """Migration v7→v8 adds source context columns to collocations."""

    def _make_v7_conn(self) -> sqlite3.Connection:
        """In-memory DB at schema version 7 (with grammar and note columns)."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
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
                disambig_key TEXT NOT NULL DEFAULT '',
                grammar TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                anki_note_id INTEGER,
                dirty_fields TEXT NOT NULL DEFAULT '',
                last_synced_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("PRAGMA user_version = 7")
        conn.commit()
        return conn

    def test_adds_source_sentence_column(self):
        """migrate_v7_to_v8 adds source_sentence TEXT column."""
        from app.srs.migrations import migrate_v7_to_v8

        conn = self._make_v7_conn()
        migrate_v7_to_v8(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(collocations)").fetchall()}
        assert "source_sentence" in cols

    def test_source_sentence_defaults_to_empty_string(self):
        """New source_sentence column defaults to empty string."""
        from app.srs.migrations import migrate_v7_to_v8

        conn = self._make_v7_conn()
        migrate_v7_to_v8(conn)
        conn.execute("INSERT INTO collocations (text, translation) VALUES ('test', 't')")
        row = conn.execute("SELECT source_sentence FROM collocations").fetchone()
        assert row["source_sentence"] == ""

    def test_adds_source_lesson_id_column(self):
        """migrate_v7_to_v8 adds source_lesson_id TEXT column (nullable)."""
        from app.srs.migrations import migrate_v7_to_v8

        conn = self._make_v7_conn()
        migrate_v7_to_v8(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(collocations)").fetchall()}
        assert "source_lesson_id" in cols

    def test_source_lesson_id_allows_null(self):
        """source_lesson_id column is nullable."""
        from app.srs.migrations import migrate_v7_to_v8

        conn = self._make_v7_conn()
        migrate_v7_to_v8(conn)
        conn.execute("INSERT INTO collocations (text, translation) VALUES ('test', 't')")
        row = conn.execute("SELECT source_lesson_id FROM collocations").fetchone()
        assert row["source_lesson_id"] is None

    def test_adds_source_line_index_column(self):
        """migrate_v7_to_v8 adds source_line_index INTEGER column (nullable)."""
        from app.srs.migrations import migrate_v7_to_v8

        conn = self._make_v7_conn()
        migrate_v7_to_v8(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(collocations)").fetchall()}
        assert "source_line_index" in cols

    def test_source_line_index_allows_null(self):
        """source_line_index column is nullable."""
        from app.srs.migrations import migrate_v7_to_v8

        conn = self._make_v7_conn()
        migrate_v7_to_v8(conn)
        conn.execute("INSERT INTO collocations (text, translation) VALUES ('test', 't')")
        row = conn.execute("SELECT source_line_index FROM collocations").fetchone()
        assert row["source_line_index"] is None

    def test_bumps_version_to_8(self):
        """migrate_v7_to_v8 bumps user_version to 8."""
        from app.srs.migrations import migrate_v7_to_v8

        conn = self._make_v7_conn()
        migrate_v7_to_v8(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8

    def test_idempotent(self):
        """Running migrate_v7_to_v8 twice does not raise."""
        from app.srs.migrations import migrate_v7_to_v8

        conn = self._make_v7_conn()
        migrate_v7_to_v8(conn)
        migrate_v7_to_v8(conn)  # second call should not raise
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8

    def test_full_migrate_includes_v8(self):
        """migrate() runs all migrations including v7→v8 and ends at CURRENT_VERSION=8."""
        from app.srs.migrations import CURRENT_VERSION, migrate

        conn = self._make_v7_conn()
        migrate(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_VERSION

    def test_grammar_default_empty_string(self, tmp_path):
        """New rows get empty string for grammar/note."""
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE collocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT UNIQUE NOT NULL,
                translation TEXT NOT NULL DEFAULT '',
                language_code TEXT NOT NULL DEFAULT 'sl'
            )
        """)
        conn.execute("PRAGMA user_version = 6")
        conn.commit()

        from app.srs.migrations import migrate_v6_to_v7

        migrate_v6_to_v7(conn)

        conn.execute("INSERT INTO collocations (text, translation) VALUES ('test', 't')")
        row = conn.execute("SELECT grammar, note FROM collocations").fetchone()
        assert row["grammar"] == ""
        assert row["note"] == ""

    def test_idempotent_v6_to_v7(self, tmp_path):
        """Running migrate_v6_to_v7 twice does not raise."""
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE collocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT UNIQUE NOT NULL,
                translation TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.execute("PRAGMA user_version = 6")
        conn.commit()

        from app.srs.migrations import migrate_v6_to_v7

        migrate_v6_to_v7(conn)
        migrate_v6_to_v7(conn)  # second call should not raise
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
