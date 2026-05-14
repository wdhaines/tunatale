"""Tests for backend/app/anki/repair_inconsistent_introduced_at.py."""

from __future__ import annotations

import sqlite3

import pytest

from app.anki.repair_inconsistent_introduced_at import apply_repair, find_inconsistent_rows, main


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE collocation_directions (
            collocation_id INTEGER NOT NULL,
            direction TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'new',
            introduced_at TEXT,
            prior_state TEXT,
            PRIMARY KEY (collocation_id, direction)
        );
        """
    )
    conn.commit()
    yield conn
    conn.close()


def _insert(
    db,
    cid: int,
    *,
    direction: str = "recognition",
    state: str = "new",
    introduced_at: str | None = None,
    prior_state: str | None = None,
) -> None:
    db.execute(
        "INSERT INTO collocation_directions (collocation_id, direction, state, introduced_at, prior_state)"
        " VALUES (?, ?, ?, ?, ?)",
        (cid, direction, state, introduced_at, prior_state),
    )


class TestFindInconsistentRows:
    def test_finds_new_with_introduced_at(self, db):
        _insert(db, 1, introduced_at="2026-05-14T13:42:59+00:00", prior_state="new")
        rows = find_inconsistent_rows(db)
        assert rows == [(1, "recognition", "2026-05-14T13:42:59+00:00", "new")]

    def test_ignores_review_with_introduced_at(self, db):
        _insert(db, 1, state="review", introduced_at="2026-05-14T13:42:59+00:00")
        assert find_inconsistent_rows(db) == []

    def test_ignores_new_without_introduced_at(self, db):
        _insert(db, 1, state="new", introduced_at=None)
        assert find_inconsistent_rows(db) == []

    def test_returns_multiple_rows(self, db):
        _insert(db, 1, direction="recognition", introduced_at="2026-05-14T13:42:59+00:00")
        _insert(db, 2, direction="recognition", introduced_at="2026-05-14T13:43:00+00:00")
        _insert(db, 3, direction="production", introduced_at="2026-05-14T13:43:01+00:00")
        assert len(find_inconsistent_rows(db)) == 3


class TestApplyRepair:
    def test_clears_introduced_at_and_prior_state(self, db):
        _insert(db, 1, introduced_at="2026-05-14T13:42:59+00:00", prior_state="new")
        count = apply_repair(db)
        assert count == 1
        row = db.execute(
            "SELECT introduced_at, prior_state FROM collocation_directions WHERE collocation_id=1"
        ).fetchone()
        assert row["introduced_at"] is None
        assert row["prior_state"] is None

    def test_preserves_state(self, db):
        _insert(db, 1, state="new", introduced_at="2026-05-14T13:42:59+00:00")
        apply_repair(db)
        state = db.execute("SELECT state FROM collocation_directions WHERE collocation_id=1").fetchone()[0]
        assert state == "new"

    def test_does_not_touch_non_new_rows(self, db):
        _insert(db, 1, state="review", introduced_at="2026-05-14T13:42:59+00:00")
        count = apply_repair(db)
        assert count == 0
        row = db.execute("SELECT introduced_at FROM collocation_directions WHERE collocation_id=1").fetchone()
        assert row["introduced_at"] == "2026-05-14T13:42:59+00:00"

    def test_noop_when_no_matches(self, db):
        _insert(db, 1, state="new", introduced_at=None)
        assert apply_repair(db) == 0

    def test_idempotent(self, db):
        _insert(db, 1, introduced_at="2026-05-14T13:42:59+00:00")
        assert apply_repair(db) == 1
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
            CREATE TABLE collocation_directions (
                collocation_id INTEGER NOT NULL,
                direction TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'new',
                introduced_at TEXT,
                prior_state TEXT,
                PRIMARY KEY (collocation_id, direction)
            );
            """
        )
        conn.execute(
            "INSERT INTO collocation_directions (collocation_id, direction, state, introduced_at) "
            "VALUES (1, 'recognition', 'new', '2026-05-14T13:42:59+00:00')"
        )
        conn.commit()
        conn.close()
        rc = main(["--tt-db", str(db_path), "--dry-run"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Plan: clear introduced_at on 1" in out
        conn = sqlite3.connect(str(db_path))
        intro = conn.execute("SELECT introduced_at FROM collocation_directions").fetchone()[0]
        conn.close()
        assert intro == "2026-05-14T13:42:59+00:00"

    def test_apply_clears_rows(self, tmp_path):
        db_path = tmp_path / "tt.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE collocation_directions (
                collocation_id INTEGER NOT NULL,
                direction TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'new',
                introduced_at TEXT,
                prior_state TEXT,
                PRIMARY KEY (collocation_id, direction)
            );
            """
        )
        conn.execute(
            "INSERT INTO collocation_directions (collocation_id, direction, state, introduced_at, prior_state) "
            "VALUES (1, 'recognition', 'new', '2026-05-14T13:42:59+00:00', 'new')"
        )
        conn.commit()
        conn.close()
        rc = main(["--tt-db", str(db_path)])
        assert rc == 0
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT introduced_at, prior_state FROM collocation_directions").fetchone()
        conn.close()
        assert row == (None, None)

    def test_apply_with_no_matches_is_noop(self, tmp_path, capsys):
        db_path = tmp_path / "tt.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE collocation_directions (
                collocation_id INTEGER NOT NULL,
                direction TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'new',
                introduced_at TEXT,
                prior_state TEXT,
                PRIMARY KEY (collocation_id, direction)
            );
            """
        )
        conn.commit()
        conn.close()
        rc = main(["--tt-db", str(db_path)])
        assert rc == 0
        assert "No inconsistent introduced_at rows found." in capsys.readouterr().out
