"""Azure Speech adapter — implements the TTSService Protocol.

Replaces the unofficial Edge Read Aloud endpoint that ``edge_tts`` talks to. The
same neural voices are served from here, but with terms, a support channel, and
a documented failure surface.

Deliberately mirrors ``EdgeTTSService``'s shape (cache layout, retry ladder,
concurrency cap) so the two are interchangeable behind ``TTSService`` and a
provider switch changes only the endpoint, not the behaviour around it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import re
import shutil
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import httpx

from app.audio.ports import TTSExhausted

logger = logging.getLogger(__name__)

# The "kept in step with EdgeTTSService" pacing invariant is real, but it lives
# in app/config.py — both providers read `tts_min_request_delay_s` and
# `tts_max_concurrent_requests`, with `tts_azure_min_request_delay_s` as the
# per-provider override. Duplicating the numbers here read as documentation and
# was worse than nothing: nothing imported them, and MAX_CONCURRENT_REQUESTS = 10
# contradicted the setting it claimed to mirror (which is 1, deliberately).
# Six, not three. Measured 2026-08-28 on a real cold day-8 render at
# concurrency 3: 76 x 429 spread across EVERY second of the run (1-6 per
# second, no quiet window) while 64% of requests succeeded anyway. So a
# throttle is a sustained condition to keep drawing against, not a burst to
# wait out. At a 64% per-request success rate a 3-rung ladder leaves a clip a
# 0.36^3 = 4.7% chance of exhausting — over 169 clips that is a near-certain
# render abort, and it is what killed the control run. Six rungs make it
# 0.36^6 = 0.22%, a 20x reduction in the event that ends a render.
#
# The rungs cost nothing when nothing is failing, and a clip in backoff does
# NOT hold the semaphore, so patience here is not throughput lost.
MAX_RETRIES = 6
# Jitter, as a fraction ADDED to a backoff. All 169 clips exist as tasks from
# the start and any number can sit in backoff at once, so a deterministic
# ladder wakes them as a stampede onto one semaphore slot
# (findings-tts-pacing-2026-08-21.md, defect 2). Additive only: the ladder is
# a floor, and shortening a wait would undo the patience above.
RETRY_JITTER = 0.5

# Edge Read Aloud returns 24 kHz / 48 kbit mono mp3. Matching it means audio
# rendered after the cutover is byte-comparable in format to the ~400 MB already
# on disk — the container and bitrate do not change mid-curriculum.
OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"

_SYNTHESIS_PATH = "/cognitiveservices/v1"
_VOICES_PATH = "/cognitiveservices/voices/list"

# 401/403 mean the key or region is wrong; retrying cannot fix that and only
# delays the diagnosis. Everything else transient (5xx, 429, transport) retries.
_FATAL_STATUSES = frozenset({401, 403})

# Word tokens for <phoneme> interleaving: unicode letters only, no digits, no
# underscore. Deliberately NOT a per-language letter class — that would be a
# language literal in core (scripts/check_language_literals.py). Measured to
# match scripts/local/render_phoneme_ab.py's Norwegian class on its seven
# documented cases.
_WORD_RE = re.compile(r"[^\W\d_]+")


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header into seconds, or ``None`` if absent/malformed.

    RFC 9110 allows two wire forms: delta-seconds ("120") or an HTTP-date
    ("Wed, 21 Oct 2026 07:28:00 GMT"). Azure has never been observed to send
    this header at all (findings-tts-pacing-2026-08-21.md) — this exists
    defensively, in case that changes or another provider sends one. The
    caller combines the result with the retry ladder via ``max(header,
    ladder)`` so a malformed or absent header falls back to the ladder rather
    than raising, and a present header can only ever LENGTHEN a wait.
    """
    if not value:
        return None
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    try:
        # email.utils.parsedate_to_datetime never returns None: on a
        # malformed date it raises ValueError, and on a valid one it returns
        # a datetime (naive if the string carried no timezone offset).
        parsed = parsedate_to_datetime(value)
    except TypeError, ValueError, OverflowError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (parsed - datetime.now(UTC)).total_seconds()


class AzureTTSService:
    """Azure Speech REST adapter.

    Implements the TTSService Protocol with:
    - Rate limiting (200 ms between requests, max 10 concurrent)
    - Optional file-based caching (keyed on text + voice + rate)
    - Retry on transient errors, fail-fast on auth errors
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        key: str | None = None,
        region: str | None = None,
        timeout: float = 30.0,
        min_delay: float | None = None,
        retry_base_delay: float | None = None,
        max_concurrent_requests: int | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        # Resolved at construction but NOT validated here: the app builds a
        # renderer at startup whether or not anyone renders anything, so an
        # empty key must not stop the process from booting. It fails at the call
        # site instead, which is where it is actionable.
        if key is None or region is None:
            from app.config import settings

            key = settings.azure_speech_key if key is None else key
            region = settings.azure_speech_region if region is None else region
        if min_delay is None:
            from app.config import settings

            # Per-provider override first, shared setting when unset — see
            # app/config.py::tts_azure_min_request_delay_s.
            min_delay = settings.tts_azure_min_request_delay_s
            if min_delay is None:
                min_delay = settings.tts_min_request_delay_s
        if max_concurrent_requests is None:
            from app.config import settings

            max_concurrent_requests = settings.tts_max_concurrent_requests
        if retry_base_delay is None:
            from app.config import settings

            retry_base_delay = settings.tts_retry_base_delay_s
        self._key = key
        self._region = region
        self._cache_dir = cache_dir
        self._timeout = timeout
        # Injectable so tests can run the retry ladder at zero cost. Patching
        # asyncio.sleep would mean a patch("app.…") entry in mock_allowlist.txt,
        # i.e. an architectural claim, for what is really just a tuning knob.
        self._min_delay = min_delay
        self._retry_base_delay = retry_base_delay
        self._max_concurrent = max_concurrent_requests
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)
        # Same injection strategy as min_delay/retry_base_delay: real code path,
        # zero wall-clock cost in tests, no mock_allowlist.txt entry needed.
        self._sleep = sleep if sleep is not None else asyncio.sleep
        # 429 headers+body are logged once per instance — a burst produces one
        # diagnostic line, not a hundred identical dumps.
        self._logged_429 = False

    # ------------------------------------------------------------------
    # TTSService Protocol implementation
    # ------------------------------------------------------------------

    async def synthesize(
        self, text: str, voice_id: str, output_path: Path, rate: str = "+0%", phonemes: Mapping[str, str] | None = None
    ) -> None:
        """Synthesize *text* to *output_path* using Azure Speech.

        Args:
            text: Text to synthesize.
            voice_id: Azure voice short name (e.g. "sl-SI-PetraNeural").
            output_path: Destination file path for the synthesized audio.
            rate: Speech rate adjustment (e.g. "+0%", "-20%").
            phonemes: Lowercased surface token -> IPA. Tokens found in *text*
                are wrapped in ``<phoneme>`` elements; everything else is
                escaped and emitted exactly as before. ``None``/``{}`` change
                nothing, including the cache key.
        """
        self._require_credentials()

        if self._cache_dir is not None:
            cached = self._cache_path(text, voice_id, rate, phonemes)
            if cached.exists():
                output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cached, output_path)
                logger.debug("Azure TTS cache hit for %r", text[:40])
                return

        audio = await self._synthesize_with_retry(text, voice_id, rate, phonemes)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio)

        if self._cache_dir is not None:
            cached = self._cache_path(text, voice_id, rate, phonemes)
            cached.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(output_path, cached)

    async def list_voices(self, language_code: str | None = None) -> list[dict]:
        """Return available Azure voices for the region, optionally filtered."""
        self._require_credentials()
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            response = await http.get(self._url(_VOICES_PATH), headers={"Ocp-Apim-Subscription-Key": self._key})
            response.raise_for_status()
            voices = response.json()
        if language_code:
            voices = [v for v in voices if language_code in v.get("Locale", "")]
        return voices

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _require_credentials(self) -> None:
        """Fail loudly, naming the setting, rather than degrading quietly.

        There is deliberately no fallback to the edge provider here. An
        automatic swap would render part of a curriculum in a different
        provider's rendition of the "same" voice, silently — id parity is not
        voice parity. Choosing edge is a human decision via TTS_PROVIDER.
        """
        if not self._key:
            raise RuntimeError(
                "AZURE_SPEECH_KEY is not set. Set it in backend/.env, or select a different provider with TTS_PROVIDER."
            )
        if not self._region:
            raise RuntimeError(
                "AZURE_SPEECH_REGION is not set. Use the machine-readable form "
                "(e.g. eastus), not the portal's display name."
            )

    def _url(self, path: str) -> str:
        return f"https://{self._region}.tts.speech.microsoft.com{path}"

    def _cache_path(self, text: str, voice_id: str, rate: str, phonemes: Mapping[str, str] | None = None) -> Path:
        key = f"{voice_id}|{rate}|{text}"
        # Extend the key ONLY when a mapping is present: backend/media and
        # backend/output hold hundreds of MB keyed on the three-part form, and
        # an unconditional extension would orphan all of it. Sorted so dict
        # ordering cannot split one mapping across two entries.
        if phonemes:
            key += "|" + ",".join(f"{token}:{ipa}" for token, ipa in sorted(phonemes.items()))
        digest = hashlib.sha256(key.encode()).hexdigest()[:16]
        return self._cache_dir / f"{digest}.mp3"  # type: ignore[operator]

    @staticmethod
    def _build_ssml(text: str, voice_id: str, rate: str, phonemes: Mapping[str, str] | None = None) -> str:
        """Wrap *text* in SSML.

        The locale is sliced off the voice id ("nb-NO-FinnNeural" -> "nb-NO")
        rather than looked up per language, which keeps every language literal
        out of this module — see scripts/check_language_literals.py.

        With a non-empty *phonemes* mapping, each word token whose lowercase
        form has an entry is wrapped in a ``<phoneme>`` element (structure
        ported from scripts/local/render_phoneme_ab.py::build_phoneme_body).
        ALL caller text is still escaped either way — markup can only enter
        the SSML through the argument, never through *text*.
        """
        body = AzureTTSService._phoneme_body(text, phonemes) if phonemes else escape(text)
        locale = "-".join(voice_id.split("-")[:2])
        return (
            f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang={quoteattr(locale)}>'
            f"<voice name={quoteattr(voice_id)}>"
            f"<prosody rate={quoteattr(rate)}>{body}</prosody>"
            f"</voice></speak>"
        )

    @staticmethod
    def _phoneme_body(text: str, phonemes: Mapping[str, str]) -> str:
        parts: list[str] = []
        last = 0
        for match in _WORD_RE.finditer(text):
            parts.append(escape(text[last : match.start()]))
            token = match.group(0)
            ipa = phonemes.get(token.lower())
            if ipa is None:
                parts.append(escape(token))
            else:
                # The ph attribute needs &, <, > and " handled; escape() covers
                # the first three, the map turns a bare " into &quot;.
                safe_ipa = escape(ipa, {'"': "&quot;"})
                parts.append(f'<phoneme alphabet="ipa" ph="{safe_ipa}">{escape(token)}</phoneme>')
            last = match.end()
        parts.append(escape(text[last:]))
        return "".join(parts)

    async def _synthesize_with_retry(
        self, text: str, voice_id: str, rate: str, phonemes: Mapping[str, str] | None = None
    ) -> bytes:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                return await self._do_synthesize(text, voice_id, rate, phonemes)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in _FATAL_STATUSES:
                    raise RuntimeError(
                        f"Azure Speech rejected the credentials (HTTP {exc.response.status_code}). "
                        "Check AZURE_SPEECH_KEY and AZURE_SPEECH_REGION."
                    ) from exc
                last_error = exc
                logger.warning(
                    "Azure TTS transient error (attempt %d): HTTP %d",
                    attempt + 1,
                    exc.response.status_code,
                )
                # 429 (rate limit) gets a longer backoff than transient 5xx,
                # because the burst window refills in ~0s of true idle but
                # the semaphore serializes requests — a short backoff would
                # just pile the next request into the same window.
                if exc.response.status_code == 429:
                    self._log_429_once(exc.response)
                    delay = self._retry_base_delay * 4 * (2**attempt)
                    # Defensive, not the point: Azure has never sent this
                    # header (findings-tts-pacing-2026-08-21.md), but honour
                    # it when present. max() means it can only LENGTHEN the
                    # wait, never shorten it below the ladder.
                    delay += delay * random.random() * RETRY_JITTER
                    retry_after = _parse_retry_after(exc.response.headers.get("Retry-After"))
                    if retry_after is not None:
                        delay = max(delay, retry_after)
                else:
                    delay = self._retry_base_delay * (2**attempt)
            except (httpx.TransportError, OSError) as exc:
                last_error = exc
                delay = self._retry_base_delay * (2**attempt)
                logger.warning("Azure TTS transient error (attempt %d): %s", attempt + 1, exc)
            # Do not sleep after the final attempt — it adds dead time to
            # every terminal failure.
            if attempt < MAX_RETRIES - 1:
                await self._sleep(delay)
        raise TTSExhausted(f"Azure TTS synthesis failed after {MAX_RETRIES} attempts") from last_error

    def _log_429_once(self, response: httpx.Response) -> None:
        """Log the 429 body AND headers — the only signal the provider gives.

        Once per adapter instance: a burst can produce dozens of 429s and
        they are one diagnostic dump, not one per request.
        """
        if self._logged_429:
            return
        self._logged_429 = True
        logger.warning(
            "Azure TTS 429 (throttled) — headers=%r body=%r",
            dict(response.headers),
            response.text,
        )

    async def _do_synthesize(
        self, text: str, voice_id: str, rate: str, phonemes: Mapping[str, str] | None = None
    ) -> bytes:
        async with self._semaphore:
            headers = {
                "Ocp-Apim-Subscription-Key": self._key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
                "User-Agent": "tunatale",
            }
            # The pacing delay must be paid on EVERY attempt — success or
            # failure — or a throttled request exits the semaphore without
            # ever sleeping and frees the slot instantly, which is exactly
            # what amplifies a burst into a cascade of 429s
            # (findings-tts-pacing-2026-08-21.md). The one exception is a
            # FATAL status (401/403): retrying cannot fix bad credentials, so
            # there is nothing to pace for and the oracle requires zero
            # sleeps on that path.
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as http:
                    response = await http.post(
                        self._url(_SYNTHESIS_PATH),
                        headers=headers,
                        content=self._build_ssml(text, voice_id, rate, phonemes).encode("utf-8"),
                    )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in _FATAL_STATUSES:
                    await self._sleep(self._min_delay)
                raise
            except httpx.TransportError, OSError:
                await self._sleep(self._min_delay)
                raise
            else:
                await self._sleep(self._min_delay)
                return response.content
