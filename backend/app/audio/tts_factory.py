"""TTS adapter construction — Azure Speech is the only provider.

Azure Speech is the sole implementation behind ``TTSService``; the unofficial
Edge Read Aloud adapter (once selectable through a provider setting) was
retired by ``tunatale-i69``. With one provider there is nothing to select and,
deliberately, nothing to fall back to: if Azure cannot render, the render
fails. An automatic runtime swap would splice a second provider's rendition of
the "same" voice into a curriculum silently, and voice-id parity is not voice
parity.
"""

from __future__ import annotations

from pathlib import Path

from app.audio.azure_tts import AzureTTSService
from app.audio.ports import TTSService


def get_tts_service(cache_dir: Path | None = None) -> TTSService:
    """Return the configured TTS adapter (Azure Speech), ledger wired in.

    Args:
        cache_dir: Optional file-cache directory, honoured by the adapter.
    """
    # The character ledger counts Azure spend; Azure is the only adapter, so
    # every service gets one.
    from app.audio.char_ledger import AzureCharacterLedger
    from app.config import settings

    ledger = AzureCharacterLedger(settings.azure_tts_usage_ledger_path)
    return AzureTTSService(cache_dir=cache_dir, ledger=ledger)
