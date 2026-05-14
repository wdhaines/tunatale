"""Tests for backend/app/anki/cleanup_unsynced_listen_vocab.py."""

from __future__ import annotations

import sqlite3

import pytest

from app.anki.cleanup_unsynced_listen_vocab import apply_cleanup, find_unsynced_listen_vocab, main


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
            anki_note_id INTEGER
        );
        CREATE TABLE collocation_directions (
            collocation_id INTEGER NOT NULL,
            direction TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'new',
            PRIMARY KEY (collocation_id, direction)
        );
        CREATE TABLE media (
            id INTEGER PRIMARY KEY,
            collocation_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            filename TEXT NOT NULL
        );
        CREATE TABLE collocation_tags (
            collocation_id INTEGER NOT NULL,
            tag TEXT NOT NULL,
            PRIMARY KEY (collocation_id, tag)
        );
        """
    )
    conn.commit()
    yield conn
    conn.close()


def _insert(
    db,
    text: str,
    *,
    lemma: str | None = None,
    word_count: int = 1,
    source: str = "llm",
    card_type: str = "vocab",
    anki_note_id: int | None = None,
) -> int:
    db.execute(
        "INSERT INTO collocations (text, lemma, word_count, source, card_type, anki_note_id) VALUES (?, ?, ?, ?, ?, ?)",
        (text, lemma, word_count, source, card_type, anki_note_id),
    )
    cid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return cid


def _add_direction(db, cid: int, direction: str = "recognition") -> None:
    db.execute(
        "INSERT INTO collocation_directions (collocation_id, direction, state) VALUES (?, ?, 'learning')",
        (cid, direction),
    )


def _add_media(db, cid: int) -> None:
    db.execute("INSERT INTO media (collocation_id, kind, filename) VALUES (?, 'image', 'test.jpg')", (cid,))


def _add_tag(db, cid: int, tag: str = "test") -> None:
    db.execute("INSERT INTO collocation_tags (collocation_id, tag) VALUES (?, ?)", (cid, tag))


class TestFindUnsyncedListenVocab:
    def test_finds_llm_vocab_no_anki_id(self, db):
        cid = _insert(db, "ana", lemma="ana")
        rows = find_unsynced_listen_vocab(db)
        assert rows == [(cid, "ana", "ana")]

    def test_ignores_anki_source_rows(self, db):
        _insert(db, "banka", source="anki")
        assert find_unsynced_listen_vocab(db) == []

    def test_ignores_cloze_rows(self, db):
        _insert(db, "kje", card_type="cloze")
        assert find_unsynced_listen_vocab(db) == []

    def test_ignores_rows_with_anki_note_id(self, db):
        _insert(db, "ana", anki_note_id=12345)
        assert find_unsynced_listen_vocab(db) == []

    def test_ignores_multiword_rows(self, db):
        _insert(db, "dober dan", word_count=2)
        assert find_unsynced_listen_vocab(db) == []

    def test_returns_multiple_rows(self, db):
        cid1 = _insert(db, "ana", lemma="ana")
        cid2 = _insert(db, "janez", lemma="janez")
        cid3 = _insert(db, "mesto", lemma="mesto")
        rows = find_unsynced_listen_vocab(db)
        assert len(rows) == 3
        assert rows == [(cid1, "ana", "ana"), (cid2, "janez", "janez"), (cid3, "mesto", "mesto")]

    def test_lemma_can_be_none(self, db):
        cid = _insert(db, "greš", lemma=None)
        rows = find_unsynced_listen_vocab(db)
        assert rows == [(cid, "greš", "")]


class TestApplyCleanup:
    def test_deletes_collocation_and_child_rows(self, db):
        cid = _insert(db, "ana", lemma="ana")
        _add_direction(db, cid)
        _add_media(db, cid)
        _add_tag(db, cid)
        count = apply_cleanup(db)
        assert count == 1
        assert db.execute("SELECT COUNT(*) FROM collocations").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM collocation_directions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM collocation_tags").fetchone()[0] == 0

    def test_deletes_only_matching_rows(self, db):
        _insert(db, "ana", lemma="ana")
        cid_keep = _insert(db, "banka", lemma="banka", source="anki")
        count = apply_cleanup(db)
        assert count == 1
        remaining = db.execute("SELECT id FROM collocations").fetchall()
        assert [r[0] for r in remaining] == [cid_keep]

    def test_noop_when_no_matches(self, db):
        _insert(db, "kje", card_type="cloze")
        assert apply_cleanup(db) == 0

    def test_idempotent(self, db):
        cid = _insert(db, "ana", lemma="ana")
        _add_direction(db, cid)
        assert apply_cleanup(db) == 1
        assert apply_cleanup(db) == 0


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
                source TEXT NOT NULL DEFAULT 'corpus',
                card_type TEXT NOT NULL DEFAULT 'vocab',
                anki_note_id INTEGER
            );
            """
        )
        conn.execute(
            "INSERT INTO collocations (text, lemma, word_count, source, card_type) VALUES (?, ?, 1, 'llm', 'vocab')",
            ("ana", "ana"),
        )
        conn.commit()
        conn.close()
        rc = main(["--tt-db", str(db_path), "--dry-run"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Plan: delete 1" in out
        conn = sqlite3.connect(str(db_path))
        remaining = conn.execute("SELECT COUNT(*) FROM collocations").fetchone()[0]
        conn.close()
        assert remaining == 1

    def test_apply_deletes_rows(self, tmp_path):
        db_path = tmp_path / "tt.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE collocations (
                id INTEGER PRIMARY KEY,
                text TEXT NOT NULL,
                lemma TEXT,
                word_count INTEGER NOT NULL DEFAULT 1,
                source TEXT NOT NULL DEFAULT 'corpus',
                card_type TEXT NOT NULL DEFAULT 'vocab',
                anki_note_id INTEGER
            );
            CREATE TABLE collocation_directions (
                collocation_id INTEGER NOT NULL,
                direction TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'new',
                PRIMARY KEY (collocation_id, direction)
            );
            CREATE TABLE media (
                id INTEGER PRIMARY KEY,
                collocation_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                filename TEXT NOT NULL
            );
            CREATE TABLE collocation_tags (
                collocation_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                PRIMARY KEY (collocation_id, tag)
            );
            """
        )
        conn.execute(
            "INSERT INTO collocations (text, lemma, word_count, source, card_type) VALUES (?, ?, 1, 'llm', 'vocab')",
            ("ana", "ana"),
        )
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO collocation_directions (collocation_id, direction, state) VALUES (?, 'recognition', 'learning')",
            (cid,),
        )
        conn.commit()
        conn.close()
        rc = main(["--tt-db", str(db_path)])
        assert rc == 0
        conn = sqlite3.connect(str(db_path))
        coll_count = conn.execute("SELECT COUNT(*) FROM collocations").fetchone()[0]
        dir_count = conn.execute("SELECT COUNT(*) FROM collocation_directions").fetchone()[0]
        conn.close()
        assert coll_count == 0
        assert dir_count == 0

    def test_apply_with_no_matches_is_noop(self, tmp_path, capsys):
        db_path = tmp_path / "tt.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE collocations (
                id INTEGER PRIMARY KEY,
                text TEXT NOT NULL,
                lemma TEXT,
                word_count INTEGER NOT NULL DEFAULT 1,
                source TEXT NOT NULL DEFAULT 'corpus',
                card_type TEXT NOT NULL DEFAULT 'vocab',
                anki_note_id INTEGER
            );
            """
        )
        conn.execute(
            "INSERT INTO collocations (text, lemma, word_count, source, card_type) VALUES (?, ?, 1, 'anki', 'vocab')",
            ("banka", "banka"),
        )
        conn.commit()
        conn.close()
        rc = main(["--tt-db", str(db_path)])
        assert rc == 0
        assert "No unsynced /listen vocab rows found." in capsys.readouterr().out
