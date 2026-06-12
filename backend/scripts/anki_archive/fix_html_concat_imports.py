"""Clean up TT collocations whose `text` was concatenated from an Anki
`<b>L2</b><br><i>EN</i>` field at import time.

Background: pre-Layer-31 `extract_l2_from_fields` regex-stripped HTML tags
without inserting whitespace, so a Pronunciation/Basic notetype field like
``<b>nič</b><br><i>nothing</i>`` collapsed into the single token
``ničnothing`` — saved as TT's `text`, with the English gloss lost. This script
walks the TT DB, identifies affected rows by cross-checking the linked Anki
note's Front field against the `<b>X</b><br><i>Y</i>` pattern, and either:

- **renames** the row in place (text=X, translation=Y) when no clean-X twin
  collocation exists, or
- **deletes** the mangled row when a clean-X twin already exists (the
  Pronunciation card duplicates the Slovene Vocabulary card; user opted to
  drop duplicates and keep the cleaner version).

Usage::

    uv run python -m app.anki.fix_html_concat_imports [--dry-run]

The script only mutates `tunatale.db`; it opens `collection.anki2` read-only.
No Anki-side safety envelope is required.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config import settings

_B_THEN_I_PATTERN = re.compile(
    r"^\s*<b>([^<]+)</b>\s*<br\s*/?>\s*<i>([^<]+)</i>",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PlanItem:
    """One row in the cleanup plan."""

    action: Literal["rename", "delete"]
    tt_id: int
    new_text: str | None
    new_translation: str | None


def _parse_b_i(flds: str) -> tuple[str, str] | None:
    """Return (l2, gloss) if the first Anki field matches the pattern, else None."""
    first_field = flds.split("\x1f")[0]
    m = _B_THEN_I_PATTERN.match(first_field)
    if m is None:
        return None
    return (m.group(1).strip(), m.group(2).strip())


def plan_cleanup(tt_conn: sqlite3.Connection, anki_conn: sqlite3.Connection) -> list[PlanItem]:
    """Build the cleanup plan by joining TT collocations against Anki notes.

    Pure: read-only on both connections. The caller is responsible for applying
    the plan (or for displaying it under --dry-run).
    """
    plan: list[PlanItem] = []
    rows = tt_conn.execute("SELECT id, text, anki_note_id FROM collocations WHERE anki_note_id IS NOT NULL").fetchall()
    for row in rows:
        tt_id = row["id"]
        tt_text = row["text"]
        anki_note_id = row["anki_note_id"]
        note = anki_conn.execute("SELECT flds FROM notes WHERE id = ?", (anki_note_id,)).fetchone()
        if note is None:
            continue
        parsed = _parse_b_i(note[0])
        if parsed is None:
            continue
        clean_l2, gloss = parsed
        # Already clean? Skip.
        if tt_text == clean_l2:
            continue
        # Does a separate TT row already use the clean L2 text as its text?
        twin = tt_conn.execute("SELECT id FROM collocations WHERE text = ? AND id != ?", (clean_l2, tt_id)).fetchone()
        if twin is not None:
            plan.append(PlanItem(action="delete", tt_id=tt_id, new_text=None, new_translation=None))
        else:
            plan.append(PlanItem(action="rename", tt_id=tt_id, new_text=clean_l2, new_translation=gloss))
    return plan


def apply_plan(tt_conn: sqlite3.Connection, plan: list[PlanItem]) -> dict[str, int]:
    """Apply a cleanup plan against the TT connection. Returns action counts.

    Defensive: a rename that would violate the UNIQUE(text) constraint falls
    back to a delete — covers race / pre-existing-dup edge cases that
    `plan_cleanup` may have missed.
    """
    counts = {"renamed": 0, "deleted": 0, "rename_fallback_to_delete": 0}
    for item in plan:
        if item.action == "rename":
            try:
                tt_conn.execute(
                    "UPDATE collocations SET text = ?, translation = ? WHERE id = ?",
                    (item.new_text, item.new_translation, item.tt_id),
                )
                counts["renamed"] += 1
            except sqlite3.IntegrityError:
                # Twin slipped in. Delete the mangled row instead.
                tt_conn.execute("DELETE FROM collocation_directions WHERE collocation_id = ?", (item.tt_id,))
                tt_conn.execute("DELETE FROM collocations WHERE id = ?", (item.tt_id,))
                counts["rename_fallback_to_delete"] += 1
        else:  # delete
            tt_conn.execute("DELETE FROM collocation_directions WHERE collocation_id = ?", (item.tt_id,))
            tt_conn.execute("DELETE FROM collocations WHERE id = ?", (item.tt_id,))
            counts["deleted"] += 1
    tt_conn.commit()
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    parser.add_argument("--dry-run", action="store_true", help="show the plan without writing")
    parser.add_argument("--anki-db", type=Path, default=None, help="override Anki collection path")
    parser.add_argument("--tt-db", type=Path, default=None, help="override TT database path")
    args = parser.parse_args(argv)

    tt_path = args.tt_db or Path(settings.database_url.removeprefix("sqlite:///"))
    anki_path = args.anki_db or (Path.home() / "Library/Application Support/Anki2/Will/collection.anki2")

    if not tt_path.exists():
        print(f"TT database not found: {tt_path}", file=sys.stderr)
        return 1
    if not anki_path.exists():
        print(f"Anki collection not found: {anki_path}", file=sys.stderr)
        return 1

    tt_conn = sqlite3.connect(str(tt_path))
    tt_conn.row_factory = sqlite3.Row
    anki_conn = sqlite3.connect(f"file:{anki_path}?mode=ro", uri=True)
    anki_conn.row_factory = sqlite3.Row

    plan = plan_cleanup(tt_conn, anki_conn)
    print(f"Found {len(plan)} mangled rows.")
    for item in plan:
        if item.action == "rename":
            print(f"  RENAME id={item.tt_id} → text={item.new_text!r} translation={item.new_translation!r}")
        else:
            print(f"  DELETE id={item.tt_id}")

    if args.dry_run or not plan:
        print("--dry-run: no changes applied." if args.dry_run else "Nothing to apply.")
        return 0

    counts = apply_plan(tt_conn, plan)
    print(f"Applied: {counts}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
