"""Pre-stage production-card images OUTSIDE the sync (tunatale-6xa).

``promote_production_cards`` mints ``PRODUCTIONS_PER_SYNC`` cards per sync and,
for each candidate with no TT image, fetches one **inline** — an LLM call for the
image query plus a Pixabay search and download, serially, while the user waits.
Measured 2026-08-17 on the real decks: 1487 Norwegian words await production and
**every one of them lacks an image**, so ~149 consecutive syncs each pay 10 live
network chains. That is the "sync takes way too long" report.

This module removes the fetch from the critical path rather than making it
faster. When it has run, ``get_image_filename`` answers for the next candidates,
promotion takes its ``else`` branch, and the mint is a local file copy.

**The 10-per-sync pacing is deliberately untouched.** It was settled with the
user on 2026-08-15 as a pedagogical decision — at a 3 new-cards/day introduction
cap there is nothing to gain from minting ahead of what the learner can meet.
This is a latency fix, not a throughput one; if a change here starts minting more
cards per sync, it has misunderstood the problem.

Two properties make this safe to run in the background, and both are load-bearing:

1. **It never opens the Anki collection.** Writes go to TT's media table and
   media dir through ``store_tt_media``. So it stays outside the Anki safety
   envelope — no ``safe_open``, no lock probe, no second sync sequence, nothing
   that can collide with Anki desktop being open. Anything added here that
   reaches for the collection breaks that property and belongs in the sync
   instead.
2. **Disagreeing with promotion is harmless.** The closed-class test below runs
   without ``upos``, because that comes from the Anki reader this must not touch,
   so it is strictly weaker than promotion's. Promotion tests closed-class
   *before* it looks for an image, so a pre-staged image for a function word is
   wasted work — never a wrong card.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, NamedTuple

from app.srs.function_words import is_function_word

from .vocab_media import safe_stem, store_tt_media

logger = logging.getLogger(__name__)

#: Rows to read before giving up on finding *limit* fetchable candidates. The
#: budget is on live fetches; scanning past skippable rows is cheap and must not
#: wedge the drain behind a run of function words.
SCAN_LIMIT = 200


class PreStageReport(NamedTuple):
    """What one pre-stage pass did. Counts, so a log line can be read at a glance."""

    fetched: int = 0
    already_had_image: int = 0
    skipped_function_word: int = 0
    no_image: int = 0


async def prestage_production_images(
    db: Any,
    media_fn: Any,
    *,
    language_code: str,
    limit: int,
) -> PreStageReport:
    """Fetch and store TT images for up to *limit* words awaiting production.

    *media_fn* is the same generator the sync uses, injected rather than imported
    so this module has no opinion about LLM or Pixabay wiring.
    """
    fetched = already = skipped = missing = 0
    # One shared set across the run, exactly as promote_production_cards does —
    # without it two words fetched in the same pass can be handed the same
    # picture, which is the duplicate-image bug that set exists to prevent.
    used_image_urls: set[str] = set()

    for cand in db.list_words_awaiting_production(limit=SCAN_LIMIT):
        if fetched >= limit:
            break

        unit = cand.item.syntactic_unit

        if db.get_image_filename(cand.collocation_id) is not None:
            already += 1
            continue

        if is_function_word(unit.text, language_code):
            skipped += 1
            continue

        media = await media_fn(
            unit.text,
            unit.translation,
            source_sentence=unit.source_sentence or "",
            grammar=unit.grammar or "",
            used_image_urls=used_image_urls,
        )
        if media is None or media.image_bytes is None:
            # Not an error: promotion will route this word to a cloze. Counted so
            # a run that finds nothing picturable is distinguishable from a run
            # that found nothing to do.
            missing += 1
            continue

        # Hash-suffixed, matching promote_production_cards and replace_item_image
        # and NOT the bare `img_<gloss>.<ext>` used at add time. A shared English
        # gloss is common ("beslutning" and "avgjørelse" are both "decision"), and
        # the bare form has the second write overwrite the first word's picture in
        # place — silently changing a card the learner already knows.
        digest = hashlib.sha256(media.image_bytes).hexdigest()[:8]
        filename = f"{safe_stem(unit.translation, 'img')}_{digest}.{media.image_ext or 'jpg'}"
        store_tt_media(db, cand.collocation_id, "image", filename, media.image_bytes)
        fetched += 1

    report = PreStageReport(fetched=fetched, already_had_image=already, skipped_function_word=skipped, no_image=missing)
    if fetched or missing:
        logger.info(
            "PRESTAGE_IMAGES fetched=%d already=%d function_word=%d no_image=%d",
            report.fetched,
            report.already_had_image,
            report.skipped_function_word,
            report.no_image,
        )
    return report
