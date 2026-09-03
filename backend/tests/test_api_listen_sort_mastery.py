"""F-4: the listen preview's within-group order — day granularity, then mastery.

The docstring promised "mastery ascending (least-known first)"; the code sorted
by ``(_group_rank, due_at)``. Against the live deck that secondary key carries
**no information at all**: every REVIEW-state ``due_at`` is date-encoded at
exactly 04:00 UTC (``rollover.py::due_at_rollover_utc``), so all 89 cards due on
the same day tie and Python's stable sort falls back to lesson-appearance order.
The user reads a mastery colour ramp laid out in no order.

The ratified key (user, 2026-08-04):

  * learning / relearning → ``due_at`` at **full precision** (ripening order).
    These cards ripen in minutes and carry real sub-day times; truncating them
    to a day would order ripening cards by mastery instead of by when they
    come up.
  * due / ahead → ``due_day``, then **mastery ascending** (least-known first).

Every test here seeds cards that TIE under the old key, so the old key returns
lesson-appearance order and cannot pass by luck — the floor-shadow shape the
brief warns about is a test with one card per day, which passes under both keys.
"""

from __future__ import annotations

import datetime
from datetime import UTC, timedelta

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

PREVIEW_URL = "/api/srs/content/lesson-1/listen-preview"


def _setup(phrases: list[str], language_code: str = "sl"):
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    lesson = Lesson(
        title="Day 1",
        language_code=language_code,
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[
                    Phrase(text=t, voice_id="female-1", language_code=language_code, role="female-1") for t in phrases
                ],
            )
        ],
        key_phrases=[],
    )
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    return db


def _seed_review(db, text: str, *, days_until_due: int, stability: float) -> None:
    """A REVIEW card due on ``anki_today() + days_until_due`` at the 04:00 UTC
    convention, with an explicit FSRS stability.

    Stability is what mastery reads (``component_mastery``: log10(s) normalised
    against a 120-day ceiling), so it is the only knob that moves a REVIEW row's
    ``progress`` — hence the only way to make two same-day rows differ.
    """
    from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc

    unit = SyntacticUnit(text=text, translation=f"t-{text}", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation(text)
    for direction in (Direction.RECOGNITION, Direction.PRODUCTION):
        ds = item.directions[direction]
        ds.state = SRSState.REVIEW
        ds.stability = stability
        ds.due_at = due_at_rollover_utc(anki_today() + timedelta(days=days_until_due))
        ds.last_review = datetime.datetime.now(UTC) - timedelta(days=5)
    db.update_collocation(item)


def _seed_learning_at(db, text: str, *, due_at: datetime.datetime) -> None:
    """A LEARNING card with a real sub-day ``due_at`` (the ripening time)."""
    unit = SyntacticUnit(text=text, translation=f"t-{text}", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation(text)
    rec = item.directions[Direction.RECOGNITION]
    rec.state = SRSState.LEARNING
    rec.due_at = due_at
    db.update_collocation(item)


async def _get_preview() -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(PREVIEW_URL)
    assert resp.status_code == 200
    return resp.json()


def _texts(preview: dict, grade_class: str) -> list[str]:
    return [c["text"] for c in preview["candidates"] if c["grade_class"] == grade_class]


class TestDueGroupMasteryTieBreak:
    """Three cards due the SAME day come back least-known first."""

    async def test_same_day_due_cards_order_by_mastery_ascending(self):
        # All three are due today, so under the old (_group_rank, due_at) key
        # they tie exactly and the stable sort returns lesson order:
        # alpha, beta, gamma. Mastery ascending is beta (s=2) < gamma (s=30)
        # < alpha (s=100), a different permutation on every position.
        db = _setup(["alpha beta gamma"])
        _seed_review(db, "alpha", days_until_due=0, stability=100.0)
        _seed_review(db, "beta", days_until_due=0, stability=2.0)
        _seed_review(db, "gamma", days_until_due=0, stability=30.0)

        preview = await _get_preview()
        assert _texts(preview, "due") == ["beta", "gamma", "alpha"]

    async def test_progress_is_monotonically_non_decreasing_across_the_due_group(self):
        # The property the ordering exists to produce: the colour ramp reads
        # red → green down the list, with no scattering.
        db = _setup(["alpha beta gamma delta"])
        _seed_review(db, "alpha", days_until_due=0, stability=90.0)
        _seed_review(db, "beta", days_until_due=0, stability=1.0)
        _seed_review(db, "gamma", days_until_due=0, stability=45.0)
        _seed_review(db, "delta", days_until_due=0, stability=8.0)

        preview = await _get_preview()
        progress = [c["progress"] for c in preview["candidates"] if c["grade_class"] == "due"]
        assert len(progress) == 4
        assert progress == sorted(progress)


class TestAheadGroupMasteryTieBreak:
    """The same tie-break governs the not-yet-due group."""

    async def test_same_day_ahead_cards_order_by_mastery_ascending(self):
        db = _setup(["alpha beta gamma"])
        _seed_review(db, "alpha", days_until_due=3, stability=100.0)
        _seed_review(db, "beta", days_until_due=3, stability=2.0)
        _seed_review(db, "gamma", days_until_due=3, stability=30.0)

        preview = await _get_preview()
        assert _texts(preview, "ahead") == ["beta", "gamma", "alpha"]


class TestDueDayStillOutranksMastery:
    """Mastery is the SECONDARY key — an earlier day always sorts first.

    This is the guard against 'fix' #1, which the brief records as wrong:
    replacing ``due_at`` with mastery outright would discard the day ordering.
    """

    async def test_earlier_day_wins_even_when_better_known(self):
        # alpha is the best-known card in the group AND the soonest due; a
        # mastery-only key would sink it to last.
        db = _setup(["alpha beta gamma"])
        _seed_review(db, "alpha", days_until_due=1, stability=100.0)
        _seed_review(db, "beta", days_until_due=2, stability=2.0)
        _seed_review(db, "gamma", days_until_due=3, stability=1.0)

        preview = await _get_preview()
        assert _texts(preview, "ahead") == ["alpha", "beta", "gamma"]


class TestLearningGroupKeepsFullPrecision:
    """Learning cards ripen in minutes — their sub-day time must not be truncated.

    Both cards here fall on the same calendar day and carry identical mastery
    (``component_mastery`` pins every LEARNING component at the 0.15 floor), so
    a day-truncating key ties them and returns lesson order — alpha, beta. Only
    a full-precision key returns ripening order.
    """

    async def test_same_day_learning_cards_order_by_time_not_lesson_order(self):
        from app.srs.anki_mirror.rollover import anki_today

        day = anki_today() + timedelta(days=1)
        db = _setup(["alpha beta"])
        _seed_learning_at(db, "alpha", due_at=datetime.datetime.combine(day, datetime.time(14, 20), tzinfo=UTC))
        _seed_learning_at(db, "beta", due_at=datetime.datetime.combine(day, datetime.time(9, 5), tzinfo=UTC))

        preview = await _get_preview()
        assert _texts(preview, "learning") == ["beta", "alpha"]
