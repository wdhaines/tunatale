"""Safety envelope for opening an Anki collection.anki2.

Every caller must go through `safe_open()` — no raw sqlite3.connect on the
collection file anywhere else in this codebase.

Gates (in order):
1. Exclusive-lock probe — aborts if Anki is running (SQLITE_BUSY).
2. SHA256 of source before open.
3. Backup via Connection.backup() (never shutil).
4. Backup validation: integrity_check + row-count match.
   Retention: prune backups beyond settings.anki_backup_keep (newest kept).
5. Connection opened read-only (mode="ro") or read-write (mode="rw") via URI.
6. In ro mode: post-run SHA256 re-check on context exit.
   In rw mode: SHA256 equality is *expected* to break; callers use
   AnkiContext.audit_changes() to verify only planned rows were touched.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from app.config import settings

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _check_ident(name: str) -> None:
    if not _IDENT_RE.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")


@dataclass
class AnkiContext:
    conn: sqlite3.Connection
    backup_path: Path
    source_sha256: str

    def audit_changes(
        self,
        table: str,
        id_col: str,
        value_col: str,
        expected: dict[int, str],
    ) -> None:
        """Diff backup vs source on (id_col, value_col) and enforce the planned write set.

        Every row whose value differs between backup and source must appear in `expected`
        with its new value matching source. Any row in `expected` whose source value
        does not match the planned value is also a failure. Raises RuntimeError listing
        both violation classes so callers know what went wrong.
        """
        _check_ident(table)
        _check_ident(id_col)
        _check_ident(value_col)

        backup_conn = sqlite3.connect(f"file:{self.backup_path}?mode=ro", uri=True)
        _register_anki_collations(backup_conn)
        try:
            backup_rows = dict(backup_conn.execute(f"SELECT {id_col}, {value_col} FROM {table}").fetchall())
        finally:
            backup_conn.close()

        source_rows = dict(self.conn.execute(f"SELECT {id_col}, {value_col} FROM {table}").fetchall())

        unexpected: dict[int, tuple[str, str]] = {}
        for rid in set(backup_rows) | set(source_rows):
            before = backup_rows.get(rid)
            after = source_rows.get(rid)
            if before != after and rid not in expected:
                unexpected[rid] = (before, after)

        missing: dict[int, tuple[str, str]] = {}
        for rid, planned in expected.items():
            if source_rows.get(rid) != planned:
                missing[rid] = (source_rows.get(rid), planned)

        if unexpected or missing:
            raise RuntimeError(
                f"audit_changes failed on {table}.{value_col}. "
                f"unexpected (unplanned writes): {unexpected}. "
                f"missing (planned but not applied or mismatched): {missing}."
            )


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _register_anki_collations(conn: sqlite3.Connection) -> None:
    """Register the custom collations Anki uses so PRAGMA integrity_check and
    queries against tables declared ``COLLATE unicase`` do not raise
    ``no such collation sequence``.
    """

    def _unicase(a: str, b: str) -> int:
        af, bf = a.casefold(), b.casefold()
        return (af > bf) - (af < bf)

    conn.create_collation("unicase", _unicase)


_BACKUP_PREFIX = "collection.anki2.bak_"


def _prune_old_backups(backup_dir: Path, keep: int) -> list[Path]:
    """Cap the backup directory to the ``keep`` most recent snapshots.

    ``safe_open`` writes a fresh full-collection backup on every call and used to
    never remove old ones, so the directory grew without bound (6 GB / 4000+
    files observed in practice). This keeps the ``keep`` newest
    ``collection.anki2.bak_*`` snapshots and deletes older ones together with any
    ``-wal`` / ``-shm`` sidecars. Snapshots are ordered by filename, whose
    fixed-width ``YYYYMMDD_HHMMSS`` timestamp sorts chronologically.

    ``keep <= 0`` disables pruning. Never raises: a failed unlink is suppressed so
    retention cannot break a sync that already succeeded. Returns the deleted
    paths (for logging/tests).
    """
    if keep <= 0:
        return []
    snapshots = sorted(
        (p for p in backup_dir.glob(f"{_BACKUP_PREFIX}*") if not p.name.endswith(("-wal", "-shm"))),
        reverse=True,
    )
    deleted: list[Path] = []
    for snap in snapshots[keep:]:
        for target in (snap, snap.with_name(f"{snap.name}-wal"), snap.with_name(f"{snap.name}-shm")):
            if not target.exists():
                continue
            with suppress(OSError):
                target.unlink()
                deleted.append(target)
    return deleted


def _validate_backup(backup_path: Path, source_note_count: int) -> None:
    """Open the backup and verify it is a valid SQLite with matching row count.

    Deletes the backup and raises RuntimeError on any failure.
    """
    try:
        conn = sqlite3.connect(str(backup_path))
        _register_anki_collations(conn)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError(f"Backup integrity_check failed: {result}")
            count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            if count != source_note_count:
                raise RuntimeError(f"Backup note count {count} != source {source_note_count}")
        finally:
            conn.close()
    except Exception:
        backup_path.unlink(missing_ok=True)
        raise


class AnkiRunningError(RuntimeError):
    """Raised when the Anki collection is exclusively locked (Anki is running)."""


def probe_lock(path: Path) -> bool:
    """Return True if the collection is locked (Anki is running), False if acquirable."""
    try:
        _probe_exclusive_lock(path)
        return False
    except AnkiRunningError:
        return True


def _probe_exclusive_lock(path: Path) -> None:
    """Raise AnkiRunningError if the database cannot be exclusively locked (Anki running)."""
    probe = sqlite3.connect(str(path), timeout=0.1)
    try:
        probe.execute("BEGIN EXCLUSIVE")
        probe.execute("ROLLBACK")
    except (sqlite3.OperationalError, sqlite3.DatabaseError) as exc:
        probe.close()
        raise AnkiRunningError(
            f"Anki collection is locked (Anki may be running): {path}\n"
            f"Close Anki before running import. Original error: {exc}"
        ) from exc
    finally:
        with suppress(Exception):  # pragma: no cover
            probe.close()


@contextmanager
def safe_open(
    collection_path: Path,
    backup_dir: Path | None = None,
    mode: Literal["ro", "rw"] = "ro",
) -> Generator[AnkiContext]:
    """Open an Anki collection with full safety checks.

    Yields an AnkiContext with a connection (read-only or read-write per ``mode``)
    and backup metadata. Raises RuntimeError if Anki is running, the backup is
    invalid, or (in ro mode) the source SHA256 changes during the run.
    """
    if backup_dir is None:
        backup_dir = settings.anki_backup_dir

    # Gate 1: lock probe
    _probe_exclusive_lock(collection_path)

    # Gate 2: SHA256 before open
    source_sha256 = _sha256_file(collection_path)

    # Get source note count for backup validation
    _src = sqlite3.connect(str(collection_path))
    _register_anki_collations(_src)
    try:
        source_note_count = _src.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    finally:
        _src.close()

    # Gate 3: backup via Connection.backup()
    # The timestamp is only second-granularity, so two callers in the same
    # second (parallel test workers, or two rapid syncs) would otherwise share
    # a filename and clobber/cross-validate each other's backup. A per-call
    # token (pid + random) keeps each backup distinct.
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique = f"{os.getpid()}_{secrets.token_hex(4)}"
    backup_path = backup_dir / f"collection.anki2.bak_{timestamp}_{unique}"

    src_conn = sqlite3.connect(str(collection_path))
    dst_conn = sqlite3.connect(str(backup_path))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    # Gate 4: validate backup
    _validate_backup(backup_path, source_note_count)

    # Retention: bound the backup directory so it can't grow without limit.
    # Runs after validation so the just-created backup is counted; failures are
    # swallowed inside _prune_old_backups and never affect this open.
    _prune_old_backups(backup_dir, getattr(settings, "anki_backup_keep", 0))

    # Gate 5: open source connection (ro or rw per mode)
    conn = sqlite3.connect(
        f"file:{collection_path}?mode={mode}",
        uri=True,
    )
    _register_anki_collations(conn)
    conn.row_factory = sqlite3.Row

    ctx = AnkiContext(conn=conn, backup_path=backup_path, source_sha256=source_sha256)
    try:
        yield ctx
    finally:
        conn.close()
        # Gate 6: post-run SHA256 re-check (only in ro — rw writes *expect* change)
        if mode == "ro":
            post_sha256 = _sha256_file(collection_path)
            if post_sha256 != source_sha256:
                import sys

                print(
                    f"\n⚠  WARNING: Anki collection SHA256 changed during import!\n"
                    f"   Backup is at: {backup_path}\n"
                    f"   Pre-run:  {source_sha256}\n"
                    f"   Post-run: {post_sha256}\n",
                    file=sys.stderr,
                )
                raise RuntimeError(
                    f"Anki collection SHA256 changed during run — something wrote to the file.\n"
                    f"Backup: {backup_path}\n"
                    f"Pre-run: {source_sha256}  Post-run: {post_sha256}"
                )


def snapshot_collection(collection_path: Path, dest: Path) -> Path:
    """A consistent read-only copy of the collection, taken WITHOUT the lock probe.

    The one sanctioned exception to "every caller goes through ``safe_open``",
    and narrow on purpose: it only ever *reads* the collection, and the file it
    writes is a fresh destination, never the source.

    **Why the lock probe is deliberately not run here.** Gate 1 of ``safe_open``
    aborts whenever Anki is running — correctly, because every other caller is
    about to write, or to read state that a concurrent write would make
    incoherent. A backup is neither. The off-box job fires from a LaunchAgent at
    03:30, when Anki may well be open; gating it on the lock means the one
    production system with no other automated off-box copy is skipped on exactly
    the nights someone left Anki running, and the job still reports success.
    That is the failure ``tunatale-pj9l`` exists to remove.

    **Why this is safe rather than merely convenient.** ``Connection.backup()``
    against a ``mode=ro`` URI takes SQLite's own read snapshot: the ``-wal`` is
    folded in and the copy is transactionally consistent even mid-write. It is
    the same mechanism ``app.storage.db_backup._snapshot`` already uses for TT's
    live per-language DBs, which the dev server holds open the whole time. The
    result is validated (``integrity_check`` + note-count match) before it is
    returned, and deleted if it does not validate — a torn copy that looks like
    a backup is worse than no copy, because you find out at restore time.

    ⚠️ **Never add ``immutable=1``.** It is the plausible-looking way to read a
    file another process holds, and it reads the main database file ONLY —
    silently missing every committed-but-uncheckpointed ``-wal`` page. That
    ships a stale collection with no error anywhere. Pinned by
    ``TestSnapshotCollection::test_folds_in_uncheckpointed_wal_content``, whose
    control asserts that an ``immutable=1`` read *disagrees* with this one.

    Returns the destination path.
    """
    if not collection_path.exists():
        raise FileNotFoundError(f"collection not found: {collection_path}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    # A previous run's file must be replaced outright, not backed into: a stale
    # snapshot with a different schema would otherwise survive in the pages the
    # new copy does not reach.
    dest.unlink(missing_ok=True)

    src_conn = sqlite3.connect(f"file:{collection_path}?mode=ro", uri=True)
    _register_anki_collations(src_conn)
    try:
        source_note_count = src_conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        dst_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    # Raises and unlinks `dest` on any mismatch.
    _validate_backup(dest, source_note_count)
    return dest
