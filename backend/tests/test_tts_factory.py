"""TTS adapter construction — Azure Speech, the only provider.

The explicit switch between Azure and Edge is gone (``tunatale-i69`` retired
the unofficial Edge adapter). ``get_tts_service`` always builds the Azure
adapter and wires the character ledger. There is deliberately no fallback path
here: see AzureTTSService._require_credentials for why an automatic swap is a
regression rather than resilience.
"""

from __future__ import annotations

from app.audio.azure_tts import AzureTTSService
from app.audio.ports import TTSService
from app.audio.tts_factory import get_tts_service


def test_default_provider_is_azure():
    """The factory returns the Azure adapter — it is the only provider."""
    assert isinstance(get_tts_service(), AzureTTSService)


def test_both_providers_satisfy_the_port():
    """The returned adapter is interchangeable behind TTSService.

    This is what lets renderer.py and slicer.py stay ignorant of the adapter.
    """
    assert isinstance(get_tts_service(), TTSService)


def test_cache_dir_reaches_either_adapter(tmp_path):
    """The file-cache contract reaches the adapter."""
    svc = get_tts_service(cache_dir=tmp_path / "cache")
    assert svc._cache_dir == tmp_path / "cache"
