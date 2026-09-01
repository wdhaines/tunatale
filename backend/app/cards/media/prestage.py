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

import asyncio
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from app.srs.function_words import is_function_word

from .vocab_media import safe_stem, store_tt_media

logger = logging.getLogger(__name__)

#: Rows to read before giving up on finding *limit* fetchable candidates. The
#: budget is on live fetches; scanning past skippable rows is cheap and must not
#: wedge the drain behind a run of function words.
SCAN_LIMIT = 200

#: How many image fetches may be in flight at once. A fetch is an LLM call plus a
#: Pixabay search and download, measured at a 10.0s median on the real deck
#: (2026-08-23), so refilling a 20-image buffer serially takes ~200s — and the
#: sync that emptied it typically came back in ~2 minutes. The refill has to
#: outrun the user, so the fetches overlap.
#:
#: Capped rather than unbounded because both ends of the chain are rate-limited
#: (Groq free tier, Pixabay); fanning out 20 at once would trade a latency
#: problem for a 429 problem. Five is a deliberate compromise, not a measurement:
#: ~40s for a full refill, comfortably inside the gap between two syncs.
PRESTAGE_CONCURRENCY = 5


class PreStageReport(NamedTuple):
    """What one pre-stage pass did. Counts, so a log line can be read at a glance."""

    fetched: int = 0
    already_had_image: int = 0
    skipped_function_word: int = 0
    no_image: int = 0
    #: Fetches that RAISED. Counted apart from ``no_image`` on purpose: that one
    #: means "searched, nothing picturable" and drives ``mark_image_unavailable``,
    #: which routes the word to a permanent cloze. A 429 or a timeout is not
    #: evidence about the word, so it must never reach that marker.
    failed: int = 0


def _log_prestage_summary(report: PreStageReport) -> None:
    """Emit the pass summary to BOTH the logger and ~/.tunatale/logs/sync.log.

    ⚠️ ALWAYS emitted, including the all-zero pass. The emit used to be guarded
    on ``if fetched or missing``, which made "ran and found nothing to do" and
    "never ran at all" produce identical evidence — and those are exactly the two
    hypotheses a reader is trying to separate when the mint reports
    awaiting_image high and minted 0 (tunatale-ouk.10).

    ⚠️ WARNING, not INFO, and persisted rather than logged. start-dev.sh runs
    uvicorn at ``--log-level warning`` and redirects it nowhere, so an INFO line
    was filtered out and, even at warning, would only reach a terminal that
    scrolls. Measured before this change: ``grep -c PRESTAGE
    ~/.tunatale/logs/sync.log`` -> 0, for the entire life of the counter.

    This is the same defect fixed for PRODUCTION_MINT in b7211aa, one component
    over, and the same remedy: persist beside SYNC_SOAK and grep the file.
    The pre-stage owns its own append rather than reusing
    ``anki_sync._write_sync_soak_log`` because it runs AFTER the sync as a
    background task, and because this module must not import the sync plugin —
    the layering that keeps it outside the Anki safety envelope.

    Best-effort by construction: a logging failure must never take down a
    pre-stage pass, so every error here is swallowed deliberately.
    """
    line = (
        f"PRESTAGE_IMAGES fetched={report.fetched} already={report.already_had_image} "
        f"function_word={report.skipped_function_word} no_image={report.no_image} "
        f"failed={report.failed}"
    )
    logger.warning(line)
    try:
        from app.config import settings

        path = Path(settings.sync_log)
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{ts} {line}\n")
    except Exception:  # noqa: BLE001 — observability must never break the pass
        logger.warning("PRESTAGE_IMAGES could not be persisted to the sync log", exc_info=True)


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
    # picture. Under concurrency it is necessary but no longer SUFFICIENT: every
    # in-flight call sees the set as it was when it started, so two words with a
    # shared English gloss can still come back with identical bytes. The digest
    # guard below is what actually closes that hole.
    used_image_urls: set[str] = set()

    # Pass 1 — pick the candidates, serially. These filters are pure DB reads and
    # a curated word list, so there is nothing here worth overlapping; doing it up
    # front means the fetch batch is exactly the words that need a live call.
    failed = 0
    wanted = []
    for cand in db.list_words_awaiting_production(limit=SCAN_LIMIT):
        if len(wanted) >= limit:
            break

        unit = cand.item.syntactic_unit

        if db.get_image_filename(cand.collocation_id) is not None:
            already += 1
            continue

        if db.is_image_unavailable(cand.collocation_id):
            # Already searched, already came back empty. Without this the same
            # unpicturable word costs a live fetch on every pass forever; the mint
            # reads the same marker and clozes the word instead of minting it.
            already += 1
            continue

        if is_function_word(unit.text, language_code):
            skipped += 1
            continue

        wanted.append(cand)

    semaphore = asyncio.Semaphore(PRESTAGE_CONCURRENCY)

    async def _fetch(cand):
        unit = cand.item.syntactic_unit
        async with semaphore:
            return cand, await media_fn(
                unit.text,
                unit.translation,
                source_sentence=unit.source_sentence or "",
                grammar=unit.grammar or "",
                used_image_urls=used_image_urls,
            )

    # Pass 2 — fetch concurrently. Safe here for the reason in the module
    # docstring: this never opens the Anki collection, so there is no lock and no
    # ordering to preserve.
    #
    # ⚠️ This was `return_exceptions=False`, whose stated reason — a failure must
    # not be "silently counted as no image and turned into a cloze by the mint" —
    # is RIGHT and is preserved below by counting failures apart from `no_image`.
    # What it got wrong is where the exception goes. The only caller schedules
    # this via `background_tasks.add_task` (app/api/anki.py), where an escaped
    # exception is swallowed: it does not surface, it abandons the whole batch
    # mid-flight, pass 3 never runs, and the pass leaves the DB and the log
    # exactly as it found them — indistinguishable from never having run.
    #
    # Measured consequence on the real deck (tunatale-ouk.10): awaiting_image=173
    # with minted=0 across six syncs, ~4 images stored in a day against a limit
    # of 20 PER SYNC, and image_unavailable_at set on 0 of 3100 collocations.
    # One bad fetch was discarding up to 19 good ones, invisibly.
    results = await asyncio.gather(*(_fetch(c) for c in wanted), return_exceptions=True)

    # Pass 3 — store serially, in deck order, so the writes are deterministic and
    # the digest guard sees a stable sequence.
    seen_digests: set[str] = set()
    for cand, result in zip(wanted, results, strict=True):
        unit = cand.item.syntactic_unit
        if isinstance(result, BaseException):
            # Counted, logged with its traceback, and pointedly NOT passed to
            # mark_image_unavailable — see PreStageReport.failed. The word stays
            # at the head and is retried next pass.
            failed += 1
            logger.warning(
                "PRESTAGE_IMAGES fetch failed for %r: %s: %s",
                unit.text,
                type(result).__name__,
                result,
                exc_info=result,
            )
            continue
        media = result[1]
        if media is None or media.image_bytes is None:
            # Not an error, and this is the ONLY place the fact is discoverable:
            # the mint no longer fetches, so without this marker it could not tell
            # "cannot be pictured" (cloze it) from "not staged yet" (wait). Record
            # it, and the next mint routes the word to a cloze.
            db.mark_image_unavailable(cand.collocation_id)
            missing += 1
            continue

        # Hash-suffixed, matching promote_production_cards and replace_item_image
        # and NOT the bare `img_<gloss>.<ext>` used at add time. A shared English
        # gloss is common ("beslutning" and "avgjørelse" are both "decision"), and
        # the bare form has the second write overwrite the first word's picture in
        # place — silently changing a card the learner already knows.
        digest = hashlib.sha256(media.image_bytes).hexdigest()[:8]
        if digest in seen_digests:
            # Two words came back with the SAME picture. Identical bytes hash to
            # one filename, so storing both would point two cards at one image —
            # the duplicate-image bug `used_image_urls` exists to prevent, which
            # concurrency can slip past. Leave this word imageless; the next pass
            # retries it with the URL set already populated.
            missing += 1
            continue
        seen_digests.add(digest)

        filename = f"{safe_stem(unit.translation, 'img')}_{digest}.{media.image_ext or 'jpg'}"
        store_tt_media(db, cand.collocation_id, "image", filename, media.image_bytes)
        fetched += 1

    report = PreStageReport(
        fetched=fetched,
        already_had_image=already,
        skipped_function_word=skipped,
        no_image=missing,
        failed=failed,
    )
    _log_prestage_summary(report)
    return report
