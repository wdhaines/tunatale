"""Stage 3: PENDING listen grades are hidden from the review badge and the served main queue.

The deliberate TT-only parity divergence documented in
``.claude/rules/anki-queue-parity.md`` and ``docs/anki-parity-layers.md``: a card
a listen has staged is provisionally handled, so TT must not also offer it in the
main review flow (the user would grade the same card twice — once in the main
queue, once in "Check your work"). Anki has no notion of a pending listen grade,
so its review count sits ABOVE TT's by the pending count until the user releases
them. This resolves the moment a pending card is applied.

Scope note: the exclusion covers the *review* pool only. A listen also stages
LEARNING-state cards, which serve from the learning queue and the learning badge
— those are untouched here, and grading one there applies + clears its pending
row like any other real grade, so it can't double-apply.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.queue_stats import resolve_bury_new, resolve_bury_review
from app.srs.database import SRSDatabase


def _seed(
    db: SRSDatabase,
    text: str,
    rec_state: SRSState = SRSState.REVIEW,
    prod_state: SRSState = SRSState.REVIEW,
    due_offset_days: int = 0,
) -> int:
    """Add one collocation with both directions in the given states. Returns its id."""
    unit = SyntacticUnit(text=text, translation="t", word_count=1, difficulty=1, source="corpus")
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation(text)
    assert item is not None
    due = date.today() + timedelta(days=due_offset_days)
    for direction, state in [(Direction.RECOGNITION, rec_state), (Direction.PRODUCTION, prod_state)]:
        # 04:00-UTC due_at convention — an instant-flavored due_at reads as
        # not-due past 20:00 local (see count_review_due_collocations' docstring).
        ds = DirectionState(
            direction=direction,
            due_at=datetime.combine(due, time(4, 0), tzinfo=UTC),
            stability=1.0,
            difficulty=5.0,
            reps=0 if state == SRSState.NEW else 1,
            lapses=0,
            state=state,
        )
        db.update_direction(item.guid, direction, ds)
    cid = db.get_collocation_id_by_guid(item.guid)
    assert cid is not None
    return cid


@pytest.fixture
def db() -> SRSDatabase:
    d = SRSDatabase(":memory:")
    try:
        yield d
    finally:
        d.close()


class TestReviewBadgeExcludesPending:
    def test_pending_collocation_drops_out_of_the_review_count(self, db):
        cid = _seed(db, "hvala")
        assert db.count_review_due_collocations(date.today()) == 1

        db.stage_pending_grade("lesson-1", cid, Direction.RECOGNITION.value, "good", "due")

        assert db.count_review_due_collocations(date.today()) == 0

    def test_it_comes_back_once_the_pending_row_is_cleared(self, db):
        cid = _seed(db, "hvala")
        db.stage_pending_grade("lesson-1", cid, Direction.RECOGNITION.value, "good", "due")
        assert db.count_review_due_collocations(date.today()) == 0

        db.clear_pending_grade(cid, Direction.RECOGNITION.value)

        assert db.count_review_due_collocations(date.today()) == 1

    def test_only_the_pending_collocation_drops(self, db):
        pending_cid = _seed(db, "hvala")
        _seed(db, "banka")
        assert db.count_review_due_collocations(date.today()) == 2

        db.stage_pending_grade("lesson-1", pending_cid, Direction.RECOGNITION.value, "good", "due")

        assert db.count_review_due_collocations(date.today()) == 1

    def test_a_pending_row_on_one_direction_hides_the_whole_collocation(self, db):
        """Collocation-level, like every other filter in this count.

        A listen stages recognition; the production sibling must go with it,
        exactly as the graded-today and learning-sibling filters behave. Serving
        the sibling would put the note back in the main flow the staging was
        meant to keep it out of.
        """
        cid = _seed(db, "hvala")
        db.stage_pending_grade("lesson-1", cid, Direction.RECOGNITION.value, "good", "due")

        assert db.count_review_due_collocations(date.today()) == 0


class TestServedMainQueueExcludesPending:
    def test_pending_collocation_is_not_served(self, db):
        from app.srs.anki_mirror.queue_engine import _compute_live_main

        cid = _seed(db, "hvala")
        assert cid in {t[0] for t in _compute_live_main(db)}

        db.stage_pending_grade("lesson-1", cid, Direction.RECOGNITION.value, "good", "due")

        assert cid not in {t[0] for t in _compute_live_main(db)}

    def test_it_is_served_again_once_applied(self, db):
        from app.srs.anki_mirror.queue_engine import _compute_live_main

        cid = _seed(db, "hvala")
        db.stage_pending_grade("lesson-1", cid, Direction.RECOGNITION.value, "good", "due")
        assert cid not in {t[0] for t in _compute_live_main(db)}

        db.clear_pending_grade(cid, Direction.RECOGNITION.value)

        assert cid in {t[0] for t in _compute_live_main(db)}

    def test_a_non_pending_sibling_card_still_serves(self, db):
        from app.srs.anki_mirror.queue_engine import _compute_live_main

        pending_cid = _seed(db, "hvala")
        other_cid = _seed(db, "banka")
        db.stage_pending_grade("lesson-1", pending_cid, Direction.RECOGNITION.value, "good", "due")

        served = {t[0] for t in _compute_live_main(db)}
        assert other_cid in served
        assert pending_cid not in served

    def test_a_new_sibling_of_a_pending_card_stays_buried(self, db):
        """The exclusion must not leak into Layer 64's new-card arithmetic.

        Anki WOULD have gathered the review direction and buried its NEW sibling.
        TT withholds the review card because it is pending — but the sibling must
        still be buried, or the served queue surfaces a new card that the badge
        (``count_new_available_collocations``, deliberately untouched here) still
        hides. Pending collocations therefore seed the sibling-bury seen-set.
        """
        from app.srs.anki_mirror.queue_engine import _compute_live_main

        cid = _seed(db, "hvala", rec_state=SRSState.REVIEW, prod_state=SRSState.NEW)
        # bury_new / bury_review default to True; note the cache stores the
        # literal "True", so seeding "1" would silently turn bury OFF.
        assert resolve_bury_new(db)[0] and resolve_bury_review(db)[0]
        assert db.count_new_available_collocations(date.today()) == 0

        db.stage_pending_grade("lesson-1", cid, Direction.RECOGNITION.value, "good", "due")

        assert db.count_new_available_collocations(date.today()) == 0
        assert cid not in {t[0] for t in _compute_live_main(db)}

    def test_new_cards_are_unaffected(self, db):
        """A listen never stages a NEW direction (_listen_grade_class returns None
        for NEW), so the new pool has nothing to exclude and Layer 64's bury
        arithmetic must not shift."""
        from app.srs.anki_mirror.queue_engine import _compute_live_main

        review_cid = _seed(db, "hvala")
        new_cid = _seed(db, "banka", rec_state=SRSState.NEW, prod_state=SRSState.NEW)
        before_new = db.count_new_available_collocations(date.today())

        db.stage_pending_grade("lesson-1", review_cid, Direction.RECOGNITION.value, "good", "due")

        assert db.count_new_available_collocations(date.today()) == before_new
        assert new_cid in {t[0] for t in _compute_live_main(db)}
