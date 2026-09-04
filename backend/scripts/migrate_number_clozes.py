#!/usr/bin/env python
"""Retire the number words' cloze production cards (tunatale-y27g).

    # step 1 — capture + delete. Dry run first; --apply writes.
    uv run python scripts/migrate_number_clozes.py --language no --delete
    uv run python scripts/migrate_number_clozes.py --language no --delete --apply

    # ...then sync from TunaTale so the pre-stage draws and the mint creates
    # the image production cards...

    # step 2 — carry the old scheduling onto the new cards.
    uv run python scripts/migrate_number_clozes.py --language no --inherit
    uv run python scripts/migrate_number_clozes.py --language no --inherit --apply

⚠️ **Why two steps.** The replacement card does not exist until a sync mints it,
and the mint refuses any word that already has a production direction
(``_AWAITING_PRODUCTION_WHERE``). Writing the inherited state up front would
therefore remove the word from the queue and no card would ever be minted. Step 1
captures the state into ``--capture-file`` so step 2 needs nothing that step 1
deleted.

⚠️ **The word list is DERIVED, never typed.** ``number_value`` over the shipping
vocabulary is the selector. The first pass at this bead hand-typed a list into
SQL and silently omitted three words (seksten, sytti, åtti).

⚠️ **Anki is graved BEFORE the TT row is deleted, and the order is load-bearing.**
``safe_open`` does not roll back, so a failure between the two halves leaves a
half-done migration; this order makes that state self-repairing. Anki-graved but
TT-not-deleted is exactly what ``detect_and_reset_orphans`` fixes on the next
sync — it sees a note grave and hard-deletes the TT collocation. The reverse
order strands a TT-less Cloze note in Anki for the reverse-import to adopt,
recreating the card this migration exists to remove.

⚠️ Anki must be closed; ``safe_open`` aborts otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.cards.number_image import number_value
from app.config import settings
from app.languages import resolve_language_context
from app.plugins.anki_sync.migrate_number_clozes import (
    apply_inheritance,
    bump_col_mod,
    delete_tt_clozes,
    grave_cloze_note,
    plan_migration,
    state_from_json,
    state_to_json,
)
from app.plugins.anki_sync.safety import safe_open
from app.srs.database import SRSDatabase

DEFAULT_CAPTURE = Path("~/.tunatale/number-cloze-migration.json").expanduser()


def _number_words_with_clozes(conn, language_code: str) -> list[str]:
    """Every vocabulary word that IS a drawable number and carries a cloze."""
    rows = conn.execute(
        """
        SELECT DISTINCT b.text
        FROM collocations z JOIN collocations b ON b.id = z.base_collocation_id
        WHERE z.card_type = 'cloze'
        """
    ).fetchall()
    return sorted(r["text"] for r in rows if number_value(r["text"], language_code) is not None)


def _do_delete(args, context, db: SRSDatabase) -> int:
    with db._get_conn() as tt_conn:
        words = _number_words_with_clozes(tt_conn, context.target_language)
        items = plan_migration(tt_conn, words)

    if not items:
        print("Nothing to migrate: no number word carries a cloze.")
        return 0

    print(f"{len(items)} number word(s) carrying a cloze:\n")
    for item in items:
        carries = f"INHERITS {item.inherited.state} reps={item.inherited.reps}" if item.inherits else "no history"
        note = item.cloze_note_id if item.cloze_note_id is not None else "—"
        print(f"  {item.base_text:10} cloze={item.cloze_id:<6} anki_note={note!s:16} {carries}")
    print(f"\n  {sum(i.inherits for i in items)} carry scheduling; {sum(not i.inherits for i in items)} start new")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0

    args.capture_file.parent.mkdir(parents=True, exist_ok=True)
    args.capture_file.write_text(json.dumps(state_to_json(items), indent=2))
    print(f"\nCaptured state -> {args.capture_file}")

    # Anki FIRST — see the module docstring on why this order is load-bearing.
    graved_notes = graved_cards = 0
    with safe_open(settings.anki_collection_path, mode="rw") as ctx:
        for item in items:
            if item.cloze_note_id is None:
                continue
            # Ask BEFORE graving: a note that is already absent (fem, fire) must
            # not be counted as deleted, and grave_cloze_note returns 0 both for
            # "not there" and for "there with no cards".
            existed = ctx.conn.execute("SELECT 1 FROM notes WHERE id = ?", (item.cloze_note_id,)).fetchone()
            removed = grave_cloze_note(ctx.conn, item.cloze_note_id)
            if existed:
                graved_notes += 1
                graved_cards += removed
        bump_col_mod(ctx.conn)
        ctx.conn.commit()
    print(
        f"Anki: graved {graved_notes} note(s), {graved_cards} card(s). col.mod bumped; col.usn and col.scm untouched."
    )

    with db._get_conn() as tt_conn:
        deleted = delete_tt_clozes(tt_conn, [i.cloze_id for i in items])
        tt_conn.commit()
    print(f"TunaTale: deleted {deleted} cloze row(s); their directions went with the cascade.")

    print("\nNEXT: sync from TunaTale. The pre-stage draws each counting picture and")
    print("the mint creates the image production card. Then re-run with --inherit.")
    return 0


def _do_inherit(args, context, db: SRSDatabase) -> int:
    if not args.capture_file.exists():
        print(f"REFUSING: no capture file at {args.capture_file}. Run --delete --apply first.")
        return 2
    items = state_from_json(json.loads(args.capture_file.read_text()))
    carriers = [i for i in items if i.inherits]
    if not carriers:
        print("Nothing to inherit: no captured word carried scheduling.")
        return 0

    with db._get_conn() as tt_conn:
        pending, ready = [], []
        for item in carriers:
            row = tt_conn.execute(
                "SELECT 1 FROM collocation_directions WHERE collocation_id = ? AND direction = 'production'",
                (item.base_id,),
            ).fetchone()
            (ready if row else pending).append(item)

        for item in ready:
            print(f"  {item.base_text:10} -> {item.inherited.state} reps={item.inherited.reps}")
        for item in pending:
            print(f"  {item.base_text:10} -- still awaiting its mint; sync again")

        if not args.apply:
            print("\nDRY RUN — nothing written. Re-run with --apply.")
            return 0

        applied = sum(apply_inheritance(tt_conn, i.base_id, i.inherited) for i in ready)
        tt_conn.commit()

    print(f"\nApplied scheduling to {applied} production direction(s), each marked dirty_fsrs=1.")
    if pending:
        print(f"{len(pending)} still pending — sync, then re-run --inherit --apply.")
    else:
        print("All captured words are done. Sync once more to push the state into Anki.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", default=None, help="language code")
    parser.add_argument("--capture-file", type=Path, default=DEFAULT_CAPTURE)
    parser.add_argument("--apply", action="store_true", help="perform the write (default: dry run)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--delete", action="store_true", help="step 1: capture state, grave clozes, delete TT rows")
    mode.add_argument("--inherit", action="store_true", help="step 2: carry the state onto the minted cards")
    args = parser.parse_args(argv)

    context = resolve_language_context(args.language, settings)
    db = SRSDatabase(context.db_url)
    return _do_delete(args, context, db) if args.delete else _do_inherit(args, context, db)


if __name__ == "__main__":
    sys.exit(main())
