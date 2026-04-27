"""Safety envelope for opening an Anki collection.anki2.

Every caller must go through `safe_open()` — no raw sqlite3.connect on the
collection file anywhere else in this codebase.

Gates (in order):
1. Exclusive-lock probe — aborts if Anki is running (SQLITE_BUSY).
2. SHA256 of source before open.
3. Backup via Connection.backup() (never shutil).
4. Backup validation: integrity_check + row-count match.
5. Connection opened read-only (mode="ro") or read-write (mode="rw") via URI.
6. In ro mode: post-run SHA256 re-check on context exit.
   In rw mode: SHA256 equality is *expected* to break; callers use
   AnkiContext.audit_changes() to verify only planned rows were touched.
"""

from __future__ import annotations

import hashlib
import re
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
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"collection.anki2.bak_{timestamp}"

    src_conn = sqlite3.connect(str(collection_path))
    dst_conn = sqlite3.connect(str(backup_path))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    # Gate 4: validate backup
    _validate_backup(backup_path, source_note_count)

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
