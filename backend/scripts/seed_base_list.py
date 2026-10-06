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
that gathers new cards by descending position, where "the back" is the front,
and it refuses to write anything when a planned card is no longer a new card in
the collection, where ``due`` is no longer a position.

A learner deck with no Anki behind it (``--tt-db <path> --no-anki``) has no
collection to write: ``--position`` then sets TunaTale's own positions and drops
the frozen queue, since no sync will come along to rebuild it. It refuses a deck
that is linked to Anki.

    uv run python scripts/seed_base_list.py --language ceb --tt-db <learner db> --no-anki --position --apply

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
from app.srs.anki_mirror.queue_stats import clear_session_main_queue, new_cards_gather_descending
from app.srs.cognate_seed import normalize
from app.srs.database import SRSDatabase
from app.srs.function_words import is_function_word

_DATA = Path(__file__).resolve().parents[1] / "app/plugins/languages"
LIST_NAME = "Fluent Forever 625"


def _position_tt_only(db: SRSDatabase, words: list[base_list.BaseWord], args: argparse.Namespace) -> int:
    try:
        positions = base_list.tt_only_positions(db, words, language_code=args.language)
    except ValueError as exc:
        print(f"REFUSING: {exc}")
        return 2
    print(f"\n{len(positions)} list card(s) to place behind the waiting cards (TunaTale only).")
    if not args.apply:
        print("\nDry run. Re-run with --apply to write.")
        return 0
    with db._get_conn() as tt_conn:
        moved = base_list.apply_tt_only_positions(tt_conn, positions)
        tt_conn.commit()
    clear_session_main_queue(db)
    print(f"Moved {moved} card(s) in TunaTale.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--language", required=True)
    parser.add_argument("--list", type=Path, default=None, help="base-list TSV (default: the language's base625.tsv)")
    parser.add_argument("--add", type=int, default=0, help="add the next N words as new cards")
    parser.add_argument("--position", action="store_true", help="move minted list cards behind everything waiting")
    parser.add_argument("--list-name", default=LIST_NAME, help=f"label on each minted card (default: {LIST_NAME})")
    parser.add_argument("--accept", default="", help="comma-separated unconfirmed words to accept")
    parser.add_argument("--tt-db", type=Path, default=None, help="TT database (default: the language's)")
    parser.add_argument("--no-anki", action="store_true", help="a deck with no Anki: TunaTale positions only")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args(argv)

    context = resolve_language_context(args.language, settings)
    list_path = args.list or _DATA / args.language / "data/base625.tsv"
    with list_path.open(encoding="utf-8") as fh:
        words = base_list.load_base_list(fh)
    db = SRSDatabase(f"sqlite:///{args.tt_db}" if args.tt_db else context.db_url)
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
            added = base_list.mint_base_words(db, chunk, language_code=args.language, list_name=args.list_name)
            print(f"Added {added} new card(s). Sync, then run --position --apply.")

    if args.position:
        if new_cards_gather_descending(db):
            print("REFUSING: this deck gathers new cards by DESCENDING position, where the back is the front.")
            return 2
        if args.no_anki:
            return _position_tt_only(db, words, args)
        assignments = base_list.back_positions(db, words, language_code=args.language)
        print(f"\n{len(assignments)} minted list card(s) to place behind the waiting cards.")
        if args.apply and assignments:
            with safe_open(settings.tt_collection_path, mode="rw") as ctx:
                ids = ",".join(str(cid) for cid, _ in assignments)
                rows = ctx.conn.execute(f"SELECT id, due, type FROM cards WHERE id IN ({ids})").fetchall()
                # `due` is a queue position only on a new card (type 0); on any other
                # it is a due day or a timestamp. The plan comes from TunaTale's
                # state, so a card that moved on in the collection means the two
                # disagree, and writing a position there would corrupt its schedule.
                not_new = sorted(cid for cid, _, card_type in rows if card_type != 0)
                if not_new:
                    shown = ", ".join(str(cid) for cid in not_new[:10])
                    print(
                        f"REFUSING: {len(not_new)} planned card(s) are not new in the collection ({shown}). Sync first."
                    )
                    return 2
                current = {cid: due for cid, due, _ in rows}
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
