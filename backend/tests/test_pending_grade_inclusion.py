"""F-14: a staged listen grade no longer hides its card from the main review flow.

**This file is the inverse of the oracle it replaces** (`test_pending_grade_exclusion.py`,
renamed here because its thesis flipped). Layer 81 introduced a deliberate TT-only
divergence: a card with a `pending_listen_grades` row was withheld from both the
review badge and the served main queue, so TT's review count sat BELOW Anki's by
the pending count. F-14 (user decision, 2026-08-05) retires it:

    "What harm is it to review the card in the queue, commit it, and remove it
    from staging at that point if you do it in that order? That way it matches
    Anki and seems a bit more intuitive to me."

A staged card that is DUE now belongs in the main queue and the badge, exactly as
Anki counts it. Grading it there applies a real grade and releases the staging, in
that order. The old "the user would grade it twice" rationale was already stale:
`drill_feedback` clears the pending row unconditionally on any real grade.

**Why these assertions are shaped this way.** The badge and the served queue mirror
each other by construction, so a surface-vs-surface test ("they agree") passes both
before AND after F-14 and proves nothing — F-5's transferable lesson, which applies
to F-14 unchanged. Every test below pins ABSOLUTE membership against the
`pending_listen_grades` table: the card is staged (asserted), and it is present
(asserted), rather than two surfaces being compared to each other.

Requirement 3 below exists for the same reason in the other direction: a NOT-due
staged card is absent, but it must not be allowed to pass for the wrong reason —
it was never due in the first place, so the test pins dueness separately.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.queue_stats import resolve_bury_new, resolve_bury_review
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from app.srs.database import SRSDatabase
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

LESSON_ID = "lesson-1"


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
    due = anki_today() + timedelta(days=due_offset_days)
    for direction, state in [(Direction.RECOGNITION, rec_state), (Direction.PRODUCTION, prod_state)]:
        # 04:00-UTC due_at convention via due_at_rollover_utc — an
        # instant-flavored due_at reads as not-due past 20:00 local (see
        # count_review_due_collocations' docstring). Seed and assertions both
        # use anki_today(), never date.today(): inside [midnight, 04:00) UTC
        # the two disagree, and date.today() would seed the card into
        # TOMORROW's Anki day, so it is legitimately not yet due. Reproduce
        # with TZ=UTC — a dev box on EDT hides it, CI runs UTC.
        ds = DirectionState(
            direction=direction,
            due_at=due_at_rollover_utc(due),
            stability=1.0,
            difficulty=5.0,
            reps=0 if state == SRSState.NEW else 1,
            lapses=0,
            state=state,
            last_review=datetime.now(UTC) - timedelta(days=10) if state != SRSState.NEW else None,
        )
        db.update_direction(item.guid, direction, ds)
    cid = db.get_collocation_id_by_guid(item.guid)
    assert cid is not None
    return cid


def _assert_staged(db: SRSDatabase, cid: int, direction: Direction = Direction.RECOGNITION) -> None:
    """Pin the premise against the TABLE, not against a surface that mirrors it.

    Every inclusion assertion in this file is only meaningful if the card really
    does hold a pending row — otherwise "it is in the queue" passes trivially.
    """
    assert db.get_pending_grade(cid, direction.value) is not None, "premise: the card must be staged"


@pytest.fixture
def db() -> SRSDatabase:
    d = SRSDatabase(":memory:")
    app.state.srs_db = d
    try:
        yield d
    finally:
        d.close()


def _with_lesson(db: SRSDatabase, phrases: list[str]) -> SRSDatabase:
    """Attach a content store + lesson so /lesson/{id}/review-queue is reachable."""
    from app.storage.store import ContentStore

    lesson = Lesson(
        title="Day 1",
        language_code="sl",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text=t, voice_id="female-1", language_code="sl", role="female-1") for t in phrases],
            )
        ],
        key_phrases=[],
    )
    store = ContentStore(":memory:")
    store.save_lesson(LESSON_ID, "curriculum-1", 1, lesson)
    app.state.content_store = store
    db.set_anki_state_cache("daily_new_cap", "0")
    return db


async def _review_queue_texts() -> list[str]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/srs/review-queue?session_start=1")
    assert resp.status_code == 200, resp.text
    return [i["text"] for i in resp.json()["queue"]]


async def _review_badge() -> int:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/srs/queue-stats")
    assert resp.status_code == 200, resp.text
    return resp.json()["review"]


async def _lesson_queue_texts() -> list[str]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/srs/lesson/{LESSON_ID}/review-queue")
    assert resp.status_code == 200, resp.text
    return [i["text"] for i in resp.json()["queue"]]


async def _grade(cid: int, rating: str = "good", *, lesson_review: bool = False) -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/api/srs/items/{cid}/direction/recognition/feedback",
            json={"rating": rating, "lesson_review": lesson_review},
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestReviewBadgeCountsPending:
    """Requirement 1, badge half: a staged DUE card is counted, like Anki counts it."""

    def test_a_staged_collocation_stays_in_the_review_count(self, db):
        cid = _seed(db, "hvala")
        assert db.count_review_due_collocations(anki_today()) == 1

        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")
        _assert_staged(db, cid)

        assert db.count_review_due_collocations(anki_today()) == 1, (
            "F-14: staging is provisional and must not remove the card from the badge"
        )

    def test_clearing_the_pending_row_changes_nothing(self, db):
        """The Layer 81 mechanism inverted: the count was never conditional on staging.

        Under the exclusion this went 0 → 1. It must now be 1 → 1, so a regression
        that re-adds the filter cannot hide behind an unchanged final value.
        """
        cid = _seed(db, "hvala")
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")
        assert db.count_review_due_collocations(anki_today()) == 1

        db.clear_pending_grade(cid, Direction.RECOGNITION.value)

        assert db.count_review_due_collocations(anki_today()) == 1

    def test_every_staged_due_card_is_counted_not_just_one(self, db):
        """Absolute membership: the count equals the whole due pool, staged or not."""
        staged_cid = _seed(db, "hvala")
        _seed(db, "banka")
        assert db.count_review_due_collocations(anki_today()) == 2

        db.stage_pending_grade(LESSON_ID, staged_cid, Direction.RECOGNITION.value, "good", "due")
        _assert_staged(db, staged_cid)

        assert db.count_review_due_collocations(anki_today()) == 2

    def test_a_pending_row_on_one_direction_does_not_hide_the_sibling(self, db):
        """The collocation-level hiding is gone in BOTH directions.

        The retired filter was collocation-scoped: staging recognition took the
        production sibling with it. Neither leaves the pool now.
        """
        cid = _seed(db, "hvala")
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")
        _assert_staged(db, cid)

        assert db.count_review_due_collocations(anki_today()) == 1


class TestServedMainQueueIncludesPending:
    """Requirement 1, queue half — pinned at the engine, below any API filtering."""

    def test_a_staged_collocation_is_served(self, db):
        from app.srs.anki_mirror.queue_engine import _compute_live_main

        cid = _seed(db, "hvala")
        assert cid in {t[0] for t in _compute_live_main(db)}

        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")
        _assert_staged(db, cid)

        assert cid in {t[0] for t in _compute_live_main(db)}

    def test_serving_is_not_conditional_on_the_pending_row(self, db):
        from app.srs.anki_mirror.queue_engine import _compute_live_main

        cid = _seed(db, "hvala")
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")
        assert cid in {t[0] for t in _compute_live_main(db)}

        db.clear_pending_grade(cid, Direction.RECOGNITION.value)

        assert cid in {t[0] for t in _compute_live_main(db)}

    def test_a_staged_card_occupies_a_budgeted_review_slot(self, db):
        """The one accepted cost of F-14, pinned so it cannot regress silently.

        The retired filter ran BEFORE the cap slice, so a staged card did not
        charge the review budget ("the budget is charged when the grade is
        actually applied"). Under F-14 it does — the parity gain, because Anki
        charges it too.

        The cap is 2 with two due cards, one staged, because that is the shape
        that DISCRIMINATES: pre-F-14 the staged card is filtered out and only one
        card is served; post-F-14 both are, each holding one of the two budgeted
        slots. A cap of 1 would yield "1 served" either way and pin nothing.
        """
        from app.srs.anki_mirror.queue_engine import _compute_live_main

        staged_cid = _seed(db, "hvala")
        other_cid = _seed(db, "banka")
        db.set_anki_state_cache("daily_review_cap", "2")
        db.stage_pending_grade(LESSON_ID, staged_cid, Direction.RECOGNITION.value, "good", "due")
        _assert_staged(db, staged_cid)

        served = {t[0] for t in _compute_live_main(db) if t[1].directions[t[3]].state == SRSState.REVIEW}

        assert served == {staged_cid, other_cid}, "a staged card takes a budgeted slot like any other due card"


class TestNotDueStagedCardIsStillAbsent:
    """Requirement 3 — absent, but pinned so it cannot pass for the wrong reason.

    A listen also stages cards that are due AHEAD of schedule. Those stay out of
    the main flow, and the reason must be dueness alone. Each test therefore
    asserts the card is staged AND that an identically-seeded card without a
    pending row is equally absent — the control that separates the two causes.
    """

    def test_a_staged_not_due_card_is_not_counted(self, db):
        cid = _seed(db, "hvala", due_offset_days=5)
        assert db.count_review_due_collocations(anki_today()) == 0, "control: not due before staging either"

        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "ahead")
        _assert_staged(db, cid)

        assert db.count_review_due_collocations(anki_today()) == 0

    def test_a_staged_not_due_card_is_not_served(self, db):
        from app.srs.anki_mirror.queue_engine import _compute_live_main

        cid = _seed(db, "hvala", due_offset_days=5)
        control_cid = _seed(db, "banka", due_offset_days=5)
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "ahead")
        _assert_staged(db, cid)

        served = {t[0] for t in _compute_live_main(db)}

        assert cid not in served
        assert control_cid not in served, "control: the un-staged twin is absent too, so dueness is the cause"


class TestApiSurfacesServeAStagedDueCard:
    """Requirement 1 at the real surfaces the user meets."""

    async def test_the_review_queue_endpoint_offers_it(self, db):
        cid = _seed(db, "hvala")
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")
        _assert_staged(db, cid)

        assert "hvala" in await _review_queue_texts()

    async def test_the_review_badge_counts_it(self, db):
        cid = _seed(db, "hvala")
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")
        _assert_staged(db, cid)

        assert await _review_badge() == 1


class TestGradingInTheMainFlowReleasesTheStaging:
    """Requirement 2: grade in the main queue → real grade, staging released."""

    async def test_grading_clears_the_pending_row(self, db):
        _with_lesson(db, ["hvala"])
        cid = _seed(db, "hvala")
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")
        assert "hvala" in await _review_queue_texts()

        await _grade(cid)

        assert db.get_pending_grade(cid, Direction.RECOGNITION.value) is None, (
            "a real grade must never leave a pending row behind"
        )

    async def test_grading_drops_it_from_check_your_work(self, db):
        """The other half of requirement 2 — the card is not offered twice.

        This is the objection F-14 has to answer: if the main queue serves a
        staged card, does the user still meet it in "Check your work"? No — the
        lesson queue is exactly this lesson's pending rows, and the grade cleared
        the row.
        """
        _with_lesson(db, ["hvala"])
        cid = _seed(db, "hvala")
        db.record_listen(LESSON_ID)
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")
        assert await _lesson_queue_texts() == ["hvala"]

        await _grade(cid)

        assert await _lesson_queue_texts() == []

    async def test_the_grade_is_real_not_provisional(self, db):
        """Released through the ordinary path: reps advance and the row goes dirty
        so a normal sync pushes it. A grade that only cleared staging would leave
        the card unreviewed."""
        _with_lesson(db, ["hvala"])
        cid = _seed(db, "hvala")
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")

        await _grade(cid)

        rec = db.get_collocation("hvala").directions[Direction.RECOGNITION]
        assert rec.reps == 2
        assert rec.dirty_fsrs is True


class TestNoSecondOfferInTheSameSession:
    """Requirement 4 — the frozen main queue must not re-serve a released card.

    Mechanism 3 of the F-14 write-up: `_compute_live_main` intersects the frozen
    order against a freshly computed live pool on every call, so a card that is
    no longer due drops out even though the frozen order still names it. Pinned
    here because it is the load-bearing reason the double-question objection does
    not apply — if it ever stopped holding, F-14 would be a user-visible bug.
    """

    async def test_a_graded_staged_card_is_not_offered_again(self, db):
        from app.srs.anki_mirror.queue_engine import build_and_freeze_main_queue

        _with_lesson(db, ["hvala"])
        cid = _seed(db, "hvala")
        _seed(db, "banka")
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")

        # Freeze the session order WITH the staged card in it, as a real session
        # does at /review mount, then grade it mid-session.
        build_and_freeze_main_queue(db)
        assert "hvala" in await _review_queue_texts()

        await _grade(cid)

        remaining = await _review_queue_texts()
        assert "hvala" not in remaining, "the frozen order must not re-serve a card that is no longer due"
        assert "banka" in remaining, "control: the rest of the frozen session survives"


class TestLayer64NewCardArithmeticIsUnchanged:
    """The `| pending` seen-set term is removed with the filter — and must be a no-op.

    Under the exclusion, pending collocations were filtered out of `due` and then
    added back into the sibling-bury seen-set by hand, purely to undo that side
    effect. With the filter gone they seed the set naturally. These tests pin the
    OUTCOME (a NEW sibling stays buried; the new pool does not shift), so the
    compensating term cannot be removed without the behaviour being checked.
    """

    def test_a_new_sibling_of_a_staged_card_stays_buried(self, db):
        from app.srs.anki_mirror.queue_engine import _compute_live_main

        cid = _seed(db, "hvala", rec_state=SRSState.REVIEW, prod_state=SRSState.NEW)
        # bury_new / bury_review default to True; note the cache stores the
        # literal "True", so seeding "1" would silently turn bury OFF.
        assert resolve_bury_new(db)[0] and resolve_bury_review(db)[0]
        assert db.count_new_available_collocations(anki_today()) == 0

        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")
        _assert_staged(db, cid)

        assert db.count_new_available_collocations(anki_today()) == 0
        served = _compute_live_main(db)
        assert cid in {t[0] for t in served}, "F-14: the review direction is served"
        assert [t for t in served if t[0] == cid and t[3] == Direction.PRODUCTION] == [], (
            "its NEW sibling must still be buried behind it"
        )

    def test_new_cards_are_unaffected(self, db):
        """A listen never stages a NEW direction (`_listen_grade_class` returns None
        for NEW), so the new pool has nothing to shift and Layer 64's bury
        arithmetic must not move."""
        from app.srs.anki_mirror.queue_engine import _compute_live_main

        review_cid = _seed(db, "hvala")
        new_cid = _seed(db, "banka", rec_state=SRSState.NEW, prod_state=SRSState.NEW)
        before_new = db.count_new_available_collocations(anki_today())

        db.stage_pending_grade(LESSON_ID, review_cid, Direction.RECOGNITION.value, "good", "due")
        _assert_staged(db, review_cid)

        assert db.count_new_available_collocations(anki_today()) == before_new
        assert new_cid in {t[0] for t in _compute_live_main(db)}
