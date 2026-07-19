"""Badge-counter mixin for SRSDatabase.

Extracted verbatim from app/srs/database.py (god-module split, stage 5).
The new/learning/review badge counters that mirror Anki's deck-browser
counts. Anki-parity danger zone — Layers 26/56/64/67/73 live here; see
.claude/rules/anki-queue-parity.md before changing anything here.
"""

from datetime import UTC, date, datetime, time, timedelta

from app.models.srs_item import Direction
from app.srs.db_base import (
    _LEARNING_STATES,
    _NON_REVIEWABLE_STATES,
    _anki_day_bounds_utc,
)


class DbCountsMixin:
    """Badge counters. Mixed into SRSDatabase; relies on SRSDatabaseBase infra."""

    def count_new_available(self) -> int:
        """Count all collocation_directions rows in the NEW state (both directions).

        Raw, bury-unaware total. Used as the upper bound for the per-direction
        new-pool overfetch in ``_compute_live_main``. The badge uses the
        bury-aware ``count_new_available_collocations`` instead.
        """
        with self._get_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM collocation_directions WHERE state = 'new'").fetchone()[0]

    def count_new_available_collocations(self, today: date) -> int:
        """Count distinct collocations with a NEW direction Anki would NOT bury
        out of today's new queue. Mirror image of ``count_review_due_collocations``.

        Anki buries a new card at queue-build when ``bury_new`` is set and a
        sibling was already gathered into today's queue. Gather order is
        learning → review → new (`builder/gathering.rs:14-21`), so a new card is
        buried whenever a sibling is:

        1. **Graded today** — grading any sibling buries the new card with
           ``queue=-2`` at grade time; that bury persists until the day
           rollover, so it still applies even when the graded sibling's review
           was pushed to a *future* due date (the ``last_review today`` clause).
        2. **In learning/relearning** — learning cards are gathered first
           (`add_new_card` then sees the note as already-seen and buries it).
        3. **A review due today** — gathered in the review phase, so the new
           sibling is buried. A *future*-due review sibling is NOT gathered and
           does NOT bury (verified against the Anki binary): pushing the
           sibling's ``due`` forward flips Anki's ``counts.new`` 0 → 1.

        ``COUNT(DISTINCT collocation_id)`` collapses a both-new note to one,
        mirroring Anki burying the second new sibling. Only meaningful when
        ``bury_new`` is set — the caller falls back to ``count_new_available``
        otherwise. (`_compute_live_main` already applies the same bury to the
        served queue; this keeps the badge consistent with it.)
        """
        start_iso, end_iso = _anki_day_bounds_utc(today)
        # Naive local-date cutoff: a due-DATE <= today check that relies on the
        # 04:00-UTC due_at convention (rollover.py::due_at_rollover_utc) — see
        # count_review_due_collocations for the full constraint.
        end_of_day_utc = datetime.combine(today, time.max).isoformat()
        with self._get_conn() as conn:
            return conn.execute(
                """
                SELECT COUNT(DISTINCT cd.collocation_id) FROM collocation_directions cd
                WHERE cd.state = 'new'
                  AND cd.collocation_id NOT IN (
                    SELECT collocation_id FROM collocation_directions
                    WHERE (length(last_review) > 10 AND last_review >= ? AND last_review < ?)
                       OR (length(last_review) = 10 AND last_review = ?)
                       OR state IN ('learning', 'relearning')
                       OR (state = 'review' AND due_at <= ?)
                  )
                """,
                (start_iso, end_iso, today.isoformat(), end_of_day_utc),
            ).fetchone()[0]

    def count_learning(self) -> int:
        """Count every learning/relearning direction (Anki red badge).

        Matches Anki deck-browser semantics exactly: every queue=1 card is
        counted, regardless of due_date or whether the next step has elapsed.
        This is the same filter as `get_learning_items` — the count and the
        list must agree.
        """
        placeholders = ",".join("?" * len(_LEARNING_STATES))
        with self._get_conn() as conn:
            return conn.execute(
                f"SELECT COUNT(*) FROM collocation_directions WHERE state IN ({placeholders})",
                _LEARNING_STATES,
            ).fetchone()[0]

    def count_review_due_collocations(self, today: date) -> int:
        """Count distinct collocations with at least one review-state direction
        due today, excluding those Anki would bury out of today's review pool.

        Anki's `bury_reviews=true` removes a note from today's review pool when
        any sibling is active. Two triggers mirror that:

        1. **Graded today** — once any direction is graded, the un-graded
           sibling goes to queue=-2 until tomorrow. Exclude collocations whose
           `last_review` for any direction falls within today's local day, so
           the badge decrements by 1 (not 2) when one direction of a dual
           note is graded.
        2. **Sibling in the learning queue** — Anki also buries the review
           card whenever its sibling sits in learning/relearning (queue=1/3),
           *including interday learning steps graded on a prior day*. The
           "graded today" filter alone misses those, over-counting the badge
           (the observed 214→208 gap was exactly the notes with a learning
           sibling). Exclude collocations with any direction in
           learning/relearning regardless of when it was last graded.

        Together these match Anki's deck-overview review count when both apps
        share the same data.
        """
        start_iso, end_iso = _anki_day_bounds_utc(today)
        # Naive local-date cutoff string. Correct ONLY because REVIEW-state
        # due_at is date-encoded at 04:00 UTC (rollover.py::due_at_rollover_utc),
        # making the lexicographic compare a due-DATE <= today check. Do NOT
        # seed tests with instant-flavored due_ats (now-1h): past 20:00 local
        # (UTC-4) their UTC date exceeds `today` and they read as not-due.
        # `_listen_grade_class` shares this exact boundary — keep them identical.
        end_of_day_utc = datetime.combine(today, time.max).isoformat()
        with self._get_conn() as conn:
            return conn.execute(
                """
                SELECT COUNT(DISTINCT cd.collocation_id) FROM collocation_directions cd
                WHERE cd.due_at <= ? AND cd.state = 'review'
                  AND cd.collocation_id NOT IN (
                    SELECT collocation_id FROM collocation_directions
                    WHERE (length(last_review) > 10 AND last_review >= ? AND last_review < ?)
                       OR (length(last_review) = 10 AND last_review = ?)
                       OR state IN ('learning', 'relearning')
                  )
                """,
                (end_of_day_utc, start_iso, end_iso, today.isoformat()),
            ).fetchone()[0]

    def count_new_introduced_today(self, today: date) -> int:
        """Count distinct collocations whose first NEW→non-NEW transition fell today.

        Filters on the explicit `introduced_at` column written once by the grade
        endpoint (`app.srs.fsrs.schedule`) and by `sync_pull` on the first
        introduction event. Mirrors Anki's `newToday` counter, which increments
        only on that first grade — subsequent reviews of the same card on later
        days do NOT bump it.

        Pre-Layer-26 rows that were introduced before `introduced_at` existed
        have NULL and naturally fall out of the count. Going forward, every new
        grade populates the column.
        """
        start_iso, end_iso = _anki_day_bounds_utc(today)
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(DISTINCT collocation_id) FROM collocation_directions
                WHERE introduced_at IS NOT NULL
                  AND introduced_at >= ?
                  AND introduced_at < ?
                """,
                (start_iso, end_iso),
            ).fetchone()
            return row[0] if row else 0

    def count_new_created_today(self, today: date) -> int:
        """Count distinct collocations created inside today's Anki-day window
        that still have at least one NEW direction.

        Input to the per-listen creation budget (staged listen): cards created
        by an earlier listen today that nobody has graded yet keep charging the
        budget, so a same-day re-listen creates ~0 more. A card introduced the
        same day it was created drops out here and charges the budget via
        ``count_new_introduced_today`` instead — never both.

        ``collocations.created_at`` is stored in SQLite ``datetime('now')``
        format (UTC, space-separated); ``datetime(?)`` normalizes the ISO
        bounds to the same shape so the string comparison is valid.
        """
        start_iso, end_iso = _anki_day_bounds_utc(today)
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(DISTINCT c.id)
                FROM collocations c
                JOIN collocation_directions d ON d.collocation_id = c.id
                WHERE d.state = 'new'
                  AND c.created_at >= datetime(?)
                  AND c.created_at < datetime(?)
                """,
                (start_iso, end_iso),
            ).fetchone()
            return row[0] if row else 0

    def count_reviews_completed_today(self, today: date) -> int:
        """Count today's review answers, mirroring Anki's per-deck ``review_today``.

        Anki increments ``review_today`` from the card's **pre-answer queue**:
        ``CardQueue::Review | CardQueue::DayLearn => review_delta += 1``
        (``rslib/.../answering/mod.rs``, verified against Anki 25.09). ``DayLearn``
        is the *interday* (re)learning queue — interday learning and interday
        relearning both count; *intraday* (re)learning does not. The revlog `type`
        alone can't reproduce this (a lapse writes type=1, interday relearn type=2,
        interday learn type=0 — all increment), so the discriminator is the
        pre-answer interval sign.

        ``tt_revlog`` carries that sign in ``last_interval`` (days-positive /
        seconds-negative, the Anki ``lastIvl`` convention — see
        ``_compute_revlog_last_interval`` and the sync ingest), so the mirror is
        ``review_kind IN (0,1,2) AND last_interval >= 1`` over the 4am window.
        ``tt_revlog`` holds both TT-native grades (written at grade time) and
        Anki-pulled grades (ingested in ``sync_pull``), so this needs no
        ``last_rating`` and no ``introduced_at`` exclusion — a new-card intro is
        ``last_interval=0`` and falls out naturally. Counts **rows** (per-answer,
        as Anki increments), not distinct cards.

        Layer 73: supersedes the old ``collocation_directions`` state heuristic,
        which over-counted intraday relearning (every ``state='relearning'`` graded
        today) and under-counted interday learning (``state='learning'`` excluded) —
        both invisible from current direction state, which holds the *post*-grade
        interval, not the pre-grade one.
        """
        start_iso, end_iso = _anki_day_bounds_utc(today)
        start_ms = int(datetime.fromisoformat(start_iso).timestamp() * 1000)
        end_ms = int(datetime.fromisoformat(end_iso).timestamp() * 1000)
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM tt_revlog
                WHERE id >= ? AND id < ?
                  AND review_kind IN (0, 1, 2)
                  AND last_interval >= 1
                """,
                (start_ms, end_ms),
            ).fetchone()
            return row[0] if row else 0

    def count_interday_learning_due(self, today: date) -> int:
        """Count interday (re)learning directions due today — Anki's queue=3.

        Layer 79: Anki gathers day-scale learning steps under the REVIEW limit
        (``gather_due_cards`` hardcodes ``LimitKind::Review`` for
        ``DueCardKind::Learning``, gathering.rs:35-61), so each one due today
        consumes the review-per-day budget exactly like a review card — while
        still displaying in the *learning* count (``day_learning`` feeds
        ``learn_count``, builder/mod.rs:189-218). Oracle-pinned by
        ``test_parity_daily_caps.py::test_anki_interday_learning_charges_review_limit``.

        "Interday footing" mirrors the ``lastIvl`` sign convention
        (interval_kind.rs): the scheduled step spans >= 1 day of wall clock
        (``due_at - last_review``); sub-day steps are queue=1 (intraday), exempt
        from the budget. Rows without ``last_review`` (listen-first
        ``promote_to_learning``) stay out — Anki keeps those at queue=0. The due
        bound is the end of today's 4am-rollover window: queue=3 dues are
        day-level, so anything due this Anki day (or overdue) is gathered
        regardless of intra-day time.
        """
        _, end_iso = _anki_day_bounds_utc(today)
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM collocation_directions
                WHERE state IN ('learning', 'relearning')
                  AND due_at IS NOT NULL
                  AND last_review IS NOT NULL
                  AND due_at < ?
                  AND julianday(due_at) - julianday(last_review) >= 1.0
                """,
                (end_iso,),
            ).fetchone()
            return row[0] if row else 0

    def count_due_collocations(
        self,
        as_of: date,
        direction: Direction = Direction.RECOGNITION,
    ) -> int:
        placeholders = ",".join("?" * len(_NON_REVIEWABLE_STATES))
        # End-of-day cutoff: any due_at strictly before (as_of + 1 day) midnight UTC counts.
        cutoff = datetime.combine(as_of + timedelta(days=1), time(0, 0), tzinfo=UTC).isoformat()
        with self._get_conn() as conn:
            return conn.execute(
                f"""
                SELECT COUNT(DISTINCT c.id) FROM collocations c
                JOIN collocation_directions d ON d.collocation_id = c.id
                WHERE d.direction = ?
                  AND d.due_at < ?
                  AND d.state NOT IN ({placeholders})
                """,
                (direction.value, cutoff, *_NON_REVIEWABLE_STATES),
            ).fetchone()[0]
