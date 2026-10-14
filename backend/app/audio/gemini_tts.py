"""Google Cloud Text-to-Speech adapter — the Gemini family of voices.

The sibling adapter serves ``<locale>-<Name>Neural`` ids; this one serves
``<locale>-<Name>Gemini``, which the other cannot render at all. They are
interchangeable behind ``TTSService`` and are told apart by voice id, never by
fallback — see ``app/audio/tts_router.py`` for why the second half matters.

Three properties of THIS provider shape everything below.

1. **Auth is a service account, not a key.** These voices accept only an OAuth
   access token minted from a service-account JSON; an API key is rejected
   outright. So the adapter resolves credentials through google-auth rather
   than reading a header out of settings the way its sibling does.
2. **Output is nondeterministic per call.** Two renders of one utterance are
   not byte-identical, so the file cache is load-bearing rather than an
   optimisation: a hit must make ZERO requests, and the model is part of the
   cache key so a model change cannot serve yesterday's audio.
3. **The quota is small and per-minute.** 429s arrive after roughly thirty
   fast requests, which is why pacing is a full 2 seconds between request
   starts and why a 429 waits a flat 20 seconds instead of climbing a ladder.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import shutil
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

import google.auth
import google.auth.transport.requests
import google.oauth2.service_account
import httpx

from app.audio.ports import Enunciation, TTSExhausted
from app.audio.speech_length import speech_seconds as measure_speech_seconds
from app.audio.take_check import TAKE_CHECK_REROLLS, TakeChecker
from app.audio.word_gaps import rewrite_word_gaps
from app.generation.section_builder import _SENTENCE_PUNCTUATION
from app.languages import language_name_for_tts_locale

logger = logging.getLogger(__name__)

# Six rungs, the same sizing the sibling uses: a throttling episode is
# exogenous and passes, so patience is worth more than a fast failure. The
# ladder's total wait is bounded by the 429 rung anyway (see RATE_LIMIT_DELAY).
MAX_RETRIES = 6

# A 429 gets a flat wait, not a doubling one. The quota is per-MINUTE, so a
# ladder would climb past the window it is waiting for: 1s, 2s, 4s would all
# land inside the same minute that just refused us. A fifth consecutive 429
# (tunatale-u8nz measured arrival after ~30 fast requests) is not a burst to
# ride out, and a render that needs more patience than this should be paced,
# not retried harder.
#
# It is a COOLDOWN rather than this caller's backoff: it is set on a 429 and
# read by every request that follows, inside the request lock. See
# _do_synthesize.
RATE_LIMIT_DELAY = 20.0

_SYNTHESIS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
_AUDIO_ENCODING = "MP3"

# Which wording of the IPA instruction is in the cache key (see ``_cache_path``).
# The model's output is nondeterministic, so a reworded instruction cannot be
# told apart from yesterday's audio by anything else: bump this on ANY change to
# the sentence, or the cache keeps serving what the old words produced.
#
# The PHRASE instruction (``_phrase_ipa_prompt``, added 2026-09-25) is the one
# addition that does NOT bump it, and deliberately: a phrase is only prompted by
# a map with two or more entries, and such a map never reached this key before —
# there was no instruction to key. Every key already in the cache is a
# single-fragment one, and bumping would throw all of it away for nothing.
PROMPT_VERSION = 1

# The same rule for the ENUNCIATION sentence (``_ENUNCIATION_INSTRUCTION``), on
# its own counter: bump this when that sentence is reworded. It is separate so
# that rewording one instruction never throws away the clips the other made —
# an enunciated key carries both, a drill key only the one above.
ENUNCIATION_VERSION = 3

# ── What is done to an Enunciated take before it is kept (bd tunatale-u8nz.32) ─
#
# What an Enunciated line is FOR: parting the words, not slowing them. The page
# has a space between two words where connected speech has none, so a learner
# reading along cannot find the boundaries; this section puts them back. The
# two things that matter are an audible gap at every word boundary and every
# word fully pronounced. How fast each word is said is free, and can stay close
# to natural (the owner, 2026-10-13).
#
# The sentence asks for a stop after every word, and the voice gives one: of 92
# lines under it, none ran two words together. But the stops are long and
# uneven, and three lines in four also break a word. So the take's pauses are
# rewritten (``word_gaps``): a line's longest pauses are its word gaps and are
# set to one length, every other pause is shut, and the speech is sped with its
# pitch kept. The three numbers are the owner's ear on 25 lines (2026-10-13):
# together they make an Enunciated line about 1.4 times its natural-speed twin,
# where the takes as spoken ran about 2.1 times.
#
# They are IN the cached audio and NOT in the cache key. Change one and bump
# ENUNCIATION_VERSION with it, or clips already cached keep the old gaps (a
# test holds the four together).
WORD_GAP_SECONDS = 0.45
SHUT_PAUSE_SECONDS = 0.03
ENUNCIATED_SPEECH_SPEED = 1.5

# How fast this provider's voices speak: characters of text per second of audio
# at speakingRate 1.0, measured on real Cebuano renders. Two readers: the cost
# estimate (``render_cost``, which bills by audio length) and the repeat guard
# below. A different language's pace would move both.
CHARS_PER_SECOND = 11.0

# ── The repeat guard on a prompted line (bd tunatale-u8nz.28, u8nz.31) ───────
#
# The provider sometimes says a line two or more times over. It is random from
# take to take (about 1.5-2% of cached clips), no request field prevents it, and
# the clip is cached, so every later render plays it. It has been heard on both
# kinds of line that are sent with an instruction: an Enunciated line, and a
# natural-speed line told its reading. A fresh take of either is therefore
# measured before it is kept: how long it SPEAKS for
# (``speech_length.speech_seconds`` — the pauses are asked for and are not
# evidence), against the length its text should take at CHARS_PER_SECOND.
#
# An Enunciated line and a natural-speed one have their own factors, because the
# Enunciated wording slows the words as well as parting them. Speech-only
# seconds over (characters / CHARS_PER_SECOND), on the take AS SPOKEN (the
# rewrite above comes after, and speeds the speech):
#
#     the wording in force, 51 fresh takes (2026-10-13): median 1.51, the
#       longest single saying 2.18 ("Moadto ta sa baybayon?"), the next 2.00.
#     the same wording, 138 stored clips: median 1.51; the two repeats the user
#       heard on 2026-10-09 at 3.34 and 2.84.
#
# 2.5 sits between the longest single saying (2.18) and the shorter repeat
# (2.84). The room on either side is thin; a line that fails all its takes with
# lengths alike is the sign the factor is wrong for it. The table belongs to the
# wording, which is what REPEAT_FACTOR_MEASURED_ON_VERSION is for (a test holds
# it to ENUNCIATION_VERSION). The wording before this one ran a median 1.08 and
# had 1.8 as its limit.
#
# The same table for a natural-speed line told its reading, measured the same
# day on the one lesson that has such clips:
#
#     51 clips: median 1.03; 50 at or under 1.36; and one at 2.38 — "Oo, Nong.
#       Nalipay kaayo mi.", which the user heard said twice.
#
# 1.8 is above the longest single saying (1.36) and below a median line said
# twice (2.06). That table belongs to the reading instruction
# (``_phrase_ipa_prompt``), hence its own constant, held to PROMPT_VERSION.
#
# NOT covered: a key phrase's pieces, which are told their reading too. Said
# once, 52 of them ran a median 1.31 and up to 1.85 ("ko sa haya"), so this
# factor would throw good takes away. Pieces need a table of their own first.
REPEAT_SPEECH_FACTOR = 1.8
ENUNCIATED_REPEAT_SPEECH_FACTOR = 2.5
REPEAT_FACTOR_MEASURED_ON_VERSION = 3
REPEAT_FACTOR_MEASURED_ON_PROMPT_VERSION = 1
# One take plus two more. A repeat is rare and independent per take, so three in
# a row is about four in a million: a line that fails all three is far likelier
# a line the limit is wrong for than one that repeated three times, which is why
# the error carries every measurement. Each extra take is billed like the first
# (about a tenth of a cent for a line); at the measured rate that is two or
# three re-rolls in a 140-line lesson.
MAX_LINE_TAKES = 3

# cloud-platform, not the narrower speech scope: the token is minted for the
# service account's own role, and a wrong scope is a 403 at synthesis time.
_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)

# The voice id, and only the voice id, says which provider serves an utterance.
# The locale and the name are both sliced out of it rather than looked up per
# language, which keeps every language literal out of this module — see
# scripts/check_language_literals.py.
_VOICE_RE = re.compile(r"^(?P<locale>[a-z]{2,3}-[A-Z]{2})-(?P<name>[A-Za-z]+)Gemini$")

# The renderer speaks in percentage strings, the provider wants a multiplier.
_RATE_RE = re.compile(r"^(?P<percent>[+-]\d+)%$")
# The multiplier range the provider accepts. Outside it a render fails as an
# HTTP 400 mid-lesson, so it is refused locally instead, naming the rate.
_MIN_SPEAKING_RATE = 0.25
_MAX_SPEAKING_RATE = 4.0

# Every 4xx but 429 is a request this provider will never accept: a wrong
# voice, a wrong locale, missing IAM. Retrying one only spends the quota it is
# already short of. 5xx and transport errors are the other kind of transient.
_FATAL_STATUS_MIN = 400
_FATAL_STATUS_MAX = 500
_THROTTLED = 429

# google-auth's credential object, cached process-wide. The cache holds the
# OBJECT rather than the token: building one is the expensive part, and a
# cached object still knows when its token has expired.
_CREDENTIALS: Any = None


async def default_token_provider() -> str:
    """Return an OAuth access token for the synthesis endpoint.

    Reads the service-account JSON at ``settings.google_application_credentials``
    when one is configured, and otherwise hands the question to google-auth's
    own default discovery (workload identity, metadata server, gcloud). The
    path is a setting rather than an env var google-auth reads for itself
    because Pydantic loads ``.env`` into settings and NOT into
    ``os.environ`` — its own lookup would never see the value.

    The refresh is blocking I/O, so it runs in a worker thread; on the event
    loop it would stall every other render task for the length of an HTTP round
    trip. No key file is read until this is called, and a cache hit never
    calls it at all.
    """
    global _CREDENTIALS
    if _CREDENTIALS is None:
        from app.config import settings

        path = settings.google_application_credentials
        if path:
            _CREDENTIALS = google.oauth2.service_account.Credentials.from_service_account_file(
                path, scopes=list(_SCOPES)
            )
        else:
            credentials, _project = google.auth.default(scopes=list(_SCOPES))
            _CREDENTIALS = credentials
    if not _CREDENTIALS.valid:
        await asyncio.to_thread(_CREDENTIALS.refresh, google.auth.transport.requests.Request())
    return _CREDENTIALS.token


def _parse_voice_id(voice_id: str) -> tuple[str, str]:
    """Split a voice id into ``(languageCode, name)``, or refuse it.

    Refusing is the point. The sibling adapter never validates its ids, so a
    mistyped one there is a 4xx from the provider; here an id that does not
    name a Gemini voice is a wrong-provider bug, and it should be a loud local
    error carrying the offending id rather than a paid request.
    """
    # fullmatch, not match: ``$`` also matches before a final newline.
    match = _VOICE_RE.fullmatch(voice_id)
    if match is None:
        raise ValueError(
            f"{voice_id!r} is not a Gemini voice id "
            "(expected <locale>-<Name>Gemini, e.g. a two-or-three letter "
            "language code, a two letter region, then the name)"
        )
    return match.group("locale"), match.group("name")


def _speaking_rate(rate: str) -> float:
    """Map a ``[+-]N%`` rate string to the provider's multiplier, ``1 + N/100``.

    Emitted even when it is 1.0, so the provider never has to infer the
    default. A rate the renderer never produces is refused rather than guessed
    at: a wrong multiplier is a clip at the wrong speed, cached forever.
    """
    match = _RATE_RE.fullmatch(rate)
    if match is None:
        raise ValueError(f"{rate!r} is not a rate adjustment (expected a form like '-20%')")
    # Rounded so the body carries 0.3, not 0.30000000000000004.
    speaking_rate = round(1 + int(match.group("percent")) / 100, 4)
    if not _MIN_SPEAKING_RATE <= speaking_rate <= _MAX_SPEAKING_RATE:
        raise ValueError(
            f"{rate!r} is a speaking rate of {speaking_rate}, outside the "
            f"{_MIN_SPEAKING_RATE}-{_MAX_SPEAKING_RATE} the provider accepts"
        )
    return speaking_rate


def _is_fatal(status: int) -> bool:
    """True for a 4xx this provider will never accept — everything but 429."""
    return _FATAL_STATUS_MIN <= status < _FATAL_STATUS_MAX and status != _THROTTLED


# The instruction that carries a fragment's IPA, in the only channel available:
# Cloud TTS rejects SSML ``<phoneme>`` for Gemini voices, so ``input.prompt`` is
# all there is. The language is named in words because an adapter never sees the
# name otherwise — it has the locale, sliced out of the voice id, and the
# registry is what turns that back into a language.
#
# 2.5 (gemini-2.5-flash-tts) is the model this works on, chosen by the user's
# listening test: it follows the IPA, where the 3.x family treats the text as a
# verbatim transcript and says nothing else. A wrong-IPA control proved it.
def _ipa_prompt(name: str, ipa: str) -> str:
    """The instruction for one spoken fragment. *name* carries its own trailing space."""
    return (
        f"Say only this one {name}syllable or word, exactly once, with nothing "
        f"before or after it. Pronounce it exactly as the IPA /{ipa}/."
    )


def _single_token_ipa(text: str, phonemes: Mapping[str, str]) -> str | None:
    """The one IPA an instruction can carry, or ``None`` when the shape does not fit.

    The instruction says "say only this one", so it fits ONE fragment: a phrase
    is a refusal, not a worse version of a yes. Punctuation is measured off
    first, because a fragment is stored with the sentence's commas and question
    marks on it ("inyong,"), and those are not what makes it several tokens.

    The entry is taken by POSITION, never by looking its key up in *text*: the
    renderer's key is the bare word, and a chunk whose caption carries a hyphen
    is keyed without one.
    """
    if len(phonemes) != 1 or any(c.isspace() for c in text.strip(_SENTENCE_PUNCTUATION)):
        return None
    return next(iter(phonemes.values()))


def _phrase_ipa_prompt(name: str, ipa: str) -> str:
    """The instruction for a whole PHRASE. *name* carries its own trailing space.

    A second sentence rather than a parameter of :func:`_ipa_prompt`, because
    "say only this one syllable or word" is a false statement about two words,
    and this is a request to be exact. Measured by the user's ear on 2026-09-25:
    blind A/B of "ilubong ugma" was wrong 2 of 3 plain and 2 of 2 right with the
    phrase reading, and not stiff.
    """
    return (
        f"Say exactly this {name}phrase, once, with nothing before or after it. "
        f"Pronounce it exactly as the IPA /{ipa}/."
    )


# How an Enunciated line asks for its pauses. This provider takes no markup for
# it (``input.ssml`` with ``<break>`` is refused, HTTP 400) and no punctuation in
# the text is reliable: measured 2026-10-06 over 45 word gaps per device, " ... "
# paused at 32, "[short pause]" tags, commas and periods at 37-39, and asking in
# the prompt at all 45, in 9 of 9 takes. "The way a language teacher dictates"
# went syllable by syllable and is ruled out.
#
# The wording sets the length, and small changes swing it a long way. The first
# one shipped ("at a normal speaking speed, then stop briefly before the next
# word") ran about 2.45x the natural-speed take, which the user found far too
# slow in a lesson. Measured 2026-10-09 over 3 lines x 5 fresh takes: this one
# 1.72x with a pause in every take, and "with a short pause between words" back
# at 2.40x, so nothing was found between the two. The user heard this one as
# the best of the set, with the pauses possibly on the short side.
_ENUNCIATION_INSTRUCTION = "Say each word whole and at a normal speaking speed, then stop briefly before the next word."


def _enunciated_prompt(name: str, ipa: str | None) -> str:
    """The instruction for an Enunciated LINE. *name* carries its own trailing space.

    The phrase sentence, then the pause, then the reading — the order that was
    heard. With no reading the last sentence is left off rather than left
    empty: the pause does not depend on it.
    """
    said = f"Say exactly this {name}phrase, once, with nothing before or after it. {_ENUNCIATION_INSTRUCTION}"
    return said if ipa is None else f"{said} Pronounce it exactly as the IPA /{ipa}/."


class UnusableLineError(RuntimeError):
    """No take of a line could be kept: each was said twice, or ran words together.

    A ``RuntimeError`` and deliberately NOT a ``TTSExhausted``: the render-level
    retry loop re-runs that one because throttling passes, and here the takes
    have already been spent. Nothing is cached and no clip is written, so the
    render fails where a bad line would otherwise have been kept for good.
    """


class RepeatedLineError(UnusableLineError):
    """Every take of a line spoke for longer than its text allows."""


def repeat_limit_seconds(text: str, speaking_rate: float, *, enunciated: bool = False) -> float:
    """The most a take of the line *text* may SPEAK for before it is a repeat.

    The line's factor (an Enunciated line has its own: its wording slows the
    words) times the text's own length at this provider's pace, stretched by
    the speaking rate the request asked for. Characters as sent, punctuation
    and spaces included: that is the count the factors were measured against.
    """
    factor = ENUNCIATED_REPEAT_SPEECH_FACTOR if enunciated else REPEAT_SPEECH_FACTOR
    return factor * len(text) / CHARS_PER_SECOND / speaking_rate


def rewrite_enunciated_take(audio: bytes, words: int) -> bytes | None:
    """An Enunciated take with its pauses rewritten, or ``None`` if it ran words together."""
    return rewrite_word_gaps(
        audio, words, gap_seconds=WORD_GAP_SECONDS, shut_seconds=SHUT_PAUSE_SECONDS, speed=ENUNCIATED_SPEECH_SPEED
    )


def enunciates(enunciation: Enunciation | None) -> bool:
    """Is there a gap in *enunciation* for this provider to pause in?

    Two words or more. A lone word has none, and the cut INSIDE a split word is
    the markup provider's device: the instruction here is about whole words. A
    ``False`` is a plain render in every respect, cache key included, so a
    one-word Enunciated line is the natural-speed clip and costs nothing.
    """
    return bool(enunciation) and len(enunciation) >= 2


def _line_ipa(text: str, phonemes: Mapping[str, str]) -> str | None:
    """The reading of a whole LINE, or ``None`` when a word of it has none.

    Each word is found in *phonemes* by its bare lowercase form — LOOKUP, where
    :func:`_phrase_ipa` goes by position — so a line that says a word twice has
    a reading, and dialogue does that constantly. The line's own punctuation
    stays where it was, around the reading of the word it was on.
    """
    read: list[str] = []
    for token in text.split():
        bare = token.strip(_SENTENCE_PUNCTUATION)
        if not any(c.isalnum() for c in bare):
            read.append(token)
            continue
        ipa = phonemes.get(bare.lower())
        if ipa is None:
            return None
        read.append(token.replace(bare, ipa, 1))
    return " ".join(read)


def _phrase_ipa(text: str, phonemes: Mapping[str, str]) -> str | None:
    """The one IPA a PHRASE instruction can carry, or ``None`` when the shape does not fit.

    The mirror of :func:`_single_token_ipa`, and its other half: a map holding
    one entry per TOKEN is read as the whole phrase's reading, in word order.

    The count is the discriminator, which refuses a phrase REPEATING a word for
    free — the map is keyed by word, so "ko ang ko" is three tokens over two
    entries, and the step stays plain rather than being rendered from a reading
    with a word missing from it. The same refusal covers a half-filled map for
    the same reason. Values are taken BY POSITION, as in the single-token path,
    never by looking their key up in *text*.
    """
    tokens = text.strip(_SENTENCE_PUNCTUATION).split()
    if len(tokens) < 2 or len(phonemes) != len(tokens):
        return None
    return " ".join(phonemes.values())


def resolve_ipa(
    text: str, phonemes: Mapping[str, str] | None, enunciation: Enunciation | None = None, line: bool = False
) -> tuple[str | None, bool]:
    """The ``(ipa, phrase)`` a synthesis of *text* would carry, without rendering.

    Public because a caller that only needs the cache key — the render-cost
    report, which must decide hit-vs-miss for a file it will never write — has
    to derive the same IPA this adapter would, and asking the request path for
    it would mean sending it. *phrase* is the flag that picks the instruction,
    and it is reported rather than kept: the two IPA functions already return
    ``None`` for a shape that does not fit, and a caller holding the IPA cannot
    tell which of the two produced it.

    ``(None, False)`` is the "plain render" answer and covers every shape that
    fits neither instruction, an absent or empty mapping included. Deciding
    whether that is worth a warning stays with :meth:`GeminiTTSService.synthesize`:
    only a caller that was actually handed a mapping knows the caller expected one.

    An *enunciation* this provider acts on (:func:`enunciates`) makes *text* a
    LINE, read by lookup (:func:`_line_ipa`) and never by the two shapes below.
    Whether the line is enunciated is the caller's to ask :func:`enunciates`;
    this still answers only what reading it carries.

    *line* says the same of a line said at natural speed, which has no
    enunciation to say it. It has to be said: a line whose words are all
    different fits the phrase shape too, and read by position it would lose
    its punctuation, which is not the reading that was heard.
    """
    if not phonemes:
        return None, False
    if line or enunciates(enunciation):
        ipa = _line_ipa(text, phonemes)
        return ipa, ipa is not None
    ipa = _single_token_ipa(text, phonemes)
    if ipa is not None:
        return ipa, False
    ipa = _phrase_ipa(text, phonemes)
    return ipa, ipa is not None


class GeminiTTSService:
    """Google Cloud TTS adapter for ``<locale>-<Name>Gemini`` voices.

    Implements the TTSService Protocol with:
    - Request pacing (``gemini_tts_min_delay`` seconds between request starts)
    - Optional file-based caching (keyed on model + voice + rate + text)
    - Retry on 429/5xx/transport, fail-fast on every other 4xx
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        model: str | None = None,
        token_provider: Callable[[], Awaitable[str]] | None = None,
        min_delay: float | None = None,
        timeout: float = 60.0,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        now: Callable[[], float] | None = None,
        speech_seconds: Callable[[bytes], float] | None = None,
        rewrite_gaps: Callable[[bytes, int], bytes | None] | None = None,
        take_checker: Callable[[str], TakeChecker | None] | None = None,
    ) -> None:
        if model is None or min_delay is None:
            from app.config import settings

            model = settings.gemini_tts_model if model is None else model
            min_delay = settings.gemini_tts_min_delay if min_delay is None else min_delay
        self._model = model
        self._min_delay = min_delay
        self._cache_dir = cache_dir
        self._timeout = timeout
        # Same injection strategy as the sibling's: the pacing and the retry
        # ladder are real code paths that a test should pin by feeding them a
        # different clock, not by patching asyncio.sleep (which would be a
        # mock_allowlist.txt entry for what is really a tuning knob).
        self._sleep = sleep if sleep is not None else asyncio.sleep
        # Injected for the same reason `sleep` is: the cooldown is a real code
        # path a test should pin by feeding it a different clock, not by
        # patching time.monotonic() process-wide, which reaches every other
        # clock in the process for the length of the test. Monotonic, not
        # time.time: a wall-clock step backwards must not shorten a wait the
        # provider has already told us to sit out.
        self._now = now if now is not None else time.monotonic
        # Injected like the two clocks above, and for the same reason: the
        # repeat guard is a real code path, and a test pins it by handing it a
        # measurement rather than by patching ffmpeg out of the process.
        self._speech_seconds = speech_seconds if speech_seconds is not None else measure_speech_seconds
        self._rewrite_gaps = rewrite_gaps if rewrite_gaps is not None else rewrite_enunciated_take
        # The per-minute quota is exhausted for a WINDOW, not for a request, so
        # the refusal is remembered here rather than in the caller that hit it.
        # 0.0 means "no cooldown outstanding" and reads as such against any
        # monotonic clock.
        self._cooldown_until: float = 0.0
        # One request in flight at a time, held across the request AND its
        # pacing sleep, as the sibling does with its semaphore. The renderer
        # gathers sections concurrently; without this, pacing held only within
        # one caller and a render opened as a burst against a per-minute quota.
        self._request_lock = asyncio.Lock()
        self._token_provider = token_provider if token_provider is not None else default_token_provider
        # The one degradation this adapter has to announce. Once per instance:
        # the renderer would otherwise emit a line per render, and one line is
        # the entire diagnostic value here.
        self._warned_phonemes = False
        # Locale -> the checker for the language served there, or None. A take
        # that fails it is drawn again (``_checked``); None checks nothing.
        self._take_checker = take_checker

    # ------------------------------------------------------------------
    # TTSService Protocol implementation
    # ------------------------------------------------------------------

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        output_path: Path,
        rate: str = "+0%",
        phonemes: Mapping[str, str] | None = None,
        speak_locale: str | None = None,
        enunciation: Enunciation | None = None,
        line: bool = False,
    ) -> None:
        """Synthesize *text* to *output_path* using Google Cloud TTS.

        Args:
            text: Text to synthesize.
            voice_id: A ``<locale>-<Name>Gemini`` voice id. Anything else
                raises :class:`ValueError` naming the id.
            output_path: Destination file path for the synthesized audio.
            rate: Speech rate adjustment as a percentage string (e.g.
                ``"-20%"``), mapped to the provider's multiplier.
            phonemes: Per-token IPA. A mapping of one entry per TOKEN is spoken
                from it, via an ``input.prompt`` instruction (there is no
                ``<phoneme>`` markup for Gemini voices): a lone token gets the
                single-fragment sentence, two or more get the phrase one. Any
                other non-empty mapping logs one warning and changes neither the
                request nor the cache key.
            speak_locale: Accepted and IGNORED, silently. The voice's own
                ``languageCode`` is explicit, so there is nothing to override.
            enunciation: The line cut into words. Two or more make *text* an
                Enunciated line: it is still sent as written, and the pause
                after each word is asked for in ``input.prompt``, with the
                line's reading when *phonemes* covers every word of it. Fewer
                change nothing, including the cache key.
            line: *text* is a whole line said at natural speed, and *phonemes*
                reads its words. It is sent with the phrase instruction and the
                line's reading, punctuation kept, and no pause is asked for.
                A reading with a word missing is not sent: one warning, and a
                plain render under the plain cache key.
        """
        language_code, name = _parse_voice_id(voice_id)
        speaking_rate = _speaking_rate(rate)
        enunciated = enunciates(enunciation)
        ipa, phrase = resolve_ipa(text, phonemes, enunciation, line)
        # A mapping we could not use is announced here and not inside
        # resolve_ipa, which is also called by callers that never render.
        if phonemes and ipa is None:
            self._warn_phonemes_unsupported()

        if self._cache_dir is not None:
            cached = self._cache_path(text, voice_id, rate, ipa, enunciated)
            if cached.exists():
                output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cached, output_path)
                logger.debug("Gemini TTS cache hit for %r", text[:40])
                return

        # A line sent with an instruction: Enunciated, or told its reading. A
        # line whose reading has a gap is sent plain, and is not measured.
        if enunciated or (line and ipa is not None):
            words = len(enunciation) if enunciated and enunciation else 0

            async def draw() -> bytes:
                return await self._line_take(text, language_code, name, speaking_rate, ipa, phrase, enunciated, words)
        else:

            async def draw() -> bytes:
                return await self._synthesize_with_retry(
                    text, language_code, name, speaking_rate, ipa, phrase, enunciated
                )

        audio = await self._checked(await draw(), text, language_code, draw)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio)

        if self._cache_dir is not None:
            cached = self._cache_path(text, voice_id, rate, ipa, enunciated)
            cached.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(output_path, cached)

    async def _checked(self, audio: bytes, text: str, locale: str, draw: Callable[[], Awaitable[bytes]]) -> bytes:
        """*audio*, or a fresh take of it when the language's take check fails it (tunatale-ve4p.29).

        Gemini is nondeterministic, so a take that says a ``t`` as ``p`` is
        fixed by drawing again, up to ``TAKE_CHECK_REROLLS`` times. Every
        re-draw is logged, and so is giving up: the best-scoring take is kept
        and cached, and ``scripts/reroll_tts_clip.py`` is the way to evict it.
        The model runs off the event loop; it is CPU work.
        """
        checker = self._take_checker(locale) if self._take_checker is not None else None
        if checker is None:
            return audio
        margin = await asyncio.to_thread(checker.margin, audio, text)
        best, best_margin = audio, margin
        for attempt in range(1, TAKE_CHECK_REROLLS + 1):
            if checker.passes(margin):
                return audio
            logger.warning("TAKE_CHECK_REROLL %r scored %.1f; drawing take %d", text, margin, attempt + 1)
            audio = await draw()
            margin = await asyncio.to_thread(checker.margin, audio, text)
            if margin is None or margin > best_margin:
                best, best_margin = audio, margin
        if checker.passes(margin):
            return audio
        logger.warning(
            "TAKE_CHECK_GAVE_UP %r: %d takes, best scored %.1f — cached as it is; "
            "evict it with scripts/reroll_tts_clip.py",
            text,
            TAKE_CHECK_REROLLS + 1,
            best_margin,
        )
        return best

    async def list_voices(self, language_code: str | None = None) -> list[dict]:
        """Return no voices.

        This provider's catalogue is not per-language and no caller asks for
        it; returning a filtered slice of a global list would imply a promise
        the registry does not use.
        """
        return []

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _warn_phonemes_unsupported(self) -> None:
        """Announce the one capability this adapter cannot honour, once."""
        if self._warned_phonemes:
            return
        self._warned_phonemes = True
        logger.warning(
            "Gemini TTS ignores the phoneme mapping: this provider takes no "
            "per-token IPA, so the rendered audio is unadapted. Clear the "
            "lexicon phonemes for a voice served by this adapter."
        )

    def _cache_path(
        self, text: str, voice_id: str, rate: str, ipa: str | None = None, enunciated: bool = False
    ) -> Path:
        """The cache file for one (model, voice, rate, text, ipa, enunciated) tuple.

        The ``gemini|`` prefix keeps this adapter's entries disjoint from the
        sibling's in the one shared cache directory: both name files by a
        16-hex digest, so without it a digest collision would serve one
        provider's audio for the other's voice. The model is in the key
        because the output is nondeterministic per call — a model swap has to
        be able to tell that yesterday's file was not produced by today's
        model.

        A spoken fragment's key carries its IPA AND the prompt's version. Both
        halves are load-bearing and neither may be dropped: without the IPA a
        prompted render would be served for the bare text (and vice versa), and
        without the version a reworded instruction would keep serving the audio
        the old words produced. With no IPA the key is EXACTLY what it was
        before prompts existed, so every clip already cached still hits.

        An Enunciated line adds its own part, after the IPA's and under its own
        version. The natural-speed section says this same text in this same
        voice, so without it the two would be one file — and with no IPA to
        tell them apart, the natural clip would play as the Enunciated one.
        """
        ipa_part = f"|ipa:{ipa}|p{PROMPT_VERSION}" if ipa is not None else ""
        say_part = f"|say:words|e{ENUNCIATION_VERSION}" if enunciated else ""
        key = f"gemini|{self._model}|{voice_id}|{rate}|{text}{ipa_part}{say_part}"
        digest = hashlib.sha256(key.encode()).hexdigest()[:16]
        return self._cache_dir / f"{digest}.mp3"  # type: ignore[operator]

    def _request_body(
        self,
        text: str,
        language_code: str,
        name: str,
        speaking_rate: float,
        ipa: str | None = None,
        phrase: bool = False,
        enunciated: bool = False,
    ) -> dict:
        """The JSON body, exactly as the endpoint is measured to accept it.

        ``model_name`` is snake_case on the wire, under ``voice``; the response
        carries base64 MP3 in ``audioContent``. ``prompt`` rides inside
        ``input``, and is omitted entirely when there is no IPA to speak — the
        field is not in the request at all for a plain render.

        *phrase* picks which of the two instructions carries the IPA, and it is
        the CALLER's measurement of the shape rather than a re-derivation here:
        the same count that decided the reading decides the sentence, and asking
        twice is asking to disagree. *enunciated* outranks it: an Enunciated
        line has a prompt whether or not it has a reading.
        """
        request_input: dict[str, str] = {"text": text}
        if enunciated or ipa is not None:
            language_name = language_name_for_tts_locale(language_code)
            named = f"{language_name} " if language_name else ""
            if enunciated:
                request_input["prompt"] = _enunciated_prompt(named, ipa)
            else:
                # Narrowed by the condition above: not enunciated, so there is a reading.
                build_prompt = _phrase_ipa_prompt if phrase else _ipa_prompt
                request_input["prompt"] = build_prompt(named, ipa)  # type: ignore[arg-type]
        return {
            "input": request_input,
            "voice": {"languageCode": language_code, "name": name, "model_name": self._model},
            "audioConfig": {"audioEncoding": _AUDIO_ENCODING, "speakingRate": speaking_rate},
        }

    async def _line_take(
        self,
        text: str,
        language_code: str,
        name: str,
        speaking_rate: float,
        ipa: str | None,
        phrase: bool,
        enunciated: bool,
        words: int,
    ) -> bytes:
        """A take of a prompted line that can be kept, or ``UnusableLineError``.

        Each fresh take is checked before the caller writes or caches it, and a
        take that fails is dropped and the line asked for again, up to
        ``MAX_LINE_TAKES``. Two things fail one. It SPEAKS for longer than
        ``repeat_limit_seconds``: the line was said twice. Or, for an Enunciated
        line of *words* words, it has no pause at one of its word gaps, so the
        gaps cannot be set (``rewrite_enunciated_take``). The length is read
        first and off the take as spoken: the rewrite speeds the speech, and a
        repeat must not reach it.

        What comes back for an Enunciated line is the take REWRITTEN. The
        measuring and the rewriting are subprocesses, so they run off the event
        loop: a render shares its loop with the API.
        """
        limit = repeat_limit_seconds(text, speaking_rate, enunciated=enunciated)
        failed: list[str] = []
        repeats = 0
        for take in range(1, MAX_LINE_TAKES + 1):
            audio = await self._synthesize_with_retry(text, language_code, name, speaking_rate, ipa, phrase, enunciated)
            seconds = await asyncio.to_thread(self._speech_seconds, audio)
            if seconds > limit:
                repeats += 1
                failed.append(f"take {take} spoke for {seconds:.2f} s")
                logger.info(
                    "Gemini TTS take %d of %r spoke for %.2f s against a limit of %.2f s; taking it again",
                    take,
                    text[:40],
                    seconds,
                    limit,
                )
                continue
            if not enunciated:
                return audio
            rewritten = await asyncio.to_thread(self._rewrite_gaps, audio, words)
            if rewritten is not None:
                return rewritten
            failed.append(f"take {take} ran words together")
            logger.info(
                "Gemini TTS take %d of %r has fewer pauses than its %d words need; taking it again",
                take,
                text[:40],
                words,
            )
        kind = "Enunciated" if enunciated else "natural-speed"
        tried = f"the {kind} line {text!r} on all {MAX_LINE_TAKES} takes: {'; '.join(failed)}"
        if repeats == MAX_LINE_TAKES:
            raise RepeatedLineError(
                f"Gemini TTS said {tried}, against a limit of {limit:.2f} s. Nothing was cached. Takes of very "
                "different lengths mean the line was repeated; takes of about the same length mean the limit is "
                "wrong for this line (see REPEAT_SPEECH_FACTOR)."
            )
        raise UnusableLineError(
            f"Gemini TTS gave no take worth keeping of {tried}. The limit on speech is {limit:.2f} s; a take "
            "with fewer pauses than the line has word gaps cannot have its gaps set. Nothing was cached."
        )

    async def _synthesize_with_retry(
        self,
        text: str,
        language_code: str,
        name: str,
        speaking_rate: float,
        ipa: str | None = None,
        phrase: bool = False,
        enunciated: bool = False,
    ) -> bytes:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                return await self._do_synthesize(text, language_code, name, speaking_rate, ipa, phrase, enunciated)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if _is_fatal(status):
                    # Not retried, and the body is in the message: it is the only
                    # place the provider writes down why.
                    raise RuntimeError(f"Gemini TTS rejected the request (HTTP {status}): {exc.response.text}") from exc
                last_error = exc
                logger.warning(
                    "Gemini TTS transient error (attempt %d): HTTP %d",
                    attempt + 1,
                    status,
                )
                if status == _THROTTLED:
                    logger.warning(
                        "Gemini TTS 429 (throttled) — headers=%r body=%r", dict(exc.response.headers), exc.response.text
                    )
                    # No wait of its own: the cooldown the request lock holds IS
                    # the wait, and it is shared with every caller queued behind
                    # this one. Retrying at once costs nothing and buys nothing —
                    # this caller simply joins the queue for the same window
                    # rather than holding a private timer alongside it.
                    continue
                delay = 2**attempt
            except (httpx.TransportError, OSError) as exc:
                last_error = exc
                delay = 2**attempt
                logger.warning("Gemini TTS transient error (attempt %d): %s", attempt + 1, exc)
            # Do not sleep after the final attempt — it is dead time on every
            # terminal failure, and on this ladder it is a 16-second rung that
            # nobody is waiting for.
            if attempt < MAX_RETRIES - 1:
                await self._sleep(delay)
        raise TTSExhausted(f"Gemini TTS synthesis failed after {MAX_RETRIES} attempts") from last_error

    async def _do_synthesize(
        self,
        text: str,
        language_code: str,
        name: str,
        speaking_rate: float,
        ipa: str | None = None,
        phrase: bool = False,
        enunciated: bool = False,
    ) -> bytes:
        token = await self._token_provider()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "tunatale",
        }
        body = self._request_body(text, language_code, name, speaking_rate, ipa, phrase, enunciated)
        # The pacing delay is paid on EVERY attempt — success or failure — so
        # that consecutive request STARTS are at least min_delay apart. A
        # throttled request that exits instantly is what turns a burst into a
        # cascade. The one exception is a fatal 4xx: it will never be accepted,
        # so there is nothing to pace for and raising immediately is the whole
        # point of it.
        async with self._request_lock:
            # The quota is per-MINUTE, so a 429 is a statement about a WINDOW,
            # not about the one request that happened to trip it. The wait lives
            # here, inside the lock, so that every caller queued behind the one
            # that was refused waits out that same window instead of spending it
            # arriving at the same refusal one after another. Sleeping it in the
            # retry loop instead released the lock first, which is how a single
            # exhausted minute turned 33 of 136 requests into 429s, every one of
            # them logged as "attempt 1" because none of them had ever retried.
            cooldown = self._cooldown_until - self._now()
            if cooldown > 0:
                await self._sleep(cooldown)
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as http:
                    response = await http.post(_SYNTHESIS_URL, headers=headers, json=body)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == _THROTTLED:
                    # Recorded under the lock, so the next caller to reach it
                    # reads a cooldown that is already in force.
                    self._cooldown_until = self._now() + RATE_LIMIT_DELAY
                if not _is_fatal(exc.response.status_code):
                    await self._sleep(self._min_delay)
                raise
            except httpx.TransportError, OSError:
                await self._sleep(self._min_delay)
                raise
            else:
                await self._sleep(self._min_delay)
                return base64.b64decode(response.json()["audioContent"])
