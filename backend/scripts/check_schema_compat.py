#!/usr/bin/env python3
"""Refuse a deploy whose code is OLDER than the database it would open.

The deploy story promises "roll back to a previous SHA in one command". That is
true of the container image and false of the data underneath it: starting a new
build runs ``app/srs/migrations.py`` against the existing volume, so rolling the
image back afterwards leaves a **newer schema under older code** — a different
and worse failure than the one being rolled back from.

``migrate`` already refuses this at boot (``SchemaTooNewError``). This script is
the same refusal moved *earlier*, so a deploy can decline to swap rather than
swap and crash-loop. It imports the check rather than reimplementing it — two
copies of a version comparison is exactly how the two would drift apart.

Reads ``PRAGMA user_version`` straight off each DB file, so it needs nothing
running and does not open the app.

Usage::

    uv run python scripts/check_schema_compat.py                 # settings' DBs
    uv run python scripts/check_schema_compat.py path/to/x.db …  # explicit

Exit 0 = every DB is at or below this build's schema version (safe to start).
Exit 1 = at least one DB is ahead (do not deploy this build).
Exit 2 = a DB named on the command line does not exist.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from app.config import settings
from app.languages import resolve_db_path
from app.srs.migrations import CURRENT_VERSION, _schema_too_new_message


def _db_paths(argv: list[str]) -> list[Path]:
    if argv:
        return [Path(a) for a in argv]
    # EVERY configured language, resolved through the registry. Reading
    # `settings.database_url` here would check one fixed language and report a
    # vacuous all-clear for the others — the exact failure `resolve_db_path`
    # exists to prevent, and the worst possible one for a pre-deploy gate.
    codes = list(settings.database_urls) if settings.database_urls else [settings.target_language]
    return [resolve_db_path(code, settings) for code in codes]


def main(argv: list[str]) -> int:
    paths = _db_paths(argv)
    if not paths:
        print("check_schema_compat: no databases configured; nothing to check.")
        return 0

    exit_code = 0
    for path in paths:
        if not path.exists():
            # A DB the app would create on first boot is not a rollback hazard,
            # but one named explicitly on the command line is a typo.
            if argv:
                print(f"MISSING  {path}: no such file", file=sys.stderr)
                exit_code = max(exit_code, 2)
            else:
                print(f"absent   {path} (will be created at v{CURRENT_VERSION} on first boot)")
            continue

        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()

        if version > CURRENT_VERSION:
            print(f"REFUSE   {path}", file=sys.stderr)
            print(f"         {_schema_too_new_message(version, path)}", file=sys.stderr)
            exit_code = 1
        elif version < CURRENT_VERSION:
            print(f"pending  {path}: v{version} -> v{CURRENT_VERSION} will migrate on boot")
        else:
            print(f"ok       {path}: v{version}")

    return exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
