"""Rolling daily backups of the per-language SQLite content/SRS DBs.

``tunatale_sl.db`` / ``tunatale_no.db`` are the ONLY store of curricula, lessons,
and — critically — the FSRS scheduling state that is not mirrored in Anki. They
are git-ignored and, before this module, had no backup layer at all: a stray E2E
run pointed at the real DB (a ``DATABASE_URLS`` casing bug) silently wiped the
Slovene curricula twice (2026-06-30, 2026-07-13).

This snapshots each DB once per calendar day into a directory OUTSIDE the repo
(so an in-repo ``rm -f`` / glob can't reach it) and keeps the N most recent daily
snapshots. Called unconditionally at app startup (``app.main.lifespan``); fully
self-guarding — never raises, so a backup hiccup can't block the server booting.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)


def _snapshot(src: Path, dest: Path) -> None:
    """Consistent copy of a live SQLite DB via the online-backup API.

    Opens the source read-only (``mode=ro`` — we never write the DB we're
    protecting) and uses ``Connection.backup`` so an active ``-wal`` is folded in
    and the copy is transactionally consistent even if the app is mid-write.
    """
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        dest_conn = sqlite3.connect(dest)
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()


def _prune(backup_dir: Path, stem: str, keep: int) -> None:
    """Keep the ``keep`` most recent ``{stem}.*.db`` snapshots; unlink older ones.

    ISO date filenames sort chronologically, so lexical sort == oldest-first.
    Scoped to ``stem`` so pruning one language never touches another's snapshots.
    """
    snapshots = sorted(backup_dir.glob(f"{stem}.*.db"))
    for old in snapshots[:-keep]:
        old.unlink()


def rotate_db_backups(
    db_paths: Iterable[Path | str],
    backup_dir: Path,
    *,
    keep_days: int = 5,
    today: date | None = None,
) -> list[Path]:
    """Snapshot each existing DB into ``backup_dir/{stem}.{YYYY-MM-DD}.db`` (once
    per calendar day — the earliest start of the day wins, so a later wipe cannot
    overwrite the morning's good copy), then prune to the ``keep_days`` most
    recent daily snapshots per DB.

    Returns the snapshot files written this call (empty if none / disabled).
    Never raises: a missing/empty/corrupt source, or an unwritable backup dir, is
    logged and skipped so startup is never blocked. ``keep_days <= 0`` disables.
    """
    if keep_days <= 0:
        return []
    day = today or date.today()
    written: list[Path] = []
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("db-backup: cannot create backup dir %s: %s", backup_dir, exc)
        return written
    for raw in db_paths:
        src = Path(raw)
        try:
            if not src.exists() or src.stat().st_size == 0:
                continue
            dest = backup_dir / f"{src.stem}.{day.isoformat()}.db"
            if not dest.exists():
                _snapshot(src, dest)
                written.append(dest)
            _prune(backup_dir, src.stem, keep_days)
        except (OSError, sqlite3.Error) as exc:
            logger.warning("db-backup: failed for %s: %s", src, exc)
    return written
