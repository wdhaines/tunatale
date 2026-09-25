"""TTS adapter construction — two providers behind one router.

There is deliberately still no fallback path here. The router picks an
adapter from the voice id; it does not try the other one when the first
fails, and ``AzureTTSService._require_credentials`` explains why an automatic
swap would be a regression rather than resilience.
"""

from __future__ import annotations

from app.audio.azure_tts import AzureTTSService
from app.audio.char_ledger import AzureCharacterLedger
from app.audio.gemini_tts import GeminiTTSService
from app.audio.ports import TTSService
from app.audio.tts_factory import get_tts_service
from app.audio.tts_router import RoutingTTSService


def test_the_factory_returns_the_router_over_both_adapters():
    """One service, two providers, chosen by voice id — not a provider switch."""
    svc = get_tts_service()

    assert isinstance(svc, RoutingTTSService)
    assert isinstance(svc._azure, AzureTTSService)
    assert isinstance(svc._gemini, GeminiTTSService)


def test_the_router_satisfies_the_port():
    """The returned service is interchangeable behind TTSService.

    This is what lets renderer.py and slicer.py stay ignorant of the adapter.
    """
    assert isinstance(get_tts_service(), TTSService)


def test_the_character_ledger_stays_on_azure():
    """The ledger counts Azure spend, which is a per-provider fact.

    A Gemini clip is not billed through that ledger, and attaching the Azure
    ledger to the Gemini adapter would be counting characters against the
    wrong provider's ceiling.
    """
    svc = get_tts_service()

    assert isinstance(svc._azure._ledger, AzureCharacterLedger)
    assert getattr(svc._gemini, "_ledger", None) is None


def test_the_cache_dir_reaches_both_adapters(tmp_path):
    """One shared cache directory, two key spaces inside it."""
    cache = tmp_path / "cache"

    svc = get_tts_service(cache_dir=cache)

    assert svc._cache_dir == cache
    assert svc._azure._cache_dir == cache
    assert svc._gemini._cache_dir == cache
