"""Stage 2b orchestrator: rewrite Anki ``notes.guid`` to deterministic TunaTale values.

Usage:
    uv run python -m app.anki.backfill_guids --deck "0. Slovene" [--dry-run] [--force]

Safety:
    - ``safe_open(mode="rw")`` creates a validated backup before any write.
    - Default mode skips notes whose current guid differs (logged as conflicts);
      ``--force`` is required to overwrite existing guids.
    - If ``col.conf.syncKey`` is set, the CLI prompts interactively before writing
      so the user knows to force-upload to AnkiWeb afterward. ``--force`` and
      ``--dry-run`` both bypass the prompt.
    - After commit, ``ctx.audit_changes`` diffs backup vs source per-row and raises
      if any row was written that was not in the plan.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from app.anki.notetype import SLOVENE_VOCAB_NOTETYPE_NAME
from app.anki.safety import safe_open
from app.anki.sqlite_reader import find_deck_id
from app.anki.sqlite_writer import (
    apply_guid_backfill,
    check_anki_web_sync_active,
    plan_guid_backfill,
)
from app.config import settings

_SUFFIX_RE = re.compile(r"^(.+?)\s\((.+)\)$")


def _check_suffix_preflight(conn: sqlite3.Connection, deck_id: int) -> None:
    """Raise if Slovene Vocabulary notes still carry disambiguation suffix in field 0."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "notetypes" not in tables:
        return
    mid_row = conn.execute("SELECT id FROM notetypes WHERE name = ?", (SLOVENE_VOCAB_NOTETYPE_NAME,)).fetchone()
    if mid_row is None:
        return
    mid = mid_row["id"]
    rows = conn.execute(
        "SELECT DISTINCT n.flds FROM notes n JOIN cards c ON c.nid = n.id WHERE n.mid = ? AND c.did = ?",
        (mid, deck_id),
    ).fetchall()
    suffix_count = sum(1 for row in rows if _SUFFIX_RE.match(row["flds"].split("\x1f")[0]))
    if suffix_count > 0:
        raise RuntimeError(
            f"Preflight failed: {suffix_count} note(s) still have disambiguation suffix in the "
            f"Slovene field. Run migrate_homonyms before backfill_guids --force."
        )


_ANKI_WEB_PROMPT = (
    "\nThis collection is linked to AnkiWeb. Backfilling GUIDs will mark every\n"
    "note as modified and invalidate AnkiWeb's delta sync.\n"
    "Recommended: after this run, open Anki → Force sync → Upload to AnkiWeb.\n"
    "Continue? [y/N] "
)


def backfill_guids(
    deck_name: str | None = None,
    anki_collection_path: Path | None = None,
    anki_backup_dir: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Plan and (optionally) apply a GUID backfill against the target Anki deck.

    Returns a summary dict:
        {
            "updated": int,             # rows actually UPDATEd in this run
            "planned_updates": int,     # rows the plan said to update (including dry-run)
            "noops": int,
            "skipped_conflicts": int,
            "skipped_duplicates": int,
            "aborted": bool,            # True iff AnkiWeb preflight prompt answered "no"
        }
    """
    if deck_name is None:
        deck_name = settings.anki_deck_name
    if anki_collection_path is None:
        anki_collection_path = settings.anki_collection_path
    if anki_backup_dir is None:
        anki_backup_dir = settings.anki_backup_dir

    summary: dict[str, Any] = {
        "updated": 0,
        "planned_updates": 0,
        "noops": 0,
        "skipped_conflicts": 0,
        "skipped_duplicates": 0,
        "aborted": False,
    }

    with safe_open(anki_collection_path, backup_dir=anki_backup_dir, mode="rw") as ctx:
        deck_id = find_deck_id(ctx.conn, deck_name)
        if deck_id is None:
            raise RuntimeError(f"Deck '{deck_name}' not found in {anki_collection_path}")

        if force:
            _check_suffix_preflight(ctx.conn, deck_id)

        plan = plan_guid_backfill(ctx.conn, deck_id, force=force)
        summary["planned_updates"] = len(plan.updates)
        summary["noops"] = len(plan.noops)
        summary["skipped_conflicts"] = len(plan.skipped_conflicts)
        summary["skipped_duplicates"] = len(plan.skipped_duplicates)

        for nid, current, expected in plan.skipped_conflicts:
            print(
                f"SKIP conflict: note_id={nid} current={current!r} expected={expected!r} (use --force to overwrite)",
                flush=True,
            )
        for nid, dup_guid in plan.skipped_duplicates:
            print(
                f"SKIP duplicate: note_id={nid} would collide on guid={dup_guid!r} — resolve the duplicate in Anki and re-run",
                flush=True,
            )

        if dry_run:
            print(
                f"[DRY RUN] planned={len(plan.updates)} noops={len(plan.noops)} "
                f"conflicts={len(plan.skipped_conflicts)} duplicates={len(plan.skipped_duplicates)}",
                flush=True,
            )
            return summary

        # AnkiWeb preflight (only when we'd actually write)
        if not force and check_anki_web_sync_active(ctx.conn):
            answer = input(_ANKI_WEB_PROMPT).strip().lower()
            if answer != "y":
                print("Aborted by user before any writes.", flush=True)
                summary["aborted"] = True
                return summary

        if not plan.updates:
            print(
                f"[NO UPDATES] noops={len(plan.noops)} conflicts={len(plan.skipped_conflicts)} "
                f"duplicates={len(plan.skipped_duplicates)}",
                flush=True,
            )
            return summary

        apply_guid_backfill(ctx.conn, plan, now_ts=int(time.time()))
        ctx.audit_changes("notes", "id", "guid", plan.updates)
        summary["updated"] = len(plan.updates)
        print(
            f"[DONE] updated={summary['updated']} noops={summary['noops']} "
            f"conflicts={summary['skipped_conflicts']} duplicates={summary['skipped_duplicates']}",
            flush=True,
        )

    return summary


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Rewrite Anki notes.guid to deterministic TunaTale values (Stage 2b)")
    parser.add_argument("--deck", default=None, help="Anki deck name")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan without writing. A backup is still created.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=("Overwrite existing non-empty guids. Also bypasses the AnkiWeb preflight prompt."),
    )
    args = parser.parse_args()

    backfill_guids(deck_name=args.deck, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":  # pragma: no cover
    _cli()
