"""Resolve the daily new-card cap from Anki or config fallbacks."""

from __future__ import annotations

from app.anki.anki_connect import AnkiConnectClient, AnkiConnectUnavailable
from app.config import settings


def resolve_daily_new_cap() -> tuple[int, str]:
    """Return (cap, source) where source is 'anki', 'config', or 'default'."""
    try:
        client = AnkiConnectClient(settings.anki_connect_url)
        config = client.get_deck_config(settings.anki_deck_name)
        cap = config["new"]["perDay"]
        return (cap, "anki")
    except AnkiConnectUnavailable:
        return (settings.anki_new_per_day_default, "config")
    except (KeyError, TypeError):
        return (20, "default")
