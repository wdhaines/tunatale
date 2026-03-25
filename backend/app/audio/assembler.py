"""Audio assembler — implements AudioProcessor Protocol using raw PCM bytes."""

from __future__ import annotations

# PCM constants: 16-bit mono, 22050 Hz (matches EdgeTTS default output when saved as PCM)
_SAMPLE_RATE = 22050
_CHANNELS = 1
_SAMPLE_WIDTH = 2  # bytes per sample (16-bit)
_BYTES_PER_MS = (_SAMPLE_RATE * _CHANNELS * _SAMPLE_WIDTH) // 1000


class AudioAssembler:
    """Simple raw-bytes audio assembler.

    Operates on raw PCM bytes (or opaque audio blobs like fake test data).
    Implements the AudioProcessor Protocol so it passes isinstance checks.
    """

    def concatenate(self, audio_bytes_list: list[bytes], silence_ms: int = 300) -> bytes:
        """Join audio chunks, inserting silence between them."""
        if not audio_bytes_list:
            return b""
        gap = self.add_silence(silence_ms)
        parts = [audio_bytes_list[0]]
        for chunk in audio_bytes_list[1:]:
            parts.append(gap)
            parts.append(chunk)
        return b"".join(parts)

    def normalize(self, audio_bytes: bytes) -> bytes:
        """Return audio unchanged (normalization requires codec support)."""
        return audio_bytes

    def add_silence(self, duration_ms: int) -> bytes:
        """Generate *duration_ms* milliseconds of 16-bit mono PCM silence."""
        n_bytes = max(0, duration_ms * _BYTES_PER_MS)
        return b"\x00" * n_bytes

    def trim_silence(self, audio_bytes: bytes, threshold_db: float = -40.0) -> bytes:
        """Return audio unchanged (trimming requires codec support)."""
        return audio_bytes
