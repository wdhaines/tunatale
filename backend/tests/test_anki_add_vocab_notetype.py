"""Tests for app.plugins.anki_sync.add_vocab_notetype (schema migration: create vocab notetype)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.anki.vocab_notetype import NORWEGIAN_VOCAB
from app.plugins.anki_sync.add_vocab_notetype import add_vocab_notetype, run


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, dty INTEGER,
            usn INTEGER, ls INTEGER, conf TEXT, models TEXT, decks TEXT, dconf TEXT, tags TEXT);
        CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB);
        CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT, config BLOB, PRIMARY KEY (ntid, ord));
        CREATE TABLE templates (ntid INTEGER, ord INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB,
            PRIMARY KEY (ntid, ord));
    """)
    conn.execute("INSERT INTO col VALUES (1, 0, 100, 1000, 18, 0, 5, 0, '{}', '{}', '{}', '{}', '{}')")
    return conn


class TestAddVocabNotetypeCore:
    def test_creates_notetype_and_bumps_scm(self):
        conn = _make_conn()
        result = add_vocab_notetype(conn, NORWEGIAN_VOCAB, now_ms=1700000000000)
        assert result == "created"
        row = conn.execute("SELECT id FROM notetypes WHERE name = 'Norwegian Vocabulary'").fetchone()
        assert row is not None
        scm = conn.execute("SELECT scm FROM col").fetchone()["scm"]
        assert scm == 1700000000000  # bumped from 1000

    def test_does_not_touch_col_usn(self):
        conn = _make_conn()
        add_vocab_notetype(conn, NORWEGIAN_VOCAB, now_ms=1700000000000)
        # Layer 61: col.usn is the sync anchor, never clobbered to -1.
        assert conn.execute("SELECT usn FROM col").fetchone()["usn"] == 5

    def test_idempotent_when_notetype_exists(self):
        conn = _make_conn()
        add_vocab_notetype(conn, NORWEGIAN_VOCAB, now_ms=1700000000000)
        scm_before = conn.execute("SELECT scm FROM col").fetchone()["scm"]
        result = add_vocab_notetype(conn, NORWEGIAN_VOCAB, now_ms=1800000000000)
        assert result == "exists"
        # No second notetype, no further scm bump.
        count = conn.execute("SELECT COUNT(*) FROM notetypes WHERE name = 'Norwegian Vocabulary'").fetchone()[0]
        assert count == 1
        assert conn.execute("SELECT scm FROM col").fetchone()["scm"] == scm_before


def _build_collection_file(tmp_path: Path) -> Path:
    db_path = tmp_path / "collection.anki2"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, dty INTEGER,
            usn INTEGER, ls INTEGER, conf TEXT, models TEXT, decks TEXT, dconf TEXT, tags TEXT);
        CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER, mod INTEGER, usn INTEGER,
            tags TEXT, flds TEXT, sfld TEXT, csum INTEGER, flags INTEGER, data TEXT);
        CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, ord INTEGER, mod INTEGER,
            usn INTEGER, type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER, factor INTEGER, reps INTEGER,
            lapses INTEGER, left INTEGER, odue INTEGER, odid INTEGER, flags INTEGER, data TEXT);
        CREATE TABLE revlog (id INTEGER PRIMARY KEY, cid INTEGER, usn INTEGER, ease INTEGER, ivl INTEGER,
            lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER);
        CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB);
        CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT, config BLOB, PRIMARY KEY (ntid, ord));
        CREATE TABLE templates (ntid INTEGER, ord INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB,
            PRIMARY KEY (ntid, ord));
        CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, common BLOB);
    """)
    conn.execute("INSERT INTO col VALUES (1, 0, 100, 1000, 18, 0, 5, 0, '{}', '{}', '{}', '{}', '{}')")
    conn.commit()
    conn.close()
    return db_path


class TestRun:
    def test_run_creates_notetype_in_collection_file(self, tmp_path):
        db_path = _build_collection_file(tmp_path)
        result = run(language_code="no", anki_collection_path=db_path, anki_backup_dir=tmp_path / "bak")
        assert result == "created"
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute("SELECT id FROM notetypes WHERE name = 'Norwegian Vocabulary'").fetchone()
            assert row is not None
        finally:
            conn.close()

    def test_run_is_idempotent(self, tmp_path):
        db_path = _build_collection_file(tmp_path)
        run(language_code="no", anki_collection_path=db_path, anki_backup_dir=tmp_path / "bak")
        result = run(language_code="no", anki_collection_path=db_path, anki_backup_dir=tmp_path / "bak2")
        assert result == "exists"

    def test_run_dry_run_makes_no_change(self, tmp_path):
        db_path = _build_collection_file(tmp_path)
        result = run(language_code="no", anki_collection_path=db_path, anki_backup_dir=tmp_path / "bak", dry_run=True)
        assert result == "dry-run"
        conn = sqlite3.connect(str(db_path))
        try:
            count = conn.execute("SELECT COUNT(*) FROM notetypes WHERE name = 'Norwegian Vocabulary'").fetchone()[0]
            assert count == 0
        finally:
            conn.close()

    def test_run_raises_for_language_without_vocab_notetype(self, tmp_path):
        db_path = _build_collection_file(tmp_path)
        with pytest.raises(ValueError, match="no TT-managed vocab notetype"):
            run(language_code="en", anki_collection_path=db_path, anki_backup_dir=tmp_path / "bak")

    def test_run_dry_run_reports_exists_when_notetype_present(self, tmp_path):
        db_path = _build_collection_file(tmp_path)
        run(language_code="no", anki_collection_path=db_path, anki_backup_dir=tmp_path / "bak")
        # Second call as dry-run: notetype already present → "would be: exists" branch.
        result = run(language_code="no", anki_collection_path=db_path, anki_backup_dir=tmp_path / "bak2", dry_run=True)
        assert result == "dry-run"

    def test_run_uses_settings_defaults_when_paths_none(self, tmp_path, monkeypatch):
        import app.plugins.anki_sync.add_vocab_notetype as mod

        db_path = _build_collection_file(tmp_path)

        class _FakeSettings:
            anki_collection_path = db_path
            anki_backup_dir = tmp_path / "bak_settings"

        monkeypatch.setattr(mod, "settings", _FakeSettings())
        result = run(language_code="no")  # paths default to settings
        assert result == "created"
