"""One-shot repair for 3 notes whose disambiguation suffix contained nested parens.

The homonym regex `^(.+?)\\s\\([^()]+\\)$` rejected inner parens, so three rows
slipped through Stage H3 migrate_homonyms:

  | TT id | Anki note id    | Old Slovene field          | Repair: bare → DisambigKey  |
  |-------|-----------------|----------------------------|-----------------------------|
  |    15 | 1775264032874   | nizek                      | short (≠tall)               |
  |   598 | 1775264032898   | star                       | old (≠new)                  |
  |   600 | 1775264032902   | star (old (≠young))        | old (≠young)                |

For each note this tool:
  - Sets Slovene field (field 0) to the bare word.
  - Sets DisambigKey (field 6) to the disambiguator.
  - Recomputes notes.guid via compute_guid(bare, 'sl', disambig).
  - Sets notes.usn=-1, mod=now.
  - Mirrors the same text/disambig_key/guid change to TunaTale's collocations table.
  - Idempotent: skips a row whose DisambigKey is already set correctly.

Run with --dry-run to preview; omit --dry-run to write.

Usage:
    uv run python -m app.anki.repair_nested_homonyms [--dry-run] [--force]

See ``.claude/rules/anki-sync.md`` for the required post-write workflow
(Upload to AnkiWeb → normalize_usns).
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import time
from pathlib import Path
from typing import Any

from app.anki.safety import safe_open
from app.common.guid import compute_guid
from app.config import settings


class DriftError(RuntimeError):
    """Raised when a note's current flds doesn't match the expected manifest hash."""


# Live-DB manifest captured 2026-04-20.
# flds_hash: SHA256 of the note's flds string at the time this tool was written.
# If the hash check fails, the note was edited since capture — use --force to override.
_MANIFEST: list[dict[str, Any]] = [
    {
        "anki_note_id": 1775264032874,
        "flds_hash": "274fb9c642af30d461f1f6f0ac568fe7ecca5c9f41d236233d9a7ce1453e8d4f",
        "slovene_bare": "nizek",
        "disambig": "short (≠tall)",
        "tt_id": 15,
    },
    {
        "anki_note_id": 1775264032898,
        "flds_hash": "3b8d785c4a2134a0a087f2d9d79602bb07d53b1d9a526a1eab36bf6d0e6dc738",
        "slovene_bare": "star",
        "disambig": "old (≠new)",
        "tt_id": 598,
    },
    {
        "anki_note_id": 1775264032902,
        "flds_hash": "b8a4ea99f8c943b473e9cc821447f7a63a3fddaa96e17b549b49be1dd28d4deb",
        "slovene_bare": "star",
        "disambig": "old (≠young)",
        "tt_id": 600,
    },
]


def repair_nested_homonyms(
    anki_collection_path: Path | None = None,
    anki_backup_dir: Path | None = None,
    tt_db_path: str | Path | None = None,
    dry_run: bool = False,
    force: bool = False,
    _manifest: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Repair the three nested-paren homonym rows on both Anki and TT sides.

    _manifest: override the module-level _MANIFEST (used in tests with synthetic DBs).

    Returns {'repaired': N, 'skipped': M}.
    """
    if anki_collection_path is None:
        anki_collection_path = settings.anki_collection_path
    if anki_backup_dir is None:
        anki_backup_dir = settings.anki_backup_dir
    if tt_db_path is None:
        tt_db_path = settings.database_url.replace("sqlite:///", "")
    if _manifest is None:
        _manifest = _MANIFEST

    results: dict[str, int] = {"repaired": 0, "skipped": 0}
    now_ts = int(time.time())

    with safe_open(anki_collection_path, backup_dir=anki_backup_dir, mode="rw") as ctx:
        anki_conn = ctx.conn
        tt_conn = sqlite3.connect(str(tt_db_path))
        tt_conn.row_factory = sqlite3.Row

        try:
            for entry in _manifest:
                nid = entry["anki_note_id"]
                bare = entry["slovene_bare"]
                disambig = entry["disambig"]
                tt_id = entry["tt_id"]

                # Read current Anki note
                row = anki_conn.execute("SELECT flds FROM notes WHERE id=?", (nid,)).fetchone()
                if row is None:
                    print(f"  WARNING nid={nid}: not found in Anki DB — skipping", flush=True)
                    results["skipped"] += 1
                    continue

                flds_str = row["flds"]
                fields = flds_str.split("\x1f")
                if len(fields) < 7:
                    fields += [""] * (7 - len(fields))

                # Idempotency check: if already repaired, skip.
                if fields[0] == bare and fields[6] == disambig:
                    print(f"  nid={nid}: already repaired — skipping", flush=True)
                    results["skipped"] += 1
                    continue

                # Drift detection: verify flds hash unless --force.
                if not force:
                    actual_hash = hashlib.sha256(flds_str.encode()).hexdigest()
                    expected_hash = entry.get("flds_hash", "")
                    if actual_hash != expected_hash:
                        raise DriftError(
                            f"nid={nid}: flds hash mismatch (expected {expected_hash[:12]}…, "
                            f"got {actual_hash[:12]}…). Use --force to override."
                        )

                # Compute new fields and guid
                new_fields = list(fields)
                new_fields[0] = bare
                new_fields[6] = disambig
                new_flds = "\x1f".join(new_fields)
                new_guid = compute_guid(bare, "sl", disambig)

                print(
                    f"  nid={nid}: slovene='{bare}' disambig='{disambig}' guid={new_guid}",
                    flush=True,
                )

                if dry_run:
                    results["repaired"] += 1
                    continue

                # Write Anki side
                anki_conn.execute(
                    "UPDATE notes SET flds=?, guid=?, mod=?, usn=-1 WHERE id=?",
                    (new_flds, new_guid, now_ts, nid),
                )

                # Write TunaTale side
                tt_conn.execute(
                    "UPDATE collocations SET text=?, disambig_key=?, guid=? WHERE id=?",
                    (bare, disambig, new_guid, tt_id),
                )

                results["repaired"] += 1

            if not dry_run:
                anki_conn.execute("UPDATE col SET mod=?, usn=-1", (now_ts,))
                anki_conn.commit()
                tt_conn.commit()
        finally:
            tt_conn.close()

    if dry_run:
        print(f"[DRY RUN] would repair {results['repaired']} skip {results['skipped']}", flush=True)
    else:
        print(f"[DONE] repaired={results['repaired']} skipped={results['skipped']}", flush=True)

    return results


def _cli() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description="One-shot repair for 3 nested-paren homonym rows")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Skip drift hash check")
    args = parser.parse_args()
    repair_nested_homonyms(dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":  # pragma: no cover
    _cli()
