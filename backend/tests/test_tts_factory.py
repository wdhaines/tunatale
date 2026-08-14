"""TTS provider selection — the explicit switch between Azure and Edge.

The switch is a human decision recorded in settings. There is deliberately no
runtime fallback path here: see AzureTTSService._require_credentials for why an
automatic swap is a regression rather than resilience.
"""

from __future__ import annotations

import pytest

from app.audio.azure_tts import AzureTTSService
from app.audio.edge_tts import EdgeTTSService
from app.audio.ports import TTSService
from app.audio.tts_factory import get_tts_service
from app.config import settings


def test_default_provider_is_azure(monkeypatch):
    """Azure is the default — the port's whole point is not using Edge."""
    monkeypatch.setattr(settings, "tts_provider", "azure")
    assert isinstance(get_tts_service(), AzureTTSService)


def test_edge_provider_selected_explicitly(monkeypatch):
    """TTS_PROVIDER=edge still yields the Edge adapter (tunatale-i69 retires it)."""
    monkeypatch.setattr(settings, "tts_provider", "edge")
    assert isinstance(get_tts_service(), EdgeTTSService)


def test_both_providers_satisfy_the_port(monkeypatch):
    """Whichever is selected is interchangeable behind TTSService.

    This is what lets renderer.py and slicer.py stay ignorant of the provider.
    """
    for provider in ("azure", "edge"):
        monkeypatch.setattr(settings, "tts_provider", provider)
        assert isinstance(get_tts_service(), TTSService)


def test_unknown_provider_raises_and_lists_the_valid_ones(monkeypatch):
    """A typo in .env fails loudly instead of silently falling back.

    Silently defaulting on an unrecognised value is how you render half a
    curriculum with the wrong provider and never find out.
    """
    monkeypatch.setattr(settings, "tts_provider", "azrue")
    with pytest.raises(ValueError, match="azrue") as exc:
        get_tts_service()
    assert "azure" in str(exc.value) and "edge" in str(exc.value)


def test_explicit_argument_overrides_the_setting(monkeypatch):
    """Callers can pin a provider without touching global settings."""
    monkeypatch.setattr(settings, "tts_provider", "edge")
    assert isinstance(get_tts_service(provider="azure"), AzureTTSService)


@pytest.mark.parametrize("provider", ["azure", "edge"])
def test_cache_dir_reaches_either_adapter(monkeypatch, tmp_path, provider):
    """Both adapters share the same file-cache contract."""
    monkeypatch.setattr(settings, "tts_provider", provider)
    svc = get_tts_service(cache_dir=tmp_path / "cache")
    assert svc._cache_dir == tmp_path / "cache"
