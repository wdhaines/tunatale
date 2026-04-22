"""edge-tts audio generation for Anki card pronunciations."""

from __future__ import annotations

import edge_tts

DEFAULT_VOICE = "sl-SI-PetraNeural"


async def generate_tts_audio(text: str, voice: str = DEFAULT_VOICE) -> bytes | None:
    """Generate TTS audio using edge-tts. Returns MP3 bytes or None on error."""
    try:
        communicate = edge_tts.Communicate(text, voice)
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        data = b"".join(chunks)
        return data or None
    except Exception:
        return None
