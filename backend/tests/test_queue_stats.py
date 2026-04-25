"""Tests for the resolve_daily_new_cap() resolver chain."""

from unittest.mock import MagicMock, patch

from app.anki.anki_connect import AnkiConnectUnavailable
from app.srs.queue_stats import resolve_daily_new_cap


def _patch_anki(config_result=None, raises=None):
    """Return a context-manager patch for AnkiConnectClient."""
    mock_client = MagicMock()
    if raises:
        mock_client.get_deck_config.side_effect = raises
    else:
        mock_client.get_deck_config.return_value = config_result

    return patch("app.srs.queue_stats.AnkiConnectClient", return_value=mock_client)


def test_returns_anki_source_when_available():
    config = {"new": {"perDay": 30}}
    with _patch_anki(config_result=config):
        cap, source = resolve_daily_new_cap()
    assert cap == 30
    assert source == "anki"


def test_falls_back_to_config_on_unavailable():
    with _patch_anki(raises=AnkiConnectUnavailable("refused")), patch("app.srs.queue_stats.settings") as mock_settings:
        mock_settings.anki_connect_url = "http://127.0.0.1:8765"
        mock_settings.anki_deck_name = "0. Slovene"
        mock_settings.anki_new_per_day_default = 25
        cap, source = resolve_daily_new_cap()
    assert cap == 25
    assert source == "config"


def test_falls_back_to_default_when_config_key_missing():
    config = {"new": {}}  # missing perDay
    with _patch_anki(config_result=config):
        cap, source = resolve_daily_new_cap()
    assert cap == 20
    assert source == "default"


def test_falls_back_to_default_on_key_error_in_top_level():
    config = {}  # missing "new" key entirely
    with _patch_anki(config_result=config):
        cap, source = resolve_daily_new_cap()
    assert cap == 20
    assert source == "default"
