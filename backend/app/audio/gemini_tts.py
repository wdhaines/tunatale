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
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

import google.auth
import google.auth.transport.requests
import google.oauth2.service_account
import httpx

from app.audio.ports import TTSExhausted

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
RATE_LIMIT_DELAY = 20.0

_SYNTHESIS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
_AUDIO_ENCODING = "MP3"

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
    ) -> None:
        """Synthesize *text* to *output_path* using Google Cloud TTS.

        Args:
            text: Text to synthesize.
            voice_id: A ``<locale>-<Name>Gemini`` voice id. Anything else
                raises :class:`ValueError` naming the id.
            output_path: Destination file path for the synthesized audio.
            rate: Speech rate adjustment as a percentage string (e.g.
                ``"-20%"``), mapped to the provider's multiplier.
            phonemes: Accepted and IGNORED. This provider takes no markup, so
                per-token IPA cannot be expressed; a non-empty mapping logs one
                warning and changes neither the request nor the cache key.
            speak_locale: Accepted and IGNORED, silently. The voice's own
                ``languageCode`` is explicit, so there is nothing to override.
        """
        language_code, name = _parse_voice_id(voice_id)
        speaking_rate = _speaking_rate(rate)
        if phonemes:
            self._warn_phonemes_unsupported()

        if self._cache_dir is not None:
            cached = self._cache_path(text, voice_id, rate)
            if cached.exists():
                output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cached, output_path)
                logger.debug("Gemini TTS cache hit for %r", text[:40])
                return

        audio = await self._synthesize_with_retry(text, language_code, name, speaking_rate)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio)

        if self._cache_dir is not None:
            cached = self._cache_path(text, voice_id, rate)
            cached.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(output_path, cached)

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

    def _cache_path(self, text: str, voice_id: str, rate: str) -> Path:
        """The cache file for one (model, voice, rate, text) tuple.

        The ``gemini|`` prefix keeps this adapter's entries disjoint from the
        sibling's in the one shared cache directory: both name files by a
        16-hex digest, so without it a digest collision would serve one
        provider's audio for the other's voice. The model is in the key
        because the output is nondeterministic per call — a model swap has to
        be able to tell that yesterday's file was not produced by today's
        model.
        """
        key = f"gemini|{self._model}|{voice_id}|{rate}|{text}"
        digest = hashlib.sha256(key.encode()).hexdigest()[:16]
        return self._cache_dir / f"{digest}.mp3"  # type: ignore[operator]

    def _request_body(self, text: str, language_code: str, name: str, speaking_rate: float) -> dict:
        """The JSON body, exactly as the endpoint is measured to accept it.

        ``model_name`` is snake_case on the wire, under ``voice``; the response
        carries base64 MP3 in ``audioContent``.
        """
        return {
            "input": {"text": text},
            "voice": {"languageCode": language_code, "name": name, "model_name": self._model},
            "audioConfig": {"audioEncoding": _AUDIO_ENCODING, "speakingRate": speaking_rate},
        }

    async def _synthesize_with_retry(self, text: str, language_code: str, name: str, speaking_rate: float) -> bytes:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                return await self._do_synthesize(text, language_code, name, speaking_rate)
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
                delay = RATE_LIMIT_DELAY if status == _THROTTLED else 2**attempt
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

    async def _do_synthesize(self, text: str, language_code: str, name: str, speaking_rate: float) -> bytes:
        token = await self._token_provider()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "tunatale",
        }
        body = self._request_body(text, language_code, name, speaking_rate)
        # The pacing delay is paid on EVERY attempt — success or failure — so
        # that consecutive request STARTS are at least min_delay apart. A
        # throttled request that exits instantly is what turns a burst into a
        # cascade. The one exception is a fatal 4xx: it will never be accepted,
        # so there is nothing to pace for and raising immediately is the whole
        # point of it.
        async with self._request_lock:
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as http:
                    response = await http.post(_SYNTHESIS_URL, headers=headers, json=body)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if not _is_fatal(exc.response.status_code):
                    await self._sleep(self._min_delay)
                raise
            except httpx.TransportError, OSError:
                await self._sleep(self._min_delay)
                raise
            else:
                await self._sleep(self._min_delay)
                return base64.b64decode(response.json()["audioContent"])
