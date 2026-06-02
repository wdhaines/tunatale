"""New-sibling bury parity (layer cluster: 28, 56; this Layer).

Pins Anki's ``bury_new`` behavior for the *new* badge: a new card is buried out
of today's new queue when a sibling was already gathered into the queue. Gather
order is intraday-learning → interday-learning → review → new
(``builder/gathering.rs:14-21``); ``add_new_card`` buries a card whose note was
already seen (``builder/burying.rs:75-93``). So a new card is buried when a
sibling is a review **due today** or a learning card — but a *future*-due review
sibling is not gathered and does **not** bury.

This is the first harness test to use a **2-template notetype** (recognition +
production on one note), the cross-direction scaffold that
``test_parity_bury.py`` flagged as "punted to Phase 2.2.x if the need recurs".

What this test covers:
- Anki buries a NEW card whose sibling is a REVIEW due today.
- Anki does NOT bury a NEW card whose sibling is a REVIEW due in the future
  (the distinction that makes a "has any review sibling" filter wrong).
- Anki buries a NEW card whose sibling is in the learning queue.
- TT's ``count_new_available_collocations`` matches Anki's ``counts.new`` for
  each scenario.

What this test does NOT cover (owned elsewhere):
- TT's badge endpoint gating on ``bury_new`` (TT-only: ``test_api_srs.py``).
- The served-queue bury (``_compute_live_main``) — already correct before this
  Layer; covered by ``test_review_queue`` and the live-data audit.
- Day-rollover release of the persisted grade-time bury (can't time-travel a
  synthetic collection; see ``test_parity_bury.py``).
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime

import pytest

from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.srs.fsrs import DEFAULT_FSRS5_PARAMS
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import (
    DEFAULT_DESIRED_RETENTION,
    SyntheticCollection,
)

FSRS_WEIGHTS = DEFAULT_FSRS5_PARAMS.weights

# A dual-template notetype (recognition = ord 0, production = ord 1), mirroring
# TunaTale's Slovene-Voc notetype. Distinct mid from the fixture's Basic.
DUAL_MID = 1600000000001

# A review due-day safely in the future but below Anki's 365_000 timestamp
# sentinel (harness gotcha #8). today_col_day for COL_CRT=2024-01-01 is < 1000.
FUTURE_DUE_DAY = 50_000


def _add_dual_notetype(coll: SyntheticCollection) -> None:
    coll.add_notetype(DUAL_MID, "Dual", ("Front", "Back"), template_count=2)


def _add_dual_note(
    coll: SyntheticCollection,
    *,
    note_id: int,
    prod_type: int,
    prod_queue: int,
    prod_due: int,
    prod_last_review_secs: int | None,
) -> tuple[int, int]:
    """Add a note with a NEW recognition card (ord 0) and a sibling production
    card (ord 1). Returns (recognition_card_id, production_card_id)."""
    rec_card_id = note_id * 10
    prod_card_id = note_id * 10 + 1
    coll.add_note(id=note_id, guid=f"g-{note_id}", fields=[f"front-{note_id}", "back"], mid=DUAL_MID)
    # Recognition (ord 0): NEW.
    coll.add_card(id=rec_card_id, note_id=note_id, ord=0, type=0, queue=0, due=note_id)
    # Production (ord 1): the sibling under test.
    coll.add_card(
        id=prod_card_id,
        note_id=note_id,
        ord=1,
        type=prod_type,
        queue=prod_queue,
        due=prod_due,
        ivl=10 if prod_type == 2 else 0,
        reps=5 if prod_type == 2 else 1,
        left=1001 if prod_queue == 1 else 0,
        stability=10.0,
        difficulty=5.0,
        last_review_secs=prod_last_review_secs,
        desired_retention=DEFAULT_DESIRED_RETENTION,
    )
    return rec_card_id, prod_card_id


def _tt_seed(
    db: SRSDatabase,
    text: str,
    prod_state: SRSState,
    *,
    prod_due_offset_days: int = 0,
) -> None:
    """Seed a TT collocation: recognition NEW, production in ``prod_state``."""
    unit = SyntacticUnit(text=text, translation="t", word_count=2, difficulty=1, source="corpus")
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation(text)
    assert item is not None
    today = date.today()
    for direction, state, off in [
        (Direction.RECOGNITION, SRSState.NEW, 0),
        (Direction.PRODUCTION, prod_state, prod_due_offset_days),
    ]:
        due = today + timedelta(days=off)
        db.update_direction(
            item.guid,
            direction,
            DirectionState(
                direction=direction,
                due_at=datetime.combine(due, dtime(4, 0), tzinfo=UTC),
                stability=10.0,
                difficulty=5.0,
                reps=0 if state == SRSState.NEW else 1,
                lapses=0,
                state=state,
            ),
        )


@pytest.mark.oracle
def test_parity_new_buried_by_review_due_today_not_by_future(
    synthetic_collection: SyntheticCollection,
) -> None:
    """A NEW card is buried by a REVIEW sibling due *today*, but not by one due
    in the future. Anki's ``counts.new`` and TT's
    ``count_new_available_collocations`` must agree (== 1: the future-sibling
    note survives, the due-today-sibling note is buried)."""
    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)
    synthetic_collection.set_bury(bury_new=True, bury_reviews=True)
    _add_dual_notetype(synthetic_collection)

    now_secs = int(time.time())
    past = now_secs - 5 * 86400

    # Note 100: production review due today (due=0 → overdue → gathered) → rec buried.
    buried_rec, _ = _add_dual_note(
        synthetic_collection,
        note_id=100,
        prod_type=2,
        prod_queue=2,
        prod_due=0,
        prod_last_review_secs=past,
    )
    # Note 200: production review due far in the future (not gathered) → rec survives.
    survivor_rec, _ = _add_dual_note(
        synthetic_collection,
        note_id=200,
        prod_type=2,
        prod_queue=2,
        prod_due=FUTURE_DUE_DAY,
        prod_last_review_secs=past,
    )
    synthetic_collection.save()

    result = run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    )
    counts = result.raw()["get_queue_0"]["counts"]
    queued_ids = {c["card_id"] for c in result.raw()["get_queue_0"]["cards"]}

    assert counts["new"] == 1, (
        f"Anki should bury the due-today-sibling new card but keep the future-sibling one; "
        f"counts.new={counts['new']} (expected 1). counts={counts}"
    )
    assert buried_rec not in queued_ids, f"new card {buried_rec} (review sibling due today) should be buried"
    assert survivor_rec in queued_ids, f"new card {survivor_rec} (review sibling due future) should survive"

    # TT side: same logical scenario.
    db = SRSDatabase(":memory:")
    _tt_seed(db, "buried_one", SRSState.REVIEW, prod_due_offset_days=0)
    _tt_seed(db, "survivor", SRSState.REVIEW, prod_due_offset_days=30)
    tt_new = db.count_new_available_collocations(date.today())

    assert tt_new == counts["new"], f"TT count_new_available_collocations={tt_new} != Anki counts.new={counts['new']}"


@pytest.mark.oracle
def test_parity_new_buried_by_learning_sibling(synthetic_collection: SyntheticCollection) -> None:
    """A NEW card is buried when its sibling sits in the learning queue. Anki's
    ``counts.new`` and TT's ``count_new_available_collocations`` agree (== 0)."""
    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)
    synthetic_collection.set_bury(bury_new=True, bury_reviews=True)
    _add_dual_notetype(synthetic_collection)

    now_secs = int(time.time())

    # Production in the intraday learning queue (due shortly, before rollover).
    buried_rec, _ = _add_dual_note(
        synthetic_collection,
        note_id=300,
        prod_type=1,
        prod_queue=1,
        prod_due=now_secs + 60,
        prod_last_review_secs=now_secs - 600,
    )
    synthetic_collection.save()

    result = run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    )
    counts = result.raw()["get_queue_0"]["counts"]
    queued_ids = {c["card_id"] for c in result.raw()["get_queue_0"]["cards"]}

    assert counts["new"] == 0, (
        f"Anki should bury the new card whose sibling is in the learning queue; counts.new={counts['new']}. counts={counts}"
    )
    assert buried_rec not in queued_ids, f"new card {buried_rec} (learning sibling) should be buried"

    db = SRSDatabase(":memory:")
    _tt_seed(db, "learning_sibling", SRSState.LEARNING)
    tt_new = db.count_new_available_collocations(date.today())

    assert tt_new == counts["new"], f"TT count_new_available_collocations={tt_new} != Anki counts.new={counts['new']}"


@pytest.mark.oracle
def test_parity_both_new_siblings_collapse_to_one(synthetic_collection: SyntheticCollection) -> None:
    """A note with both directions NEW contributes ONE new card, not two: Anki
    gathers the first and buries the second (Layer 28). Three such notes → Anki
    ``counts.new == 3`` (and serves 3 cards), matching TT's
    ``COUNT(DISTINCT collocation_id)``. This is the case behind the badge widget
    in ``frontend/tests/review-flow.spec.ts`` (3 words → "3", not "6")."""
    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)
    synthetic_collection.set_bury(bury_new=True, bury_reviews=True)
    _add_dual_notetype(synthetic_collection)

    for note_id in (100, 200, 300):
        # prod_type/queue=0 makes the production sibling NEW too (both directions new).
        _add_dual_note(
            synthetic_collection,
            note_id=note_id,
            prod_type=0,
            prod_queue=0,
            prod_due=note_id,
            prod_last_review_secs=None,
        )
    synthetic_collection.save()

    result = run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    )
    counts = result.raw()["get_queue_0"]["counts"]
    served = result.raw()["get_queue_0"]["cards"]

    assert counts["new"] == 3, f"Anki buries the 2nd new sibling per note → 3, got counts.new={counts['new']}"
    assert len(served) == 3, f"Anki serves one new card per note (3), got {len(served)}"

    db = SRSDatabase(":memory:")
    for text in ("zdravo", "hvala", "prosim"):
        _tt_seed(db, text, SRSState.NEW)
    tt_new = db.count_new_available_collocations(date.today())

    assert tt_new == counts["new"], f"TT count_new_available_collocations={tt_new} != Anki counts.new={counts['new']}"


def test_dual_notetype_builds_two_sibling_cards(synthetic_collection: SyntheticCollection) -> None:
    """The 2-template notetype yields two cards on one note (no ``--run-oracle``).

    Pins the harness extension itself: ``add_notetype(..., template_count=2)``
    plus two ``add_card`` calls produce a note whose cards share a ``nid`` and
    differ by ``ord`` — the precondition for cross-direction sibling bury.
    """
    import sqlite3

    _add_dual_notetype(synthetic_collection)
    _add_dual_note(
        synthetic_collection,
        note_id=400,
        prod_type=2,
        prod_queue=2,
        prod_due=0,
        prod_last_review_secs=int(time.time()) - 86400,
    )
    synthetic_collection.save()

    with sqlite3.connect(str(synthetic_collection.path)) as conn:
        templates = conn.execute("SELECT ord FROM templates WHERE ntid = ?", (DUAL_MID,)).fetchall()
        cards = conn.execute("SELECT ord FROM cards WHERE nid = 400 ORDER BY ord").fetchall()

    assert {t[0] for t in templates} == {0, 1}, "dual notetype must declare two templates"
    assert [c[0] for c in cards] == [0, 1], "the dual note must have a card per ord (0=recognition, 1=production)"
