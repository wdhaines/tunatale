"""Config readers for the FSRS load balancer (Layer 55).

`loadBalancerEnabled` is a global collection-config bool (config table, like
`fsrsShortTermWithStepsEnabled`). `easy_days_percentages` is a per-preset
repeated float (deck_config protobuf field 4, same packed-float encoding as
learn/relearn steps). Both are read at sync time and cached so the live grade
path never opens collection.anki2.
"""

from __future__ import annotations

import logging
import sqlite3
import struct
from datetime import UTC, datetime

from app.anki.protobuf_wire import compute_anki_day_index
from app.srs.database import SRSDatabase
from app.srs.load_balancer import LOAD_BALANCE_DAYS
from app.srs.queue_stats import (
    _EASY_DAYS_FIELD,
    _read_easy_days_from_deck_config_table,
    _read_load_balancer_enabled_from_config_table,
    build_live_load_balancer,
    refresh_easy_days,
    refresh_load_balancer_enabled,
    resolve_easy_days,
    resolve_load_balancer_enabled,
    warn_if_multi_deck_preset,
)
from tests._helpers.protobuf import encode_varint, pb_len_field, pb_varint_field

# A fixed crt + now so compute_anki_day_index is deterministic across the test run.
_COL_CRT = 1_700_000_000
_NOW = datetime(2026, 5, 24, 18, 0, tzinfo=UTC)
_TODAY = compute_anki_day_index(_COL_CRT, 4, _NOW)


def _insert_direction(
    db: SRSDatabase,
    *,
    coll_id: int,
    note_id: int,
    card_id: int,
    anki_due: int | None,
    direction: str = "recognition",
    dirty_fsrs: int = 0,
    state: str = "review",
) -> None:
    with db._get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO collocations (id, text, anki_note_id) VALUES (?, ?, ?)",
            (coll_id, f"c{coll_id}", note_id),
        )
        conn.execute(
            "INSERT INTO collocation_directions "
            "(collocation_id, direction, due_at, anki_card_id, anki_due, dirty_fsrs, state) "
            "VALUES (?, ?, '2026-01-01T00:00:00+00:00', ?, ?, ?, ?)",
            (coll_id, direction, card_id, anki_due, dirty_fsrs, state),
        )
        conn.commit()


def _insert_revlog(db: SRSDatabase, *, rid: int, coll_id: int, direction: str, interval: int, card_id: int) -> None:
    with db._get_conn() as conn:
        conn.execute(
            "INSERT INTO tt_revlog "
            "(id, collocation_id, direction, button_chosen, interval, last_interval, factor, "
            "taken_millis, review_kind, anki_card_id) VALUES (?, ?, ?, 3, ?, 1, 0, 0, 1, ?)",
            (rid, coll_id, direction, interval, card_id),
        )
        conn.commit()


def _make_config_conn(value: bytes | None):
    """Anki connection with a `config` table; optionally set loadBalancerEnabled."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE config (key TEXT PRIMARY KEY, val BLOB, mtime_secs INT, usn INT)")
    if value is not None:
        conn.execute("INSERT INTO config (key, val) VALUES ('loadBalancerEnabled', ?)", (value,))
    conn.commit()
    return conn


def _make_deck_config_blob_with_easy_days(easy_days):
    blob = b""
    if easy_days is not None:
        payload = struct.pack(f"<{len(easy_days)}f", *easy_days)
        tag = encode_varint((_EASY_DAYS_FIELD << 3) | 2)
        blob += tag + encode_varint(len(payload)) + payload
    return blob


def _make_modern_anki_conn_with_easy_days(deck_name="0. Slovene", easy_days=None):
    """Modern Anki connection with easy_days_percentages in deck_config."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    config_id = 1774580286260
    deck_id = 12345

    conn.execute(
        "CREATE TABLE deck_config (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB)"
    )
    conn.execute(
        "INSERT INTO deck_config VALUES (?, ?, 0, -1, ?)",
        (config_id, "Slovene", _make_deck_config_blob_with_easy_days(easy_days)),
    )
    conn.execute(
        "CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, "
        "usn INTEGER, common BLOB, kind BLOB)"
    )
    inner = pb_varint_field(1, config_id)
    conn.execute(
        "INSERT INTO decks VALUES (?, ?, 0, -1, NULL, ?)",
        (deck_id, deck_name, pb_len_field(1, inner)),
    )
    conn.commit()
    return conn


class TestEasyDaysField:
    def test_field_is_4(self):
        # repeated float easy_days_percentages = 4; (deck_config.proto:190)
        assert _EASY_DAYS_FIELD == 4


class TestLoadBalancerEnabledReader:
    def test_reads_true(self):
        conn = _make_config_conn(b"true")
        assert _read_load_balancer_enabled_from_config_table(conn) is True
        conn.close()

    def test_reads_false(self):
        conn = _make_config_conn(b"false")
        assert _read_load_balancer_enabled_from_config_table(conn) is False
        conn.close()

    def test_missing_key_returns_none(self):
        conn = _make_config_conn(None)
        assert _read_load_balancer_enabled_from_config_table(conn) is None
        conn.close()

    def test_no_config_table_returns_none(self):
        conn = sqlite3.connect(":memory:")
        assert _read_load_balancer_enabled_from_config_table(conn) is None
        conn.close()

    def test_refresh_then_resolve(self):
        conn = _make_config_conn(b"true")
        db = SRSDatabase(":memory:")
        refresh_load_balancer_enabled(db, conn)
        assert resolve_load_balancer_enabled(db) is True
        conn.close()

    def test_refresh_false_then_resolve(self):
        conn = _make_config_conn(b"false")
        db = SRSDatabase(":memory:")
        refresh_load_balancer_enabled(db, conn)
        assert resolve_load_balancer_enabled(db) is False
        conn.close()

    def test_refresh_noop_when_key_absent(self):
        conn = _make_config_conn(None)
        db = SRSDatabase(":memory:")
        refresh_load_balancer_enabled(db, conn)
        # Nothing cached → resolve falls back to default (False).
        assert resolve_load_balancer_enabled(db) is False
        conn.close()

    def test_resolve_defaults_false(self):
        assert resolve_load_balancer_enabled(SRSDatabase(":memory:")) is False

    def test_resolve_db_none_creation_fails(self, monkeypatch):
        monkeypatch.setattr(
            "app.srs.database.SRSDatabase.__init__",
            lambda self, x: (_ for _ in ()).throw(Exception("boom")),
        )
        assert resolve_load_balancer_enabled(None) is False


class TestEasyDaysReader:
    def test_reads_seven_floats(self):
        days = [1.0, 0.0, 0.5, 1.0, 1.0, 1.0, 1.0]
        conn = _make_modern_anki_conn_with_easy_days(easy_days=days)
        out = _read_easy_days_from_deck_config_table(conn, "0. Slovene")
        assert out == days
        conn.close()

    def test_absent_returns_none(self):
        conn = _make_modern_anki_conn_with_easy_days(easy_days=None)
        assert _read_easy_days_from_deck_config_table(conn, "0. Slovene") is None
        conn.close()

    def test_no_tables_returns_none(self):
        conn = sqlite3.connect(":memory:")
        assert _read_easy_days_from_deck_config_table(conn, "0. Slovene") is None
        conn.close()

    def test_unknown_deck_returns_none(self):
        conn = _make_modern_anki_conn_with_easy_days(easy_days=[1.0] * 7)
        assert _read_easy_days_from_deck_config_table(conn, "Nonexistent") is None
        conn.close()

    def test_refresh_then_resolve(self):
        days = [1.0, 1.0, 0.0, 1.0, 1.0, 0.5, 1.0]
        conn = _make_modern_anki_conn_with_easy_days(easy_days=days)
        db = SRSDatabase(":memory:")
        refresh_easy_days(db, conn, "0. Slovene")
        assert resolve_easy_days(db) == days
        conn.close()

    def test_refresh_noop_when_absent(self):
        conn = _make_modern_anki_conn_with_easy_days(easy_days=None)
        db = SRSDatabase(":memory:")
        refresh_easy_days(db, conn, "0. Slovene")
        assert resolve_easy_days(db) is None
        conn.close()

    def test_resolve_defaults_none(self):
        assert resolve_easy_days(SRSDatabase(":memory:")) is None

    def test_resolve_db_none_creation_fails(self, monkeypatch):
        monkeypatch.setattr(
            "app.srs.database.SRSDatabase.__init__",
            lambda self, x: (_ for _ in ()).throw(Exception("boom")),
        )
        assert resolve_easy_days(None) is None


class TestLoadBalancerDBHelpers:
    def test_histogram_buckets_in_range_only(self):
        db = SRSDatabase(":memory:")
        # In range: today+0, today+5, today+98. Out: today-1, today+99, NULL.
        _insert_direction(db, coll_id=1, note_id=10, card_id=100, anki_due=_TODAY)
        _insert_direction(db, coll_id=2, note_id=20, card_id=200, anki_due=_TODAY + 5)
        _insert_direction(db, coll_id=3, note_id=30, card_id=300, anki_due=_TODAY + 98)
        _insert_direction(db, coll_id=4, note_id=40, card_id=400, anki_due=_TODAY - 1)
        _insert_direction(db, coll_id=5, note_id=50, card_id=500, anki_due=_TODAY + 99)
        _insert_direction(db, coll_id=6, note_id=60, card_id=600, anki_due=None)
        rows = db.get_load_balancer_histogram(_TODAY, LOAD_BALANCE_DAYS)
        dues = sorted(due for _, _, due in rows)
        assert dues == [_TODAY, _TODAY + 5, _TODAY + 98]

    def test_histogram_skips_unsynced(self):
        db = SRSDatabase(":memory:")
        with db._get_conn() as conn:
            conn.execute("INSERT INTO collocations (id, text, anki_note_id) VALUES (1, 'x', NULL)")
            conn.execute(
                "INSERT INTO collocation_directions "
                "(collocation_id, direction, due_at, anki_card_id, anki_due, state) "
                "VALUES (1, 'recognition', '2026-01-01T00:00:00+00:00', NULL, ?, 'review')",
                (_TODAY + 1,),
            )
            conn.commit()
        assert db.get_load_balancer_histogram(_TODAY, LOAD_BALANCE_DAYS) == []

    def test_session_replay_uses_latest_dirty_interval(self):
        db = SRSDatabase(":memory:")
        _insert_direction(db, coll_id=1, note_id=10, card_id=100, anki_due=_TODAY, dirty_fsrs=1)
        # two grades; the latest (higher id) interval=7 wins over the earlier interval=3
        _insert_revlog(db, rid=1000, coll_id=1, direction="recognition", interval=3, card_id=100)
        _insert_revlog(db, rid=2000, coll_id=1, direction="recognition", interval=7, card_id=100)
        # a clean direction must NOT be replayed
        _insert_direction(db, coll_id=2, note_id=20, card_id=200, anki_due=_TODAY, dirty_fsrs=0)
        _insert_revlog(db, rid=3000, coll_id=2, direction="recognition", interval=9, card_id=200)
        rows = db.get_load_balancer_session_replay()
        assert rows == [(100, 10, 7)]


class TestBuildLiveLoadBalancer:
    def _enable(self, db: SRSDatabase, *, enabled: bool = True) -> None:
        db.set_anki_state_cache("load_balancer_enabled", "true" if enabled else "false")
        db.set_anki_state_cache("col_crt", str(_COL_CRT))

    def test_disabled_returns_none(self):
        db = SRSDatabase(":memory:")
        self._enable(db, enabled=False)
        assert build_live_load_balancer(db, now=_NOW) is None

    def test_no_col_crt_returns_none(self):
        db = SRSDatabase(":memory:")
        db.set_anki_state_cache("load_balancer_enabled", "true")
        # col_crt cache absent → cannot compute day index
        assert build_live_load_balancer(db, now=_NOW) is None

    def test_builds_histogram_from_anki_due(self):
        db = SRSDatabase(":memory:")
        self._enable(db)
        _insert_direction(db, coll_id=1, note_id=10, card_id=100, anki_due=_TODAY + 5)
        _insert_direction(db, coll_id=2, note_id=20, card_id=200, anki_due=_TODAY + 5)
        _insert_direction(db, coll_id=3, note_id=30, card_id=300, anki_due=_TODAY + 12)
        # Pass col_crt explicitly (the live grade path does this) — exercises the
        # branch that skips the resolve_col_crt fallback.
        lb = build_live_load_balancer(db, now=_NOW, col_crt=_COL_CRT)
        assert lb is not None
        assert len(lb.days[5].cards) == 2
        assert len(lb.days[12].cards) == 1

    def test_replays_dirty_session_grades(self):
        db = SRSDatabase(":memory:")
        self._enable(db)
        # one card due today (offset 0), graded in TT this session to interval 8.
        _insert_direction(db, coll_id=1, note_id=10, card_id=100, anki_due=_TODAY, dirty_fsrs=1)
        _insert_revlog(db, rid=1000, coll_id=1, direction="recognition", interval=8, card_id=100)
        lb = build_live_load_balancer(db, now=_NOW)
        assert lb is not None
        # stale day-0 entry (anki_due) is kept AND the new position is added (never remove)
        assert len(lb.days[0].cards) == 1
        assert len(lb.days[8].cards) == 1

    def test_passes_bury_reviews_and_easy_days(self):
        db = SRSDatabase(":memory:")
        self._enable(db)
        db.set_anki_state_cache("bury_review", "False")
        db.set_anki_state_cache("easy_days_percentages", "[1.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0]")
        lb = build_live_load_balancer(db, now=_NOW)
        assert lb is not None
        assert lb.bury_reviews is False
        assert lb.easy_days[1] != lb.easy_days[0]  # Tuesday set to minimum

    def test_default_easy_days_none(self):
        db = SRSDatabase(":memory:")
        self._enable(db)
        lb = build_live_load_balancer(db, now=_NOW)
        assert lb is not None
        assert lb.easy_days == [1.0] * 7


class TestMultiDeckPresetWarning:
    def _conn_with_decks(self, deck_confs: dict[str, int]):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, "
            "usn INTEGER, common BLOB, kind BLOB)"
        )
        for i, (name, conf_id) in enumerate(deck_confs.items(), start=1):
            kind = pb_len_field(1, pb_varint_field(1, conf_id))
            conn.execute("INSERT INTO decks VALUES (?, ?, 0, -1, NULL, ?)", (i, name, kind))
        conn.commit()
        return conn

    def test_single_deck_no_warning(self, caplog):
        conn = self._conn_with_decks({"0. Slovene": 555, "Default": 1})
        with caplog.at_level(logging.WARNING):
            warn_if_multi_deck_preset(conn, "0. Slovene")
        assert not any("preset" in r.message for r in caplog.records)
        conn.close()

    def test_two_decks_same_preset_warns(self, caplog):
        conn = self._conn_with_decks({"0. Slovene": 555, "Other": 555})
        with caplog.at_level(logging.WARNING):
            warn_if_multi_deck_preset(conn, "0. Slovene")
        assert any("preset" in r.message for r in caplog.records)
        conn.close()

    def test_unknown_deck_no_warning(self, caplog):
        conn = self._conn_with_decks({"0. Slovene": 555})
        with caplog.at_level(logging.WARNING):
            warn_if_multi_deck_preset(conn, "Nonexistent")
        assert not caplog.records
        conn.close()

    def test_no_decks_table_no_warning(self, caplog):
        conn = sqlite3.connect(":memory:")
        with caplog.at_level(logging.WARNING):
            warn_if_multi_deck_preset(conn, "0. Slovene")
        assert not caplog.records
        conn.close()
