"""Tests for backend/app/anki/repair_cloze_lemmas.py."""

from __future__ import annotations

import sqlite3

import pytest

from app.anki.repair_cloze_lemmas import apply_repair, find_broken_rows, main


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
            card_type TEXT NOT NULL DEFAULT 'vocab'
        );
        """
    )
    conn.commit()
    yield conn
    conn.close()


def _insert(db, text: str, lemma: str | None, *, word_count: int = 1, card_type: str = "cloze") -> int:
    db.execute(
        "INSERT INTO collocations (text, lemma, word_count, card_type) VALUES (?, ?, ?, ?)",
        (text, lemma, word_count, card_type),
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


class TestFindBrokenRows:
    def test_finds_empty_lemma(self, db):
        cid = _insert(db, "sem", "")
        broken = find_broken_rows(db)
        assert broken == [(cid, "sem", "", 1)]

    def test_finds_null_lemma(self, db):
        cid = _insert(db, "sem", None)
        broken = find_broken_rows(db)
        assert broken == [(cid, "sem", None, 1)]

    def test_finds_mismatched_lemma(self, db):
        cid = _insert(db, "vsak", "vsakevery")
        broken = find_broken_rows(db)
        assert broken == [(cid, "vsak", "vsakevery", 1)]

    def test_finds_wrong_word_count_on_single_word_text(self, db):
        cid = _insert(db, "sem", "sem", word_count=2)
        broken = find_broken_rows(db)
        assert broken == [(cid, "sem", "sem", 2)]

    def test_ignores_correctly_lemmatized_row(self, db):
        _insert(db, "kje", "kje")
        assert find_broken_rows(db) == []

    def test_ignores_vocab_rows(self, db):
        # vocab rows can legitimately have a non-casefold lemma — leave alone.
        _insert(db, "Banka", "banke", card_type="vocab")
        assert find_broken_rows(db) == []

    def test_ignores_multiword_rows(self, db):
        # Multi-word cloze (text has whitespace) is left alone.
        _insert(db, "dober dan", None, word_count=2)
        assert find_broken_rows(db) == []


class TestApplyRepair:
    def test_fixes_empty_lemma_using_casefold_text(self, db):
        _insert(db, "Sem", "")
        count = apply_repair(db)
        assert count == 1
        row = db.execute("SELECT lemma, word_count FROM collocations WHERE text='Sem'").fetchone()
        assert row["lemma"] == "sem"
        assert row["word_count"] == 1

    def test_fixes_garbled_lemma(self, db):
        _insert(db, "vsak", "vsakevery")
        count = apply_repair(db)
        assert count == 1
        row = db.execute("SELECT lemma, word_count FROM collocations WHERE text='vsak'").fetchone()
        assert row["lemma"] == "vsak"
        assert row["word_count"] == 1

    def test_fixes_word_count_on_single_word_text(self, db):
        _insert(db, "sem", "sem", word_count=2)
        count = apply_repair(db)
        assert count == 1
        row = db.execute("SELECT lemma, word_count FROM collocations WHERE text='sem'").fetchone()
        assert row["lemma"] == "sem"
        assert row["word_count"] == 1

    def test_idempotent_when_nothing_to_fix(self, db):
        _insert(db, "kje", "kje")
        assert apply_repair(db) == 0


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
                word_count INTEGER NOT NULL DEFAULT 1,
                card_type TEXT NOT NULL DEFAULT 'vocab'
            );
            """
        )
        conn.execute(
            "INSERT INTO collocations (text, lemma, word_count, card_type) VALUES (?, ?, 1, 'cloze')",
            ("vsak", "vsakevery"),
        )
        conn.commit()
        conn.close()
        rc = main(["--tt-db", str(db_path), "--dry-run"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Plan: repair 1" in out
        # Verify row was not actually changed
        conn = sqlite3.connect(str(db_path))
        lemma = conn.execute("SELECT lemma FROM collocations WHERE text='vsak'").fetchone()[0]
        conn.close()
        assert lemma == "vsakevery"

    def test_apply_writes_repair(self, tmp_path):
        db_path = tmp_path / "tt.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE collocations (
                id INTEGER PRIMARY KEY,
                text TEXT NOT NULL,
                lemma TEXT,
                word_count INTEGER NOT NULL DEFAULT 1,
                card_type TEXT NOT NULL DEFAULT 'vocab'
            );
            """
        )
        conn.execute(
            "INSERT INTO collocations (text, lemma, word_count, card_type) VALUES (?, ?, 1, 'cloze')",
            ("sem", ""),
        )
        conn.commit()
        conn.close()
        rc = main(["--tt-db", str(db_path)])
        assert rc == 0
        conn = sqlite3.connect(str(db_path))
        lemma = conn.execute("SELECT lemma FROM collocations WHERE text='sem'").fetchone()[0]
        conn.close()
        assert lemma == "sem"

    def test_apply_with_no_broken_rows_is_noop(self, tmp_path, capsys):
        db_path = tmp_path / "tt.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE collocations (
                id INTEGER PRIMARY KEY,
                text TEXT NOT NULL,
                lemma TEXT,
                word_count INTEGER NOT NULL DEFAULT 1,
                card_type TEXT NOT NULL DEFAULT 'vocab'
            );
            """
        )
        conn.execute(
            "INSERT INTO collocations (text, lemma, word_count, card_type) VALUES (?, ?, 1, 'cloze')",
            ("kje", "kje"),
        )
        conn.commit()
        conn.close()
        rc = main(["--tt-db", str(db_path)])
        assert rc == 0
        assert "No broken cloze lemmas" in capsys.readouterr().out
