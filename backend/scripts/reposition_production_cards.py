#!/usr/bin/env python
"""CLI for the one-shot production-card repositioning (Layer 83, tunatale-uze6).

    uv run python scripts/reposition_production_cards.py --language no
    uv run python scripts/reposition_production_cards.py --language no --apply

Dry-run by default: it prints the plan and touches nothing. ``--apply`` performs the
write inside ``safety.safe_open(mode="rw")`` (lock probe, SHA256 backup, integrity
check) and verifies every changed row against the backup afterwards via
``ctx.audit_changes``, so a write outside the plan fails the run rather than shipping
silently.

All of the logic — and all of the tests — live in
``app.plugins.anki_sync.reposition_production_cards``. This file is the wiring.

⚠️ Anki must be closed; ``safe_open`` aborts otherwise.
"""

from __future__ import annotations

import argparse
import sys

from app.config import settings
from app.languages import resolve_language_context
from app.plugins.anki_sync.reposition_production_cards import (
    apply_repositioning,
    mirror_positions_to_tt,
    plan_repositioning,
)
from app.plugins.anki_sync.safety import safe_open
from app.plugins.anki_sync.sqlite_reader import find_deck_id
from app.srs.anki_mirror.queue_stats import new_cards_gather_descending
from app.srs.database import SRSDatabase


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", default=None, help="language code, e.g. the configured target language")
    parser.add_argument("--apply", action="store_true", help="perform the write (default: dry run)")
    args = parser.parse_args(argv)

    context = resolve_language_context(args.language, settings)
    db = SRSDatabase(context.db_url)

    # The band is only ahead of the queue under ascending gather. On a
    # HighestPosition deck the tail already IS the front, and repositioning would
    # move these cards to the BACK — the exact bug being fixed, inverted.
    if new_cards_gather_descending(db):
        print(
            f"REFUSING: deck {context.deck_name!r} gathers new cards by DESCENDING position, "
            "where MAX(due)+1 is already the front of the queue. Repositioning would move "
            "these cards to the back. Nothing to do here."
        )
        return 2

    mode = "rw" if args.apply else "ro"
    with safe_open(settings.anki_collection_path, mode=mode) as ctx:
        deck_id = find_deck_id(ctx.conn, context.deck_name)
        if deck_id is None:
            print(f"REFUSING: deck {context.deck_name!r} not found in the collection")
            return 2

        plan = plan_repositioning(ctx.conn, deck_id)
        print(f"deck            {context.deck_name}")
        print(f"minted cards    {plan.total}")
        print(f"already placed  {plan.already_placed}")
        print(f"to move         {len(plan.moves)}")
        if plan.moves:
            first, last = plan.moves[0], plan.moves[-1]
            print(f"first move      card {first[0]} -> position {first[1]}")
            print(f"last move       card {last[0]} -> position {last[1]}")

        if not args.apply:
            print("\nDRY RUN — nothing written. Re-run with --apply.")
            return 0

        if plan.moves:
            apply_repositioning(ctx.conn, plan)
            ctx.conn.commit()
            # cards.due is an INTEGER column, so the planned values must be ints.
            # Passing str() here reported all 158 planned writes as "missing" on a run
            # whose writes had in fact all landed (2026-08-22) — the audit compares
            # against the raw source value, and -1000000 != '-1000000'.
            ctx.audit_changes("cards", "id", "due", dict(plan.moves))
        else:
            print("\nCollection already placed; nothing to write there.")

    # Always run, even when the collection needed no writes: the mirror is the half
    # that can be stale on its own, and a run with empty `moves` is exactly the case
    # that repairs it.
    with db._get_conn() as tt_conn:
        mirrored = mirror_positions_to_tt(tt_conn, plan)
        tt_conn.commit()
    print(f"\nDone. {len(plan.moves)} cards repositioned, {mirrored} TunaTale mirror rows updated.")
    print("Backup + audit passed. Sync from TunaTale when convenient; nothing forces a full sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
