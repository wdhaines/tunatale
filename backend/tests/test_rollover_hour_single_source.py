"""ANKI_ROLLOVER_HOUR — single source for Anki's 4 AM study-day rollover.

The rollover hour was hardcoded in four spots (database `_anki_day_bounds_utc`,
a stray `time(4, 0)`, `sync_common._local_today_4am`, and protobuf_wire's two
`rollover_hour=4` defaults) under a "keep them in sync" comment. This pins the
canonical home (`app.config`) and value, and documents the helpers that derive
from it across `srs/` and `anki/`.
"""

from datetime import UTC, datetime

from app.anki.protobuf_wire import review_due_at_for_col_day
from app.anki.sync_common import _local_today_4am
from app.config import ANKI_ROLLOVER_HOUR
from app.srs import database as database_mod


def test_canonical_value_and_home():
    assert ANKI_ROLLOVER_HOUR == 4
    # database references the shared constant, not a private copy.
    assert database_mod.ANKI_ROLLOVER_HOUR == ANKI_ROLLOVER_HOUR


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
