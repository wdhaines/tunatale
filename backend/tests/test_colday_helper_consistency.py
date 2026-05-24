"""Pin the col-day↔datetime helper conventions (investigated 2026-05-24).

Background
----------
Two TT helpers convert between Anki's ``cards.due`` col-day index and wall-clock:

  * ``compute_anki_day_index(col_crt, 4, now)``  — wall-clock → col-day index
  * ``review_due_at_for_col_day(col_crt, N)``    — col-day index → UTC datetime

They are deliberately **NOT round-trip inverses**: ``review_due_at_for_col_day(N)``
fed back through ``compute_anki_day_index`` yields ``N - 1``. A load-balancer
replay drilling session flagged this and asked whether it was a production bug.

Ground truth (real collection, Anki closed, ``col.sched.today``)
---------------------------------------------------------------
``col_crt = 1388836800`` (2014-01-04 12:00 UTC; created at UTC-5, machine now EDT).
At 2026-05-24 18:53 UTC, Anki reported ``col.sched.today == 4523`` and
``next_day_at == 2026-05-25 08:00 UTC`` (4am EDT). So col_day ``N`` surfaces at
4am-local on calendar date ``2026-05-24 + (N - 4523)``.

Verdict: NOT a production bug — a latent inconsistency that cancels everywhere real
-----------------------------------------------------------------------------------
  1. ``compute_anki_day_index(now) == 4523`` == Anki's ``today`` exactly. CORRECT.
  2. ``review_due_at_for_col_day(N)`` returns the **correct calendar date**; only the
     time-of-day is 04:00 UTC (Layer 49 anchor) instead of the true local rollover
     08:00 UTC — i.e. 4h early, same date.
  3. ``DirectionState.due_date == due_at.date()`` (the live "due today" field) is
     therefore the correct calendar date, advancing by exactly ``interval`` days.
  4. The TT-native grade path (``_review_due_at_from_interval``) and the sync
     writeback path (``compute_due_at``) BOTH use ``review_due_at_for_col_day``, so
     they produce byte-identical ``due_at`` — sync ``_direction_differs`` never sees
     a spurious diff, and there is no TT-native-vs-synced discrepancy.
  5. No production path feeds a ``due_at`` back into ``compute_anki_day_index``. The
     only inversion lives in diagnostic replay code (``measure_stage3b_premise.py``),
     which is exactly what surfaced the false alarm.

So the helpers live in two *separate, internally-consistent domains*:
  * Index domain:    ``compute_anki_day_index`` + ``_compute_last_review`` (Layer 45)
                     + ``_interval_from_state`` (Layer 51) — these round-trip.
  * Datetime domain: ``review_due_at_for_col_day`` + ``compute_due_at``
                     + ``_review_due_at_from_interval`` — 04:00-UTC anchored.

Do NOT "fix" ``review_due_at_for_col_day`` to round-trip. Making it land on the
true local rollover would require threading the local UTC offset everywhere and
would shift every stored ``due_at`` by that offset — a one-time mass sync
write-back for zero correctness gain (the calendar date is already right). The
tests below fail loudly if someone tries.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.anki.protobuf_wire import compute_anki_day_index, review_due_at_for_col_day
from app.anki.sqlite_reader import compute_due_at
from app.srs.fsrs import _review_due_at_from_interval

# Ground-truth constants captured from the real collection on 2026-05-24.
_COL_CRT = 1388836800  # 2014-01-04 12:00 UTC
_NOW = datetime(2026, 5, 24, 18, 53, 8, tzinfo=UTC)
_ANKI_TODAY = 4523  # col.sched.today at _NOW
_NEXT_DAY_AT = datetime(2026, 5, 25, 8, 0, tzinfo=UTC)  # Anki next_day_at == 4am EDT


class TestComputeAnkiDayIndexGroundTruth:
    """compute_anki_day_index must equal Anki's days_elapsed bit-exact."""

    def test_matches_anki_today(self):
        assert compute_anki_day_index(_COL_CRT, 4, _NOW) == _ANKI_TODAY

    def test_increments_at_local_rollover_boundary(self):
        # One second before Anki's next_day_at, still on the current day.
        assert compute_anki_day_index(_COL_CRT, 4, _NEXT_DAY_AT - timedelta(seconds=1)) == _ANKI_TODAY
        # At next_day_at, the index has rolled over.
        assert compute_anki_day_index(_COL_CRT, 4, _NEXT_DAY_AT) == _ANKI_TODAY + 1


class TestReviewDueAtDateCorrectness:
    """review_due_at_for_col_day lands on the calendar date Anki actually surfaces."""

    def test_today_col_day_maps_to_today_date(self):
        assert review_due_at_for_col_day(_COL_CRT, _ANKI_TODAY).date() == date(2026, 5, 24)

    def test_future_col_day_advances_by_exact_days(self):
        # N=4600 → 77 days past today's calendar date.
        assert review_due_at_for_col_day(_COL_CRT, 4600).date() == date(2026, 5, 24) + timedelta(days=77)

    def test_layer49_0400_utc_anchor_preserved(self):
        # The Layer 49 time-of-day anchor is 04:00 UTC. This is intentional;
        # pinned so a "round-trip fix" that shifts it trips this assertion.
        due = review_due_at_for_col_day(_COL_CRT, _ANKI_TODAY)
        assert (due.hour, due.minute) == (4, 0)


class TestNativeGradeMatchesSyncWriteback:
    """The two due_at writers must agree — this is what prevents sync churn."""

    def test_native_equals_sync_for_each_interval(self):
        today = compute_anki_day_index(_COL_CRT, 4, _NOW)
        for interval in (1, 3, 21, 77):
            native = _review_due_at_from_interval(_NOW.date(), interval, _COL_CRT, _NOW)
            sync = compute_due_at(2, today + interval, _COL_CRT)
            assert native == sync, f"native/sync diverge at interval={interval}"

    def test_native_due_date_advances_by_interval(self):
        today = compute_anki_day_index(_COL_CRT, 4, _NOW)
        today_date = review_due_at_for_col_day(_COL_CRT, today).date()
        for interval in (1, 3, 21, 77):
            native = _review_due_at_from_interval(_NOW.date(), interval, _COL_CRT, _NOW)
            assert (native.date() - today_date).days == interval


class TestDomainsAreNotInverses:
    """Document (not lament) the intentional non-inverse relationship.

    If this test ever *passes* the round-trip cleanly, someone changed the
    datetime-domain anchor — re-read this module's docstring before celebrating,
    because the change shifts every stored due_at and triggers a mass sync
    write-back (rule 6 / Layer 49).
    """

    def test_round_trip_is_off_by_one_by_design(self):
        for n in (4521, 4523, 4524, 4525, 4600):
            dt = review_due_at_for_col_day(_COL_CRT, n)
            assert compute_anki_day_index(_COL_CRT, 4, dt) == n - 1
