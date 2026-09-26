#!/usr/bin/env python
"""Add a Fluent Forever style base list as picture cards, AFTER the cards waiting.

    uv run python scripts/seed_base_list.py --language ceb                      # dry run
    uv run python scripts/seed_base_list.py --language ceb --add 75 --apply     # next 75 words
    # ...sync the language in the app: it mints the cards and fetches their media...
    uv run python scripts/seed_base_list.py --language ceb --position --apply   # to the back
    # ...sync again to carry the positions to Anki.

``--add`` takes the next words in LIST ORDER that are not cards yet, so chunks go
in order. Chunk because each minted card costs a sync-time image query (an LLM
call) plus a picture and an audio fetch: 75 took about ten minutes on 2026-09-26,
and the LLM's free tier has a daily token budget.

``--position`` gives every minted, still-new list card the position its rank
fixes (``app.srs.base_list``), behind every card already waiting. It writes
TunaTale's own collection (``tt_collection``) inside ``safe_open`` — ``usn = -1``
and ``mod`` per card, one ``col.mod`` bump, never ``col.usn`` or ``col.scm`` —
and points TunaTale's ``anki_due`` mirror at the same values. It refuses a deck
that gathers new cards by descending position, where "the back" is the front.

Run it on the LIVE side (``./switch.sh status``) under that side's env; back up
the language's DB first. The logic and tests live in ``app.srs.base_list``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.config import settings
from app.languages import resolve_language_context
from app.plugins.anki_sync.reposition_production_cards import (
    RepositionPlan,
    apply_repositioning,
    mirror_positions_to_tt,
)
from app.plugins.anki_sync.safety import safe_open
from app.srs import base_list
from app.srs.anki_mirror.queue_stats import new_cards_gather_descending
from app.srs.cognate_seed import normalize
from app.srs.database import SRSDatabase
from app.srs.function_words import is_function_word

_DATA = Path(__file__).resolve().parents[1] / "app/plugins/languages"
LIST_NAME = "Fluent Forever 625"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--language", required=True)
    parser.add_argument("--list", type=Path, default=None, help="base-list TSV (default: the language's base625.tsv)")
    parser.add_argument("--add", type=int, default=0, help="add the next N words as new cards")
    parser.add_argument("--position", action="store_true", help="move minted list cards behind everything waiting")
    parser.add_argument("--accept", default="", help="comma-separated unconfirmed words to accept")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args(argv)

    context = resolve_language_context(args.language, settings)
    list_path = args.list or _DATA / args.language / "data/base625.tsv"
    with list_path.open(encoding="utf-8") as fh:
        words = base_list.load_base_list(fh)
    db = SRSDatabase(context.db_url)
    rows, _ = db.list_collocations(limit=1_000_000)
    have = frozenset(normalize(item.syntactic_unit.text) for _, item, _ in rows)
    plan = base_list.plan_base_list(
        words,
        have=have,
        function_word=lambda w: is_function_word(w, args.language),
        accept=frozenset(normalize(w) for w in args.accept.split(",") if w.strip()),
    )
    print(
        f"{len(words)} list rows: {len(plan.to_add)} to add, {len(plan.already_have)} already cards, "
        f"{len(plan.function_words)} function words (clozes, not here), {len(plan.unconfirmed)} unconfirmed."
    )
    if plan.unconfirmed:
        print("UNCONFIRMED (the dictionary cannot vouch for them; --accept to add):")
        print("  " + ", ".join(f"{w.text} ({w.english})" for w in plan.unconfirmed))

    if args.add:
        chunk = plan.to_add[: args.add]
        print(f"\nNext {len(chunk)}: " + ", ".join(w.text for w in chunk))
        if args.apply:
            added = base_list.mint_base_words(db, chunk, language_code=args.language, list_name=LIST_NAME)
            print(f"Added {added} new card(s). Sync, then run --position --apply.")

    if args.position:
        if new_cards_gather_descending(db):
            print("REFUSING: this deck gathers new cards by DESCENDING position, where the back is the front.")
            return 2
        assignments = base_list.back_positions(db, words, language_code=args.language)
        print(f"\n{len(assignments)} minted list card(s) to place behind the waiting cards.")
        if args.apply and assignments:
            with safe_open(settings.tt_collection_path, mode="rw") as ctx:
                ids = ",".join(str(cid) for cid, _ in assignments)
                current = dict(ctx.conn.execute(f"SELECT id, due FROM cards WHERE id IN ({ids})").fetchall())
                present = [(cid, pos) for cid, pos in assignments if cid in current]
                moves = [(cid, pos) for cid, pos in present if current[cid] != pos]
                apply_repositioning(ctx.conn, RepositionPlan(assignments=present, moves=moves))
                ctx.conn.commit()
            with db._get_conn() as tt_conn:
                mirrored = mirror_positions_to_tt(tt_conn, present)
                tt_conn.commit()
            print(f"Moved {len(moves)} card(s) in the collection; mirrored {mirrored} in TunaTale. Sync now.")

    if not args.apply:
        print("\nDry run. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
