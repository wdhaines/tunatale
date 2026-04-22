"""Media pipeline: fetch audio (Forvo → TTS) and image (Pixabay) for an Anki card."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .forvo import fetch_forvo_audio
from .normalize import normalize_audio
from .pixabay import fetch_pixabay_image
from .tts import DEFAULT_VOICE, generate_tts_audio


@dataclass
class MediaResult:
    audio_bytes: bytes | None = None
    audio_source: str | None = None
    image_bytes: bytes | None = None
    image_ext: str | None = None
    image_url: str | None = None


async def fetch_card_media(
    word: str,
    english: str,
    *,
    pixabay_key: str,
    http_client: Any = None,
    tts_voice: str = DEFAULT_VOICE,
    normalize: bool = True,
    used_image_urls: set[str] | None = None,
    _forvo_fn: Callable[..., bytes | None] | None = None,
    _tts_fn: Callable[..., Awaitable[bytes | None]] | None = None,
    _pixabay_fn: Callable[..., Any] | None = None,
    _normalize_fn: Callable[..., bytes] | None = None,
) -> MediaResult:
    """Fetch audio and image for a vocabulary card.

    Tries Forvo first, falls back to edge-tts. Image from Pixabay.
    Pass used_image_urls (a shared set) across cards to prevent duplicate images.
    """
    forvo_fn = _forvo_fn or fetch_forvo_audio
    tts_fn = _tts_fn or generate_tts_audio
    pixabay_fn = _pixabay_fn or fetch_pixabay_image
    norm_fn = _normalize_fn or normalize_audio

    result = MediaResult()

    audio = forvo_fn(word, http_client=http_client)
    if audio is not None:
        result.audio_source = "forvo"
        result.audio_bytes = audio
    else:
        audio = await tts_fn(word, voice=tts_voice)
        if audio is not None:
            result.audio_source = "tts"
            result.audio_bytes = audio

    if result.audio_bytes is not None and normalize:
        result.audio_bytes = norm_fn(result.audio_bytes)

    img = pixabay_fn(
        english,
        api_key=pixabay_key,
        http_client=http_client,
        used_urls=frozenset(used_image_urls) if used_image_urls is not None else frozenset(),
    )
    if img is not None:
        result.image_bytes, result.image_ext, result.image_url = img
        if used_image_urls is not None:
            used_image_urls.add(result.image_url)

    return result
