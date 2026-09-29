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

- **phrase leg**: one key per 5-tuple ``(processed_text, voice_id, rate,
  sorted(phonemes), speak_locale)`` — the renderer's ``synth_memo`` key — where
  the text is ``preprocess(phrase.text, section.section_type)``, phonemes are
  planned only when BOTH ``source_word`` and ``syllable_span`` are present
  (``_phrase_phonemes``), and ``speak_locale`` is the target locale for
  phrases whose ``language_code`` matches and the phrase's OWN language's
  locale otherwise (an English line declares en-US).
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
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audio import gemini_tts  # noqa: E402
from app.audio.azure_tts import AzureTTSService  # noqa: E402
from app.audio.gemini_tts import GeminiTTSService, resolve_ipa  # noqa: E402
from app.audio.preprocessing.base import TextPreprocessor  # noqa: E402
from app.audio.slicer import PARENT_RATE, alignment_installed  # noqa: E402
from app.audio.tts_router import provider_for  # noqa: E402
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

# ---------------------------------------------------------------------------
# The Gemini estimate's three constants, and why this unit and not a billable
# body (see the module docstring). Each is a MEASURED or PUBLISHED number, not a
# fitted curve, and they are named so a reader can disagree with one of them
# without having to find it in an expression.
#
# _GEMINI_CHARS_PER_SECOND is speaking RATE — characters of Cebuano audio per
# second at speakingRate 1.0, measured on real renders rather than assumed from
# a language. It is the only measured number here and the only one worth
# re-measuring: it moves the whole estimate proportionally, and the pace it was
# measured at is Cebuano, so a different language's rate estimate is as wrong as
# this one is for a different voice.
# ---------------------------------------------------------------------------
_GEMINI_CHARS_PER_SECOND = 11.0
_GEMINI_AUDIO_TOKENS_PER_SECOND = 25.0
_GEMINI_USD_PER_MILLION_AUDIO_TOKENS = 10.0  # gemini-2.5-flash-tts on Cloud TTS

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
class GeminiLegStats:
    """One cost leg's Gemini keys, in the units that provider bills in.

    Azure reports the exact billable characters of each request. Gemini has no
    such unit to report, so this leg reports the two numbers the estimate is
    built from — the synthesized characters of the misses, and the audio tokens
    those characters predict — and the dollars follow from the second. ``chars``
    sums over MISSES only, and it is the count the token estimate is derived
    from, so it is reported rather than recomputed by a reader.
    """

    distinct: int
    hits: int
    misses: int
    chars: int
    audio_tokens: float

    @classmethod
    def empty(cls) -> GeminiLegStats:
        return cls(distinct=0, hits=0, misses=0, chars=0, audio_tokens=0.0)


@dataclass(frozen=True)
class RenderCost:
    """Both cost legs of a render, and their sum.

    The four Azure legs are the Azure-billable ones: keys are routed to a
    provider before they are counted, and a Gemini voice contributes to
    ``gemini_phrase``/``gemini_slicer`` and never to the ``*_chars`` above. The
    Gemini legs default to empty, so an all-Azure caller names only the two it
    has.
    """

    phrase: LegStats
    slicer: LegStats
    gemini_phrase: GeminiLegStats = field(default_factory=GeminiLegStats.empty)
    gemini_slicer: GeminiLegStats = field(default_factory=GeminiLegStats.empty)

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

    @property
    def gemini_distinct(self) -> int:
        return self.gemini_phrase.distinct + self.gemini_slicer.distinct

    @property
    def gemini_hits(self) -> int:
        return self.gemini_phrase.hits + self.gemini_slicer.hits

    @property
    def gemini_misses(self) -> int:
        return self.gemini_phrase.misses + self.gemini_slicer.misses

    @property
    def gemini_chars(self) -> int:
        return self.gemini_phrase.chars + self.gemini_slicer.chars

    @property
    def gemini_audio_tokens(self) -> float:
        """Both Gemini legs' estimated tokens, unrounded.

        The rounding happens where the number is PRINTED; anything that
        accumulates over legs must not round between them, or a many-leg render
        would drift by a token per leg.
        """
        return self.gemini_phrase.audio_tokens + self.gemini_slicer.audio_tokens

    @property
    def gemini_usd(self) -> float:
        """The same tokens priced, at the published per-million rate."""
        return self.gemini_audio_tokens / 1_000_000 * _GEMINI_USD_PER_MILLION_AUDIO_TOKENS


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


@dataclass(frozen=True)
class RenderKeys:
    """Every distinct synthesis key a scope would send, split by provider leg.

    The four dicts are the renderer keys, keyed by the renderer's own dedupe
    tuples (a 5-tuple for the phrase leg, ``(source_word, voice_id)`` for the
    slicer leg) and valued by the field tuple the adapters' ``_cache_path`` and
    ``_billable_body`` take. They are MUTABLE dicts on a frozen record: the
    record is a completed collection, not a value that should be edited in
    place, and freezing it keeps a caller from rebinding a leg.

    Separate from :class:`RenderCost` because it needs no cache directory — a
    caller that wants to address a key (the re-roll script) must not have to
    open a cache to do it, and a key collection that could not be built without
    one would put the address space in the wrong place.
    """

    phrase: dict[_MemoKey, _SynthValue] = field(default_factory=dict)
    slicer: dict[tuple[str, str], _SynthValue] = field(default_factory=dict)
    gemini_phrase: dict[_MemoKey, _SynthValue] = field(default_factory=dict)
    gemini_slicer: dict[tuple[str, str], _SynthValue] = field(default_factory=dict)

    @property
    def gemini_values(self) -> list[_SynthValue]:
        """Both Gemini legs' value tuples, in that order, possibly overlapping.

        A caller dedupes on the ADAPTER's key (two renderer keys can be one
        file — they differ only in what this provider ignores); listing both
        legs keeps that caller's job at the adapter, where the truth is, rather
        than hiding a dedupe behind this property.
        """
        return [*self.gemini_phrase.values(), *self.gemini_slicer.values()]

    @property
    def azure_values(self) -> list[_SynthValue]:
        """Both Azure legs' value tuples — the counterpart of ``gemini_values``."""
        return [*self.phrase.values(), *self.slicer.values()]


def collect_keys(
    lessons: Iterable[Lesson],
    *,
    language_code: str,
    preprocessor: TextPreprocessor,
    planner: PhonemePlanner | None,
    target_locale: str | None,
    syllabify_fn: Callable[[str], list[str] | None] | None,
    slicer_enabled: bool,
    parent_rate: str,
) -> RenderKeys:
    """The synthesis keys *lessons* would send, mirroring ``_render_section``.

    Every rule of the key derivation lives here, unchanged and in the renderer's
    order, and NO cache is touched: a key is a function of the lessons and the
    injected resolvers alone. :func:`price_lessons` is this plus the cache
    question, and a caller that only wants to ADDRESS a key (which file is it?)
    must be able to ask without opening a cache — the address space is the
    adapter's ``_cache_path``, and it is the adapter's alone.

    All resolvers are injected so a test can stub any one of them (see the
    STAGE 1 brief). ``slicer_enabled`` is the renderer's OWN gate —
    ``build_slicers`` returns a slicer only when ``alignment_installed()`` and
    the language has ``AlignmentConfig`` wiring — and ``syllabify_fn`` is the
    same function the slicer was constructed with (``AlignmentConfig.syllabify_fn``);
    neither is restated here.
    """
    keys = RenderKeys()

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
                # Rule 3: a phrase declares its own language's locale — the
                # target locale for the section's language, en-US for an English
                # line (renderer.py::_phrase_locale). For an en-US voice that
                # changes neither the SSML nor the cache key.
                speak_locale = (
                    target_locale if phrase.language_code == language_code else get_tts_locale(phrase.language_code)
                )
                # Rule 4: dedupe by the 5-tuple, render-scoped (here: whole scope).
                key = _memo_key(text, phrase.voice_id, phrase.rate, ph_map, speak_locale)
                # The 5-tuple is the same dedupe key for BOTH providers — it is
                # the renderer's, and the renderer is what chooses the adapter.
                # Only the PRICING splits, by the voice id's own suffix: a Gemini
                # key billed as Azure characters would be wrong by two
                # multipliers at once, and would also move the Azure allowance
                # line for a request Azure never receives.
                leg = keys.gemini_phrase if provider_for(phrase.voice_id) == "gemini" else keys.phrase
                leg.setdefault(key, (text, phrase.voice_id, phrase.rate, ph_map, speak_locale))

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
                    # Dedupe by (source_word, voice_id), the slicer's _words memo
                    # — the renderer's key, and split by provider for the same
                    # reason the phrase leg is: the parent word is rendered by
                    # whichever adapter owns its voice, so its rate and its
                    # currency are that provider's.
                    skey = (phrase.source_word, phrase.voice_id)
                    leg = keys.gemini_slicer if provider_for(phrase.voice_id) == "gemini" else keys.slicer
                    leg.setdefault(
                        skey,
                        (phrase.source_word, phrase.voice_id, parent_rate, None, target_locale),
                    )

    return keys


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

    Keys are deduped ACROSS all lessons in scope: identical 5-tuples share one
    TTS-cache key, and a cache populated mid-curriculum serves every later
    lesson, so this is the number of requests a cold render of the whole scope
    would actually send.
    """
    tts = AzureTTSService(cache_dir=cache_dir)
    # One adapter for the whole scope, for the same reason as the Azure one: the
    # cache directory is the whole address space, and this one is only ever
    # asked where a file IS, never to synthesize.
    gemini = GeminiTTSService(cache_dir=cache_dir)
    keys = collect_keys(
        lessons,
        language_code=language_code,
        preprocessor=preprocessor,
        planner=planner,
        target_locale=target_locale,
        syllabify_fn=syllabify_fn,
        slicer_enabled=slicer_enabled,
        parent_rate=parent_rate,
    )

    return RenderCost(
        phrase=_leg_stats(keys.phrase, tts),
        slicer=_leg_stats(keys.slicer, tts),
        gemini_phrase=_gemini_leg_stats(keys.gemini_phrase, gemini),
        gemini_slicer=_gemini_leg_stats(keys.gemini_slicer, gemini),
    )


def gemini_cache_path(gemini: GeminiTTSService, value: _SynthValue) -> Path:
    """The file one Gemini key IS, in the adapter's own address space.

    The ``resolve_ipa`` + ``_cache_path`` pair, and nothing else: the IPA is part
    of the file's name, so a key cannot be addressed without it, and a plain
    file cannot answer which prompt produced it. It is public because two
    callers must agree on this path to the byte — the cost report, which
    decides hit-vs-miss without writing, and the re-roll script, which must
    never recompute a digest by hand (a hand-rolled digest is a silent miss,
    and a silent miss here is a clip that is evicted from nowhere).
    """
    text, voice_id, rate, phonemes, _speak_locale = value
    ipa, _phrase = resolve_ipa(text, phonemes)
    return gemini._cache_path(text, voice_id, rate, ipa)


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


def _gemini_leg_stats(keys: Mapping[object, _SynthValue], gemini: GeminiTTSService) -> GeminiLegStats:
    """Hit/miss, miss characters and estimated audio tokens for one Gemini leg.

    A hit is ``GeminiTTSService._cache_path(...).exists()`` — the adapter's OWN
    key, which is the only address space that decides hit-vs-miss — and only
    misses cost anything. The IPA is the adapter's too (``resolve_ipa``), because
    it is part of the file's name and a plain file cannot answer a prompt.

    ``speak_locale`` is NOT in this key and that is the adapter's own shape, not
    an omission: this provider accepts the argument and ignores it, so a locale
    cannot change the file. Passing it would invent a request the render never
    makes and split one file into two.
    """
    hits = misses = chars = 0
    audio_tokens = 0.0
    # Two renderer keys that differ only in what this provider ignores (the
    # locale, or a mapping that resolves to the same IPA) are ONE file and one
    # request, so the leg dedupes again on the adapter's own key.
    files: set[Path] = set()
    for text, voice_id, rate, phonemes, _speak_locale in keys.values():
        path = gemini_cache_path(gemini, (text, voice_id, rate, phonemes, _speak_locale))
        if path in files:
            continue
        files.add(path)
        if path.exists():
            hits += 1
            continue
        misses += 1
        chars += len(text)
        # The SENTENCE is what the provider bills, never the IPA that rides a
        # prompt alongside it. Text length becomes AUDIO SECONDS through the
        # measured speaking rate, and seconds become tokens through the token
        # rate; a rate above 1.0 buys fewer seconds for the same characters, so
        # the speaking rate divides.
        audio_tokens += (
            len(text) / _GEMINI_CHARS_PER_SECOND / gemini_tts._speaking_rate(rate) * _GEMINI_AUDIO_TOKENS_PER_SECOND
        )
    return GeminiLegStats(distinct=len(files), hits=hits, misses=misses, chars=chars, audio_tokens=audio_tokens)


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
        cache_dir=cache_dir,
    )
    _print_report(lessons, cache_dir, cost)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
