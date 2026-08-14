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
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.api import srs as api_srs
from app.config import ANKI_ROLLOVER_HOUR
from app.models import srs_item
from app.plugins.anki_sync import sqlite_reader, sync_common
from app.plugins.anki_sync.sync_common import _local_today_4am
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
    # NAIVE datetimes on purpose: the rollover is a LOCAL wall-clock concept, so
    # "one hour after the rollover" must be expressed in local time. These used
    # to be tzinfo=UTC, which asserted that an aware `now` supplies its OWN
    # arithmetic zone — a contract no production call site uses (all pass
    # now=None) and one that disagreed with anki_day_bounds_utc_dt. See
    # rollover._local_now (tunatale-vnf.5).
    after = _local_today_4am(datetime(2026, 1, 2, ANKI_ROLLOVER_HOUR + 1))
    before = _local_today_4am(datetime(2026, 1, 2, ANKI_ROLLOVER_HOUR - 1))
    assert after.hour == ANKI_ROLLOVER_HOUR
    assert before.hour == ANKI_ROLLOVER_HOUR
    assert before.date() < after.date()


def test_helpers_are_single_sourced_in_rollover_module():
    # Identity, not equality: the legacy names must be the same function
    # objects, so a future edit lands in exactly one place.
    assert sync_common._local_today_4am is rollover.local_today_rollover
    assert database_mod._anki_day_bounds_utc is rollover.anki_day_bounds_utc


def test_anki_today_flips_at_rollover():
    # Naive = local wall-clock, for the reason spelled out in
    # test_local_today_4am_anchors_on_rollover_hour above.
    after = rollover.anki_today(datetime(2026, 1, 2, ANKI_ROLLOVER_HOUR + 1))
    before = rollover.anki_today(datetime(2026, 1, 2, ANKI_ROLLOVER_HOUR - 1))
    assert after == date(2026, 1, 2)
    assert before == date(2026, 1, 1)


def test_local_today_rollover_accepts_naive_now():
    anchored = rollover.local_today_rollover(datetime(2026, 1, 2, ANKI_ROLLOVER_HOUR + 1))
    assert anchored.hour == ANKI_ROLLOVER_HOUR
    assert anchored.tzinfo is not None


def test_due_at_rollover_utc_convention():
    expected = datetime(2026, 1, 2, ANKI_ROLLOVER_HOUR, tzinfo=UTC)
    assert rollover.due_at_rollover_utc(date(2026, 1, 2)) == expected


def test_anki_day_bounds_utc_dt_matches_string_variant():
    """`anki_day_bounds_utc` must be a thin isoformat wrapper around
    `anki_day_bounds_utc_dt` — single-sourced arithmetic, two return shapes."""
    today = date(2026, 5, 8)
    now = datetime(2026, 5, 8, 16, 0, tzinfo=UTC)
    start_dt, end_dt = rollover.anki_day_bounds_utc_dt(today, now=now)
    start_iso, end_iso = rollover.anki_day_bounds_utc(today, now=now)
    assert (start_dt.isoformat(), end_dt.isoformat()) == (start_iso, end_iso)


def test_anki_day_bounds_utc_dt_shifts_back_before_rollover(monkeypatch):
    """Regression (docs/master-cleanup-list item 1): the datetime-returning
    variant used by api/srs.py's grade-eligibility / touched-today windows
    must shift the anchor back a day when `now` precedes today's rollover,
    exactly like the ISO-string variant already does. Forces TZ so the
    result is deterministic regardless of the host's real timezone (the
    function always anchors on the REAL system-local tz, not `now`'s own
    tzinfo — matching `test_anki_day_bounds_shifts_back_before_rollover` in
    test_srs_database.py)."""
    import time as _time

    monkeypatch.setenv("TZ", "America/Los_Angeles")
    _time.tzset()
    today = date(2026, 5, 8)

    # 4 AM PDT == 11:00 UTC. 02:00 PDT (before rollover) → anchored on May 7.
    start_before, end_before = rollover.anki_day_bounds_utc_dt(today, now=datetime(2026, 5, 8, 9, 0, tzinfo=UTC))
    assert start_before == datetime(2026, 5, 7, 11, 0, tzinfo=UTC)
    assert end_before == datetime(2026, 5, 8, 11, 0, tzinfo=UTC)

    # 09:00 PDT (after rollover) → anchored on May 8 itself.
    start_after, end_after = rollover.anki_day_bounds_utc_dt(today, now=datetime(2026, 5, 8, 16, 0, tzinfo=UTC))
    assert start_after == datetime(2026, 5, 8, 11, 0, tzinfo=UTC)
    assert end_after == datetime(2026, 5, 9, 11, 0, tzinfo=UTC)


def test_no_hardcoded_rollover_literals_remain():
    # Ratchet: new day-boundary code must route through app.srs.anki_mirror.rollover
    # (or take the constant), never re-hardcode the hour.
    for mod in (database_mod, sync_common, fsrs, sqlite_reader, srs_item, api_srs, protobuf_wire):
        src = inspect.getsource(mod)
        assert "time(4," not in src, mod.__name__
        assert "rollover_hour: int = 4" not in src, mod.__name__


# ── zone consistency between the two entry points (tunatale-vnf.5) ────────────


@pytest.mark.parametrize("offset_hours", [-11, -6, -1, 0, 1, 6, 11])
def test_day_label_and_window_agree_for_any_aware_now(offset_hours):
    """`anki_today(now)` must name the day whose bounds actually contain `now`.

    These two are always used together — `anki_day_bounds_utc_dt(anki_today(now),
    now)` is the idiom at every call site — so a disagreement means the day LABEL
    comes from one clock and the WINDOW from another.

    They used to disagree: `anki_day_bounds_utc_dt` converts `now` to local,
    while `local_today_rollover` promoted only NAIVE input and otherwise kept
    `now.tzinfo`, so an aware `now` in some other zone got that zone's day
    arithmetic and local's window. Parametrized across offsets because the
    failure is invisible at offsets near the host's — which is exactly why it
    survived (it produced the tunatale-3oz fixture bug and nothing else caught
    it).
    """
    tz = timezone(timedelta(hours=offset_hours))
    now = datetime(2026, 5, 8, 2, 0, tzinfo=tz)

    day = rollover.anki_today(now)
    start, end = rollover.anki_day_bounds_utc_dt(day, now)

    assert start <= now.astimezone(UTC) < end, (
        f"anki_today said {day}, but that day's window is [{start}, {end}) "
        f"which does not contain now={now.astimezone(UTC)}"
    )


def test_aware_now_gives_the_same_day_as_the_equivalent_local_wall_clock():
    """The rollover is a LOCAL wall-clock concept, so only the instant matters.

    Two datetimes naming the SAME instant in different zones must produce the
    same Anki day. Under the old code the answer depended on which zone the
    caller happened to express the instant in, which is the bug in one line.
    """
    instant_utc = datetime(2026, 5, 8, 2, 0, tzinfo=UTC)
    same_instant_elsewhere = instant_utc.astimezone(timezone(timedelta(hours=9)))
    assert instant_utc == same_instant_elsewhere, "precondition: same instant, different zone"

    assert rollover.anki_today(instant_utc) == rollover.anki_today(same_instant_elsewhere)
