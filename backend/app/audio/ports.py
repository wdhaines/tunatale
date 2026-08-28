"""Audio port protocols."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable


class TTSExhausted(RuntimeError):
    """A clip's retry ladder ran out — the provider kept pushing back.

    Distinct from the bare ``RuntimeError`` an adapter raises for a missing key
    or region, because only THIS one is worth re-running: a throttling episode
    is exogenous and passes, a missing ``AZURE_SPEECH_KEY`` does not. The
    render-level retry loop
    (``render_service._with_render_retries``) retries this and nothing else.

    Still a ``RuntimeError``, so ``api/audio.py``'s mapping to a 503 carrying
    the adapter's own message is unchanged.
    """


@runtime_checkable
class TTSService(Protocol):
    """Protocol for text-to-speech synthesis services."""

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        output_path: Path,
        rate: str = "+0%",
        phonemes: Mapping[str, str] | None = None,
    ) -> None:
        """Synthesize *text*, optionally wrapping known tokens in ``<phoneme>``.

        *phonemes* maps a lowercased surface token to its IPA. It is per-token,
        not whole-text IPA. ``None`` and ``{}`` must behave identically to a
        provider without the capability at all, including the cache key.
        """
        ...

    async def list_voices(self, language_code: str | None = None) -> list[dict]: ...
