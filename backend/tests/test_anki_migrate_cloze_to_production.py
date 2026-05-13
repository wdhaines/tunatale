"""Tests for backend/app/anki/migrate_cloze_to_production.py."""

from __future__ import annotations

import sqlite3

import pytest

from app.anki.migrate_cloze_to_production import run_migration


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE collocations (
            id INTEGER PRIMARY KEY,
            text TEXT,
            card_type TEXT NOT NULL DEFAULT 'vocab'
        );
        CREATE TABLE collocation_directions (
            id INTEGER PRIMARY KEY,
            collocation_id INTEGER NOT NULL,
            direction TEXT NOT NULL,
            FOREIGN KEY (collocation_id) REFERENCES collocations(id)
        );
    """)
    conn.commit()
    yield conn
    conn.close()


def _add_collocation(db, text: str, card_type: str, directions: list[str]) -> int:
    db.execute(
        "INSERT INTO collocations (text, card_type) VALUES (?, ?)",
        (text, card_type),
    )
    coll_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    for d in directions:
        db.execute(
            "INSERT INTO collocation_directions (collocation_id, direction) VALUES (?, ?)",
            (coll_id, d),
        )
    db.commit()
    return coll_id


class TestMigrateClozeToProduction:
    def test_cloze_recognition_only_flips_to_production(self, db):
        """Cloze collocation with recognition row only → row flips to production."""
        _add_collocation(db, "sem", "cloze", ["recognition"])
        result = run_migration(db)
        assert result["flipped"] == 1
        row = db.execute("SELECT direction FROM collocation_directions WHERE collocation_id = 1").fetchone()
        assert row["direction"] == "production"

    def test_cloze_both_directions_left_alone(self, db):
        """Cloze collocation with both directions → leave alone."""
        _add_collocation(db, "vsak", "cloze", ["recognition", "production"])
        result = run_migration(db)
        assert result["flipped"] == 0
        assert result["skipped_both_directions"] == 1
        dirs = {
            r["direction"]
            for r in db.execute("SELECT direction FROM collocation_directions WHERE collocation_id = 1").fetchall()
        }
        assert dirs == {"recognition", "production"}

    def test_vocab_collocation_left_alone(self, db):
        """Vocab collocation with recognition + production → leave alone."""
        _add_collocation(db, "banka", "vocab", ["recognition", "production"])
        result = run_migration(db)
        assert result["flipped"] == 0
        assert result["skipped_vocab"] == 0  # only checks cloze rows
        dirs = {
            r["direction"]
            for r in db.execute("SELECT direction FROM collocation_directions WHERE collocation_id = 1").fetchall()
        }
        assert dirs == {"recognition", "production"}

    def test_dry_run_does_not_write(self, db):
        """Dry run reports counts without modifying rows."""
        _add_collocation(db, "sem", "cloze", ["recognition"])
        result = run_migration(db, dry_run=True)
        assert result["flipped"] == 1
        # Row still says recognition (dry run didn't write)
        row = db.execute("SELECT direction FROM collocation_directions WHERE collocation_id = 1").fetchone()
        assert row["direction"] == "recognition"
