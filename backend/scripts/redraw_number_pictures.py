#!/usr/bin/env python
"""Swap existing number cards' photos for the drawn picture (tunatale-w4m7.10).

    uv run python scripts/redraw_number_pictures.py --language ceb            # dry run: the plan
    uv run python scripts/redraw_number_pictures.py --language ceb --apply    # write it

Then sync the language: the ordinary push writes each new picture into its Anki
note. Numbers minted from now on are drawn at creation; this is for the cards
minted before that, which kept a Pixabay photo. Logic and tests live in
``app.cards.number_redraw``.

Run it on the LIVE side (``./switch.sh status``) under that side's env, once per
learner DB (``--db`` for a learner other than the owner), and back the DB up
first. It rewrites the Image field of whatever note a row points at, so read the
dry run before applying — for a language whose number cards came from the
user's own Anki deck, those are cards the user made.
"""

from __future__ import annotations

import argparse
import sys

from app.cards.number_redraw import apply_number_redraws, plan_number_redraws
from app.config import settings
from app.languages import resolve_language_context
from app.srs.database import SRSDatabase


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--language", required=True)
    parser.add_argument("--db", default=None, help="database URL (default: the language's configured DB)")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args(argv)

    db = SRSDatabase(args.db or resolve_language_context(args.language, settings).db_url)
    plan = plan_number_redraws(db, args.language)
    for r in plan:
        push = "push" if r.linked else "no Anki note"
        print(f"  {r.collocation_id:>6}  {r.text:<14} {r.old_filename}  ->  {r.new_filename}  ({push})")
    print(f"{len(plan)} card(s) to redraw.")
    if args.apply and plan:
        apply_number_redraws(db, plan, args.language)
        print(f"Redrew {len(plan)}. Sync the language to carry them to Anki.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
