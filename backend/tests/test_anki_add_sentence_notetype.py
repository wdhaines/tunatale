"""Tests for app.plugins.anki_sync.add_sentence_notetype (schema migration: create "TT Sentence")."""

from __future__ import annotations

import sqlite3

from app.cards.sentence_notetype import NAME
from app.plugins.anki_sync.add_sentence_notetype import add_sentence_notetype, run
from tests.test_anki_add_vocab_notetype import _build_collection_file, _make_conn


class TestAddSentenceNotetype:
    def test_creates_notetype_and_bumps_scm_not_usn(self):
        conn = _make_conn()
        assert add_sentence_notetype(conn, now_ms=1700000000000) == "created"
        mid, mtime = conn.execute("SELECT id, mtime_secs FROM notetypes WHERE name = ?", (NAME,)).fetchone()
        assert mid == 1700000000000
        assert mtime == 1700000000
        assert tuple(conn.execute("SELECT scm, mod, usn FROM col").fetchone()) == (1700000000000, 1700000000000, 5)

    def test_id_stays_above_existing_notetypes(self):
        conn = _make_conn()
        conn.execute("INSERT INTO notetypes VALUES (1800000000000, 'Other', 0, 0, x'')")
        add_sentence_notetype(conn, now_ms=1700000000000)
        assert conn.execute("SELECT id FROM notetypes WHERE name = ?", (NAME,)).fetchone()[0] == 1800000000001

    def test_idempotent(self):
        conn = _make_conn()
        add_sentence_notetype(conn, now_ms=1700000000000)
        assert add_sentence_notetype(conn, now_ms=1800000000000) == "exists"
        assert conn.execute("SELECT COUNT(*) FROM notetypes WHERE name = ?", (NAME,)).fetchone()[0] == 1
        assert conn.execute("SELECT scm FROM col").fetchone()[0] == 1700000000000


class TestRun:
    def test_creates_then_exists(self, tmp_path):
        db = _build_collection_file(tmp_path)
        assert run(anki_collection_path=db, anki_backup_dir=tmp_path / "b1") == "created"
        assert run(anki_collection_path=db, anki_backup_dir=tmp_path / "b2") == "exists"

    def test_dry_run_changes_nothing(self, tmp_path, capsys):
        db = _build_collection_file(tmp_path)
        assert run(anki_collection_path=db, anki_backup_dir=tmp_path / "b", dry_run=True) == "dry-run"
        assert "would be: created" in capsys.readouterr().out
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT COUNT(*) FROM notetypes").fetchone()[0] == 0

    def test_dry_run_reports_an_existing_notetype(self, tmp_path, capsys):
        db = _build_collection_file(tmp_path)
        run(anki_collection_path=db, anki_backup_dir=tmp_path / "b1")
        run(anki_collection_path=db, anki_backup_dir=tmp_path / "b2", dry_run=True)
        assert "would be: exists" in capsys.readouterr().out

    def test_paths_default_to_settings(self, tmp_path, monkeypatch):
        import app.plugins.anki_sync.add_sentence_notetype as mod

        db = _build_collection_file(tmp_path)

        class _Settings:
            anki_collection_path = db
            anki_backup_dir = tmp_path / "b"

        monkeypatch.setattr(mod, "settings", _Settings())
        assert run() == "created"
