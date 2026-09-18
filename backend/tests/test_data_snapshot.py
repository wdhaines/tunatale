"""The data-transfer helper: consistent SQLite snapshots and a count comparison.

`scripts/data_snapshot.py` is the half of `data-transfer.sh` that touches data.
It runs on BOTH machines — the Mac's uv Python and the box's system python3
(3.12, stdlib only) — which is why the 3.12-syntax test exists: ruff targets
3.14 and rewrites `except (A, B):` into PEP 758's bare-comma form, which 3.12
cannot parse. That would pass every test here and fail only on the box.

The snapshot tests are about the one property that makes a transfer safe: a
copy taken while the source is live includes everything COMMITTED, including
rows still sitting in the `-wal` file, which a plain `cp` of the main file
silently drops. This project has lost its curricula to copies twice.
"""

from __future__ import annotations

import ast
import sqlite3
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "data_snapshot.py"
sys.path.insert(0, str(SCRIPT.parent))

from data_snapshot import collect, compare, db_stats, dir_stats, snapshot_db  # noqa: E402


def _wal_db(path: Path, rows: int) -> sqlite3.Connection:
    """A WAL-mode DB whose rows are committed but NOT checkpointed.

    The connection is returned open: closing the last connection checkpoints
    the WAL into the main file, which would make the test pass for a plain cp.
    """
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE lessons (id INTEGER PRIMARY KEY, body TEXT)")
    conn.executemany("INSERT INTO lessons (body) VALUES (?)", [(f"l{i}",) for i in range(rows)])
    conn.commit()
    return conn


def test_the_python_312_on_the_box_can_parse_the_helper():
    ast.parse(SCRIPT.read_text(encoding="utf-8"), feature_version=(3, 12))


class TestSnapshotDb:
    def test_includes_rows_that_exist_only_in_the_wal(self, tmp_path):
        src = tmp_path / "live.db"
        live = _wal_db(src, rows=7)
        try:
            assert (tmp_path / "live.db-wal").stat().st_size > 0, "fixture precondition: rows are in the WAL"
            # Control: the main file alone does NOT hold the rows. Copy it byte for
            # byte, the way `cp live.db` would, and count.
            (tmp_path / "cp.db").write_bytes(src.read_bytes())
            cp_rows = (
                sqlite3.connect(tmp_path / "cp.db")
                .execute("SELECT count(*) FROM sqlite_master WHERE name='lessons'")
                .fetchone()[0]
            )
            assert cp_rows == 0, "control: a plain copy of the main file misses the WAL"

            dst = tmp_path / "snap.db"
            snapshot_db(src, dst)
        finally:
            live.close()

        assert sqlite3.connect(dst).execute("SELECT count(*) FROM lessons").fetchone()[0] == 7

    def test_does_not_modify_the_source(self, tmp_path):
        src = tmp_path / "live.db"
        live = _wal_db(src, rows=3)
        try:
            # The DATA files only. `-shm` is WAL's shared-memory index, which every
            # reader updates (read marks), read-only or not; it holds no data. And
            # files only: an autouse conftest fixture parks a tt-media/ dir here.
            data = ("live.db", "live.db-wal")
            before = {name: (tmp_path / name).read_bytes() for name in data}
            snapshot_db(src, tmp_path / "out" / "snap.db")
            after = {name: (tmp_path / name).read_bytes() for name in data}
        finally:
            live.close()
        assert after == before

    def test_a_missing_source_raises_instead_of_creating_an_empty_db(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            snapshot_db(tmp_path / "nope.db", tmp_path / "snap.db")
        assert not (tmp_path / "nope.db").exists()

    def test_the_snapshot_is_a_standalone_file(self, tmp_path):
        """No -wal beside the copy: it is what gets shipped, alone."""
        src = tmp_path / "live.db"
        live = _wal_db(src, rows=2)
        try:
            snapshot_db(src, tmp_path / "snap.db")
        finally:
            live.close()
        assert not (tmp_path / "snap.db-wal").exists()
        assert sqlite3.connect(tmp_path / "snap.db").execute("PRAGMA journal_mode").fetchone()[0] == "delete"


class TestStats:
    def test_db_stats_counts_every_user_table_and_checks_integrity(self, tmp_path):
        db = tmp_path / "a.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE lessons (id INTEGER PRIMARY KEY AUTOINCREMENT)")
        conn.execute("CREATE TABLE media (id INTEGER)")
        conn.executemany("INSERT INTO lessons DEFAULT VALUES", [()] * 3)
        conn.commit()
        conn.close()

        stats = db_stats(db)

        assert stats == {"integrity": "ok", "tables": {"lessons": 3, "media": 0}}

    def test_dir_stats_counts_files_recursively(self, tmp_path):
        (tmp_path / "d" / "sub").mkdir(parents=True)
        (tmp_path / "d" / "a.mp3").write_bytes(b"12345")
        (tmp_path / "d" / "sub" / "b.png").write_bytes(b"123")
        assert dir_stats(tmp_path / "d") == {"files": 2, "bytes": 8}

    def test_a_missing_dir_is_reported_not_counted_as_empty(self, tmp_path):
        """A missing directory and an empty one must not compare equal: a
        transfer that dropped `media/` entirely would otherwise verify clean."""
        assert dir_stats(tmp_path / "gone") == {"missing": True}

    def test_collect_names_every_item(self, tmp_path):
        db = tmp_path / "x.db"
        sqlite3.connect(db).execute("CREATE TABLE t (id INTEGER)").connection.close()
        (tmp_path / "m").mkdir()
        out = collect({"no": db}, {"media": tmp_path / "m"})
        assert out == {
            "dbs": {"no": {"integrity": "ok", "tables": {"t": 0}}},
            "dirs": {"media": {"files": 0, "bytes": 0}},
        }


class TestCompare:
    BASE = {
        "dbs": {"no": {"integrity": "ok", "tables": {"lessons": 11, "media": 7449}}},
        "dirs": {"media": {"files": 7400, "bytes": 461_000_000}},
    }

    def test_identical_sides_have_no_mismatches(self):
        assert compare(self.BASE, self.BASE) == []

    def test_a_row_count_difference_names_the_table_and_both_counts(self):
        other = {**self.BASE, "dbs": {"no": {"integrity": "ok", "tables": {"lessons": 10, "media": 7449}}}}
        assert compare(self.BASE, other) == ["no.lessons: 11 != 10"]

    def test_a_table_missing_on_one_side_is_a_mismatch(self):
        other = {**self.BASE, "dbs": {"no": {"integrity": "ok", "tables": {"lessons": 11}}}}
        assert compare(self.BASE, other) == ["no.media: 7449 != (absent)"]

    def test_a_failed_integrity_check_is_a_mismatch_even_with_equal_counts(self):
        other = {**self.BASE, "dbs": {"no": {**self.BASE["dbs"]["no"], "integrity": "*** in database main ***"}}}
        assert compare(self.BASE, other) == ["no integrity: 'ok' != '*** in database main ***'"]

    def test_a_directory_difference_is_a_mismatch(self):
        other = {**self.BASE, "dirs": {"media": {"missing": True}}}
        assert compare(self.BASE, other) == ["dir media: {'files': 7400, 'bytes': 461000000} != {'missing': True}"]

    def test_a_database_missing_entirely_is_a_mismatch(self):
        other = {**self.BASE, "dbs": {}}
        assert compare(self.BASE, other) == ["db no: present != (absent)"]


class TestAnkiCollections:
    """TunaTale's own Anki collection (tt_collection.anki2) is transferred too,
    and Anki indexes use its custom `unicase` collation. Plain sqlite3 does not
    know it, so counting or integrity-checking such a table raised
    `no such collation sequence: unicase` on the first real run (2026-09-18)."""

    @staticmethod
    def _anki_like(path: Path) -> None:
        conn = sqlite3.connect(path)
        conn.create_collation("unicase", lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower()))
        conn.execute("CREATE TABLE tags (tag TEXT NOT NULL COLLATE unicase)")
        conn.execute("CREATE INDEX idx_tags ON tags (tag COLLATE unicase)")
        conn.executemany("INSERT INTO tags VALUES (?)", [("Verb",), ("adj",), ("Noun",)])
        conn.commit()
        conn.close()

    def test_stats_work_on_a_table_indexed_with_unicase(self, tmp_path):
        self._anki_like(tmp_path / "col.anki2")
        assert db_stats(tmp_path / "col.anki2") == {"integrity": "ok", "tables": {"tags": 3}}

    def test_snapshot_integrity_checks_a_unicase_collection(self, tmp_path):
        self._anki_like(tmp_path / "col.anki2")
        snapshot_db(tmp_path / "col.anki2", tmp_path / "snap.anki2")
        assert db_stats(tmp_path / "snap.anki2")["tables"] == {"tags": 3}


class TestMissingDatabases:
    """A fresh box has no tt_collection.anki2 yet: `status` must SAY so, not
    crash. A snapshot of a missing file still raises (TestSnapshotDb)."""

    def test_stats_report_a_missing_db(self, tmp_path):
        assert db_stats(tmp_path / "gone.db") == {"missing": True}

    def test_a_db_missing_on_one_side_is_a_mismatch(self):
        present = {"dbs": {"col": {"integrity": "ok", "tables": {"cards": 5}}}, "dirs": {}}
        missing = {"dbs": {"col": {"missing": True}}, "dirs": {}}
        assert compare(present, missing) == ["db col: present != (missing)"]
        assert compare(missing, present) == ["db col: (missing) != present"]

    def test_missing_on_both_sides_is_not_a_mismatch(self):
        missing = {"dbs": {"col": {"missing": True}}, "dirs": {}}
        assert compare(missing, missing) == []
