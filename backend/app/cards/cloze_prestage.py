"""Write cloze sentences for words the deck's own examples cannot serve (tunatale-keb0).

``cards.cloze_source.choose_cloze_sentence`` declines when a note's examples
carry no blankable form of the word, and its docstring has always named what
should happen next — *"the word needs the LLM tier, which is not built"*. This is
that tier. Those words are closed-class, so there is no image path either: today
they are counted ``unservable`` by ``_fallback_to_cloze`` and get **no production
card at all** (27 of them on the live Norwegian deck).

Two constraints shape this module, and neither is negotiable:

1. **It never opens the Anki collection**, so it runs as a background task
   without a lock probe or a second sync sequence — the same property that makes
   ``prestage_production_images`` safe. The consequence is that it cannot read a
   note's example sentences (``get_cloze_material`` is the Anki reader) and so
   cannot judge them. It generates from the word and its gloss, which TT holds
   itself.
2. **The sync makes no network call.** An LLM round trip inside the mint is the
   regression ``prestage_production_images`` was built to undo — a measured
   10.0s median per word, 88-97%% of a 30-160s sync. The mint reads this cache;
   the round trip happens here.

⚠️ **This does not rewrite existing clozes.** Improving a cloze already in the
deck needs the note's own sentence to compare against, which point 1 forbids
seeing — and the user's decision (2026-09-06) is that a bad cloze is minted
anyway and fixed by hand from ``/review``. A pass that judged and rewrote stored
sentences would need the sync to cache each note's examples into TT first; that
is a separate piece of work, not a tweak to this one.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, NamedTuple

from app.cards.field_map import upos_for_disambig
from app.languages import get_language
from app.llm.cloze_quality import generate_cloze_sentence, judge_cloze, translate_cloze_sentence
from app.srs.function_words import is_function_word

logger = logging.getLogger(__name__)

#: How far down the awaiting-production queue to look for candidates. Mirrors
#: ``prestage.SCAN_LIMIT`` — the filters below are pure DB reads, so scanning
#: wide costs nothing and keeps the pass from wedging behind a head of words
#: that are already cached.
SCAN_LIMIT = 200

#: Concurrent LLM chains. Lower than the image pre-stage's 5 because each
#: candidate here is TWO calls (generate, then judge) against one rate-limited
#: provider, where an image is one Groq call plus one Pixabay call against two.
CONCURRENCY = 3


class ClozePreStageReport(NamedTuple):
    """What one pass did. Counts, so the log line reads at a glance."""

    written: int = 0
    already_cached: int = 0
    skipped_open_class: int = 0
    failed: int = 0


async def prestage_cloze_sentences(
    db: Any,
    llm: Any,
    *,
    language_code: str,
    limit: int,
) -> ClozePreStageReport:
    """Generate, judge and cache a cloze sentence for up to *limit* awaiting words."""
    language = get_language(language_code)

    # Pass 1 — pick candidates serially. Pure DB reads and a curated word list,
    # so there is nothing here worth overlapping.
    wanted: list[tuple[str, str]] = []
    already = skipped = 0
    for cand in db.list_words_awaiting_production(limit=SCAN_LIMIT):
        if len(wanted) >= limit:
            break
        unit = cand.item.syntactic_unit

        # Only closed-class words reach the cloze fork, and this test runs
        # without `upos` — that comes from the Anki reader this must not touch.
        # So it is strictly WEAKER than the mint's, exactly as the image
        # pre-stage's is: disagreeing costs a wasted generation, never a wrong
        # card, because the mint re-decides with the better test.
        # upos comes from TT's OWN disambig_key (the deck's "Word class",
        # copied onto the collocation), never from the Anki reader. Passing it
        # is not an optimisation: is_function_word WITHOUT upos returns False
        # for enn / mens / fordi / hvis, because False there means "no POS
        # supplied" rather than "open class". The unqualified call would
        # classify every word here as open and this pass would select nothing.
        upos = upos_for_disambig(unit.disambig_key)
        if not is_function_word(unit.text, language_code, upos=upos):
            skipped += 1
            continue

        if db.get_cached_cloze_sentence(unit.text, language_code) is not None:
            # Without this the same word costs two live LLM calls on every pass
            # forever — the failure `is_image_unavailable` prevents for pictures.
            already += 1
            continue

        wanted.append((unit.text, unit.translation))

    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def _write_one(word: str, gloss: str):
        async with semaphore:
            sentence = await generate_cloze_sentence(llm, word=word, gloss=gloss, pos="", language=language.name)
            if sentence is None:
                return word, None, None, ""
            verdict = await judge_cloze(llm, sentence=sentence, surface=word, language=language.name)
            # A translation OF THE SENTENCE, so the mint has one to put in the
            # cloze's sentence slot. Without it the only gloss in scope there is
            # the WORD's, and using that wrote the same text into both slots on
            # 18 live cards (tunatale-ml06). Failure yields "" and still caches.
            translation = await translate_cloze_sentence(llm, sentence=sentence, language=language.name)
            return word, sentence, verdict, translation

    # ⚠️ `return_exceptions=True`, and the reason is measured rather than
    # stylistic: the only caller schedules this via `background_tasks.add_task`,
    # where an escaped exception is SWALLOWED — it abandons the whole batch
    # mid-flight and leaves the DB exactly as it found it, indistinguishable
    # from never having run. One bad fetch discarding up to 19 good ones is
    # tunatale-ouk.10, and it was invisible for six syncs.
    results = await asyncio.gather(*(_write_one(w, g) for w, g in wanted), return_exceptions=True)

    written = failed = 0
    failures: list[str] = []
    for (word, _gloss), result in zip(wanted, results, strict=True):
        if isinstance(result, BaseException):
            failed += 1
            failures.append(f"{word}: {type(result).__name__}: {result}")
            continue
        _word, sentence, verdict, sentence_translation = result
        if sentence is None or verdict is None:
            failed += 1
            continue
        # An underdetermined sentence is still CACHED. These words have no image
        # path, so declining reproduces the `unservable` state this pass exists
        # to clear — a card with a loose blank beats no card at all, which is the
        # user's own call (2026-09-06). The verdict rides along so `/review` can
        # offer "try again" on exactly the ones that need it.
        db.set_cached_cloze_sentence(
            word,
            language_code,
            sentence=sentence,
            status=verdict.status,
            competitors=verdict.competitors,
            sentence_translation=sentence_translation,
        )
        written += 1

    # WARNING, not INFO: start-dev.sh runs uvicorn at --log-level warning and
    # this only ever runs inside that dev server, so an info line here is
    # written nowhere a human will read it — the defect that hid the
    # PRODUCTION_MINT backlog for a month.
    line = f"PRESTAGE_CLOZE written={written} already={already} open_class={skipped} failed={failed}"
    if failures:
        line += " failures=" + " | ".join(failures)
    logger.warning(line)
    return ClozePreStageReport(written=written, already_cached=already, skipped_open_class=skipped, failed=failed)
