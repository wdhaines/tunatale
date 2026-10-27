#!/usr/bin/env python
"""Re-picture every card that shares a photograph with a more common word (tunatale-jlwy).

    uv run python scripts/repicture_shared_photos.py --language ceb            # dry run: the plan
    uv run python scripts/repicture_shared_photos.py --language ceb --apply    # fetch and write

Then sync the language: the ordinary push writes each new picture into its Anki
note. In each group the most common word (the language's frequency source) keeps
its photo; a tie keeps the oldest card's. Every other card is re-fetched through
the sync's own media generator with the shared picture barred, and keeps its old
photo if no new one comes back. Logic and tests live in
``app.cards.shared_photo_repair``.

Cost: one Pixabay search per card, two when the first hit is the shared photo,
plus one LLM image query for each card whose query is not cached (the dry run
counts them). Run it on the LIVE side (``./switch.sh status``) under that side's
env, once per learner DB (``--db`` for a learner other than the owner), and back
the DB up first.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from app.cards.shared_photo_repair import apply_repictures, plan_shared_photos
from app.config import settings
from app.languages import resolve_language_context
from app.srs.database import SRSDatabase


def _sync_media_fn(db: SRSDatabase, language_code: str) -> Callable[..., Awaitable[Any]]:
    """The media generator peer sync uses, with a plain Groq client."""
    from app.api.anki import _build_media_fn
    from app.llm.client import LLMClient

    return _build_media_fn(
        LLMClient(groq_api_key=settings.groq_api_key, groq_model=settings.llm_model), db, language_code
    )


def main(argv: list[str] | None = None, *, _media_fn: Callable[..., Awaitable[Any]] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--language", required=True)
    parser.add_argument("--db", default=None, help="database URL (default: the language's configured DB)")
    parser.add_argument("--apply", action="store_true", help="fetch and write (default: dry run)")
    args = parser.parse_args(argv)

    db = SRSDatabase(args.db or resolve_language_context(args.language, settings).db_url)
    plan = plan_shared_photos(db, args.language)
    others = [card for group in plan for card in group.others]
    for group in plan:
        k = group.keeper
        tie = "  (tie: oldest keeps)" if group.tied else ""
        print(f"{group.sha256[:8]}  keeps: {k.collocation_id} {k.text} '{k.gloss}' zipf {k.zipf:.2f}{tie}")
        for c in group.others:
            push = "push" if c.linked else "no Anki note"
            print(f"          re-picture: {c.collocation_id} {c.text} '{c.gloss}' zipf {c.zipf:.2f} ({push})")
    uncached = sum(1 for c in others if not c.query_cached)
    print(
        f"{len(plan)} shared photo(s), {len(others)} card(s) to re-picture, "
        f"{sum(g.tied for g in plan)} tie(s), {uncached} LLM image quer(ies) not cached."
    )
    if args.apply and plan:
        results = asyncio.run(apply_repictures(db, plan, _media_fn or _sync_media_fn(db, args.language)))
        for r in results:
            if r.new_filename is None:
                print(f"  kept   {r.collocation_id} {r.text}: {r.reason}")
            else:
                print(f"  new    {r.collocation_id} {r.text}: {r.old_filename} -> {r.new_filename}")
        done = sum(1 for r in results if r.new_filename is not None)
        print(f"Re-pictured {done} of {len(results)}. Sync the language to carry them to Anki.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
