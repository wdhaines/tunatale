"""Tests for resolve_daily_new_cap() — cache-driven resolver chain."""

from __future__ import annotations

from app.srs.database import SRSDatabase
from app.srs.queue_stats import resolve_daily_new_cap


def _make_db() -> SRSDatabase:
    return SRSDatabase(":memory:")


def test_returns_cache_source_when_cache_present():
    db = _make_db()
    db.set_anki_state_cache("daily_new_cap", "30")
    cap, source = resolve_daily_new_cap(db)
    assert cap == 30
    assert source == "cache"


def test_falls_back_to_config_when_no_cache(monkeypatch):
    from app.srs import queue_stats

    db = _make_db()
    monkeypatch.setattr(queue_stats.settings, "anki_new_per_day_default", 25)
    cap, source = resolve_daily_new_cap(db)
    assert cap == 25
    assert source == "config"


def test_falls_back_to_default_when_config_zero(monkeypatch):
    from app.srs import queue_stats

    db = _make_db()
    monkeypatch.setattr(queue_stats.settings, "anki_new_per_day_default", 0)
    cap, source = resolve_daily_new_cap(db)
    assert cap == 20
    assert source == "default"
