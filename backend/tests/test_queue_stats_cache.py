"""Tests for Change 4: cache-driven daily-new-cap (no AnkiConnect)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from app.srs.database import SRSDatabase
from app.srs.queue_stats import _refresh_daily_new_cap, resolve_daily_new_cap


def _make_db() -> SRSDatabase:
    return SRSDatabase(":memory:")


def _make_anki_conn(new_per_day: int = 20, deck_name: str = "0. Slovene") -> sqlite3.Connection:
    """Build a minimal in-memory collection.anki2 with a deck config."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    deck_id = 12345
    dconf_id = 1

    # Build col.dconf JSON (legacy format)
    dconf_json = json.dumps(
        {
            str(dconf_id): {
                "id": dconf_id,
                "name": "Default",
                "new": {"perDay": new_per_day, "order": 0},
            }
        }
    )
    decks_json = json.dumps(
        {
            str(deck_id): {
                "id": deck_id,
                "name": deck_name,
                "conf": dconf_id,
            }
        }
    )

    conn.execute(
        "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
        "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
        "decks TEXT, dconf TEXT, tags TEXT)"
    )
    conn.execute(
        "INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', ?, ?, '{}')",
        (decks_json, dconf_json),
    )
    conn.commit()
    return conn


class TestRefreshDailyNewCap:
    def test_reads_new_per_day_from_legacy_dconf(self):
        db = _make_db()
        conn = _make_anki_conn(new_per_day=30)
        _refresh_daily_new_cap(db, conn, "0. Slovene")

        row = db.get_anki_state_cache("daily_new_cap")
        assert row is not None
        value, _ = row
        assert int(value) == 30

    def test_no_error_when_deck_not_found(self):
        db = _make_db()
        conn = _make_anki_conn()
        _refresh_daily_new_cap(db, conn, "No Such Deck")  # must not raise

    def test_no_error_when_dconf_empty(self):
        db = _make_db()
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        conn.execute("INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', '{}', '{}', '{}')")
        conn.commit()
        _refresh_daily_new_cap(db, conn, "0. Slovene")  # must not raise


class TestResolveDailyNewCapCache:
    def test_returns_cache_when_fresh(self):
        db = _make_db()
        db.set_anki_state_cache("daily_new_cap", "35")
        cap, source = resolve_daily_new_cap(db)
        assert cap == 35
        assert source == "cache"

    def test_returns_config_when_cache_missing(self, monkeypatch):
        from app.srs import queue_stats

        db = _make_db()
        monkeypatch.setattr(queue_stats.settings, "anki_new_per_day_default", 25)
        cap, source = resolve_daily_new_cap(db)
        assert cap == 25
        assert source == "config"

    def test_returns_config_when_cache_stale(self, monkeypatch):
        from app.srs import queue_stats

        db = _make_db()
        # Write a cache entry timestamped 31 days ago
        old_ts = (datetime.now(UTC) - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
        db._conn.execute(
            "INSERT INTO anki_state_cache (key, value, updated_at) VALUES (?, ?, ?)",
            ("daily_new_cap", "99", old_ts),
        )
        db._conn.commit()
        monkeypatch.setattr(queue_stats.settings, "anki_new_per_day_default", 25)
        cap, source = resolve_daily_new_cap(db)
        assert source == "config"

    def test_returns_default_when_no_config(self, monkeypatch):
        from app.srs import queue_stats

        db = _make_db()
        monkeypatch.setattr(queue_stats.settings, "anki_new_per_day_default", 0)
        cap, source = resolve_daily_new_cap(db)
        assert cap == 20
        assert source == "default"

    def test_works_without_db_arg_falls_back_to_config(self, monkeypatch):
        from app.srs import queue_stats

        monkeypatch.setattr(queue_stats.settings, "anki_new_per_day_default", 15)
        monkeypatch.setattr(queue_stats.settings, "database_url", "sqlite:///:memory:")
        cap, source = resolve_daily_new_cap()
        assert source in ("config", "default")


class TestReadNewPerDayFromAnki:
    def test_returns_none_when_no_col_row(self):
        from app.srs.queue_stats import _read_new_per_day_from_anki

        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        conn.commit()
        assert _read_new_per_day_from_anki(conn, "0. Slovene") is None

    def test_returns_none_on_invalid_json(self):
        from app.srs.queue_stats import _read_new_per_day_from_anki

        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        conn.execute("INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', '{not json}', '{}', '{}')")
        conn.commit()
        assert _read_new_per_day_from_anki(conn, "0. Slovene") is None

    def test_returns_none_when_dconf_not_dict(self):
        from app.srs.queue_stats import _read_new_per_day_from_anki

        decks = json.dumps({"1": {"id": 1, "name": "0. Slovene", "conf": 99}})
        dconf = json.dumps({"99": "not-a-dict"})
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        conn.execute(
            "INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', ?, ?, '{}')",
            (decks, dconf),
        )
        conn.commit()
        assert _read_new_per_day_from_anki(conn, "0. Slovene") is None

    def test_returns_none_when_new_per_day_key_missing(self):
        from app.srs.queue_stats import _read_new_per_day_from_anki

        decks = json.dumps({"1": {"id": 1, "name": "0. Slovene", "conf": 1}})
        dconf = json.dumps({"1": {"id": 1, "name": "Default", "new": {}}})  # no perDay
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
            "decks TEXT, dconf TEXT, tags TEXT)"
        )
        conn.execute(
            "INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', ?, ?, '{}')",
            (decks, dconf),
        )
        conn.commit()
        assert _read_new_per_day_from_anki(conn, "0. Slovene") is None


def test_resolve_daily_new_cap_when_db_creation_fails(monkeypatch):
    """When no db passed and SRSDatabase creation raises, fall back to config."""
    from app.srs import queue_stats

    monkeypatch.setattr(queue_stats.settings, "anki_new_per_day_default", 18)
    monkeypatch.setattr(queue_stats.settings, "database_url", "sqlite:////__invalid/path/db.sqlite")
    cap, source = resolve_daily_new_cap()
    assert source in ("config", "default")


def test_resolve_falls_back_when_cache_has_invalid_timestamp(monkeypatch):
    """Corrupted updated_at in cache → skip cache, use config."""
    from app.srs import queue_stats

    db = _make_db()
    db._conn.execute(
        "INSERT INTO anki_state_cache (key, value, updated_at) VALUES (?, ?, ?)",
        ("daily_new_cap", "42", "not-a-valid-timestamp"),
    )
    db._conn.commit()
    monkeypatch.setattr(queue_stats.settings, "anki_new_per_day_default", 18)
    cap, source = resolve_daily_new_cap(db)
    assert source in ("config", "default")
