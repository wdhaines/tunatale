"""Single source for Anki's study-day rollover arithmetic.

Two day domains exist BY DESIGN (Layer 54) — do not merge them:

- **col-day index domain** — integer day indices anchored on ``col.crt``
  (``app.srs.anki_mirror.protobuf_wire.compute_anki_day_index`` /
  ``review_due_at_for_col_day``). Owned by ``protobuf_wire``.
- **local-day domain** (this module) — wall-clock rollover anchors used for
  "graded today" bucketing, Anki-day bounds, and the current Anki-day date.

This module also owns the shared *due_at convention*: day-level due
timestamps sit at ``ANKI_ROLLOVER_HOUR`` **UTC** on the due date
(``due_at_rollover_utc``), matching sync_pull's writeback via
``review_due_at_for_col_day``.

Leaf module: imports only stdlib and ``app.config``, so any layer (models,
srs, anki, api) may use it without cycle risk.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, tzinfo

from app.config import ANKI_ROLLOVER_HOUR


def _local_now(now: datetime | None) -> datetime:
    """Normalize *now* into the local zone — the ONE definition both entry points use.

    The rollover is a LOCAL wall-clock concept: it is a property of the user's
    machine, not of whichever zone a caller happened to express an instant in.
    So an aware ``now`` is CONVERTED here rather than being allowed to supply its
    own arithmetic zone, and two datetimes naming the same instant give the same
    Anki day.

    ⚠️ This exists because the two entry points used to normalize differently.
    ``anki_day_bounds_utc_dt`` converted to local; ``local_today_rollover``
    promoted only NAIVE input and otherwise kept ``now.tzinfo``. Handed the same
    UTC-aware ``now`` they disagreed about which Anki day it was — the day label
    from one clock, the window from the other — which is latent in production
    (every real call site passes ``now=None``) and produced a real test bug at
    UTC+5 and beyond (tunatale-3oz). Sharing one helper is the fix: they can
    disagree only if someone stops calling it.

    ⚠️ Use bare ``.astimezone()``, NOT ``.astimezone(datetime.now().astimezone().tzinfo)``.
    The latter is what ``anki_day_bounds_utc_dt`` used to do, and it snapshots
    *today's* UTC offset into a fixed-offset object — applying it to a date in
    another DST regime is then off by an hour. Bare ``.astimezone()`` consults
    the system zone database and resolves the offset for the instant in
    question, correctly for both naive input (read as local wall-clock) and
    aware input (converted). Swapping to the snapshot form moved
    ``test_anki_today_flips_at_rollover`` by a day across the EST/EDT boundary,
    which is how this was caught.
    """
    return (now or datetime.now()).astimezone()


def _most_recent_rollover(anchor_day: date, now: datetime, tz: tzinfo | None) -> datetime:
    """The rollover moment on *anchor_day* in *tz*, shifted back one day if *now* precedes it."""
    candidate = datetime.combine(anchor_day, time(ANKI_ROLLOVER_HOUR), tzinfo=tz)
    if now < candidate:
        candidate = datetime.combine(anchor_day - timedelta(days=1), time(ANKI_ROLLOVER_HOUR), tzinfo=tz)
    return candidate


def local_today_rollover(now: datetime | None = None) -> datetime:
    """Return the datetime of today's rollover (4 AM) in local timezone.

    Mirrors Anki's day-cutoff concept — entries with a revlog.id before this
    timestamp are "before today" for the purpose of counting introductions.
    Returns the most recent rollover (yesterday's if before it today).
    Accepts an optional *now* override for testability. Naive *now* is read as
    local wall-clock; aware *now* is CONVERTED to local rather than supplying
    its own arithmetic zone — see :func:`_local_now` for why that distinction
    was a bug rather than a preference.
    """
    now = _local_now(now)
    return _most_recent_rollover(now.date(), now, now.tzinfo)


def local_next_rollover(now: datetime | None = None) -> datetime:
    """The datetime of the NEXT rollover — Anki's ``next_day_at`` / ``col.sched.day_cutoff``.

    Anki's ANSWERING path measures elapsed time against this instant, not against
    ``now``: ``days_elapsed = next_day_at.elapsed_days_since(lrt)``, an integer
    division of the duration by 86400 (rslib ``scheduler/answering/mod.rs``,
    ``timestamp.rs``). That is a third formulation, distinct from both TT day
    domains — it is a duration, not a difference of day indices — which is why
    ``_grade_elapsed_days`` cannot be expressed with either of them.

    Verified against the real backend: for a card with sub-day ``lrt``, the
    post-grade stability Anki produces matches the recall formula fed with
    ``(day_cutoff - lrt) // 86400`` to six significant figures, in and out of the
    rollover band. See ``tests/test_parity_grade_elapsed.py``.
    """
    return local_today_rollover(now) + timedelta(days=1)


def anki_day_bounds_utc_dt(today: date, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return the UTC-aware [start, end) `datetime` bounds of the Anki day
    anchored on `today` — the same arithmetic as `anki_day_bounds_utc`, but as
    `datetime` objects for callers doing direct datetime comparison (e.g.
    `today_start <= lr < today_end`) instead of SQL text-range queries.

    The window runs from `ANKI_ROLLOVER_HOUR` local on `today` to the same hour
    the next day. When the wall-clock `now` is *before* today's rollover, the
    active Anki day is still yesterday's, so the anchor shifts back one day —
    matching what `local_today_rollover` does for sync-side counts. Counting on
    the local-midnight boundary instead silently sibling-buries cards graded in
    the `[midnight, rollover)` window that Anki still treats as graded yesterday
    (the 66-vs-73 review-badge divergence, 2026-06-02).
    """
    now = _local_now(now)
    day_start = _most_recent_rollover(today, now, now.tzinfo)
    start_utc = day_start.astimezone(UTC)
    return start_utc, start_utc + timedelta(days=1)


def anki_day_bounds_utc(today: date, now: datetime | None = None) -> tuple[str, str]:
    """Return the UTC [start, end) ISO bounds of the Anki day anchored on `today`.

    Thin ISO-string wrapper around `anki_day_bounds_utc_dt` — see there for the
    shared arithmetic and rationale (single-sourced so a future rollover-hour
    change lands once, not once per return-shape).
    """
    start, end = anki_day_bounds_utc_dt(today, now)
    return start.isoformat(), end.isoformat()


def anki_today(now: datetime | None = None) -> date:
    """The current Anki-day date: the calendar date of the most recent rollover.

    In the `[midnight, rollover)` local window this is *yesterday's* date,
    where `date.today()` would already say today. Route "which Anki day is
    it?" call sites through this instead of `date.today()` (the danger-zone-2
    audit target, docs/refactor-suggestions-2026-07.md item #11).
    """
    return local_today_rollover(now).date()


def due_at_rollover_utc(day: date) -> datetime:
    """Day-level due_at convention: ``ANKI_ROLLOVER_HOUR`` UTC on *day*.

    This is the col-day/due_at domain's fixed time-of-day (see
    ``review_due_at_for_col_day``), used for NEW-card placeholders and
    day-level due writes so stored due_at values compare consistently.
    """
    return datetime.combine(day, time(ANKI_ROLLOVER_HOUR), tzinfo=UTC)
