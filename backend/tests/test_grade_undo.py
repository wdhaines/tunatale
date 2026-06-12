"""Tests for app.srs.grade_undo — DirectionState snapshot serialization round-trip.

The undo endpoint restores a verbatim pre-grade DirectionState from a JSON
snapshot in anki_state_cache. Every field must survive the round-trip, or an
undo silently corrupts scheduling state (the API tests assert dataclass
equality end-to-end; these pin the serializer in isolation).
"""

from __future__ import annotations

import datetime

from app.models.srs_item import Direction, DirectionState, SRSState
from app.srs.grade_undo import direction_from_dict, direction_to_dict


def test_round_trip_minimal_state():
    ds = DirectionState(
        direction=Direction.RECOGNITION,
        due_at=datetime.datetime(2026, 6, 11, 4, 0, tzinfo=datetime.UTC),
    )
    assert direction_from_dict(direction_to_dict(ds)) == ds


def test_round_trip_fully_populated_state():
    ds = DirectionState(
        direction=Direction.PRODUCTION,
        due_at=datetime.datetime(2026, 6, 12, 4, 0, tzinfo=datetime.UTC),
        stability=3.25,
        difficulty=6.5,
        reps=7,
        lapses=2,
        state=SRSState.RELEARNING,
        last_review=datetime.datetime(2026, 6, 10, 18, 30, 12, 345000, tzinfo=datetime.UTC),
        last_review_time_ms=4200,
        anki_card_id=1234567890,
        anki_due=812,
        anki_card_mod=1718000000,
        bury_kind="sched",
        dirty_fsrs=True,
        last_synced_at="2026-06-09T00:00:00+00:00",
        last_rating=2,
        left=1001,
        prior_state=SRSState.REVIEW,
        prior_left=2002,
        prior_stability=9.75,
        introduced_at=datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC),
        fsrs_force_next=True,
    )
    assert direction_from_dict(direction_to_dict(ds)) == ds


def test_dict_is_json_safe():
    import json

    ds = DirectionState(
        direction=Direction.RECOGNITION,
        due_at=datetime.datetime(2026, 6, 11, 4, 0, tzinfo=datetime.UTC),
        state=SRSState.LEARNING,
        last_review=datetime.datetime(2026, 6, 11, 3, 0, tzinfo=datetime.UTC),
        prior_state=SRSState.NEW,
    )
    restored = direction_from_dict(json.loads(json.dumps(direction_to_dict(ds))))
    assert restored == ds
