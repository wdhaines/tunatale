"""Audio port protocols."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable


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
