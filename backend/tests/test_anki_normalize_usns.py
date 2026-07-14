"""Tests for app.plugins.anki_sync.normalize_usns (post-full-upload USN repair)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.plugins.anki_sync.normalize_usns import normalize_usns


def _build_db(
    tmp_path: Path,
    *,
    col_usn: int,
    cards: list[tuple[int, int]],
    notes: list[tuple[int, int]],
    revlog: list[tuple[int, int]],
) -> Path:
    """Create minimal Anki DB with col/notes/cards/revlog tables.

    cards/notes/revlog entries: [(id, usn), ...]
    """
    db_path = tmp_path / "collection.anki2"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER,
            ver INTEGER, dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT,
            models TEXT, decks TEXT, dconf TEXT, tags TEXT);
        CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER,
            mod INTEGER, usn INTEGER, tags TEXT, flds TEXT, sfld TEXT,
            csum INTEGER, flags INTEGER, data TEXT);
        CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER,
            ord INTEGER, mod INTEGER, usn INTEGER, type INTEGER, queue INTEGER,
            due INTEGER, ivl INTEGER, factor INTEGER, reps INTEGER,
            lapses INTEGER, left INTEGER, odue INTEGER, odid INTEGER,
            flags INTEGER, data TEXT);
        CREATE TABLE revlog (id INTEGER PRIMARY KEY, cid INTEGER, usn INTEGER,
            ease INTEGER, ivl INTEGER, lastIvl INTEGER, factor INTEGER,
            time INTEGER, type INTEGER);
        """
    )
    conn.execute("INSERT INTO col VALUES (1,0,0,0,18,0,?,0,'{}','{}','{}','{}','{}')", (col_usn,))
    for nid, usn in notes:
        conn.execute(
            "INSERT INTO notes VALUES (?, '', 0, 0, ?, '', '', '', 0, 0, '')",
            (nid, usn),
        )
    for cid, usn in cards:
        conn.execute(
            "INSERT INTO cards VALUES (?, 0, 0, 0, 0, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, '')",
            (cid, usn),
        )
    for rid, usn in revlog:
        conn.execute("INSERT INTO revlog VALUES (?, 0, ?, 0, 0, 0, 0, 0, 0)", (rid, usn))
    conn.commit()
    conn.close()
    return db_path


def _read_usns(db_path: Path, table: str) -> list[tuple[int, int]]:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(f"SELECT id, usn FROM {table} ORDER BY id").fetchall()
    finally:
        conn.close()


class TestNormalizeUsns:
    def test_resets_rows_with_usn_greater_than_col(self, tmp_path):
        db_path = _build_db(
            tmp_path,
            col_usn=0,
            cards=[(1, 0), (2, 5), (3, 28), (4, -1)],
            notes=[(10, 0), (11, 3)],
            revlog=[(100, 0), (101, 7)],
        )
        results = normalize_usns(anki_collection_path=db_path, anki_backup_dir=tmp_path / "bak")
        assert results == {"cards": 2, "notes": 1, "revlog": 1}
        assert _read_usns(db_path, "cards") == [(1, 0), (2, 0), (3, 0), (4, -1)]
        assert _read_usns(db_path, "notes") == [(10, 0), (11, 0)]
        assert _read_usns(db_path, "revlog") == [(100, 0), (101, 0)]

    def test_preserves_usn_minus_one(self, tmp_path):
        """Dirty rows (usn=-1) stay dirty — they still need to sync."""
        db_path = _build_db(
            tmp_path,
            col_usn=10,
            cards=[(1, -1)],
            notes=[(10, -1)],
            revlog=[(100, -1)],
        )
        results = normalize_usns(anki_collection_path=db_path, anki_backup_dir=tmp_path / "bak")
        assert results == {"cards": 0, "notes": 0, "revlog": 0}
        assert _read_usns(db_path, "cards") == [(1, -1)]

    def test_noop_when_everything_clean(self, tmp_path):
        db_path = _build_db(
            tmp_path,
            col_usn=10,
            cards=[(1, 5), (2, 10)],
            notes=[(10, 8)],
            revlog=[(100, 10)],
        )
        results = normalize_usns(anki_collection_path=db_path, anki_backup_dir=tmp_path / "bak")
        assert results == {"cards": 0, "notes": 0, "revlog": 0}

    def test_dry_run_makes_no_changes(self, tmp_path):
        db_path = _build_db(
            tmp_path,
            col_usn=0,
            cards=[(1, 5)],
            notes=[(10, 3)],
            revlog=[(100, 7)],
        )
        results = normalize_usns(
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            dry_run=True,
        )
        assert results == {"cards": 1, "notes": 1, "revlog": 1}
        assert _read_usns(db_path, "cards") == [(1, 5)]
        assert _read_usns(db_path, "notes") == [(10, 3)]
        assert _read_usns(db_path, "revlog") == [(100, 7)]

    def test_uses_settings_defaults_when_args_are_none(self, tmp_path, monkeypatch):
        import app.plugins.anki_sync.normalize_usns as mod

        db_path = _build_db(tmp_path, col_usn=0, cards=[(1, 5)], notes=[], revlog=[])
        backup_dir = tmp_path / "bak_settings"

        class _FakeSettings:
            anki_collection_path = db_path
            anki_backup_dir = backup_dir

        monkeypatch.setattr(mod, "settings", _FakeSettings())
        results = mod.normalize_usns()
        assert results == {"cards": 1, "notes": 0, "revlog": 0}
