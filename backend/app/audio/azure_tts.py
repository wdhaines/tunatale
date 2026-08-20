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
import shutil
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import httpx

logger = logging.getLogger(__name__)

# Kept in step with EdgeTTSService so the provider switch is not also a
# throughput or pacing change. Overridden by app/config.py settings.
MIN_REQUEST_DELAY_S = 0.2
MAX_CONCURRENT_REQUESTS = 10
MAX_RETRIES = 3

# Edge Read Aloud returns 24 kHz / 48 kbit mono mp3. Matching it means audio
# rendered after the cutover is byte-comparable in format to the ~400 MB already
# on disk — the container and bitrate do not change mid-curriculum.
OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"

_SYNTHESIS_PATH = "/cognitiveservices/v1"
_VOICES_PATH = "/cognitiveservices/voices/list"

# 401/403 mean the key or region is wrong; retrying cannot fix that and only
# delays the diagnosis. Everything else transient (5xx, 429, transport) retries.
_FATAL_STATUSES = frozenset({401, 403})


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
        retry_base_delay: float = 0.5,
        max_concurrent_requests: int | None = None,
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

            min_delay = settings.tts_min_request_delay_s
        if max_concurrent_requests is None:
            from app.config import settings

            max_concurrent_requests = settings.tts_max_concurrent_requests
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

    # ------------------------------------------------------------------
    # TTSService Protocol implementation
    # ------------------------------------------------------------------

    async def synthesize(self, text: str, voice_id: str, output_path: Path, rate: str = "+0%") -> None:
        """Synthesize *text* to *output_path* using Azure Speech.

        Args:
            text: Text to synthesize.
            voice_id: Azure voice short name (e.g. "sl-SI-PetraNeural").
            output_path: Destination file path for the synthesized audio.
            rate: Speech rate adjustment (e.g. "+0%", "-20%").
        """
        self._require_credentials()

        if self._cache_dir is not None:
            cached = self._cache_path(text, voice_id, rate)
            if cached.exists():
                output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cached, output_path)
                logger.debug("Azure TTS cache hit for %r", text[:40])
                return

        audio = await self._synthesize_with_retry(text, voice_id, rate)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio)

        if self._cache_dir is not None:
            cached = self._cache_path(text, voice_id, rate)
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

    def _cache_path(self, text: str, voice_id: str, rate: str) -> Path:
        key = f"{voice_id}|{rate}|{text}"
        digest = hashlib.sha256(key.encode()).hexdigest()[:16]
        return self._cache_dir / f"{digest}.mp3"  # type: ignore[operator]

    @staticmethod
    def _build_ssml(text: str, voice_id: str, rate: str) -> str:
        """Wrap *text* in SSML.

        The locale is sliced off the voice id ("nb-NO-FinnNeural" -> "nb-NO")
        rather than looked up per language, which keeps every language literal
        out of this module — see scripts/check_language_literals.py.
        """
        locale = "-".join(voice_id.split("-")[:2])
        return (
            f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang={quoteattr(locale)}>'
            f"<voice name={quoteattr(voice_id)}>"
            f"<prosody rate={quoteattr(rate)}>{escape(text)}</prosody>"
            f"</voice></speak>"
        )

    async def _synthesize_with_retry(self, text: str, voice_id: str, rate: str) -> bytes:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                return await self._do_synthesize(text, voice_id, rate)
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
                    delay = self._retry_base_delay * 4 * (2**attempt)
                else:
                    delay = self._retry_base_delay * (2**attempt)
            except (httpx.TransportError, OSError) as exc:
                last_error = exc
                delay = self._retry_base_delay * (2**attempt)
                logger.warning("Azure TTS transient error (attempt %d): %s", attempt + 1, exc)
            # Do not sleep after the final attempt — it adds dead time to
            # every terminal failure.
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(delay)
        raise RuntimeError(f"Azure TTS synthesis failed after {MAX_RETRIES} attempts") from last_error

    async def _do_synthesize(self, text: str, voice_id: str, rate: str) -> bytes:
        async with self._semaphore:
            headers = {
                "Ocp-Apim-Subscription-Key": self._key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
                "User-Agent": "tunatale",
            }
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                response = await http.post(
                    self._url(_SYNTHESIS_PATH),
                    headers=headers,
                    content=self._build_ssml(text, voice_id, rate).encode("utf-8"),
                )
                response.raise_for_status()
                audio = response.content
            await asyncio.sleep(self._min_delay)
            return audio
