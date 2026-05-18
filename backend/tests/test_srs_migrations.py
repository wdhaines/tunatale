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
        assert CURRENT_VERSION == 23

    def test_migrates_v15_to_v16_deletes_phantom_directions(self, tmp_path):
        """v16 deletes direction rows that were auto-filled by the pre-fix
        _build_directions when the Anki notetype had no card at that ord
        (e.g. phonics on the "Basic" notetype). User-added rows pending
        their first sync are preserved.
        """
        import sqlite3

        from app.srs.migrations import _set_version, migrate_v15_to_v16

        conn = sqlite3.connect(str(tmp_path / "test.db"))
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE collocations (id INTEGER PRIMARY KEY, text TEXT, anki_note_id INTEGER)")
        conn.execute(
            "CREATE TABLE collocation_directions ("
            "collocation_id INTEGER, direction TEXT, anki_card_id INTEGER, "
            "reps INTEGER DEFAULT 0, dirty_fsrs INTEGER DEFAULT 0, state TEXT DEFAULT 'new', "
            "PRIMARY KEY (collocation_id, direction))"
        )
        # Phonics-style: synced from Anki, only recognition card, phantom production row.
        conn.execute("INSERT INTO collocations (id, text, anki_note_id) VALUES (1, 'How is č pronounced?', 1001)")
        conn.execute("INSERT INTO collocation_directions VALUES (1, 'recognition', 1001, 1, 0, 'review')")
        conn.execute("INSERT INTO collocation_directions VALUES (1, 'production', NULL, 0, 0, 'new')")
        # User-added: not yet synced, both directions phantom-looking but legitimate.
        conn.execute("INSERT INTO collocations (id, text, anki_note_id) VALUES (2, 'krava', NULL)")
        conn.execute("INSERT INTO collocation_directions VALUES (2, 'recognition', NULL, 0, 0, 'new')")
        conn.execute("INSERT INTO collocation_directions VALUES (2, 'production', NULL, 0, 0, 'new')")
        # Synced, reviewed: must NOT be deleted even though anki_card_id IS NULL.
        # (Edge case: shouldn't happen in practice but the safety filter covers it.)
        conn.execute("INSERT INTO collocations (id, text, anki_note_id) VALUES (3, 'dober dan', 1002)")
        conn.execute("INSERT INTO collocation_directions VALUES (3, 'recognition', NULL, 5, 0, 'review')")
        # Synced, dirty (pending push): must NOT be deleted.
        conn.execute("INSERT INTO collocations (id, text, anki_note_id) VALUES (4, 'svinjina', 1003)")
        conn.execute("INSERT INTO collocation_directions VALUES (4, 'recognition', NULL, 0, 1, 'new')")
        _set_version(conn, 15)

        migrate_v15_to_v16(conn)

        survivors = {
            (r["collocation_id"], r["direction"])
            for r in conn.execute("SELECT collocation_id, direction FROM collocation_directions").fetchall()
        }
        assert (1, "production") not in survivors  # phantom — deleted
        assert (1, "recognition") in survivors  # has anki_card_id — kept
        assert (2, "recognition") in survivors  # user-added — kept
        assert (2, "production") in survivors  # user-added — kept
        assert (3, "recognition") in survivors  # has reps>0 — kept
        assert (4, "recognition") in survivors  # dirty_fsrs=1 — kept
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 16

        # Idempotent
        migrate_v15_to_v16(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 16

    def test_migrates_v16_to_v17_adds_created_at_index(self, tmp_path):
        """v17 creates idx_collocations_created_at for recency-prioritized new queue."""
        import sqlite3

        from app.srs.migrations import _set_version, migrate_v16_to_v17

        conn = sqlite3.connect(str(tmp_path / "test.db"))
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE collocations (id INTEGER PRIMARY KEY, created_at TEXT DEFAULT (datetime('now')))")
        _set_version(conn, 16)

        migrate_v16_to_v17(conn)

        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_collocations_created_at'"
        ).fetchone()
        assert row is not None, "idx_collocations_created_at index must exist"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 17

        # Idempotent
        migrate_v16_to_v17(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 17

    def test_migrates_v17_to_v18_adds_introduced_at_column(self, tmp_path):
        """v18 adds collocation_directions.introduced_at for first-grade tracking."""
        import sqlite3

        from app.srs.migrations import _set_version, migrate_v17_to_v18

        conn = sqlite3.connect(str(tmp_path / "test.db"))
        conn.row_factory = sqlite3.Row
        conn.execute(
            """CREATE TABLE collocation_directions (
                collocation_id INTEGER,
                direction TEXT,
                state TEXT,
                last_review TEXT,
                prior_state TEXT,
                reps INTEGER DEFAULT 0
            )"""
        )
        conn.execute(
            "INSERT INTO collocation_directions (collocation_id, direction, state, prior_state, reps) "
            "VALUES (1, 'recognition', 'review', 'new', 3)"
        )
        _set_version(conn, 17)

        migrate_v17_to_v18(conn)

        cols = {r[1] for r in conn.execute("PRAGMA table_info(collocation_directions)")}
        assert "introduced_at" in cols
        # Pre-existing rows must keep introduced_at=NULL (we don't backfill — Anki
        # revlog reconstruction is the source of truth for old rows post-sync).
        row = conn.execute("SELECT introduced_at FROM collocation_directions").fetchone()
        assert row["introduced_at"] is None
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 18

        # Idempotent
        migrate_v17_to_v18(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 18

    def test_migrates_v18_to_v19_adds_card_type_column(self, tmp_path):
        """v19 adds collocations.card_type for Phase F cloze cards."""
        import sqlite3

        from app.srs.migrations import _set_version, migrate_v18_to_v19

        conn = sqlite3.connect(str(tmp_path / "test.db"))
        conn.row_factory = sqlite3.Row
        conn.execute(
            """CREATE TABLE collocations (
                id INTEGER PRIMARY KEY,
                text TEXT,
                translation TEXT
            )"""
        )
        conn.execute("INSERT INTO collocations (id, text, translation) VALUES (1, 'test', 'test')")
        _set_version(conn, 18)

        migrate_v18_to_v19(conn)

        cols = {r[1] for r in conn.execute("PRAGMA table_info(collocations)")}
        assert "card_type" in cols
        # DEFAULT 'vocab' should apply to existing rows
        row = conn.execute("SELECT card_type FROM collocations WHERE id = 1").fetchone()
        assert row["card_type"] == "vocab"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 19

        # Idempotent
        migrate_v18_to_v19(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 19

    def test_migrates_v19_to_v20_adds_bury_kind_and_backfills(self, tmp_path):
        """v20 adds collocation_directions.bury_kind and marks pre-existing buried rows as 'user'.

        Backfilling state='buried' rows as 'user' is the safe default: prior
        to this migration, every buried row was wiped by unbury_if_needed
        regardless of source. Marking them 'user' preserves whatever Anki had
        sync_pulled as queue=-2/-3 — sibling-buries from earlier today
        would lose their auto-unbury at next rollover, but the user can
        manually unbury or wait for the next sync_pull to overwrite the kind.
        """
        import sqlite3

        from app.srs.migrations import _set_version, migrate_v19_to_v20

        conn = sqlite3.connect(str(tmp_path / "test.db"))
        conn.row_factory = sqlite3.Row
        conn.execute(
            """CREATE TABLE collocation_directions (
                collocation_id INTEGER, direction TEXT, state TEXT
            )"""
        )
        conn.executemany(
            "INSERT INTO collocation_directions (collocation_id, direction, state) VALUES (?, ?, ?)",
            [
                (1, "recognition", "buried"),
                (2, "production", "buried"),
                (3, "recognition", "review"),
                (4, "recognition", "new"),
            ],
        )
        _set_version(conn, 19)

        migrate_v19_to_v20(conn)

        cols = {r[1] for r in conn.execute("PRAGMA table_info(collocation_directions)")}
        assert "bury_kind" in cols
        rows = conn.execute(
            "SELECT collocation_id, state, bury_kind FROM collocation_directions ORDER BY collocation_id"
        ).fetchall()
        # Buried rows → 'user' (preserved across rollover)
        assert rows[0]["bury_kind"] == "user"
        assert rows[1]["bury_kind"] == "user"
        # Non-buried rows → NULL
        assert rows[2]["bury_kind"] is None
        assert rows[3]["bury_kind"] is None
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 20

        # Idempotent
        migrate_v19_to_v20(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 20

    def test_migrates_v20_to_v21_adds_sentence_translation(self, tmp_path):
        """v21 adds sentence_translation column to collocations with default ''."""
        import sqlite3

        from app.srs.migrations import _set_version, migrate_v20_to_v21

        conn = sqlite3.connect(str(tmp_path / "test.db"))
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE collocations (
            id INTEGER PRIMARY KEY, text TEXT, source_sentence TEXT NOT NULL DEFAULT ''
        )""")
        conn.execute("INSERT INTO collocations (id, text, source_sentence) VALUES (1, 'test', 'Kje je banka?')")
        _set_version(conn, 20)

        migrate_v20_to_v21(conn)

        cols = {r[1] for r in conn.execute("PRAGMA table_info(collocations)")}
        assert "sentence_translation" in cols
        row = conn.execute("SELECT sentence_translation FROM collocations WHERE id = 1").fetchone()
        assert row["sentence_translation"] == ""
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 21

        # Idempotent
        migrate_v20_to_v21(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 21

    def test_migrates_v21_to_v22_expands_media_kind(self, tmp_path):
        """v22 recreates media table without CHECK constraint, allowing audio_tts_sentence."""
        import sqlite3

        from app.srs.migrations import _set_version, migrate_v21_to_v22

        conn = sqlite3.connect(str(tmp_path / "test.db"))
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collocation_id INTEGER,
            kind TEXT NOT NULL CHECK(kind IN ('image','audio_forvo','audio_tts')),
            filename TEXT NOT NULL,
            path TEXT,
            anki_filename TEXT,
            sha256 TEXT,
            bytes INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )""")
        conn.execute("INSERT INTO media (kind, filename) VALUES ('audio_tts', 'tts_test.mp3')")
        _set_version(conn, 21)

        migrate_v21_to_v22(conn)

        # Verify we can insert audio_tts_sentence now
        conn.execute(
            "INSERT INTO media (collocation_id, kind, filename) VALUES (1, 'audio_tts_sentence', 'tts_sentence_abc.mp3')",
        )
        rows = conn.execute("SELECT kind FROM media WHERE kind = 'audio_tts_sentence'").fetchall()
        assert len(rows) == 1
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 22

        # Idempotent
        migrate_v21_to_v22(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 22

    def test_migrate_v22_to_v23_marks_audio_dirty_on_synthesized_cloze(self, tmp_path):
        """v23 marks audio dirty on cloze rows with sentence audio + anki_note_id."""
        import sqlite3

        from app.srs.migrations import _set_version, migrate_v22_to_v23

        conn = sqlite3.connect(str(tmp_path / "test.db"))
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE collocations (
                id INTEGER PRIMARY KEY,
                guid TEXT,
                card_type TEXT,
                anki_note_id INTEGER,
                dirty_fields TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collocation_id INTEGER,
                kind TEXT
            )
        """)
        # Cloze with audio + anki_note_id → should be marked
        conn.execute(
            "INSERT INTO collocations (id, guid, card_type, anki_note_id, dirty_fields) VALUES (1, 'g1', 'cloze', 100, '')"
        )
        # Cloze without audio → should NOT be marked
        conn.execute(
            "INSERT INTO collocations (id, guid, card_type, anki_note_id, dirty_fields) VALUES (2, 'g2', 'cloze', 101, '')"
        )
        # Vocab with audio → should NOT be marked
        conn.execute(
            "INSERT INTO collocations (id, guid, card_type, anki_note_id, dirty_fields) VALUES (3, 'g3', 'vocab', 102, '')"
        )
        # Cloze with audio but no anki_note_id → should NOT be marked
        conn.execute(
            "INSERT INTO collocations (id, guid, card_type, anki_note_id, dirty_fields) VALUES (4, 'g4', 'cloze', NULL, '')"
        )
        conn.execute("INSERT INTO media (collocation_id, kind) VALUES (1, 'audio_tts_sentence')")
        _set_version(conn, 22)

        migrate_v22_to_v23(conn)

        rows = {
            r["id"]: r["dirty_fields"] for r in conn.execute("SELECT id, dirty_fields FROM collocations").fetchall()
        }
        assert rows[1] == "audio"  # cloze + audio + anki_note_id
        assert rows[2] == ""  # cloze + no audio
        assert rows[3] == ""  # vocab
        assert rows[4] == ""  # cloze + no anki_note_id
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 23

    def test_migrate_v22_to_v23_idempotent(self, tmp_path):
        """Running v23 migration twice leaves dirty_fields unchanged."""
        import sqlite3

        from app.srs.migrations import _set_version, migrate_v22_to_v23

        conn = sqlite3.connect(str(tmp_path / "test2.db"))
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE collocations (
                id INTEGER PRIMARY KEY,
                guid TEXT,
                card_type TEXT,
                anki_note_id INTEGER,
                dirty_fields TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collocation_id INTEGER,
                kind TEXT
            )
        """)
        conn.execute(
            "INSERT INTO collocations (id, guid, card_type, anki_note_id, dirty_fields) VALUES (1, 'g1', 'cloze', 100, '')"
        )
        conn.execute("INSERT INTO media (collocation_id, kind) VALUES (1, 'audio_tts_sentence')")
        _set_version(conn, 22)

        migrate_v22_to_v23(conn)
        migrate_v22_to_v23(conn)

        rows = conn.execute("SELECT dirty_fields FROM collocations WHERE id = 1").fetchall()
        assert rows[0]["dirty_fields"] == "audio"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 23

    def test_migrates_v14_to_v15_fills_null_lemma(self, tmp_path):
        """v15 fills lemma for single-word rows that have lemma=NULL."""
        import sqlite3

        from app.srs.migrations import _set_version, migrate_v14_to_v15

        conn = sqlite3.connect(str(tmp_path / "test.db"))
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE collocations (id INTEGER PRIMARY KEY, text TEXT, word_count INTEGER, lemma TEXT)")
        conn.execute("INSERT INTO collocations (text, word_count, lemma) VALUES ('banka', 1, NULL)")
        conn.execute("INSERT INTO collocations (text, word_count, lemma) VALUES ('dober dan', 2, NULL)")
        _set_version(conn, 14)

        migrate_v14_to_v15(conn)

        rows = {r["text"]: r["lemma"] for r in conn.execute("SELECT text, lemma FROM collocations").fetchall()}
        assert rows["banka"] == "banka"  # single-word: filled
        assert rows["dober dan"] is None  # multi-word: left as NULL
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 15

        # Idempotent
        migrate_v14_to_v15(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 15

    def test_migrates_v13_to_v14_adds_anki_card_mod(self, tmp_path):
        """v14 adds anki_card_mod to mirror Anki's cards.mod for fnvhash tiebreak."""
        import sqlite3

        from app.srs.migrations import _column_exists, _set_version, migrate_v13_to_v14

        conn = sqlite3.connect(str(tmp_path / "test.db"))
        conn.execute(
            "CREATE TABLE collocation_directions ("
            "collocation_id INTEGER, direction TEXT, "
            "anki_card_id INTEGER, anki_due INTEGER)"
        )
        _set_version(conn, 13)
        assert not _column_exists(conn, "collocation_directions", "anki_card_mod")

        migrate_v13_to_v14(conn)

        assert _column_exists(conn, "collocation_directions", "anki_card_mod")
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 14

        # Idempotent
        migrate_v13_to_v14(conn)
        assert _column_exists(conn, "collocation_directions", "anki_card_mod")

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
        assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_VERSION
        cols = {r[1] for r in conn.execute("PRAGMA table_info(collocation_directions)").fetchall()}
        assert "anki_due" in cols
        assert "left" in cols
        assert "due_at" in cols


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
        assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_VERSION


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
                last_rating INTEGER,
                anki_due INTEGER,
                PRIMARY KEY (collocation_id, direction)
            )
        """)
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
        conn.execute("""
            CREATE TABLE collocation_tags (
                collocation_id INTEGER NOT NULL REFERENCES collocations(id) ON DELETE CASCADE,
                tag TEXT NOT NULL,
                PRIMARY KEY (collocation_id, tag)
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

    def test_full_migrate_includes_v9(self):
        """migrate() runs all migrations including v8→v9 and ends at CURRENT_VERSION=10."""
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


class TestMigrateV10ToV11:
    """Tests for migration from v10 to v11 (add left and due_at columns)."""

    def _make_v10_conn(self):
        """Create an in-memory DB with v10 schema."""
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row

        # Create collocations table (v1)
        conn.execute("""
            CREATE TABLE collocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT UNIQUE NOT NULL,
                translation TEXT NOT NULL DEFAULT '',
                language_code TEXT NOT NULL DEFAULT 'sl'
            )
        """)

        # Create collocation_directions table (v3 schema, before v10)
        conn.execute("""
            CREATE TABLE collocation_directions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collocation_id INTEGER NOT NULL,
                direction TEXT NOT NULL,
                due_date TEXT,
                stability REAL NOT NULL DEFAULT 0,
                difficulty REAL NOT NULL DEFAULT 0,
                reps INTEGER NOT NULL DEFAULT 0,
                lapses INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'new',
                last_review TEXT,
                anki_card_id INTEGER,
                anki_due INTEGER,
                dirty_fsrs BOOLEAN NOT NULL DEFAULT 0,
                last_synced_at TEXT,
                last_rating INTEGER,
                FOREIGN KEY (collocation_id) REFERENCES collocations(id)
            )
        """)

        # Add v9 column (last_review_time_ms)
        conn.execute("ALTER TABLE collocation_directions ADD COLUMN last_review_time_ms INTEGER NOT NULL DEFAULT 0")

        conn.execute("PRAGMA user_version = 10")
        conn.commit()
        return conn

    def test_adds_left_and_due_at_columns(self, tmp_path):
        """migrate_v10_to_v11 adds left INTEGER and due_at TEXT columns."""
        from app.srs.migrations import migrate_v10_to_v11

        conn = self._make_v10_conn()
        migrate_v10_to_v11(conn)

        columns = [row[1] for row in conn.execute("PRAGMA table_info(collocation_directions)").fetchall()]
        assert "left" in columns, f"left column not found in {columns}"
        assert "due_at" in columns, f"due_at column not found in {columns}"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 11

    def test_idempotent_v10_to_v11(self, tmp_path):
        """Running migrate_v10_to_v11 twice does not raise."""
        from app.srs.migrations import migrate_v10_to_v11

        conn = self._make_v10_conn()
        migrate_v10_to_v11(conn)
        migrate_v10_to_v11(conn)  # second call should not raise
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 11


class TestMigrateV11ToV12:
    """Tests for migration from v11 to v12 (repair state='new' + last_review invariant)."""

    def _make_v11_conn(self):
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE collocation_directions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collocation_id INTEGER NOT NULL,
                direction TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'new',
                reps INTEGER NOT NULL DEFAULT 0,
                last_review TEXT
            )
        """)
        conn.execute("PRAGMA user_version = 11")
        conn.commit()
        return conn

    def test_clears_last_review_on_invariant_violations(self):
        from app.srs.migrations import migrate_v11_to_v12

        conn = self._make_v11_conn()
        # Bad row: state=new with last_review set and reps=0 — the bug pattern.
        conn.execute(
            "INSERT INTO collocation_directions (collocation_id, direction, state, reps, last_review) "
            "VALUES (1, 'production', 'new', 0, '2026-05-05T12:00:00+00:00')"
        )
        # Healthy review row — must be untouched.
        conn.execute(
            "INSERT INTO collocation_directions (collocation_id, direction, state, reps, last_review) "
            "VALUES (2, 'production', 'review', 1, '2026-05-05T12:00:00+00:00')"
        )
        # Healthy new row — must be untouched.
        conn.execute(
            "INSERT INTO collocation_directions (collocation_id, direction, state, reps, last_review) "
            "VALUES (3, 'production', 'new', 0, NULL)"
        )

        migrate_v11_to_v12(conn)

        rows = {r["collocation_id"]: r for r in conn.execute("SELECT * FROM collocation_directions")}
        assert rows[1]["last_review"] is None  # repaired
        assert rows[2]["last_review"] == "2026-05-05T12:00:00+00:00"  # untouched
        assert rows[3]["last_review"] is None  # untouched
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 12

    def test_idempotent_v11_to_v12(self):
        from app.srs.migrations import migrate_v11_to_v12

        conn = self._make_v11_conn()
        migrate_v11_to_v12(conn)
        migrate_v11_to_v12(conn)  # second call must not raise or change state
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 12


class TestMigrateV12ToV13:
    """Tests for migration from v12 to v13 (add prior_state/prior_left/prior_stability)."""

    def _make_v12_conn(self):
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE collocation_directions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collocation_id INTEGER NOT NULL,
                direction TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'new',
                reps INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("PRAGMA user_version = 12")
        conn.commit()
        return conn

    def test_adds_prior_state_columns(self):
        from app.srs.migrations import migrate_v12_to_v13

        conn = self._make_v12_conn()
        migrate_v12_to_v13(conn)

        cols = {r[1] for r in conn.execute("PRAGMA table_info(collocation_directions)").fetchall()}
        assert "prior_state" in cols
        assert "prior_left" in cols
        assert "prior_stability" in cols
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 13

    def test_idempotent_v12_to_v13(self):
        """Re-running the migration on a partially-migrated DB skips the existing columns."""
        from app.srs.migrations import migrate_v12_to_v13

        conn = self._make_v12_conn()
        migrate_v12_to_v13(conn)
        migrate_v12_to_v13(conn)  # second call exercises the column-already-exists branches
        cols = {r[1] for r in conn.execute("PRAGMA table_info(collocation_directions)").fetchall()}
        assert "prior_state" in cols
        assert "prior_left" in cols
        assert "prior_stability" in cols
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
