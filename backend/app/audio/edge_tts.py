"""EdgeTTS adapter — implements TTSService Protocol."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
from pathlib import Path

import edge_tts

logger = logging.getLogger(__name__)

# Rate limiting constants (ported from prototype)
MIN_REQUEST_DELAY_S = 0.2
MAX_CONCURRENT_REQUESTS = 3
MAX_RETRIES = 3


class EdgeTTSService:
    """Microsoft Edge TTS adapter.

    Implements the TTSService Protocol with:
    - Rate limiting (200 ms between requests, max 3 concurrent)
    - Optional file-based caching (keyed on text + voice + rate)
    - Retry on transient errors
    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    # ------------------------------------------------------------------
    # TTSService Protocol implementation
    # ------------------------------------------------------------------

    async def synthesize(self, text: str, voice_id: str, output_path: Path, rate: str = "+0%") -> None:
        """Synthesize *text* to *output_path* using Edge TTS.

        Args:
            text: Text to synthesize.
            voice_id: Edge TTS voice short name (e.g. "sl-SI-PetraNeural").
            output_path: Destination file path for the synthesized audio.
            rate: Speech rate adjustment (e.g. "+0%", "-20%").
        """
        if self._cache_dir is not None:
            cached = self._cache_path(text, voice_id, rate)
            if cached.exists():
                shutil.copy2(cached, output_path)
                logger.debug("EdgeTTS cache hit for %r", text[:40])
                return

        await self._synthesize_with_retry(text, voice_id, output_path, rate)

        if self._cache_dir is not None:
            cached = self._cache_path(text, voice_id, rate)
            cached.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(output_path, cached)

    async def list_voices(self, language_code: str | None = None) -> list[dict]:
        """Return available Edge TTS voices, optionally filtered by language."""
        voices = await edge_tts.list_voices()
        if language_code:
            voices = [v for v in voices if language_code in v.get("Locale", "")]
        return voices

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _cache_path(self, text: str, voice_id: str, rate: str) -> Path:
        key = f"{voice_id}|{rate}|{text}"
        digest = hashlib.sha256(key.encode()).hexdigest()[:16]
        return self._cache_dir / f"{digest}.mp3"  # type: ignore[operator]

    async def _synthesize_with_retry(self, text: str, voice_id: str, output_path: Path, rate: str) -> None:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                await self._do_synthesize(text, voice_id, output_path, rate)
                return
            except (ConnectionResetError, ConnectionError, OSError) as exc:
                last_error = exc
                logger.warning("EdgeTTS transient error (attempt %d): %s", attempt + 1, exc)
                await asyncio.sleep(0.5 * (2**attempt))
        raise RuntimeError(f"EdgeTTS synthesis failed after {MAX_RETRIES} attempts") from last_error

    async def _do_synthesize(self, text: str, voice_id: str, output_path: Path, rate: str) -> None:
        async with self._semaphore:
            communicate = edge_tts.Communicate(text, voice_id, rate=rate)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            await communicate.save(str(output_path))
            await asyncio.sleep(MIN_REQUEST_DELAY_S)
