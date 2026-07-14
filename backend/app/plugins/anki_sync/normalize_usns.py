"""Post-full-upload USN repair.

When an Anki migration bumps ``col.scm`` (e.g., adding a new notetype field),
AnkiWeb demands a full upload. The upload succeeds, but it preserves the old
row ``usn`` values while the server sets a fresh host USN (often ``0``). Any
row whose ``usn > col.usn`` is then perpetually seen as "ahead of server" and
re-uploaded on every subsequent incremental sync.

This tool resets every ``cards.usn``, ``notes.usn``, ``revlog.usn`` greater
than ``col.usn`` back down to ``col.usn``. Rows with ``usn = -1`` (legitimately
dirty) are preserved.

Run this **after** completing the forced full upload in Anki (File → Sync →
Upload to AnkiWeb). See ``.claude/rules/anki-sync.md`` for the full workflow
and diagnostic queries.

Usage:
    uv run python -m app.anki.normalize_usns [--dry-run]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.config import settings
from app.plugins.anki_sync.safety import safe_open


def normalize_usns(
    anki_collection_path: Path | None = None,
    anki_backup_dir: Path | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Reset rows with ``usn > col.usn`` back to ``col.usn`` in cards/notes/revlog.

    Returns {'cards': N, 'notes': M, 'revlog': R} counts of rows touched
    (or that would be touched in dry_run mode).
    """
    if anki_collection_path is None:
        anki_collection_path = settings.anki_collection_path
    if anki_backup_dir is None:
        anki_backup_dir = settings.anki_backup_dir

    results = {"cards": 0, "notes": 0, "revlog": 0}

    with safe_open(anki_collection_path, backup_dir=anki_backup_dir, mode="rw") as ctx:
        conn = ctx.conn
        col_usn = conn.execute("SELECT usn FROM col").fetchone()["usn"]

        for table in ("cards", "notes", "revlog"):
            count = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE usn > ?", (col_usn,)).fetchone()[0]
            results[table] = count

        if dry_run:
            print(
                f"[DRY RUN] col.usn={col_usn} cards={results['cards']} "
                f"notes={results['notes']} revlog={results['revlog']}",
                flush=True,
            )
            return results

        for table in ("cards", "notes", "revlog"):
            if results[table] > 0:
                conn.execute(
                    f"UPDATE {table} SET usn = ? WHERE usn > ?",
                    (col_usn, col_usn),
                )
        conn.commit()

        print(
            f"[DONE] col.usn={col_usn} cards={results['cards']} notes={results['notes']} revlog={results['revlog']}",
            flush=True,
        )

    return results


def _cli() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Reset cards/notes/revlog usn > col.usn back to col.usn (post-full-upload repair)"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    normalize_usns(dry_run=args.dry_run)


if __name__ == "__main__":  # pragma: no cover
    _cli()
