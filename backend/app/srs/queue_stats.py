"""Resolve the daily new-card cap from the Anki state cache or config fallbacks."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    from app.srs.database import SRSDatabase

_CACHE_MAX_AGE_DAYS = 30


def _read_new_per_day_from_anki(conn: sqlite3.Connection, deck_name: str) -> int | None:
    """Return new-cards-per-day from Anki's deck config, or None if unavailable."""
    row = conn.execute("SELECT decks, dconf FROM col LIMIT 1").fetchone()
    if row is None:
        return None

    try:
        decks = json.loads(row[0] or "{}")
        dconf_json = json.loads(row[1] or "{}")
    except (json.JSONDecodeError, TypeError):
        return None

    deck_info = next(
        (v for v in decks.values() if isinstance(v, dict) and v.get("name") == deck_name),
        None,
    )
    if deck_info is None:
        return None

    conf_id = str(deck_info.get("conf", 1))
    deck_conf = dconf_json.get(conf_id)
    if not isinstance(deck_conf, dict):
        return None

    try:
        return int(deck_conf["new"]["perDay"])
    except (KeyError, TypeError, ValueError):
        return None


def _refresh_daily_new_cap(db: SRSDatabase, conn: sqlite3.Connection, deck_name: str) -> None:
    """Read the new-per-day cap from collection.anki2 and write it to the cache."""
    cap = _read_new_per_day_from_anki(conn, deck_name)
    if cap is not None:
        db.set_anki_state_cache("daily_new_cap", str(cap))


def resolve_daily_new_cap(db: SRSDatabase | None = None) -> tuple[int, str]:
    """Return (cap, source) where source is 'cache', 'config', or 'default'.

    Priority:
    1. anki_state_cache (written during sync) — 'cache'
    2. settings.anki_new_per_day_default — 'config'
    3. Hard default 20 — 'default'
    """
    if db is None:
        try:
            from app.srs.database import SRSDatabase

            db = SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
        except Exception:
            db = None

    if db is not None:
        row = db.get_anki_state_cache("daily_new_cap")
        if row is not None:
            value_str, updated_at = row
            try:
                age = datetime.now(UTC) - datetime.fromisoformat(updated_at).replace(tzinfo=UTC)
                if age < timedelta(days=_CACHE_MAX_AGE_DAYS):
                    return (int(value_str), "cache")
            except (ValueError, TypeError, OverflowError):
                pass

    config_default = getattr(settings, "anki_new_per_day_default", 0)
    if config_default:
        return (config_default, "config")

    return (20, "default")
