"""Provider selection for text-to-speech.

Two implementations sit behind ``TTSService``: Azure Speech (the official
endpoint, the default) and Edge Read Aloud (the unofficial one being retired by
``tunatale-i69``). ``TTS_PROVIDER`` picks between them.

The selection is EXPLICIT and there is no automatic runtime fallback — if the
chosen provider cannot render, the render fails. An automatic swap would splice
a second provider's rendition of the "same" voice into a curriculum silently,
and voice-id parity is not voice parity.
"""

from __future__ import annotations

from pathlib import Path

from app.audio.azure_tts import AzureTTSService
from app.audio.edge_tts import EdgeTTSService
from app.audio.ports import TTSService

PROVIDERS = {
    "azure": AzureTTSService,
    "edge": EdgeTTSService,
}


def get_tts_service(cache_dir: Path | None = None, provider: str | None = None) -> TTSService:
    """Return the configured TTS adapter.

    Args:
        cache_dir: Optional file-cache directory, honoured by both adapters.
        provider: Override for ``settings.tts_provider``; mainly for callers
            that need to pin a provider without mutating global settings.
    """
    if provider is None:
        from app.config import settings

        provider = settings.tts_provider

    try:
        factory = PROVIDERS[provider]
    except KeyError:
        # Loud, and it names the valid options: an unrecognised value silently
        # falling back to a default is how half a curriculum gets rendered by
        # the wrong provider without anyone noticing.
        valid = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"Unknown TTS_PROVIDER {provider!r}. Valid providers: {valid}.") from None

    return factory(cache_dir=cache_dir)
