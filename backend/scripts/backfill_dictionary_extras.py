"""Give a learner's TT deck, which has no Anki, its dictionary layers (tunatale-h0q3.2).

The owner's Cebuano cards get Wolff's spelling and layers through Anki
(``app.plugins.anki_sync.backfill_dictionary_fields``). A learner with no Anki
(user 2) has no note to pull them from, so this writes the extras into their TT
DB directly, through ``app.cards.dictionary_extras``: the same extras the pull
would make.

- Dry run by default; ``--apply`` first copies the DB to ``<db>.pre-h0q32-<stamp>``.
- Never overwrites: a card already carrying any of the layers is left alone,
  and other extras on a card are kept, ahead of the layers.
- Refuses a DB with any card linked to Anki: there the pull owns extras.

Run on the live side with the live instance's env (see docs/deployment.md):
    .venv/bin/python scripts/backfill_dictionary_extras.py --db data/users/2/tunatale_ceb.db --language ceb [--apply]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from app.cards.dictionary_extras import dictionary_extras
from app.languages import get_vocab_notetype
from app.srs.database import SRSDatabase


def _backup(path: Path) -> Path:
    dest = path.with_name(f"{path.name}.pre-h0q32-{datetime.now().strftime('%Y%m%dT%H%M%S')}")
    src, dst = sqlite3.connect(str(path)), sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, type=Path, help="The learner's TT DB file")
    parser.add_argument("--language", required=True, help="Language code (e.g. ceb)")
    parser.add_argument("--apply", action="store_true", help="Write; without it this is a dry run")
    args = parser.parse_args(argv)

    vocab = get_vocab_notetype(args.language)
    if vocab is None or not vocab.dictionary:
        print(f"Language {args.language!r} has no dictionary layers", file=sys.stderr)
        return 1
    if not args.db.is_file():
        print(f"{args.db} not found", file=sys.stderr)
        return 1
    db = SRSDatabase(str(args.db))
    try:
        rows = db.list_vocab_for_extras(args.language)
        if any(r.anki_note_id is not None for r in rows):
            print(
                f"{args.db} has cards linked to Anki: their layers come through Anki, not this script", file=sys.stderr
            )
            return 1
        plan, misses, already = [], [], 0
        for row in rows:
            layers = dictionary_extras(args.language, row.text, row.translation)
            if not layers:
                misses.append(row)
            elif {e.label for e in row.extras} & {e.label for e in layers}:
                already += 1
            else:
                plan.append((row, layers))
        if args.apply and plan:
            print(f"Backed up to {_backup(args.db)}", flush=True)
            for row, layers in plan:
                db.set_extras(row.id, (*row.extras, *layers))
    finally:
        db.close()

    tag, verb = ("[DONE]", "filled") if args.apply else ("[DRY RUN]", "would fill")
    print(
        f"{tag} {args.db}: {verb} {len(plan)} cards; {already} already have them; "
        f"{len(misses)} not settled by the dictionary:"
    )
    for row in misses:
        print(f"  {row.text}\t{row.translation}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
