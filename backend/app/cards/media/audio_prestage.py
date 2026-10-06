"""Render a card's word audio when it is about to be heard, not when it is minted.

A sync can mint hundreds of cards at once — a 462-word base list did — and at
about ten new cards a day most are not seen for weeks. Rendering TTS for each at
mint is harmless on an unmetered voice and a waste on a metered one, so (the
user's call, 2026-10-06, tunatale-r8hk):

- the mint keeps what is free: a Forvo recording, when there is one
  (:func:`mint_audio_mode`);
- this pass renders the rest, for the cards a learner has already met and the
  next :data:`AUDIO_LOOKAHEAD` in new-card order.

It also heals: a card whose only add-time fetch failed, or whose wrong audio a
repair dropped, is in the same state — a TunaTale card with no word audio — and
is served by the same query.

Two properties, both load-bearing, both shared with the image pre-stage
(``prestage.py``):

1. **It never opens the Anki collection.** It writes TT's media table and media
   dir and flags the row ``audio``; ``sync_push`` carries that to the note on the
   next sync. So it can run on a request path and for a learner who has no Anki.
2. **It only touches cards TunaTale originated.** An imported note owns its own
   audio field, and TT has no business filling one in — nor a guarantee the
   notetype has an ``Audio`` field to push to, without which the media refresh
   would drop the row and this pass would render it again on every run.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from app.audio.tts_router import provider_for
from app.languages import get_tts_voice

from .pipeline import AudioMode, fetch_card_media
from .prestage import MAX_FAILURE_REASONS, _failure_reason
from .vocab_media import safe_stem, store_tt_media

logger = logging.getLogger(__name__)

#: How many of the next new cards are close enough to be worth rendering. Three
#: days at the usual ten-a-day cap: enough that a learner who misses a day of
#: syncing still hears every new card, and few enough that a seeded list of
#: hundreds stays unrendered until it is actually reached.
AUDIO_LOOKAHEAD = 30

#: The ``source`` of a collocation imported from an Anki note. Those are the
#: cards this pass must not give audio to (property 2 above).
_IMPORTED_SOURCE = "anki"

#: DBs with a pass in flight, by ``id``. Two triggers — a sync finishing and a
#: deck being opened — can fire together, and the second would render the same
#: cards again: a second metered request for audio the first is about to store.
_RUNNING: set[int] = set()


class AudioPreStageReport(NamedTuple):
    """What one pass did. Counts, so the log line reads at a glance."""

    stored: int = 0
    forvo: int = 0
    tts: int = 0
    no_audio: int = 0
    failed: int = 0
    seen_queue: int = 0
    upcoming_queue: int = 0
    skipped_busy: bool = False
    failures: tuple[str, ...] = ()


def mint_audio_mode(language_code: str) -> AudioMode:
    """The audio a sync mint asks for in *language_code*: all of it, or only Forvo.

    ``"forvo"`` when the language's card voice belongs to a provider listed in
    ``settings.deferred_card_tts_providers`` — the metered ones. The provider is
    read off the voice id by the router's own rule, so this names no language.
    """
    from app.config import settings

    deferred = provider_for(get_tts_voice(language_code)) in settings.deferred_card_tts_providers
    return "forvo" if deferred else "full"


def _is_candidate(db: Any, coll_id: int, item: Any) -> bool:
    unit = item.syntactic_unit
    return unit.card_type == "vocab" and unit.source != _IMPORTED_SOURCE and db.get_audio_filename(coll_id) is None


def _log_summary(report: AudioPreStageReport) -> None:
    """Emit the pass summary to the logger AND the sync log, an empty pass too.

    Same reasoning as ``prestage._log_prestage_summary``: "ran and found nothing"
    and "never ran" must not leave identical evidence, and a WARNING that only
    reaches a scrolling terminal is not evidence at all.
    """
    line = (
        f"PRESTAGE_AUDIO stored={report.stored} forvo={report.forvo} tts={report.tts} "
        f"no_audio={report.no_audio} failed={report.failed} "
        f"seen_queue={report.seen_queue} upcoming_queue={report.upcoming_queue}"
    )
    if report.failures:
        line += " failures=" + " | ".join(report.failures)
    logger.warning(line)
    try:
        from app.config import settings

        path = Path(settings.sync_log)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} {line}\n")
    except Exception:  # noqa: BLE001 — observability must never break the pass
        logger.warning("PRESTAGE_AUDIO could not be persisted to the sync log", exc_info=True)


async def prestage_card_audio(
    db: Any,
    *,
    language_code: str,
    limit: int,
    fetch_fn: Any = None,
) -> AudioPreStageReport:
    """Give word audio to up to *limit* cards that are heard now or soon.

    Cards already met come first, then the next :data:`AUDIO_LOOKAHEAD` new
    cards in the order the queue will introduce them. *fetch_fn* is
    ``fetch_card_media`` unless a caller hands in its own reference to it.
    """
    if limit <= 0:
        return AudioPreStageReport()
    if id(db) in _RUNNING:
        return AudioPreStageReport(skipped_busy=True)
    _RUNNING.add(id(db))
    try:
        report = await _run_pass(db, language_code, limit, fetch_fn or fetch_card_media)
    finally:
        _RUNNING.discard(id(db))
    _log_summary(report)
    return report


async def _run_pass(db: Any, language_code: str, limit: int, fetch_fn: Any) -> AudioPreStageReport:
    seen = db.list_seen_vocab_missing_word_audio(exclude_source=_IMPORTED_SOURCE)
    upcoming = [
        (coll_id, item)
        for coll_id, item, _lang in db.get_new_items(limit=AUDIO_LOOKAHEAD)
        if _is_candidate(db, coll_id, item)
    ]
    stored = forvo = tts = no_audio = failed = 0
    failures: list[str] = []

    # Serial on purpose. The image pre-stage overlaps its fetches to outrun a
    # 20-image refill; this one is normally a handful of words, and a burst
    # against a per-minute TTS quota is exactly what its adapter paces against.
    for coll_id, item in (seen + upcoming)[:limit]:
        unit = item.syntactic_unit
        try:
            media = await fetch_fn(
                unit.text,
                unit.translation,
                pixabay_key="",
                language_code=language_code,
                image_query="",
                audio="full",
            )
        except Exception as exc:  # noqa: BLE001 — one bad fetch must not cost the others
            failed += 1
            reason = _failure_reason(exc)
            if reason not in failures:
                failures.append(reason)
            continue
        if media is None or media.audio_bytes is None:
            # Left as it is: still a candidate, so the next pass tries again.
            no_audio += 1
            continue
        source = media.audio_source or "tts"
        # Hash-suffixed, like every image name: a new render never overwrites a
        # file another card, another language's deck or another learner's DB
        # still points at, and the Anki media sync sees a plain addition.
        prefix = language_code if source == "forvo" else "tts"
        filename = f"{safe_stem(unit.text, prefix)}_{hashlib.sha256(media.audio_bytes).hexdigest()[:8]}.mp3"
        store_tt_media(db, coll_id, f"audio_{source}", filename, media.audio_bytes)
        db.add_dirty_field_by_id(coll_id, "audio")
        stored += 1
        forvo += source == "forvo"
        tts += source != "forvo"

    return AudioPreStageReport(
        stored=stored,
        forvo=forvo,
        tts=tts,
        no_audio=no_audio,
        failed=failed,
        seen_queue=len(seen),
        upcoming_queue=len(upcoming),
        failures=tuple(failures[:MAX_FAILURE_REASONS]),
    )
