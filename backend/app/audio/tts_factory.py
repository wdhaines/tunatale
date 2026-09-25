"""TTS adapter construction — two providers, routed by voice id.

The unofficial Edge Read Aloud adapter (once selectable through a provider
setting) was retired by ``tunatale-i69``, leaving Azure Speech as the only
implementation. Gemini-TTS voices arrived after that and needed a second
adapter, so ``get_tts_service`` now builds both and returns them behind
``RoutingTTSService``.

There is still nothing to select and, deliberately, nothing to fall back to.
A voice id names its provider, and the router honours that; if the named
adapter cannot render, the render fails. An automatic runtime swap would
splice a second provider's rendition of the "same" voice into a curriculum
silently, and voice-id parity is not voice parity.
"""

from __future__ import annotations

from pathlib import Path

from app.audio.azure_tts import AzureTTSService
from app.audio.gemini_tts import GeminiTTSService
from app.audio.ports import TTSService
from app.audio.tts_router import RoutingTTSService


def get_tts_service(cache_dir: Path | None = None) -> TTSService:
    """Return the routing TTS service, both adapters wired in.

    Args:
        cache_dir: Optional file-cache directory, shared by both adapters.
    """
    # The character ledger counts Azure spend, which is a per-provider fact —
    # it stays on the Azure adapter and is not attached to the other one.
    from app.audio.char_ledger import AzureCharacterLedger
    from app.config import settings

    ledger = AzureCharacterLedger(settings.azure_tts_usage_ledger_path)
    azure = AzureTTSService(cache_dir=cache_dir, ledger=ledger)
    gemini = GeminiTTSService(cache_dir=cache_dir)
    return RoutingTTSService(azure, gemini, cache_dir=cache_dir)
