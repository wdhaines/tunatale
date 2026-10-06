#!/usr/bin/env python3
"""Read-only report: price a render in Azure-billable characters BEFORE it runs.

AGENTS.md mandates pricing every render in characters before running it, because
there is no usable Azure-side character meter (``SynthesizedCharacters`` is not
queryable; the queryable metrics returned 0 for a day that demonstrably
synthesized hundreds of clips — see ``.claude/rules/paid-vendors.md``). The local
estimate IS the instrument, and this script is that instrument, tracked.

For every distinct synthesis key in each cost leg of the requested lessons, it
reports: distinct key count, TTS-cache hits, cache misses, and the summed
``len(AzureTTSService._billable_body(...))`` over the misses ONLY — a miss is a
request the render would actually send to Azure, and a hit bills nothing. It
then prints the monthly-allowance context (500,000 chars/month on the F0 tier).

A render can be spoken by either provider, and each key is routed to the one
that owns its voice id before anything is counted (see
``tts_router.provider_for``), so the two never share a leg and the figures above
are Azure's alone. Gemini keys are priced differently and deliberately
LOOSELY: this provider has no billable-body unit to sum, because it has no SSML
at all — the request is text plus an optional prompt, and Cloud TTS bills the
AUDIO that comes back, measured in audio tokens. So those legs are an ESTIMATE,
``_GEMINI_CHARS_PER_SECOND`` of synthesized characters per second of audio
(measured on Cebuano) times ``_GEMINI_AUDIO_TOKENS_PER_SECOND`` tokens per
second, divided by the speaking rate, priced at
``_GEMINI_USD_PER_MILLION_AUDIO_TOKENS`` — three named constants, because the
honest thing about this number is that it is derived, and a reader is entitled
to see the derivation and disagree with a term. Hit-vs-miss is NOT estimated:
it is the same ``.exists()`` against the same shared cache directory, for both
providers, so ``--cache-dir <empty dir>`` still gives the true cold price and the
differing numbers between the two providers are the providers', not this
script's.

The two legs mirror ``LessonRenderer._render_section`` exactly — this script
WIRES the renderer's existing functions, it does not re-derive their rules:

- **phrase leg**: one key per tuple ``(processed_text, voice_id, rate,
  sorted(phonemes), speak_locale, enunciation)`` — the renderer's ``synth_memo``
  key — where the text is ``preprocess(phrase.text, section.section_type)``,
  phonemes are planned only when BOTH ``source_word`` and ``syllable_span`` are
  present (``_phrase_phonemes``), and ``speak_locale`` is the target locale for
  phrases whose ``language_code`` matches and the phrase's OWN language's
  locale otherwise (an English line declares en-US). A target-language line in
  an Enunciated section is cut into words by ``app.audio.enunciation.plan_line``
  — the function the renderer calls, with the language's own long-word cut —
  and that cut is the last field: Azure bills the ``<break>`` it becomes (21
  characters a word gap, most of such a request), and Gemini's line is priced
  at about twice its natural length.
- **slicer leg**: one key per ``(source_word, voice_id)`` — the slicer's
  ``_words`` memo key — for every provenance phrase the renderer's
  ``_apply_slicing`` hands to ``ChunkSlicer._build_parent``, which synthesizes
  the WHOLE parent word at ``slicer.PARENT_RATE`` (``-40%``) with
  ``phonemes=None`` and the slicer's ``speak_locale``, and only fires when the
  alignment ``syllabify_fn(source_word)`` returns >= 2 syllables.

A phrase either receives phonemes OR is sliced, never both (``_apply_slicing``
skips every phrase in the section's ``ipa_indices``), so the two legs are
mutually exclusive — but the phrase leg still prices the chunk's OWN synthesis
even when sliced; the slicer leg is an ADDITIONAL parent-word request.

Read-only by construction: this script never calls ``synthesize``. It tests
cache existence with the adapter's OWN ``_cache_path`` against the real
``settings.tts_cache_dir`` (or ``--cache-dir``), exactly as ``.claude/rules/paid-vendors.md``
prescribes — the adapter's ``_cache_path`` is the only address space that
decides hit-vs-miss, and ``_billable_body`` is the only billable unit.

Usage::

    uv run python scripts/report_render_cost.py --language no --lesson inside-the-cabin-5533b2f1
    uv run python scripts/report_render_cost.py --language no --all
    uv run python scripts/report_render_cost.py --language no --all --cache-dir /tmp/empty-cache   # cold price

Exit 0 on a successful run — this is a report for a human, not a CI gate.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The pricing core lives in app/ so the in-app estimate shares it; re-exported
# here for the tests and reroll_tts_clip.py, which import it from this script.
from app.audio.render_cost import (  # noqa: E402, F401
    _MONTHLY_ALLOWANCE,
    GeminiLegStats,
    LegStats,
    RenderCost,
    RenderKeys,
    _memo_key,
    _phrase_phonemes,
    _SynthValue,
    collect_keys,
    gemini_cache_path,
    price_lessons,
)
from app.audio.slicer import PARENT_RATE, alignment_installed  # noqa: E402
from app.config import settings  # noqa: E402
from app.languages import (  # noqa: E402
    get_alignment,
    get_phoneme_planner,
    get_preprocessor,
    get_slow_word,
    get_tts_locale,
    resolve_db_path,
)
from app.models.lesson import Lesson  # noqa: E402
from app.storage.store import ContentStore  # noqa: E402


def _select_lessons(store: ContentStore, code: str, lesson_ids: list[str] | None) -> list[Lesson]:
    """Lessons named by ``--lesson``, or every stored lesson of *code*.

    ``--all`` (and the default, when no selector is given) prices the whole
    stored curriculum of the language — the number ``paid-vendors.md`` means by "what does
    this cost from empty?". A named lesson of another language is priced as
    asked but called out, so a mis-typed id cannot silently report another
    language's cost.
    """
    if lesson_ids:
        lessons: list[Lesson] = []
        for lesson_id in lesson_ids:
            lesson = store.get_lesson(lesson_id)
            if lesson is None:
                print(f"no such lesson: {lesson_id}", file=sys.stderr)
                continue
            if lesson.language_code != code:
                print(
                    f"note: {lesson_id} is {lesson.language_code!r}, not {code!r}; pricing it anyway",
                    file=sys.stderr,
                )
            lessons.append(lesson)
        return lessons
    return [lesson for _, _, _, lesson in store.list_lessons() if lesson.language_code == code]


def _print_report(lessons: list[Lesson], cache_dir: Path, cost: RenderCost) -> None:
    """One line per leg plus a TOTAL, then the monthly-allowance context.

    The sharing line is what lets a reader answer "what fraction of the F0
    month does this render burn?" without a calculator.
    """
    scope = (
        ", ".join("title: " + lesson.title for lesson in lessons) if len(lessons) <= 3 else f"{len(lessons)} lessons"
    )
    print(f"scope\t{scope}")
    print(f"cache_dir\t{cache_dir}")
    print(
        f"phrase_leg\tdistinct={cost.phrase.distinct}\thits={cost.phrase.hits}\tmisses={cost.phrase.misses}\tbillable={cost.phrase.billable_chars}"
    )
    print(
        f"slicer_leg\tdistinct={cost.slicer.distinct}\thits={cost.slicer.hits}\tmisses={cost.slicer.misses}\tbillable={cost.slicer.billable_chars}"
    )
    print(f"TOTAL\tdistinct={cost.distinct}\thits={cost.hits}\tmisses={cost.misses}\tbillable={cost.billable_chars}")
    share = cost.billable_chars / _MONTHLY_ALLOWANCE * 100.0
    print(f"monthly_allowance\t{_MONTHLY_ALLOWANCE}\tshare={share:.1f}%")
    # The Gemini block is CONDITIONAL, and that is the whole design: an
    # all-Azure report's output is byte-identical to what this script always
    # printed, so a reader (or a diff) sees no change at all until a Gemini voice
    # is in scope. A Gemini voice's request is in neither the Azure lines above
    # nor the F0 share, so a reader who saw only those would read a Cebuano
    # render as free.
    if cost.gemini_distinct:
        for label, leg in (("gemini_phrase_leg", cost.gemini_phrase), ("gemini_slicer_leg", cost.gemini_slicer)):
            print(
                f"{label}\tdistinct={leg.distinct}\thits={leg.hits}\tmisses={leg.misses}"
                f"\tchars={leg.chars}\taudio_tokens={round(leg.audio_tokens)}"
            )
        print(
            f"gemini_TOTAL\tdistinct={cost.gemini_distinct}\thits={cost.gemini_hits}"
            f"\tmisses={cost.gemini_misses}\tchars={cost.gemini_chars}"
            f"\taudio_tokens={round(cost.gemini_audio_tokens)}\tusd={cost.gemini_usd:.4f}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--language", default=None, help="default: settings.target_language")
    parser.add_argument(
        "--lesson",
        action="append",
        metavar="ID",
        help="a stored lesson id to price (repeatable)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="price every stored lesson of the language (the default when no --lesson is given)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="TTS cache dir to test hits against (default: settings.tts_cache_dir; "
        "point at an EMPTY dir for the cold price)",
    )
    args = parser.parse_args(argv)

    code = args.language or settings.target_language
    db_path = resolve_db_path(code, settings)
    store = ContentStore(db_path)
    lessons = _select_lessons(store, code, args.lesson)
    if not lessons:
        print(f"no lessons to price for language {code!r}", file=sys.stderr)
        return 1

    cache_dir = args.cache_dir or settings.tts_cache_dir
    no_alignment = get_alignment(code)
    cost = price_lessons(
        lessons,
        language_code=code,
        preprocessor=get_preprocessor(code),
        planner=get_phoneme_planner(code),
        target_locale=get_tts_locale(code),
        syllabify_fn=no_alignment.syllabify_fn if no_alignment is not None else None,
        slicer_enabled=alignment_installed() and no_alignment is not None,
        parent_rate=PARENT_RATE,
        slow_word_fn=get_slow_word(code),
        cache_dir=cache_dir,
    )
    _print_report(lessons, cache_dir, cost)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
