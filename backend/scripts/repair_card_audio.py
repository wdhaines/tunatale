#!/usr/bin/env python
"""Redo the word audio of cards a sync voiced in the wrong language (tunatale-r8hk).

    uv run python scripts/repair_card_audio.py --language ceb --source base-list --source cognate
    uv run python scripts/repair_card_audio.py --language ceb --source base-list --source cognate \\
        --db sqlite:///…/tunatale_ceb.db --db sqlite:///…/users/2/tunatale_ceb.db --tts-limit 200 --apply

Dry run by default: it prints what would be dropped and how much metered TTS the
``--tts-limit`` could spend at most. With ``--apply`` it, per database:

1. drops the word audio of every vocab card from the named ``--source``s (plus
   any ``--word``) and flags the Audio field for the next sync;
2. stores a Forvo recording, in ``--language``, for each card that has one;
3. renders TTS for up to ``--tts-limit`` of the rest — cards already met first,
   then the next few new ones. Everything else is left for the audio pre-stage,
   which reaches a card when it is about to be seen.

Give every learner's DB in ONE run (repeat ``--db``): lookups and renders are
shared, so the second learner costs no second request. Then sync the language
to carry the result to Anki. Run it on the LIVE side under that side's env, and
back the DBs up first. Logic and tests live in ``app.cards.media.audio_repair``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.cards.media.audio_prestage import prestage_card_audio
from app.cards.media.audio_repair import MemoFetch, apply_audio_repair, plan_audio_repair
from app.config import settings
from app.languages import resolve_language_context
from app.srs.database import SRSDatabase


async def _repair(args: argparse.Namespace, fetch_fn=None) -> None:
    fetch = MemoFetch(fetch_fn)
    for url in args.db or [resolve_language_context(args.language, settings).db_url]:
        db = SRSDatabase(url)
        plan = plan_audio_repair(db, sources=args.source, words=args.word)
        seen = [r for r in plan if r.seen]
        print(f"{url}")
        print(f"  {len(plan)} card(s) to redo: {len(seen)} already met, {len(plan) - len(seen)} not yet.")
        print(
            f"  TTS: at most {min(args.tts_limit, len(plan))} render(s), "
            f"{sum(len(r.text) for r in seen)} characters across the cards already met."
        )
        if not args.apply or not plan:
            continue
        report = await apply_audio_repair(db, plan, args.language, fetch_fn=fetch, delay=args.forvo_delay)
        print(
            f"  dropped {report.dropped}, Forvo gave {report.forvo}; "
            f"awaiting TTS: {report.awaiting_tts_seen} met, {report.awaiting_tts_unseen} not yet."
        )
        staged = await prestage_card_audio(db, language_code=args.language, limit=args.tts_limit, fetch_fn=fetch)
        print(f"  rendered {staged.tts} by TTS ({staged.no_audio} got nothing, {staged.failed} failed).")
    if args.apply:
        print("Sync the language to carry this to Anki.")


def main(argv: list[str] | None = None, *, fetch_fn=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--language", required=True)
    parser.add_argument("--db", action="append", help="database URL; repeat per learner (default: the language's DB)")
    parser.add_argument("--source", action="append", default=[], help="redo every vocab card with this source")
    parser.add_argument("--word", action="append", default=[], help="redo this card too")
    parser.add_argument("--tts-limit", type=int, default=0, help="TTS renders per DB after the Forvo sweep (default 0)")
    parser.add_argument("--forvo-delay", type=float, default=1.0, help="seconds between Forvo requests")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args(argv)
    if not args.source and not args.word:
        parser.error("name the cards to redo with --source and/or --word")
    asyncio.run(_repair(args, fetch_fn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
