"""Tests for the rolling daily DB-backup rotation (app.storage.db_backup).

Covers the failure net that would have prevented the 2026-07-13 Slovene wipe:
one snapshot per calendar day per DB, kept N days, in a dir outside the repo,
fully self-guarding so a backup hiccup never blocks server startup.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date
from pathlib import Path

from app.storage.db_backup import rotate_db_backups


def _make_db(path: Path, marker: str) -> None:
    """Create a tiny valid SQLite DB with a recognizable row."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES (?)", (marker,))
    conn.commit()
    conn.close()


def _read_marker(path: Path) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT v FROM t").fetchone()[0]
    finally:
        conn.close()


def test_snapshots_are_consistent_copies(tmp_path: Path) -> None:
    src = tmp_path / "tunatale_sl.db"
    _make_db(src, "slovene-data")
    backup_dir = tmp_path / "db-backups"

    written = rotate_db_backups([src], backup_dir, today=date(2026, 7, 14))

    dest = backup_dir / "tunatale_sl.2026-07-14.db"
    assert written == [dest]
    assert dest.exists()
    # A real online-backup copy, not an empty file: the row round-trips.
    assert _read_marker(dest) == "slovene-data"


def test_earliest_of_day_wins_no_overwrite(tmp_path: Path) -> None:
    """A second start on the same day must NOT overwrite the morning's good copy
    (the whole point: a later wipe can't clobber a pre-wipe snapshot)."""
    src = tmp_path / "tunatale_sl.db"
    backup_dir = tmp_path / "db-backups"
    backup_dir.mkdir()
    good = backup_dir / "tunatale_sl.2026-07-14.db"
    _make_db(good, "morning-good")

    # Source now holds post-wipe data; a same-day re-run must leave `good` alone.
    _make_db(src, "post-wipe")
    written = rotate_db_backups([src], backup_dir, today=date(2026, 7, 14))

    assert written == []  # nothing written — today's snapshot already exists
    assert _read_marker(good) == "morning-good"


def test_missing_source_skipped(tmp_path: Path) -> None:
    missing = tmp_path / "nope.db"
    backup_dir = tmp_path / "db-backups"
    assert rotate_db_backups([missing], backup_dir, today=date(2026, 7, 14)) == []


def test_empty_source_skipped(tmp_path: Path) -> None:
    empty = tmp_path / "tunatale_sl.db"
    empty.touch()  # 0 bytes
    backup_dir = tmp_path / "db-backups"
    assert rotate_db_backups([empty], backup_dir, today=date(2026, 7, 14)) == []


def test_keep_days_zero_disables(tmp_path: Path) -> None:
    src = tmp_path / "tunatale_sl.db"
    _make_db(src, "x")
    backup_dir = tmp_path / "db-backups"
    assert rotate_db_backups([src], backup_dir, keep_days=0, today=date(2026, 7, 14)) == []
    assert not backup_dir.exists()


def test_prune_keeps_only_n_most_recent(tmp_path: Path) -> None:
    src = tmp_path / "tunatale_sl.db"
    _make_db(src, "x")
    backup_dir = tmp_path / "db-backups"
    backup_dir.mkdir()
    # Four older daily snapshots already on disk (ISO dates sort chronologically).
    for d in ("2026-07-08", "2026-07-09", "2026-07-10", "2026-07-11"):
        (backup_dir / f"tunatale_sl.{d}.db").write_bytes(b"old")

    rotate_db_backups([src], backup_dir, keep_days=3, today=date(2026, 7, 14))

    remaining = sorted(p.name for p in backup_dir.glob("tunatale_sl.*.db"))
    assert remaining == [
        "tunatale_sl.2026-07-10.db",
        "tunatale_sl.2026-07-11.db",
        "tunatale_sl.2026-07-14.db",
    ]


def test_prune_is_per_stem(tmp_path: Path) -> None:
    """Pruning one language's snapshots must not touch another's."""
    sl = tmp_path / "tunatale_sl.db"
    no = tmp_path / "tunatale_no.db"
    _make_db(sl, "sl")
    _make_db(no, "no")
    backup_dir = tmp_path / "db-backups"
    backup_dir.mkdir()
    for d in ("2026-07-01", "2026-07-02", "2026-07-03"):
        (backup_dir / f"tunatale_no.{d}.db").write_bytes(b"old")

    rotate_db_backups([sl, no], backup_dir, keep_days=2, today=date(2026, 7, 14))

    # sl: only today. no: pruned to 2 newest (today + 2026-07-03).
    assert sorted(p.name for p in backup_dir.glob("tunatale_sl.*.db")) == ["tunatale_sl.2026-07-14.db"]
    assert sorted(p.name for p in backup_dir.glob("tunatale_no.*.db")) == [
        "tunatale_no.2026-07-03.db",
        "tunatale_no.2026-07-14.db",
    ]


def test_prune_removes_sidecars_alongside_their_snapshot(tmp_path: Path) -> None:
    """A pruned snapshot must take its -wal/-shm/-journal with it.

    Anything that opens a snapshot in WAL mode (a restore check, an ad-hoc
    sqlite3 read) leaves sidecars next to it. The sweep globbed `*.db` only, so
    it unlinked the snapshot and left the sidecars behind forever — observed as
    2026-07-16…21 orphans in the real ~/.tunatale/db-backups.
    """
    src = tmp_path / "tunatale_sl.db"
    _make_db(src, "x")
    backup_dir = tmp_path / "db-backups"
    backup_dir.mkdir()
    old = backup_dir / "tunatale_sl.2026-07-08.db"
    old.write_bytes(b"old")
    for suffix in ("-wal", "-shm", "-journal"):
        (backup_dir / f"{old.name}{suffix}").write_bytes(b"side")

    rotate_db_backups([src], backup_dir, keep_days=1, today=date(2026, 7, 14))

    assert sorted(p.name for p in backup_dir.iterdir()) == ["tunatale_sl.2026-07-14.db"]


def test_prune_sweeps_orphaned_sidecars(tmp_path: Path) -> None:
    """Sidecars whose snapshot is already gone are swept too.

    Without this the pre-existing orphans stay forever: their `.db` no longer
    exists, so no future prune pass would ever reach them.
    """
    src = tmp_path / "tunatale_sl.db"
    _make_db(src, "x")
    backup_dir = tmp_path / "db-backups"
    backup_dir.mkdir()
    for suffix in ("-wal", "-shm"):
        (backup_dir / f"tunatale_sl.2026-07-16.db{suffix}").write_bytes(b"orphan")

    rotate_db_backups([src], backup_dir, keep_days=5, today=date(2026, 7, 14))

    assert sorted(p.name for p in backup_dir.iterdir()) == ["tunatale_sl.2026-07-14.db"]


def test_prune_keeps_sidecars_of_retained_snapshots(tmp_path: Path) -> None:
    """The sweep must not reach into a snapshot it is keeping.

    A live -wal holds committed pages not yet checkpointed into the .db; deleting
    it under a retained snapshot would silently truncate that snapshot's data.
    """
    src = tmp_path / "tunatale_sl.db"
    _make_db(src, "x")
    backup_dir = tmp_path / "db-backups"
    backup_dir.mkdir()
    kept = backup_dir / "tunatale_sl.2026-07-13.db"
    kept.write_bytes(b"kept")
    (backup_dir / f"{kept.name}-wal").write_bytes(b"live-pages")

    rotate_db_backups([src], backup_dir, keep_days=5, today=date(2026, 7, 14))

    assert sorted(p.name for p in backup_dir.iterdir()) == [
        "tunatale_sl.2026-07-13.db",
        "tunatale_sl.2026-07-13.db-wal",
        "tunatale_sl.2026-07-14.db",
    ]


def test_prune_sidecar_sweep_is_per_stem(tmp_path: Path) -> None:
    """One language's sweep must not touch another's sidecars."""
    sl = tmp_path / "tunatale_sl.db"
    _make_db(sl, "sl")
    backup_dir = tmp_path / "db-backups"
    backup_dir.mkdir()
    (backup_dir / "tunatale_no.2026-07-16.db-wal").write_bytes(b"other-language")

    rotate_db_backups([sl], backup_dir, keep_days=5, today=date(2026, 7, 14))

    assert (backup_dir / "tunatale_no.2026-07-16.db-wal").exists()


def test_corrupt_source_logged_and_others_continue(tmp_path: Path, caplog) -> None:
    corrupt = tmp_path / "tunatale_sl.db"
    corrupt.write_bytes(b"this is not a sqlite database at all, but non-empty")
    good = tmp_path / "tunatale_no.db"
    _make_db(good, "no-data")
    backup_dir = tmp_path / "db-backups"

    with caplog.at_level(logging.WARNING):
        written = rotate_db_backups([corrupt, good], backup_dir, today=date(2026, 7, 14))

    # Corrupt one skipped + logged; the good one still backed up.
    assert written == [backup_dir / "tunatale_no.2026-07-14.db"]
    assert any("db-backup" in r.message and "tunatale_sl" in r.getMessage() for r in caplog.records)


def test_unwritable_backup_dir_logged_not_raised(tmp_path: Path, caplog) -> None:
    src = tmp_path / "tunatale_sl.db"
    _make_db(src, "x")
    # A regular file where the backup dir's parent must be a directory → mkdir fails.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file")
    backup_dir = blocker / "db-backups"

    with caplog.at_level(logging.WARNING):
        written = rotate_db_backups([src], backup_dir, today=date(2026, 7, 14))

    assert written == []
    assert any("db-backup" in r.getMessage() for r in caplog.records)


def test_defaults_to_today(tmp_path: Path) -> None:
    """today=None uses date.today() — the real call path from lifespan."""
    src = tmp_path / "tunatale_sl.db"
    _make_db(src, "x")
    backup_dir = tmp_path / "db-backups"

    written = rotate_db_backups([src], backup_dir)

    expected = backup_dir / f"tunatale_sl.{date.today().isoformat()}.db"
    assert written == [expected]
