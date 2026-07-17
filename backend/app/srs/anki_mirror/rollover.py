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
    Accepts an optional *now* override for testability; naive *now* is
    promoted to the system-local zone, aware *now* keeps its own tz.
    """
    now = now or datetime.now()
    if now.tzinfo is None:
        now = now.astimezone()
    return _most_recent_rollover(now.date(), now, now.tzinfo)


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
    local_tz = datetime.now().astimezone().tzinfo
    now = (now or datetime.now(local_tz)).astimezone(local_tz)
    day_start = _most_recent_rollover(today, now, local_tz)
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
