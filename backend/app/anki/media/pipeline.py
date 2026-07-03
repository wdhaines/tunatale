"""Media pipeline: fetch audio (Forvo → TTS) and image (Pixabay) for an Anki card."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

import anyio

from app.languages import get_tts_voice

from .forvo import fetch_forvo_audio
from .normalize import normalize_audio
from .pixabay import fetch_pixabay_image
from .tts import generate_tts_audio


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
    language_code: str = "sl",
    http_client: Any = None,
    tts_voice: str | None = None,
    normalize: bool = True,
    used_image_urls: set[str] | None = None,
    image_query: str | None = None,
    _forvo_fn: Callable[..., bytes | None] | None = None,
    _tts_fn: Callable[..., Awaitable[bytes | None]] | None = None,
    _pixabay_fn: Callable[..., Any] | None = None,
    _normalize_fn: Callable[..., bytes] | None = None,
) -> MediaResult:
    """Fetch audio and image for a vocabulary card.

    Tries Forvo first, falls back to edge-tts. Image from Pixabay.
    Pass used_image_urls (a shared set) across cards to prevent duplicate images.

    ``image_query`` controls image selection (see ``query_llm`` contract):
      * ``None`` — legacy: Pixabay derives the query from ``english``.
      * ``""``   — skip the image entirely (abstract word, no depiction).
      * non-empty — sent to Pixabay verbatim as a sense-disambiguated query.
    """
    forvo_fn = _forvo_fn or fetch_forvo_audio
    tts_fn = _tts_fn or generate_tts_audio
    pixabay_fn = _pixabay_fn or fetch_pixabay_image
    norm_fn = _normalize_fn or normalize_audio
    # Resolve the synthesis voice from the card's language so a non-Slovene card
    # never gets Slovene TTS. Callers may still override explicitly (tests).
    voice = tts_voice or get_tts_voice(language_code)

    result = MediaResult()

    # Forvo / Pixabay / normalize are synchronous (httpx.Client, ffmpeg
    # subprocess) — offload to a worker thread so a slow fetch doesn't block
    # the event loop and stall every other in-flight request.
    audio = await anyio.to_thread.run_sync(
        partial(forvo_fn, word, language_code=language_code, http_client=http_client)
    )
    if audio is not None:
        result.audio_source = "forvo"
        result.audio_bytes = audio
    else:
        audio = await tts_fn(word, voice=voice)
        if audio is not None:
            result.audio_source = "tts"
            result.audio_bytes = audio

    if result.audio_bytes is not None and normalize:
        result.audio_bytes = await anyio.to_thread.run_sync(norm_fn, result.audio_bytes)

    # image_query == "" is the explicit "abstract word, no image" skip sentinel.
    if image_query != "":
        img = await anyio.to_thread.run_sync(
            partial(
                pixabay_fn,
                english,
                api_key=pixabay_key,
                http_client=http_client,
                used_urls=frozenset(used_image_urls) if used_image_urls is not None else frozenset(),
                query=image_query,
            )
        )
        if img is not None:
            result.image_bytes, result.image_ext, result.image_url = img
            if used_image_urls is not None:
                used_image_urls.add(result.image_url)

    return result
