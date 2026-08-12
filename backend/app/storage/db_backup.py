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


# SQLite leaves these next to a DB file. A snapshot grows them the moment
# anything opens it (a restore check, an ad-hoc sqlite3 read), so they must be
# pruned with — and only with — the snapshot they belong to.
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


# Matches ONLY the ``{stem}.YYYY-MM-DD.db`` names ``rotate_db_backups`` writes.
# A bare ``{stem}.*.db`` would also match the pre-migration snapshots
# (``{stem}.pre-v41.db``) — and since 'p' sorts after any ISO date, such a
# snapshot would be mistaken for the newest daily backup: it would survive
# pruning itself while evicting a real one. Rotation must be unable to reach
# them, whatever directory they are pointed at.
_DATE_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"


def _prune(backup_dir: Path, stem: str, keep: int) -> None:
    """Keep the ``keep`` most recent daily ``{stem}.{date}.db`` snapshots; unlink older ones.

    ISO date filenames sort chronologically, so lexical sort == oldest-first.
    Scoped to ``stem`` so pruning one language never touches another's snapshots.

    Sidecars are swept by *name*, not by walking the pruned snapshots: an
    interrupted writer can leave a ``-wal``/``-shm`` whose ``.db`` is already
    gone, and such an orphan is unreachable from the snapshot list forever
    (2026-07-16…21 accumulated exactly that way in the real backup dir). A
    sidecar survives only if its base snapshot is one we are keeping — deleting
    a live ``-wal`` under a retained snapshot would truncate its data.
    """
    snapshots = sorted(backup_dir.glob(f"{stem}.{_DATE_GLOB}.db"))
    kept = {p.name for p in snapshots[-keep:]}
    for old in snapshots[:-keep]:
        old.unlink()
    for suffix in _SIDECAR_SUFFIXES:
        for sidecar in backup_dir.glob(f"{stem}.{_DATE_GLOB}.db{suffix}"):
            if sidecar.name.removesuffix(suffix) not in kept:
                sidecar.unlink()


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


def snapshot_before_migration(src: Path | str, backup_dir: Path, *, from_version: int) -> Path:
    """Snapshot ``src`` into ``{backup_dir}/{stem}.pre-v{from_version}.db``.

    Called from ``migrations.migrate`` when — and only when — there is a pending
    migration, so this fires on a deploy that advances the schema, not on every
    boot. It differs from ``rotate_db_backups`` in the two ways that matter:

    - **It never rotates.** The rolling daily snapshots keep 5 days; the one
      snapshot that makes a schema rollback possible must outlive that window,
      because you find out you need it long after the deploy. Nothing in this
      module deletes these files (``_prune`` is scoped to date-shaped names).
    - **It raises.** ``rotate_db_backups`` swallows every error so a backup
      hiccup can't block startup. Here the opposite is correct: failing to
      snapshot and then migrating anyway destroys the only copy of the
      pre-migration state, which is precisely the outcome this exists to
      prevent. A loud failure to boot beats a silent one-way door.

    Tagged with the version being migrated FROM, so the name says which build
    can still open it — and at most one snapshot per schema version is ever
    written. The first one wins: a migration that failed half-way and left the
    DB mutated must not overwrite the good copy on the retry.
    """
    src = Path(src)
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"{src.stem}.pre-v{from_version}.db"
    if dest.exists():
        logger.info("pre-migration snapshot already exists, keeping the earlier copy: %s", dest)
        return dest
    _snapshot(src, dest)
    logger.info("pre-migration snapshot of schema v%d written to %s", from_version, dest)
    return dest
