"""Price a render in Azure-billable characters before it runs.

The pricing core, shared by ``scripts/report_render_cost.py`` (the tracked
instrument AGENTS.md requires before any TTS render) and the in-app
"Re-render audio" estimate (tunatale-9paa). Moved here unchanged from the script
so the two can never disagree; the script's docstring explains every rule.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from app.audio import gemini_tts
from app.audio.azure_tts import AzureTTSService
from app.audio.gemini_tts import GeminiTTSService, resolve_ipa
from app.audio.preprocessing.base import TextPreprocessor
from app.audio.slicer import PARENT_RATE
from app.audio.tts_router import provider_for
from app.languages import PhonemePlanner, get_phoneme_planner, get_preprocessor, get_tts_locale
from app.models.lesson import Lesson, Phrase, SectionType

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


@dataclass(frozen=True)
class RenderEstimate:
    """What a re-render from the tools menu would cost, before the click (tunatale-9paa)."""

    billable_chars: int  # Azure characters, the F0 allowance's unit
    new_clips: int  # cache misses, both providers
    cached_clips: int  # cache hits, both providers: free
    gemini_usd: float
    monthly_allowance: int = _MONTHLY_ALLOWANCE


def estimate_render(
    lesson: Lesson, section_types: Collection[SectionType] | None, *, cache_dir: Path
) -> RenderEstimate:
    """Price re-rendering *section_types* of *lesson* (``None``: all of it).

    The app's own renderer configuration, not the scripts': the app renders
    WITHOUT the slicer (main.py builds its renderer with no slicers), so the
    estimate prices none — a slicer leg here would quote requests the app never
    sends. Everything else is ``price_lessons``, so the estimate and the tracked
    instrument cannot disagree. The lesson title's one clip is not priced; the
    renderer synthesizes it outside the sections.
    """
    scoped = (
        lesson
        if section_types is None
        else replace(lesson, sections=[s for s in lesson.sections if s.section_type in section_types])
    )
    code = lesson.language_code
    cost = price_lessons(
        [scoped],
        language_code=code,
        preprocessor=get_preprocessor(code),
        planner=get_phoneme_planner(code),
        target_locale=get_tts_locale(code),
        syllabify_fn=None,
        slicer_enabled=False,
        parent_rate=PARENT_RATE,
        cache_dir=cache_dir,
    )
    return RenderEstimate(
        billable_chars=cost.billable_chars,
        new_clips=cost.misses + cost.gemini_misses,
        cached_clips=cost.hits + cost.gemini_hits,
        gemini_usd=cost.gemini_usd,
    )
