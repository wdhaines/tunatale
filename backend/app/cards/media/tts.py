"""TTS audio generation for Anki card pronunciations.

Provider-agnostic: renders through whichever adapter ``TTS_PROVIDER`` selects
(see app/audio/tts_factory.py), rather than talking to edge-tts directly.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


async def generate_tts_audio(text: str, voice: str | None = None) -> bytes | None:
    """Generate TTS audio for *text*. Returns MP3 bytes, or None on error.

    Returning None rather than raising is the callers' contract (media pipeline
    and cloze backfill both treat None as "no audio for this item"), but the
    failure is LOGGED rather than swallowed silently — an unset AZURE_SPEECH_KEY
    would otherwise show up only as cards mysteriously missing audio.
    """
    speak_locale: str | None = None
    if voice is None:
        from app.config import settings
        from app.languages import get_tts_locale, get_tts_voice

        voice = get_tts_voice(settings.target_language)
        # Declared only on the voice WE resolved, where the language of *text*
        # is known to be the target language. A caller that passes its own voice
        # owns the locale question: it may well be handing us English text with
        # an English voice, and declaring the target locale there would be worse
        # than declaring nothing. Today's default voice is female-1, which is
        # native for both wired languages, so the adapter emits no wrapper at
        # all — this matters for a language whose native catalogue cannot even
        # fill role 1.
        speak_locale = get_tts_locale(settings.target_language)

    from app.audio.tts_factory import get_tts_service

    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tts.mp3"
            await get_tts_service().synthesize(text, voice, out, speak_locale=speak_locale)
            data = out.read_bytes() if out.exists() else b""
        return data or None
    except Exception as exc:
        logger.warning("TTS generation failed for %r (voice=%s): %s", text[:40], voice, exc)
        return None
