#!/usr/bin/env python3
"""Check that every ``media`` row's filename matches a file on disk byte-for-byte.

``media.filename`` values are served as static audio from the media directory.
The production box is ext4 — case-SENSITIVE — while macOS APFS is
case-insensitive by default, so a row whose spelling differs from the on-disk
name only in case resolves fine on a developer's Mac and 404s in production
with no local warning. This checker is the only thing that notices.

Measured 2026-09-13 (backend/): ``tunatale_sl.db`` has 1379 media rows, 3 of
which are case-only mismatches (e.g. db='sl_zemlja.mp3' vs disk='sl_Zemlja.mp3');
``tunatale_no.db`` is clean (7303/7303). Neither count is hardcoded here — the
checker reports whatever it finds.

Exact-byte comparison is deliberate. 158 filenames on disk are non-ASCII and
every one of them is stored NFC on disk AND in both DBs — there is no
normalization confound, so DO NOT add ``unicodedata.normalize()``: a
normalizing comparison would paper over a genuine mismatch, which is the whole
thing this checker exists to catch.

A missing database or a missing media directory is a SKIP (exit 0), not a
failure — CI has neither, and a checker that failed there would be an outage.
The DBs are opened read-only; nothing here writes anything.

Usage::

    # exit 0 = clean (or nothing to check); exit 1 = at least one mismatch
    uv run python scripts/check_media_filename_case.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path


def check_db(db_path: Path, media_dir: Path) -> tuple[int, list[str]]:
    """Compare every ``media.filename`` against the exact on-disk entries.

    Returns ``(exit_code, messages)`` where each message is a ``FAIL:`` line
    naming the db, the row id, the DB spelling, and (for a case-only mismatch)
    the actual on-disk spelling(s).
    """
    entries = set(os.listdir(media_dir))
    messages: list[str] = []
    label = db_path.name
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        # A tunatale_*.db without a `media` table is a SKIP for the same reason a
        # missing DB is: this is a zero-tolerance gate, and a hard crash there is
        # an outage rather than a finding. Reachable by any new language DB that
        # exists before its first media row — the glob finds it regardless.
        if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='media'").fetchone() is None:
            return 0, [f"SKIP: {label} has no media table (nothing to check)"]
        rows = conn.execute("SELECT id, filename FROM media").fetchall()
    for row_id, filename in rows:
        if filename in entries:
            continue
        folded = filename.casefold()
        candidates = sorted(e for e in entries if e.casefold() == folded)
        if candidates:
            messages.append(
                f"FAIL: {label} row {row_id}: db='{filename}' disk='{', '.join(candidates)}' (case-only mismatch)"
            )
        else:
            messages.append(f"FAIL: {label} row {row_id}: db='{filename}' absent from media/ entirely")
    return 1 if messages else 0, messages


def do_check(db_dir: Path = Path("."), media_dir: Path = Path("media")) -> int:
    """Discover DBs by glob, check each, print. Returns the exit code."""
    if not media_dir.is_dir():
        print("SKIP: media directory not found (nothing to check)")
        return 0
    dbs = sorted(db_dir.glob("tunatale_*.db"))
    if not dbs:
        print("SKIP: no tunatale_*.db databases found (nothing to check)")
        return 0
    exit_code = 0
    for db in dbs:
        code, messages = check_db(db, media_dir)
        exit_code |= code
        for msg in messages:
            print(msg)
    return exit_code


if __name__ == "__main__":
    sys.exit(do_check())
