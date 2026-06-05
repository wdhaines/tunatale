"""Fix biti clozes-only data: repurpose base cloze (858), drop duplicate (868), clear si leak (866).

Background:
  biti ("to be") was treated as a function word, producing a base cloze (858,
  blanking "ste" in "Zdravo kje ste") plus a separately-clicked 2pl conjugation
  cloze (868, identical). This fix repurposes 858 as the conjugation cloze and
  deletes 868 (TT-only, never synced to Anki). Additionally clears a legacy
  anomaly where 866 (si) had the grammar hint stored in its translation field.

What this does:
  1. Collocation 858: text 'biti' → 'ste', disambig_key '' → 'morph:verb-2pl',
     grammar ← format_morphology_hint('biti', 'verb:2pl'), guid recomputed.
     Keeps lemma='biti', anki_note_id (Anki note stays linked).
  2. Collocation 868: deleted entirely (TT-only; anki_note_id IS NULL).
     Cascades: collocation_directions, tt_revlog rows.
  3. Collocation 866 (si): clears translation field (was a stale copy of the grammar hint)
     and marks ``dirty_fields`` so sync_push propagates the empty value to Anki.

Usage::

    uv run python -m app.anki.fix_biti_clozes_only --dry-run
    uv run python -m app.anki.fix_biti_clozes_only          # apply
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from app.common.guid import compute_guid
from app.srs.function_words import format_morphology_hint

SOURCE_ID = 858
DUPLICATE_ID = 868
SI_LEMMA_ID = 866  # si translation field leaked grammar hint


def plan_fix(tt_conn: sqlite3.Connection) -> dict:
    """Read-only inspection: return current state of rows, or None if missing."""
    result: dict[str, object] = {}
    for cid in (SOURCE_ID, DUPLICATE_ID, SI_LEMMA_ID):
        row = tt_conn.execute("SELECT * FROM collocations WHERE id = ?", (cid,)).fetchone()
        result[str(cid)] = dict(row) if row else None

    # Check if translation is already dirty — if so, no change needed
    si_row = result.get(str(SI_LEMMA_ID))
    if si_row:
        dirty = (si_row.get("dirty_fields") or "").split(",")
        result["si_translation_dirty"] = "translation" in dirty

    # Count directions + revlog for the duplicate
    if result.get(str(DUPLICATE_ID)):
        result["duplicate_directions"] = tt_conn.execute(
            "SELECT COUNT(*) FROM collocation_directions WHERE collocation_id = ?",
            (DUPLICATE_ID,),
        ).fetchone()[0]
        result["duplicate_revlog"] = tt_conn.execute(
            "SELECT COUNT(*) FROM tt_revlog WHERE collocation_id = ?",
            (DUPLICATE_ID,),
        ).fetchone()[0]
    return result


def apply_fix(tt_conn: sqlite3.Connection) -> dict[str, int]:
    """Apply the fix. Returns counts of rows touched."""
    counts: dict[str, int] = {"source_updated": 0, "duplicate_deleted": 0, "directions_deleted": 0, "revlog_deleted": 0}

    cur = tt_conn.execute("SELECT changes()")
    _ = cur.fetchone()[0]  # reset changes() to 0

    # 1. Delete duplicate collocation directions (cascade)
    tt_conn.execute("DELETE FROM collocation_directions WHERE collocation_id = ?", (DUPLICATE_ID,))
    counts["directions_deleted"] = tt_conn.execute("SELECT changes()").fetchone()[0]

    # 2. Delete duplicate tt_revlog rows
    tt_conn.execute("DELETE FROM tt_revlog WHERE collocation_id = ?", (DUPLICATE_ID,))
    counts["revlog_deleted"] = tt_conn.execute("SELECT changes()").fetchone()[0]

    # 3. Delete the duplicate collocation itself (before updating 858 to avoid unique constraint)
    tt_conn.execute("DELETE FROM collocations WHERE id = ?", (DUPLICATE_ID,))
    counts["duplicate_deleted"] = tt_conn.execute("SELECT changes()").fetchone()[0]

    # 4. Update source (858): repurpose as 2pl conjugation cloze
    new_guid = compute_guid("ste", "sl", "morph:verb-2pl")
    new_grammar = format_morphology_hint("biti", "verb:2pl")
    cur = tt_conn.execute(
        """UPDATE collocations
           SET text = 'ste',
               disambig_key = 'morph:verb-2pl',
               grammar = ?,
               guid = ?
           WHERE id = ?""",
        (new_grammar, new_guid, SOURCE_ID),
    )
    counts["source_updated"] = tt_conn.execute("SELECT changes()").fetchone()[0]

    # 5. Clear si translation field (was a stale copy of grammar hint)
    tt_conn.execute("UPDATE collocations SET translation = '' WHERE id = ?", (SI_LEMMA_ID,))
    counts["si_translation_cleared"] = tt_conn.execute("SELECT changes()").fetchone()[0]

    # 6. Mark translation as dirty so sync_push sends the empty value to Anki
    #    (push runs before pull, so Anki's stale translation gets overwritten
    #    before pull can restore it).
    existing = tt_conn.execute("SELECT dirty_fields FROM collocations WHERE id = ?", (SI_LEMMA_ID,)).fetchone()
    current_dirty = (existing["dirty_fields"] or "") if existing else ""
    dirty_set = {f for f in current_dirty.split(",") if f}
    if "translation" not in dirty_set:
        dirty_set.add("translation")
    new_dirty = ",".join(sorted(dirty_set))
    tt_conn.execute("UPDATE collocations SET dirty_fields = ? WHERE id = ?", (new_dirty, SI_LEMMA_ID))
    counts["si_dirty_fields_set"] = tt_conn.execute("SELECT changes()").fetchone()[0]

    tt_conn.commit()
    return counts


def _print_plan(info: dict) -> None:  # pragma: no cover
    print("Plan: fix biti clozes-only collocations")
    src = info.get("858")
    dup = info.get("868")
    si_row = info.get("866")
    if src:
        print(f"  Update collocation 858: text={src['text']!r} → 'ste', set disambig_key='morph:verb-2pl'")
        if src.get("anki_note_id"):
            print(f"    → keeps anki_note_id={src['anki_note_id']} (linked note preserved)")
    else:
        print("  Collocation 858: not found (nothing to update)")

    if dup:
        d_cnt = info.get("duplicate_directions", 0)
        r_cnt = info.get("duplicate_revlog", 0)
        print(f"  Delete collocation 868 ({d_cnt} direction(s), {r_cnt} revlog row(s))")
    else:
        print("  Collocation 868: not found (nothing to delete)")

    si_dirty = info.get("si_translation_dirty", False)
    if si_row and si_row.get("translation"):
        print(f"  Clear collocation 866 translation: {si_row['translation']!r} → ''")
        print("    → Mark dirty_fields so sync_push propagates to Anki")
    elif not si_dirty:
        print("  Collocation 866: translation already empty, mark dirty for sync_push")
    else:
        print("  Collocation 866: translation already empty and dirty — nothing to fix")


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    parser.add_argument("--dry-run", action="store_true", help="show the plan without writing")
    parser.add_argument("--tt-db", type=Path, default=None, help="override TT database path")
    args = parser.parse_args(argv)

    from app.config import settings

    tt_path = args.tt_db or Path(settings.database_url.removeprefix("sqlite:///"))
    if not tt_path.exists():
        print(f"TT database not found: {tt_path}", file=sys.stderr)
        return 1

    if args.dry_run:
        tt_conn = sqlite3.connect(f"file:{tt_path}?mode=ro", uri=True)
        tt_conn.row_factory = sqlite3.Row
        try:
            info = plan_fix(tt_conn)
            _print_plan(info)
            print("--dry-run: no changes applied.")
        finally:
            tt_conn.close()
        return 0

    tt_conn = sqlite3.connect(str(tt_path))
    tt_conn.row_factory = sqlite3.Row
    try:
        info = plan_fix(tt_conn)
        if not info.get("858") and not info.get("868"):
            print("Nothing to fix.")
            return 0
        _print_plan(info)
        counts = apply_fix(tt_conn)
        print(f"Applied: {counts}")
    finally:
        tt_conn.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
