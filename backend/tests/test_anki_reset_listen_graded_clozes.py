"""Tests for backend/app/anki/reset_listen_graded_clozes.py."""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from app.anki.reset_listen_graded_clozes import apply_reset, find_graded_cloze_rows, main


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE collocations (
            id INTEGER PRIMARY KEY,
            text TEXT NOT NULL,
            lemma TEXT,
            word_count INTEGER NOT NULL DEFAULT 1,
            source TEXT NOT NULL DEFAULT 'corpus',
            card_type TEXT NOT NULL DEFAULT 'vocab',
            anki_note_id INTEGER,
            updated_at TEXT
        );
        CREATE TABLE collocation_directions (
            collocation_id INTEGER NOT NULL,
            direction TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'new',
            reps INTEGER NOT NULL DEFAULT 0,
            lapses INTEGER NOT NULL DEFAULT 0,
            last_review TEXT,
            last_review_time_ms INTEGER NOT NULL DEFAULT 0,
            introduced_at TEXT,
            prior_state TEXT,
            prior_left INTEGER,
            prior_stability REAL,
            left INTEGER,
            due_at TEXT,
            stability REAL NOT NULL DEFAULT 1.0,
            fsrs_difficulty REAL NOT NULL DEFAULT 5.0,
            due_date TEXT NOT NULL,
            dirty_fsrs INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (collocation_id, direction)
        );
        """
    )
    conn.commit()
    yield conn
    conn.close()


def _insert_collocation(
    db,
    text: str,
    *,
    lemma: str | None = None,
    card_type: str = "cloze",
    anki_note_id: int | None = None,
) -> int:
    db.execute(
        "INSERT INTO collocations (text, lemma, card_type, anki_note_id) VALUES (?, ?, ?, ?)",
        (text, lemma, card_type, anki_note_id),
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _add_direction(
    db,
    cid: int,
    state: str = "learning",
) -> None:
    today = date.today().isoformat()
    db.execute(
        "INSERT INTO collocation_directions (collocation_id, direction, state, due_date, reps) VALUES (?, 'production', ?, ?, 1)",
        (cid, state, today),
    )


class TestFindGradedClozeRows:
    def test_finds_learning_cloze_without_anki_id(self, db):
        cid = _insert_collocation(db, "kje", lemma="kje")
        _add_direction(db, cid, state="learning")
        rows = find_graded_cloze_rows(db)
        assert len(rows) == 1
        assert rows[0][1] == cid
        assert rows[0][2] == "kje"

    def test_finds_relearning_cloze_without_anki_id(self, db):
        cid = _insert_collocation(db, "kje", lemma="kje")
        _add_direction(db, cid, state="relearning")
        rows = find_graded_cloze_rows(db)
        assert len(rows) == 1

    def test_ignores_new_state(self, db):
        cid = _insert_collocation(db, "kje", lemma="kje")
        _add_direction(db, cid, state="new")
        assert find_graded_cloze_rows(db) == []

    def test_ignores_cloze_with_anki_id(self, db):
        cid = _insert_collocation(db, "kje", lemma="kje", anki_note_id=12345)
        _add_direction(db, cid, state="learning")
        assert find_graded_cloze_rows(db) == []

    def test_ignores_vocab_rows(self, db):
        cid = _insert_collocation(db, "banka", lemma="banka", card_type="vocab")
        _add_direction(db, cid, state="learning")
        assert find_graded_cloze_rows(db) == []

    def test_returns_multiple_rows(self, db):
        cid1 = _insert_collocation(db, "kje", lemma="kje")
        _add_direction(db, cid1, state="learning")
        cid2 = _insert_collocation(db, "je", lemma="je")
        _add_direction(db, cid2, state="learning")
        rows = find_graded_cloze_rows(db)
        assert len(rows) == 2

    def test_lemma_can_be_none(self, db):
        cid = _insert_collocation(db, "kje", lemma=None)
        _add_direction(db, cid, state="learning")
        rows = find_graded_cloze_rows(db)
        assert rows[0][2] == ""


class TestApplyReset:
    def _assert_reset(self, db, cid: int) -> None:
        row = db.execute(
            "SELECT * FROM collocation_directions WHERE collocation_id = ?",
            (cid,),
        ).fetchone()
        assert row is not None
        assert row["state"] == "new"
        assert row["reps"] == 0
        assert row["lapses"] == 0
        assert row["last_review"] is None
        assert row["last_review_time_ms"] == 0
        assert row["introduced_at"] is None
        assert row["prior_state"] is None
        assert row["prior_left"] is None
        assert row["prior_stability"] is None
        assert row["left"] is None
        assert row["due_at"] is None
        assert row["stability"] == 1.0
        assert row["fsrs_difficulty"] == 5.0
        assert row["due_date"] == date.today().isoformat()
        assert row["dirty_fsrs"] == 0

    def test_resets_matching_row(self, db):
        cid = _insert_collocation(db, "kje", lemma="kje")
        _add_direction(db, cid, state="learning")
        count = apply_reset(db)
        assert count == 1
        self._assert_reset(db, cid)

    def test_resets_relearning_row(self, db):
        cid = _insert_collocation(db, "kje", lemma="kje")
        _add_direction(db, cid, state="relearning")
        count = apply_reset(db)
        assert count == 1
        self._assert_reset(db, cid)

    def test_updates_collocation_updated_at(self, db):
        cid = _insert_collocation(db, "kje", lemma="kje")
        _add_direction(db, cid, state="learning")
        assert db.execute("SELECT updated_at FROM collocations WHERE id = ?", (cid,)).fetchone()[0] is None
        apply_reset(db)
        assert db.execute("SELECT updated_at FROM collocations WHERE id = ?", (cid,)).fetchone()[0] is not None

    def test_resets_only_matching_rows(self, db):
        cid_reset = _insert_collocation(db, "kje", lemma="kje")
        _add_direction(db, cid_reset, state="learning")
        cid_keep = _insert_collocation(db, "banka", lemma="banka", card_type="vocab")
        _add_direction(db, cid_keep, state="learning")
        count = apply_reset(db)
        assert count == 1
        self._assert_reset(db, cid_reset)
        unchanged = db.execute(
            "SELECT state FROM collocation_directions WHERE collocation_id = ?",
            (cid_keep,),
        ).fetchone()
        assert unchanged["state"] == "learning"

    def test_noop_when_no_matches(self, db):
        cid = _insert_collocation(db, "kje", lemma="kje")
        _add_direction(db, cid, state="new")
        assert apply_reset(db) == 0

    def test_idempotent(self, db):
        cid = _insert_collocation(db, "kje", lemma="kje")
        _add_direction(db, cid, state="learning")
        assert apply_reset(db) == 1
        assert apply_reset(db) == 0


class TestMainCli:
    def test_returns_nonzero_when_db_missing(self, tmp_path):
        rc = main(["--tt-db", str(tmp_path / "missing.db")])
        assert rc == 1

    def test_dry_run_reports_without_writing(self, tmp_path, capsys):
        db_path = tmp_path / "tt.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE collocations (
                id INTEGER PRIMARY KEY,
                text TEXT NOT NULL,
                lemma TEXT,
                card_type TEXT NOT NULL DEFAULT 'vocab',
                anki_note_id INTEGER
            );
            CREATE TABLE collocation_directions (
                collocation_id INTEGER NOT NULL,
                direction TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'new',
                reps INTEGER NOT NULL DEFAULT 0,
                lapses INTEGER NOT NULL DEFAULT 0,
                last_review TEXT,
                last_review_time_ms INTEGER NOT NULL DEFAULT 0,
                introduced_at TEXT,
                prior_state TEXT,
                prior_left INTEGER,
                prior_stability REAL,
                left INTEGER,
                due_at TEXT,
                stability REAL NOT NULL DEFAULT 1.0,
                fsrs_difficulty REAL NOT NULL DEFAULT 5.0,
                due_date TEXT NOT NULL,
                dirty_fsrs INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (collocation_id, direction)
            );
            """
        )
        conn.execute(
            "INSERT INTO collocations (text, lemma, card_type) VALUES ('kje', 'kje', 'cloze')",
        )
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO collocation_directions (collocation_id, direction, state, due_date) VALUES (?, 'production', 'learning', ?)",
            (cid, date.today().isoformat()),
        )
        conn.commit()
        conn.close()

        rc = main(["--tt-db", str(db_path), "--dry-run"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Plan: reset 1" in out

        conn = sqlite3.connect(str(db_path))
        remaining = conn.execute("SELECT state FROM collocation_directions").fetchone()[0]
        conn.close()
        assert remaining == "learning"

    def test_apply_resets_rows(self, tmp_path, capsys):
        db_path = tmp_path / "tt.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE collocations (
                id INTEGER PRIMARY KEY,
                text TEXT NOT NULL,
                lemma TEXT,
                card_type TEXT NOT NULL DEFAULT 'vocab',
                anki_note_id INTEGER,
                updated_at TEXT
            );
            CREATE TABLE collocation_directions (
                collocation_id INTEGER NOT NULL,
                direction TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'new',
                reps INTEGER NOT NULL DEFAULT 0,
                lapses INTEGER NOT NULL DEFAULT 0,
                last_review TEXT,
                last_review_time_ms INTEGER NOT NULL DEFAULT 0,
                introduced_at TEXT,
                prior_state TEXT,
                prior_left INTEGER,
                prior_stability REAL,
                left INTEGER,
                due_at TEXT,
                stability REAL NOT NULL DEFAULT 1.0,
                fsrs_difficulty REAL NOT NULL DEFAULT 5.0,
                due_date TEXT NOT NULL,
                dirty_fsrs INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (collocation_id, direction)
            );
            """
        )
        conn.execute(
            "INSERT INTO collocations (text, lemma, card_type) VALUES ('kje', 'kje', 'cloze')",
        )
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO collocation_directions (collocation_id, direction, state, reps, due_date, stability) VALUES (?, 'production', 'learning', 3, ?, 2.5)",
            (cid, date.today().isoformat()),
        )
        conn.commit()
        conn.close()

        rc = main(["--tt-db", str(db_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Reset: 1" in out

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT state, reps FROM collocation_directions").fetchone()
        conn.close()
        assert row["state"] == "new"
        assert row["reps"] == 0

    def test_apply_with_no_matches_is_noop(self, tmp_path, capsys):
        db_path = tmp_path / "tt.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE collocations (
                id INTEGER PRIMARY KEY,
                text TEXT NOT NULL,
                lemma TEXT,
                card_type TEXT NOT NULL DEFAULT 'vocab',
                anki_note_id INTEGER,
                updated_at TEXT
            );
            CREATE TABLE collocation_directions (
                collocation_id INTEGER NOT NULL,
                direction TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'new',
                reps INTEGER NOT NULL DEFAULT 0,
                lapses INTEGER NOT NULL DEFAULT 0,
                last_review TEXT,
                last_review_time_ms INTEGER NOT NULL DEFAULT 0,
                introduced_at TEXT,
                prior_state TEXT,
                prior_left INTEGER,
                prior_stability REAL,
                left INTEGER,
                due_at TEXT,
                stability REAL NOT NULL DEFAULT 1.0,
                fsrs_difficulty REAL NOT NULL DEFAULT 5.0,
                due_date TEXT NOT NULL,
                dirty_fsrs INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (collocation_id, direction)
            );
            """
        )
        conn.execute(
            "INSERT INTO collocations (text, lemma, card_type) VALUES ('banka', 'banka', 'vocab')",
        )
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO collocation_directions (collocation_id, direction, state, due_date) VALUES (?, 'recognition', 'new', ?)",
            (cid, date.today().isoformat()),
        )
        conn.commit()
        conn.close()

        rc = main(["--tt-db", str(db_path)])
        assert rc == 0
        assert "No graded cloze rows found." in capsys.readouterr().out
