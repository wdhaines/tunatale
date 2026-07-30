"""edge-tts audio generation for Anki card pronunciations."""

from __future__ import annotations

import edge_tts


async def generate_tts_audio(text: str, voice: str | None = None) -> bytes | None:
    """Generate TTS audio using edge-tts. Returns MP3 bytes or None on error."""
    if voice is None:
        from app.config import settings
        from app.languages import get_tts_voice

        voice = get_tts_voice(settings.target_language)
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
