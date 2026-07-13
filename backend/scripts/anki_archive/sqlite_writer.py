"""Write-side helpers for the Stage 2b GUID backfill.

Relocated here from ``app/anki/sqlite_writer.py`` (deleted 2026-07-08, commit
dceffed) — the live tree had no callers left, but the archived one-shot
migrations ``backfill_guids`` and ``merge_dupes`` still import these helpers.
Kept in the archive package (like ``notetype_builders.py``) so those migrations
remain runnable; ``source = ["app"]`` in pyproject keeps it out of coverage.

Pure SQL functions — no CLI glue, no safety envelope. Callers open the
connection via ``app.anki.safety.safe_open(..., mode="rw")`` and pass
``ctx.conn`` into ``apply_guid_backfill``.

plan_guid_backfill partitions each note in the target deck into:
  - updates:            guid must be rewritten (conflict promoted by --force, or pre-empty)
  - noops:              current guid already matches compute_guid(text, "sl")
  - skipped_conflicts:  current guid differs and --force was NOT passed
  - skipped_duplicates: multiple notes would collide on the same computed guid
                        (resolved in Anki by the user before re-running)
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

from app.anki.sqlite_reader import extract_disambig_from_fields, extract_l2_from_fields, fetch_notes_for_deck
from app.common.guid import compute_guid


@dataclass
class BackfillPlan:
    updates: dict[int, str] = field(default_factory=dict)
    noops: list[int] = field(default_factory=list)
    skipped_conflicts: list[tuple[int, str, str]] = field(default_factory=list)
    skipped_duplicates: list[tuple[int, str]] = field(default_factory=list)


def read_col_conf(conn: sqlite3.Connection) -> dict:
    """Parse ``col.conf`` JSON. Returns {} for NULL, empty string, or malformed JSON."""
    row = conn.execute("SELECT conf FROM col").fetchone()
    if row is None:
        return {}
    raw = row[0]
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError, TypeError, ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def check_anki_web_sync_active(conn: sqlite3.Connection) -> bool:
    """True when col.conf.syncKey is a non-null value (collection linked to AnkiWeb)."""
    return read_col_conf(conn).get("syncKey") is not None


def plan_guid_backfill(
    conn: sqlite3.Connection,
    deck_id: int,
    force: bool,
    language_code: str = "sl",
) -> BackfillPlan:
    """Compute the partitioning of notes for backfill without writing anything."""
    notes = fetch_notes_for_deck(conn, deck_id)

    expected_by_note: dict[int, str] = {}
    current_by_note: dict[int, str] = {}
    for note in notes:
        l2_text = extract_l2_from_fields(note.fields)
        disambig = extract_disambig_from_fields(note.fields)
        expected_by_note[note.id] = compute_guid(l2_text, language_code, disambig)
        current_by_note[note.id] = note.anki_guid

    # Find duplicates: multiple notes computing to the same expected guid.
    buckets: dict[str, list[int]] = defaultdict(list)
    for nid, guid in expected_by_note.items():
        buckets[guid].append(nid)

    plan = BackfillPlan()
    duplicate_ids: set[int] = set()
    for guid, ids in buckets.items():
        if len(ids) > 1:
            duplicate_ids.update(ids)
            for nid in ids:
                plan.skipped_duplicates.append((nid, guid))

    for note in notes:
        if note.id in duplicate_ids:
            continue
        current = current_by_note[note.id]
        expected = expected_by_note[note.id]
        if current == expected:
            plan.noops.append(note.id)
        elif force:
            plan.updates[note.id] = expected
        else:
            plan.skipped_conflicts.append((note.id, current, expected))

    return plan


def apply_guid_backfill(
    conn: sqlite3.Connection,
    plan: BackfillPlan,
    now_ts: int,
) -> None:
    """Apply ``plan.updates`` in a single transaction. Bumps col.mod / col.usn only when non-empty.

    Each updated note gets usn=-1 so Anki sync treats the row as dirty. Without this,
    Anki's integrity check on next open re-detects the bumped mod and rewrites usn
    itself, which bumps col.scm and forces a full AnkiWeb resync.

    Any SQL exception triggers ROLLBACK and re-raises.
    """
    if not plan.updates:
        return

    update_rows = [(new_guid, now_ts, note_id) for note_id, new_guid in plan.updates.items()]

    try:
        conn.execute("BEGIN")
        conn.executemany(
            "UPDATE notes SET guid=?, mod=?, usn=-1 WHERE id=?",
            update_rows,
        )
        conn.execute("UPDATE col SET mod=?, usn=-1", (now_ts,))
        conn.execute("COMMIT")
    except Exception:
        with contextlib.suppress(sqlite3.OperationalError):
            conn.execute("ROLLBACK")
        raise
