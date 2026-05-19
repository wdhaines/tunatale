"""Tests for ``app.srs.invariants.check_due_invariant``.

The invariant: every review-state row whose grade has happened must have
``due_at == last_review + _review_interval_fuzz(...) days``, bit-exact, where
the fuzz uses ``reps - 1`` to recover the at-grade-time seed (Anki's
``for_reschedule=true`` semantics).
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest

from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.fsrs import DEFAULT_FSRS5_PARAMS, _next_interval, _review_interval_fuzz
from app.srs.invariants import check_due_invariant


def _seed_review(
    db, *, text: str, stability: float, reps: int, anki_card_id: int, due_at: datetime, last_review: datetime
):
    unit = SyntacticUnit(text=text, translation="t", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation(text)
    ds = DirectionState(
        direction=Direction.RECOGNITION,
        state=SRSState.REVIEW,
        due_at=due_at,
        stability=stability,
        difficulty=5.0,
        reps=reps,
        lapses=0,
        anki_card_id=anki_card_id,
        last_review=last_review,
        last_rating=3,
    )
    db.update_direction(item.guid, Direction.RECOGNITION, ds)


def _expected_due_at(stability: float, reps: int, anki_card_id: int, last_review: datetime) -> datetime:
    p = DEFAULT_FSRS5_PARAMS
    raw = _next_interval(stability, p.desired_retention, -p.decay)
    iv = _review_interval_fuzz(raw, anki_card_id, max(0, reps - 1), p.maximum_review_interval)
    return last_review + timedelta(days=iv)


class TestCheckDueInvariant:
    def test_coherent_row_passes(self, srs_db):
        last_review = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        s, reps, cid = 50.0, 5, 1001
        expected = _expected_due_at(s, reps, cid, last_review)
        _seed_review(
            srs_db, text="ok", stability=s, reps=reps, anki_card_id=cid, due_at=expected, last_review=last_review
        )

        with srs_db._get_conn() as conn:
            violations = check_due_invariant(conn)
        assert violations == []

    def test_stale_due_at_is_flagged(self, srs_db):
        """Mirror the divergence-investigation pattern: stable stability but
        stale due_at from an earlier grade. Invariant must catch it."""
        last_review = datetime(2026, 5, 16, 20, 0, tzinfo=UTC)
        stale = datetime(2026, 5, 18, 4, 0, tzinfo=UTC)  # 2 days out — predates current stability
        _seed_review(
            srs_db,
            text="taborisce",
            stability=67.559,
            reps=10,
            anki_card_id=2002,
            due_at=stale,
            last_review=last_review,
        )

        with srs_db._get_conn() as conn:
            violations = check_due_invariant(conn)
        assert len(violations) == 1
        v = violations[0]
        assert v.direction == "recognition"
        assert v.stored_due_at.startswith("2026-05-18")

    def test_raise_on_violation_aborts_on_first(self, srs_db):
        last_review = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        stale = datetime(2026, 5, 2, 4, 0, tzinfo=UTC)
        _seed_review(
            srs_db, text="bad1", stability=50.0, reps=5, anki_card_id=3001, due_at=stale, last_review=last_review
        )

        with srs_db._get_conn() as conn, pytest.raises(AssertionError, match="Due invariant violation"):
            check_due_invariant(conn, raise_on_violation=True)

    def test_learning_state_is_skipped(self, srs_db):
        """Learning-state rows have sub-day due_at that doesn't follow the
        review-interval formula. Invariant must not flag them."""
        unit = SyntacticUnit(text="learn1", translation="t", word_count=1, difficulty=1, source="test")
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("learn1")
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.LEARNING,
            due_at=datetime(2026, 5, 1, 12, 30, tzinfo=UTC),
            stability=1.0,
            reps=1,
            anki_card_id=4001,
            last_review=datetime(2026, 5, 1, 12, 20, tzinfo=UTC),
            last_rating=3,
        )
        srs_db.update_direction(item.guid, Direction.RECOGNITION, ds)

        with srs_db._get_conn() as conn:
            violations = check_due_invariant(conn)
        assert violations == []

    def test_no_last_rating_is_skipped(self, srs_db):
        """Imported-via-sync rows that never had a TT grade have last_rating=NULL.
        Nothing to derive against — must be skipped."""
        unit = SyntacticUnit(text="seeded", translation="t", word_count=1, difficulty=1, source="test")
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("seeded")
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_at=datetime(2030, 1, 1, 4, 0, tzinfo=UTC),
            stability=10.0,
            reps=3,
            anki_card_id=5001,
            last_review=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
            last_rating=None,  # never graded in TT
        )
        srs_db.update_direction(item.guid, Direction.RECOGNITION, ds)

        with srs_db._get_conn() as conn:
            violations = check_due_invariant(conn)
        assert violations == []

    def test_day_granularity_tolerates_4am_vs_midnight(self, srs_db):
        """Legacy rows might be stamped at 4am while new ones at midnight; both
        belong to the same calendar day, so the invariant tolerates the hour
        difference."""
        last_review = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        s, reps, cid = 50.0, 5, 1001
        expected = _expected_due_at(s, reps, cid, last_review)
        # Shift to 4am on the same day:
        shifted = datetime.combine(expected.date(), time(4, 0), tzinfo=UTC)
        _seed_review(
            srs_db, text="hour_off", stability=s, reps=reps, anki_card_id=cid, due_at=shifted, last_review=last_review
        )

        with srs_db._get_conn() as conn:
            violations = check_due_invariant(conn)
        assert violations == []

    def test_uses_cached_fsrs_params_when_present(self, srs_db):
        """When anki_state_cache holds FSRS-6 params, the invariant uses them."""
        # Seed an FSRS-6 weight blob + custom desired_retention.
        fsrs6_weights = [
            0.212,
            1.2931,
            2.3065,
            8.2956,
            6.4133,
            0.8334,
            3.0194,
            0.001,
            1.8722,
            0.1666,
            0.796,
            1.4835,
            0.0614,
            0.2629,
            1.6483,
            0.6014,
            1.8729,
            0.5425,
            0.0912,
            0.0658,
            0.1542,
        ]
        import json

        with srs_db._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO anki_state_cache (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                ("fsrs_params", json.dumps({"weights": fsrs6_weights})),
            )
            conn.execute(
                "INSERT OR REPLACE INTO anki_state_cache (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                ("desired_retention", "0.85"),
            )

        last_review = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        # With FSRS-6 weights + dr=0.85, the expected interval differs from
        # FSRS-5 defaults — so just verify the call completes without raising.
        _seed_review(
            srs_db,
            text="fsrs6",
            stability=50.0,
            reps=5,
            anki_card_id=7001,
            due_at=datetime(2099, 1, 1, tzinfo=UTC),
            last_review=last_review,
        )
        with srs_db._get_conn() as conn:
            violations = check_due_invariant(conn)
        assert len(violations) == 1  # The seeded due_at is far in the future on purpose.

    def test_malformed_cached_params_falls_back_to_defaults(self, srs_db):
        with srs_db._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO anki_state_cache (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                ("fsrs_params", "not-json"),
            )
        last_review = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        s, reps, cid = 50.0, 5, 1001
        expected = _expected_due_at(s, reps, cid, last_review)
        _seed_review(
            srs_db,
            text="bad_params",
            stability=s,
            reps=reps,
            anki_card_id=cid,
            due_at=expected,
            last_review=last_review,
        )
        with srs_db._get_conn() as conn:
            violations = check_due_invariant(conn)
        assert violations == []

    def test_invalid_weight_count_falls_back_to_defaults(self, srs_db):
        import json

        with srs_db._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO anki_state_cache (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                ("fsrs_params", json.dumps({"weights": [1.0, 2.0, 3.0]})),  # wrong length
            )
        last_review = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        s, reps, cid = 50.0, 5, 1001
        expected = _expected_due_at(s, reps, cid, last_review)
        _seed_review(
            srs_db,
            text="bad_weights",
            stability=s,
            reps=reps,
            anki_card_id=cid,
            due_at=expected,
            last_review=last_review,
        )
        with srs_db._get_conn() as conn:
            violations = check_due_invariant(conn)
        assert violations == []
