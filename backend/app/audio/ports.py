"""Audio port protocols."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class TTSService(Protocol):
    """Protocol for text-to-speech synthesis services."""

    async def synthesize(self, text: str, voice_id: str, output_path: Path, rate: str = "+0%") -> None: ...

    async def list_voices(self, language_code: str | None = None) -> list[dict]: ...
