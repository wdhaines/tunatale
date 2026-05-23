"""Tests for the bogus tt_revlog row cleanup one-shot script."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from app.anki.cleanup_bogus_tt_revlog_rows import (
    BOGUS_ID_CUTOFF,
    cleanup_bogus_tt_revlog_rows,
    main,
)


def _create_tt_db(tmp_path: Path) -> Path:
    """Create a file-based TT DB with just the tt_revlog table."""
    path = tmp_path / "tunatale.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE tt_revlog (
            id INTEGER PRIMARY KEY,
            collocation_id INTEGER NOT NULL,
            direction TEXT NOT NULL,
            button_chosen INTEGER NOT NULL,
            interval INTEGER NOT NULL,
            last_interval INTEGER NOT NULL,
            factor INTEGER NOT NULL,
            taken_millis INTEGER NOT NULL,
            review_kind INTEGER NOT NULL,
            anki_card_id INTEGER
        )
        """
    )
    conn.commit()
    conn.close()
    return path


def _insert_row(
    db_path: Path,
    *,
    id: int,
    collocation_id: int = 1,
    direction: str = "production",
    button_chosen: int = 3,
    taken_millis: int = 0,
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO tt_revlog VALUES (?, ?, ?, ?, 0, 0, 0, ?, 1, NULL)",
        (id, collocation_id, direction, button_chosen, taken_millis),
    )
    conn.commit()
    conn.close()


def _count_rows(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    n = conn.execute("SELECT COUNT(*) FROM tt_revlog").fetchone()[0]
    conn.close()
    return n


class TestCleanup:
    def test_deletes_rows_below_cutoff(self, tmp_path: Path) -> None:
        db = _create_tt_db(tmp_path)
        _insert_row(db, id=2019, collocation_id=573, taken_millis=2019)  # bogus
        _insert_row(db, id=3208, collocation_id=564, taken_millis=3208)  # bogus
        _insert_row(db, id=1_700_000_000_000, collocation_id=1)  # real

        summary = cleanup_bogus_tt_revlog_rows(db)

        assert summary["bogus_rows_found"] == 2
        assert summary["bogus_rows_deleted"] == 2
        assert summary["affected_directions"] == 2
        assert _count_rows(db) == 1

    def test_dry_run_does_not_delete(self, tmp_path: Path) -> None:
        db = _create_tt_db(tmp_path)
        _insert_row(db, id=2019, collocation_id=573, taken_millis=2019)
        _insert_row(db, id=1_700_000_000_000, collocation_id=1)

        summary = cleanup_bogus_tt_revlog_rows(db, dry_run=True)

        assert summary["bogus_rows_found"] == 1
        assert summary["bogus_rows_deleted"] == 0
        assert _count_rows(db) == 2

    def test_idempotent(self, tmp_path: Path) -> None:
        db = _create_tt_db(tmp_path)
        _insert_row(db, id=2019, collocation_id=573, taken_millis=2019)
        _insert_row(db, id=1_700_000_000_000, collocation_id=1)

        cleanup_bogus_tt_revlog_rows(db)
        # Second run: no bogus rows remain.
        summary = cleanup_bogus_tt_revlog_rows(db)

        assert summary["bogus_rows_found"] == 0
        assert summary["bogus_rows_deleted"] == 0
        assert _count_rows(db) == 1

    def test_cutoff_boundary(self, tmp_path: Path) -> None:
        """A row exactly at the cutoff is kept (strict ``<`` comparison)."""
        db = _create_tt_db(tmp_path)
        _insert_row(db, id=BOGUS_ID_CUTOFF - 1, collocation_id=1)
        _insert_row(db, id=BOGUS_ID_CUTOFF, collocation_id=2)

        summary = cleanup_bogus_tt_revlog_rows(db)

        assert summary["bogus_rows_found"] == 1
        # Cutoff row preserved.
        conn = sqlite3.connect(str(db))
        remaining = [r[0] for r in conn.execute("SELECT id FROM tt_revlog ORDER BY id")]
        conn.close()
        assert remaining == [BOGUS_ID_CUTOFF]

    def test_empty_db_returns_zero(self, tmp_path: Path) -> None:
        db = _create_tt_db(tmp_path)
        summary = cleanup_bogus_tt_revlog_rows(db)
        assert summary == {
            "bogus_rows_found": 0,
            "bogus_rows_deleted": 0,
            "affected_directions": 0,
        }

    def test_affected_directions_dedupes_pairs(self, tmp_path: Path) -> None:
        """Multiple bogus rows for one (coll, direction) count as one direction."""
        db = _create_tt_db(tmp_path)
        _insert_row(db, id=2019, collocation_id=573, direction="production", taken_millis=2019)
        _insert_row(db, id=2130, collocation_id=573, direction="production", taken_millis=2130)
        _insert_row(db, id=2815, collocation_id=843, direction="recognition", taken_millis=2815)

        summary = cleanup_bogus_tt_revlog_rows(db)

        assert summary["bogus_rows_found"] == 3
        assert summary["affected_directions"] == 2

    def test_verbose_logs_each_row(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        db = _create_tt_db(tmp_path)
        _insert_row(db, id=2019, collocation_id=573, direction="production", button_chosen=1, taken_millis=2019)

        with caplog.at_level(logging.INFO, logger="app.anki.cleanup_bogus_tt_revlog_rows"):
            cleanup_bogus_tt_revlog_rows(db, verbose=True)

        assert any("bogus row" in r.message and "coll=573" in r.message for r in caplog.records)

    def test_quiet_does_not_log(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        db = _create_tt_db(tmp_path)
        _insert_row(db, id=2019, collocation_id=573, taken_millis=2019)

        with caplog.at_level(logging.INFO, logger="app.anki.cleanup_bogus_tt_revlog_rows"):
            cleanup_bogus_tt_revlog_rows(db, verbose=False)

        assert not any("bogus row" in r.message for r in caplog.records)


class TestMain:
    def test_main_apply(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        db = _create_tt_db(tmp_path)
        _insert_row(db, id=2019, collocation_id=573, taken_millis=2019)
        _insert_row(db, id=1_700_000_000_000, collocation_id=1)

        monkeypatch.setattr(
            "app.anki.cleanup_bogus_tt_revlog_rows.settings",
            type("S", (), {"database_url": f"sqlite:///{db}"})(),
        )
        rc = main([])
        out = capsys.readouterr().out
        assert rc == 0
        assert "APPLIED" in out
        assert "bogus_rows_found=1" in out
        assert "bogus_rows_deleted=1" in out
        assert _count_rows(db) == 1

    def test_main_dry_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        db = _create_tt_db(tmp_path)
        _insert_row(db, id=2019, collocation_id=573, taken_millis=2019)

        monkeypatch.setattr(
            "app.anki.cleanup_bogus_tt_revlog_rows.settings",
            type("S", (), {"database_url": f"sqlite:///{db}"})(),
        )
        rc = main(["--dry-run"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "DRY-RUN" in out
        assert "bogus_rows_deleted=0" in out
        assert _count_rows(db) == 1  # not deleted

    def test_main_verbose_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        db = _create_tt_db(tmp_path)
        _insert_row(db, id=2019, collocation_id=573, taken_millis=2019)

        monkeypatch.setattr(
            "app.anki.cleanup_bogus_tt_revlog_rows.settings",
            type("S", (), {"database_url": f"sqlite:///{db}"})(),
        )
        with caplog.at_level(logging.INFO):
            main(["--verbose", "--dry-run"])

        assert any("bogus row" in r.message for r in caplog.records)
