#!/usr/bin/env python
"""Bring a deck's TT rows in line with first-class short forms (tunatale-0rlg).

    .venv/bin/python scripts/apply_short_forms.py --language ceb [--db <path>] [--apply]

Renames each shared card's comma headword to its long form, brings each subject
gloss to the pronoun table's, and gives back the recognition of each second sense
its Short form line tells apart. A dry run until --apply. Then sync: the push
carries the changes to Anki.

Live order, so the backfill reads the new words and glosses: add_short_form_field
(and its full upload), this script, sync, then
``backfill_dictionary_fields --refresh``, sync. A learner with no Anki runs this
script and then ``backfill_dictionary_extras --refresh`` on their own DB.

A renamed card's audio still says both forms.
"""

from __future__ import annotations

import argparse
import sys

from app.config import settings
from app.languages import resolve_language_context
from app.models.srs_item import Direction
from app.plugins.anki_sync.convert_pronoun_clozes import gloss_updates
from app.plugins.anki_sync.short_form_cards import plan_front_renames, plan_unsuspends
from app.srs.database import SRSDatabase


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--language", required=True)
    parser.add_argument("--db", default=None, help="database (default: the language's configured DB)")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args(argv)

    db = SRSDatabase(args.db or resolve_language_context(args.language, settings).db_url)
    try:
        with db._get_conn() as conn:
            renames = plan_front_renames(conn, args.language)
            glosses = gloss_updates(conn, args.language)
            unsuspends = plan_unsuspends(conn, args.language)
            translations = {
                row_id: conn.execute("SELECT translation FROM collocations WHERE id = ?", (row_id,)).fetchone()[0]
                for row_id, _old, _new in renames
            }
        for row_id, old, new in renames:
            print(f"  RENAME    id={row_id:<6} {old!r} -> {new!r}")
        for row_id, text, old, new in glosses:
            print(f"  GLOSS     id={row_id:<6} {text}: {old!r} -> {new!r}")
        for row_id, text, key in unsuspends:
            print(f"  UNSUSPEND id={row_id:<6} {text} key={key}: recognition")
        if not (renames or glosses or unsuspends):
            print("Nothing to do.")
            return 0
        if not args.apply:
            print("\nDRY RUN: nothing written. Re-run with --apply.")
            return 0
        for row_id, _old, new in renames:
            db.update_collocation_fields(row_id, text=new, translation=translations[row_id])
        for row_id, text, _old, new in glosses:
            db.update_collocation_fields(row_id, text=text, translation=new)
        for row_id, _text, _key in unsuspends:
            db.set_suspended(row_id, False, Direction.RECOGNITION)
        print(
            f"\nRenamed {len(renames)}, re-glossed {len(glosses)}, unsuspended {len(unsuspends)}. Sync to carry them."
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
