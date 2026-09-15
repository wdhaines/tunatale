#!/usr/bin/env python3
"""Read-only report: price a render in Azure-billable characters BEFORE it runs.

CLAUDE.md mandates pricing every render in characters before running it, because
there is no usable Azure-side character meter (``SynthesizedCharacters`` is not
queryable; the queryable metrics returned 0 for a day that demonstrably
synthesized hundreds of clips — see AGENTS.md § "Paid vendor usage"). The local
estimate IS the instrument, and this script is that instrument, tracked.

For every distinct synthesis key in each cost leg of the requested lessons, it
reports: distinct key count, TTS-cache hits, cache misses, and the summed
``len(AzureTTSService._billable_body(...))`` over the misses ONLY — a miss is a
request the render would actually send to Azure, and a hit bills nothing. It
then prints the monthly-allowance context (500,000 chars/month on the F0 tier).

The two legs mirror ``LessonRenderer._render_section`` exactly — this script
WIRES the renderer's existing functions, it does not re-derive their rules:

- **phrase leg**: one key per 5-tuple ``(processed_text, voice_id, rate,
  sorted(phonemes), speak_locale)`` — the renderer's ``synth_memo`` key — where
  the text is ``preprocess(phrase.text, section.section_type)``, phonemes are
  planned only when BOTH ``source_word`` and ``syllable_span`` are present
  (``_phrase_phonemes``), and ``speak_locale`` is the target locale only for
  phrases whose ``language_code`` matches.
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
``settings.tts_cache_dir`` (or ``--cache-dir``), exactly as CLAUDE.md
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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audio.azure_tts import AzureTTSService  # noqa: E402
from app.audio.preprocessing.base import TextPreprocessor  # noqa: E402
from app.audio.slicer import PARENT_RATE, alignment_installed  # noqa: E402
from app.config import settings  # noqa: E402
from app.languages import (  # noqa: E402
    PhonemePlanner,
    get_alignment,
    get_phoneme_planner,
    get_preprocessor,
    get_tts_locale,
    resolve_db_path,
)
from app.models.lesson import Lesson, Phrase  # noqa: E402
from app.storage.store import ContentStore  # noqa: E402

# The Azure F0 tier's monthly allowance. Both the report's share line and the
# reader's "is this affordable" judgement hang off this one literal.
_MONTHLY_ALLOWANCE = 500_000

# The renderer's dedupe key: (processed text, voice, rate, sorted phoneme
# mapping, speak locale) — renderer.py::_synth. Neither the mapping nor the
# locale is an attribute of the text; both change the audio AND the cache key.
_MemoKey = tuple[str, str, str, tuple[tuple[str, str], ...] | None, str | None]
# The value carried for a key: same fields, phonemes in the dict form
# ``_cache_path`` / ``_billable_body`` expect.
_SynthValue = tuple[str, str, str, Mapping[str, str] | None, str | None]


@dataclass(frozen=True)
class LegStats:
    """One cost leg's distinct keys and their cache outcome."""

    distinct: int
    hits: int
    misses: int
    billable_chars: int

    @classmethod
    def empty(cls) -> LegStats:
        return cls(distinct=0, hits=0, misses=0, billable_chars=0)


@dataclass(frozen=True)
class RenderCost:
    """Both cost legs of a render, and their sum."""

    phrase: LegStats
    slicer: LegStats

    @property
    def distinct(self) -> int:
        return self.phrase.distinct + self.slicer.distinct

    @property
    def hits(self) -> int:
        return self.phrase.hits + self.slicer.hits

    @property
    def misses(self) -> int:
        return self.phrase.misses + self.slicer.misses

    @property
    def billable_chars(self) -> int:
        return self.phrase.billable_chars + self.slicer.billable_chars


def _phrase_phonemes(
    planner: PhonemePlanner | None,
    language_code: str,
    phrase: Phrase,
) -> Mapping[str, str] | None:
    """Mirror of renderer.py::_phrase_phonemes: when would this phrase carry IPA?

    ``None`` for plain synthesis — and the ONLY two conditions that matter for
    cost are: no planner, or a phrase without BOTH ``source_word`` and
    ``syllable_span`` (or in another language). This is the condition the dead
    probe had inverted; the inversion is exactly the branch where
    ``source_word is None``, which crashes loudly in
    ``norwegian_breakdown.flat_syllables`` (verified 2026-09-15).
    """
    if planner is None:
        return None
    if phrase.language_code != language_code:
        return None
    if phrase.source_word is None or phrase.syllable_span is None:
        return None
    result = planner.plan_chunk(
        phrase.source_word,
        phrase.syllable_span,
        upos=phrase.upos or None,
        chunk_text=phrase.text,
    )
    if result is None:
        return None
    return {phrase.text.lower(): result}


def _memo_key(
    text: str,
    voice_id: str,
    rate: str,
    phonemes: Mapping[str, str] | None,
    speak_locale: str | None,
) -> _MemoKey:
    """The renderer's 5-tuple, exactly as renderer.py::_synth builds it."""
    return (
        text,
        voice_id,
        rate,
        tuple(sorted(phonemes.items())) if phonemes else None,
        speak_locale,
    )


def price_lessons(
    lessons: Iterable[Lesson],
    *,
    language_code: str,
    preprocessor: TextPreprocessor,
    planner: PhonemePlanner | None,
    target_locale: str | None,
    syllabify_fn: Callable[[str], list[str] | None] | None,
    slicer_enabled: bool,
    parent_rate: str,
    cache_dir: Path,
) -> RenderCost:
    """Price *lessons* against *cache_dir*, mirroring ``_render_section``.

    All resolvers are injected so a test can stub any one of them (see the
    STAGE 1 brief). ``slicer_enabled`` is the renderer's OWN gate —
    ``build_slicers`` returns a slicer only when ``alignment_installed()`` and
    the language has ``AlignmentConfig`` wiring — and ``syllabify_fn`` is the
    same function the slicer was constructed with (``AlignmentConfig.syllabify_fn``);
    neither is restated here.

    Keys are deduped ACROSS all lessons in scope: identical 5-tuples share one
    TTS-cache key, and a cache populated mid-curriculum serves every later
    lesson, so this is the number of requests a cold render of the whole scope
    would actually send.
    """
    tts = AzureTTSService(cache_dir=cache_dir)
    phrase_keys: dict[_MemoKey, _SynthValue] = {}
    slicer_keys: dict[tuple[str, str], _SynthValue] = {}

    for lesson in lessons:
        for section in lesson.sections:
            # ipa_indices and phoneme_maps are per-section, exactly as
            # renderer.py::_render_section computes them before _apply_slicing.
            ipa_indices: set[int] = set()
            phoneme_maps: list[Mapping[str, str] | None] = []
            for i, phrase in enumerate(section.phrases):
                ph_map = _phrase_phonemes(planner, language_code, phrase)
                phoneme_maps.append(ph_map)
                if ph_map is not None:
                    ipa_indices.add(i)

            for i, (phrase, ph_map) in enumerate(zip(section.phrases, phoneme_maps, strict=True)):
                # Rule 1: the synthesized text is PREPROCESSED text,
                # never phrase.text itself.
                text = preprocessor.preprocess(phrase.text, section.section_type)
                # Rule 3: the target locale declares itself only for phrases in
                # the section's own language; a narrator line is English.
                speak_locale = target_locale if phrase.language_code == language_code else None
                # Rule 4: dedupe by the 5-tuple, render-scoped (here: whole scope).
                key = _memo_key(text, phrase.voice_id, phrase.rate, ph_map, speak_locale)
                phrase_keys.setdefault(key, (text, phrase.voice_id, phrase.rate, ph_map, speak_locale))

                # Rule 5: the slicer is a second, MUTUALLY EXCLUSIVE cost source.
                # _apply_slicing skips every phrase with planned phonemes.
                if (
                    slicer_enabled
                    and i not in ipa_indices
                    and phrase.source_word is not None
                    and phrase.syllable_span is not None
                ):
                    # _build_parent returns None WITHOUT synthesizing when the
                    # word cannot be split into >=2 syllables — the only case a
                    # provenance phrase costs nothing extra.
                    if syllabify_fn is not None:
                        syllables = syllabify_fn(phrase.source_word)
                        if syllables is None or len(syllables) < 2:
                            continue
                    # Dedupe by (source_word, voice_id), the slicer's _words memo.
                    skey = (phrase.source_word, phrase.voice_id)
                    slicer_keys.setdefault(
                        skey,
                        (phrase.source_word, phrase.voice_id, parent_rate, None, target_locale),
                    )

    return RenderCost(
        phrase=_leg_stats(phrase_keys, tts),
        slicer=_leg_stats(slicer_keys, tts),
    )


def _leg_stats(keys: Mapping[object, _SynthValue], tts: AzureTTSService) -> LegStats:
    """Hit/miss and billable characters for one leg's distinct keys.

    A hit is ``AzureTTSService._cache_path(...).exists()`` — the adapter's OWN
    key, which is the only address space that decides hit-vs-miss — and only
    misses sum ``len(_billable_body(...))``: a hit makes no Azure request.
    """
    hits = misses = 0
    billable = 0
    for text, voice_id, rate, phonemes, speak_locale in keys.values():
        if tts._cache_path(text, voice_id, rate, phonemes, speak_locale).exists():
            hits += 1
        else:
            misses += 1
            billable += len(AzureTTSService._billable_body(text, voice_id, rate, phonemes, speak_locale))
    return LegStats(distinct=len(keys), hits=hits, misses=misses, billable_chars=billable)


def _select_lessons(store: ContentStore, code: str, lesson_ids: list[str] | None) -> list[Lesson]:
    """Lessons named by ``--lesson``, or every stored lesson of *code*.

    ``--all`` (and the default, when no selector is given) prices the whole
    stored curriculum of the language — the number CLAUDE.md means by "what does
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
        cache_dir=cache_dir,
    )
    _print_report(lessons, cache_dir, cost)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
