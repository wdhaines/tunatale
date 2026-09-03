"""Oracle parity for the deck studied-today marker TT writes during sync.

``AnkiSync._recompute_anki_studied_today_all_decks`` rewrites each revlog-touched
deck's ``new_today`` / ``review_today`` counters and stamps them with a day
index. Anki accepts those counts as *today's* only when the stamp equals its own
``col.sched.today``; otherwise it treats them as a leftover from another study
day and does not charge the deck's daily limits for them.

The function used to compute the two halves in different day domains: the revlog
window came from ``_local_today_4am()`` (the local-day domain, correct), while
the stamp came from ``compute_anki_day_index`` (the index domain, which turns
over at local midnight for a real 4 AM-local ``col.crt``). Inside
``[local midnight, 04:00)`` that wrote yesterday's counts under tomorrow's
stamp — so Anki discarded them and served new cards the user's daily cap had
already been spent on, until its own next grade rewrote the field.

The band is pinned rather than waited for; see ``tests/_helpers/localtz.py``.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from tests._helpers.localtz import local_timezone, timezone_with_local_hour
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import SyntheticCollection

# Two cards graded today, a daily limit of exactly two, and one untouched new
# card. If Anki accepts the stamp the limit is spent and it serves 0 new; if it
# rejects the stamp the counters are ignored and it serves the leftover card.
# That difference is the whole test — the raw stamp is asserted too, but the
# behaviour is what the user actually feels.
_GRADED_TODAY = 2
_NEW_LIMIT = 2


@pytest.mark.oracle
def test_studied_today_marker_is_the_day_anki_is_on(synthetic_collection: SyntheticCollection) -> None:
    from app.plugins.anki_sync.sync_engine import AnkiSync
    from app.plugins.anki_sync.sync_writer import OfflineWriter
    from app.srs.anki_mirror.protobuf_wire import anki_today_col_day, compute_anki_day_index
    from app.srs.database import SRSDatabase
    from tests._helpers.anki_sync_push import FakeReader

    # 02:00 local — inside `[local midnight, 04:00)`, the window where the two
    # day domains disagree for a collection created at 4 AM local.
    zone = timezone_with_local_hour(2)

    with local_timezone(zone):
        now = datetime.now(tz=UTC)
        # A realistic col.crt: 4 AM LOCAL on the creation day, which is what Anki
        # itself writes. The bug does not reproduce for an arbitrary crt.
        crt_local_4am = (now.astimezone() - timedelta(days=800)).replace(hour=4, minute=0, second=0, microsecond=0)
        col_crt = int(crt_local_4am.timestamp())

        synthetic_collection.col_crt = col_crt
        synthetic_collection.set_daily_limits(new=_NEW_LIMIT, reviews=200)

        graded_ms = int((now - timedelta(hours=1)).timestamp() * 1000)
        for i in range(_GRADED_TODAY):
            synthetic_collection.add_note(id=3001 + i, guid=f"g-st-{i}", fields=["f", "b"])
            synthetic_collection.add_card(id=30010 + i, note_id=3001 + i, ord=0, type=2, queue=2, due=0, ivl=5, reps=1)
            # First-ever revlog for the card falls inside today → counts toward newToday.
            synthetic_collection.add_revlog(id=graded_ms + i, card_id=30010 + i, ease=3, ivl=5, last_ivl=1, time=1000)
        # The leftover the daily limit should be blocking.
        synthetic_collection.add_note(id=3100, guid="g-st-fresh", fields=["f", "b"])
        synthetic_collection.add_card(id=31000, note_id=3100, ord=0, type=0, queue=0, due=1)
        synthetic_collection.save()

        # Guard against a vacuous pass: if the two domains happen to agree here,
        # this test cannot tell the fix from the bug.
        assert compute_anki_day_index(col_crt) != anki_today_col_day(col_crt), (
            "the two day domains agree in this configuration, so the assertions below "
            "prove nothing — check the pinned zone and col.crt"
        )

        conn = sqlite3.connect(str(synthetic_collection.path))
        try:
            sync = AnkiSync(
                db=SRSDatabase(":memory:"),
                _reader=FakeReader(),
                _writer=OfflineWriter(conn),
                _anki_col_crt=col_crt,
            )
            sync._recompute_anki_studied_today_all_decks()
        finally:
            conn.close()

        raw = run_oracle(synthetic_collection.path, [{"op": "deck_today", "deck_id": 1}]).raw()

    deck = raw[next(k for k in raw if k.startswith("deck_today"))]
    stamped_day, stamped_count = deck["new_today"]

    assert stamped_count == _GRADED_TODAY, f"recompute miscounted today's new cards: {deck}"
    assert stamped_day == deck["today"], (
        f"studied-today stamp is {stamped_day}, Anki is on day {deck['today']} — "
        f"Anki will discard these counters and not charge the daily limit. {deck}"
    )
    assert deck["new_count"] == 0, (
        f"Anki served {deck['new_count']} new cards despite the daily limit of {_NEW_LIMIT} "
        f"already being spent today — the stamp was not accepted. {deck}"
    )
