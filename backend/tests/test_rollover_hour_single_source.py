"""ANKI_ROLLOVER_HOUR — single source for Anki's 4 AM study-day rollover.

The rollover hour and its arithmetic used to be reimplemented per module:
database `_anki_day_bounds_utc`, sync_common `_local_today_4am`, hardcoded
`rollover_hour: int = 4` signature defaults in fsrs/sqlite_reader, and eight
`time(4, 0)` due_at literals across five files. `app.srs.anki_mirror.rollover` is now the
single home. This pins the canonical constant (`app.config`), the helper
identities (the old names must BE the rollover-module functions, not copies),
and a source-level ratchet against new hardcoded literals.
"""

import inspect
from datetime import UTC, date, datetime

from app.anki import sqlite_reader, sync_common
from app.anki.sync_common import _local_today_4am
from app.api import srs as api_srs
from app.config import ANKI_ROLLOVER_HOUR
from app.models import srs_item
from app.srs import database as database_mod
from app.srs import fsrs
from app.srs.anki_mirror import protobuf_wire, rollover
from app.srs.anki_mirror.protobuf_wire import review_due_at_for_col_day


def test_canonical_value_and_home():
    assert ANKI_ROLLOVER_HOUR == 4
    # rollover module references the shared constant, not a private copy.
    assert rollover.ANKI_ROLLOVER_HOUR == ANKI_ROLLOVER_HOUR


def test_review_due_surfaces_at_rollover_hour_utc():
    dt = review_due_at_for_col_day(col_crt=0, col_day=0)
    assert dt.hour == ANKI_ROLLOVER_HOUR
    assert dt.tzinfo == UTC


def test_local_today_4am_anchors_on_rollover_hour():
    # Just after the rollover → today's rollover; just before → yesterday's.
    after = _local_today_4am(datetime(2026, 1, 2, ANKI_ROLLOVER_HOUR + 1, tzinfo=UTC))
    before = _local_today_4am(datetime(2026, 1, 2, ANKI_ROLLOVER_HOUR - 1, tzinfo=UTC))
    assert after.hour == ANKI_ROLLOVER_HOUR
    assert before.hour == ANKI_ROLLOVER_HOUR
    assert before.date() < after.date()


def test_helpers_are_single_sourced_in_rollover_module():
    # Identity, not equality: the legacy names must be the same function
    # objects, so a future edit lands in exactly one place.
    assert sync_common._local_today_4am is rollover.local_today_rollover
    assert database_mod._anki_day_bounds_utc is rollover.anki_day_bounds_utc


def test_anki_today_flips_at_rollover():
    after = rollover.anki_today(datetime(2026, 1, 2, ANKI_ROLLOVER_HOUR + 1, tzinfo=UTC))
    before = rollover.anki_today(datetime(2026, 1, 2, ANKI_ROLLOVER_HOUR - 1, tzinfo=UTC))
    assert after == date(2026, 1, 2)
    assert before == date(2026, 1, 1)


def test_local_today_rollover_accepts_naive_now():
    anchored = rollover.local_today_rollover(datetime(2026, 1, 2, ANKI_ROLLOVER_HOUR + 1))
    assert anchored.hour == ANKI_ROLLOVER_HOUR
    assert anchored.tzinfo is not None


def test_due_at_rollover_utc_convention():
    expected = datetime(2026, 1, 2, ANKI_ROLLOVER_HOUR, tzinfo=UTC)
    assert rollover.due_at_rollover_utc(date(2026, 1, 2)) == expected


def test_no_hardcoded_rollover_literals_remain():
    # Ratchet: new day-boundary code must route through app.srs.anki_mirror.rollover
    # (or take the constant), never re-hardcode the hour.
    for mod in (database_mod, sync_common, fsrs, sqlite_reader, srs_item, api_srs, protobuf_wire):
        src = inspect.getsource(mod)
        assert "time(4," not in src, mod.__name__
        assert "rollover_hour: int = 4" not in src, mod.__name__
