"""Mint each pattern's affix drill card for a deck whose drill lessons are already studied (tunatale-5i1p.6).

A study mark mints the card (``app.srs.affix_drill_card``), but a learner whose
pattern lessons were studied before it shipped will not study them again for
it. This calls the same mint function once per pattern that has a listened
drill lesson, from the LATEST such lesson, then voices the card through the
same synthesis the study mark schedules. TT database only: the next sync
creates the note.

Dry run by default; ``--apply`` writes. Price first: the speech is Gemini, about
$0.004 a card (``.claude/rules/paid-vendors.md``).

Run from ``backend/``, once per learner::

    uv run python scripts/mint_affix_drill_cards.py --language ceb             # the owner's deck, dry run
    uv run python scripts/mint_affix_drill_cards.py --language ceb --apply
    uv run python scripts/mint_affix_drill_cards.py --language ceb --db <data/users/2/...> --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.audio.affix_card_audio import synthesize_drill_card_audio
from app.audio.tts_factory import get_tts_service
from app.config import settings
from app.languages import resolve_db_path
from app.srs.affix_drill_card import affix_card_key, affix_drill_card, mint_affix_drill_card
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore


def _latest_drill_lessons(db, store) -> list[tuple[str, object]]:
    """``(lesson id, lesson)`` per affix-card key, the most recently listened lesson for each."""
    latest: dict[str, tuple[str, object]] = {}
    for listened in db.get_listened_lessons():  # most recent first
        lesson = store.get_readable_content(listened["lesson_id"])
        key = affix_card_key(lesson) if lesson is not None else None
        if key is not None and key not in latest:
            latest[key] = (listened["lesson_id"], lesson)
    return list(latest.values())


async def _run(db_path: str, code: str, apply: bool) -> int:
    store = ContentStore(db_path)
    db = SRSDatabase(db_path, pre_migration_backup_dir=settings.migration_backup_dir)
    tts = get_tts_service(cache_dir=settings.tts_cache_dir) if apply else None
    for lesson_id, lesson in _latest_drill_lessons(db, store):
        if lesson.language_code != code:
            continue
        key = affix_card_key(lesson)
        if db.list_sibling_rows(key):
            print(f"{key}\talready in the deck")
            continue
        card = affix_drill_card(db, store, lesson)
        if card is None:
            print(
                f"{key}\tfrom {lesson_id}: no card (the pattern has no checked chain, or its drill voiced no chain root)"
            )
            continue
        print(f"{key}\tfrom {lesson_id}: {card.model} | " + " | ".join(f"+{s.cue} {s.answer}" for s in card.steps))
        if not apply:
            continue
        cid, card = mint_affix_drill_card(db, store, lesson)
        await synthesize_drill_card_audio(
            db, cid, card, code, tts=tts, cache_dir=settings.tts_cache_dir, media_dir=settings.media_dir
        )
        print(f"{key}\tminted row {cid}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--language", required=True)
    ap.add_argument("--db", default=None, help="the learner's database (default: the configured one for --language)")
    ap.add_argument("--apply", action="store_true", help="write and synthesize (default: dry run)")
    args = ap.parse_args(argv)
    db_path = args.db or str(resolve_db_path(args.language, settings))
    return asyncio.run(_run(db_path, args.language, args.apply))


if __name__ == "__main__":
    sys.exit(main())
