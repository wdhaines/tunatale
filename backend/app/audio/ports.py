"""Audio port protocols."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class TTSService(Protocol):
    """Protocol for text-to-speech synthesis services."""

    async def synthesize(self, text: str, voice_id: str, output_path: Path, rate: str = "+0%") -> None: ...

    async def list_voices(self, language_code: str | None = None) -> list[dict]: ...


@runtime_checkable
class AudioProcessor(Protocol):
    """Protocol for audio processing operations."""

    def concatenate(self, audio_bytes_list: list[bytes], silence_ms: int = 300) -> bytes: ...

    def normalize(self, audio_bytes: bytes) -> bytes: ...

    def add_silence(self, duration_ms: int) -> bytes: ...

    def trim_silence(self, audio_bytes: bytes, threshold_db: float = -40.0) -> bytes: ...
