"""Generating a review session — a story with no curriculum and no day.

bd tunatale-2r5v, second child of tunatale-9p9d.

⚠️ THE ROUTE MUST NOT ACCEPT A CURRICULUM OR A DAY. That is not a validation
nicety, it is the epic's decision expressed as an interface: a review session is
drawn from the whole language deck, so a route that lets a caller scope it to one
plan is Option C undone. ``test_it_refuses_a_curriculum_id`` is the guard.

WHY THIS IS A SMALL CHANGE, measured before it was written: a REVIEW prompt
consumes NOTHING from a CurriculumDay. The REVIEW template's format fields are
exactly cefr_block / language_code / language_name / review_collocations, and two
deliberately unrelated days produce byte-identical prompts through the real
``build_story_prompts``. ``test_it_matches_what_the_day_shaped_path_produces``
pins that, so the new dayless entry point cannot drift from the old one.

CEFR comes from the most recent curriculum in the language, falling back to
"A2" when the learner has none. The fallback is not an edge case — it is the
proof that a session can exist outside any plan, which is the whole point, so
``test_with_no_curricula_at_all_it_still_works`` is load-bearing too.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.common.guid import compute_guid
from app.generation.story import (
    NoReviewVocabularyError,
    StoryGenerator,
    build_review_session_prompts,
    build_story_prompts,
)
from app.languages import get_language
from app.main import app
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.strategy import ContentStrategy
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

WORD = "zapadla_beseda"

# ── helpers ──────────────────────────────────────────────────────────────────


def _day() -> CurriculumDay:
    return CurriculumDay(
        day=1,
        title="Ordering Coffee",
        focus="Café vocabulary",
        collocations=["dober dan"],
        learning_objective="Order a coffee",
        story_guidance="Scene at a Ljubljana café",
    )


def _story() -> dict:
    return {
        "title": "A Missed Train",
        "key_phrases": [{"phrase": "dober dan", "translation": "good day"}],
        "scenes": [
            {
                "label": "On the Platform",
                "lines": [
                    {"speaker": "female-1", "text": "Dober dan!", "translation": "Good day!"},
                    {"speaker": "male-1", "text": "Prosim kavo.", "translation": "A coffee please."},
                ],
            }
        ],
        "dialogue_glosses": [{"word": "kavo", "translation": "coffee"}],
        "morphology_focus": [],
    }


@pytest.fixture
def seeded_db():
    """One overdue RECOGNITION card, so the review pool is non-empty.

    ⚠️ A NEW item is excluded from the pool BY STATE, so seeding one and
    expecting it to appear fails for a reason that has nothing to do with the
    code under test. It is promoted to REVIEW here deliberately.
    """
    from datetime import UTC, datetime, timedelta

    with SRSDatabase(":memory:") as db:
        unit = SyntacticUnit(text=WORD, translation="gloss", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        db.update_direction(
            compute_guid(WORD, "sl", ""),
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.REVIEW,
                due_at=datetime.now(UTC) - timedelta(days=20),
                stability=2.0,
                last_review=datetime.now(UTC) - timedelta(days=20, hours=3),
                reps=4,
            ),
        )
        yield db


@pytest.fixture
def language():
    return get_language("sl")


def _generator_returning_a_story():
    """A real StoryGenerator over a fake LLM.

    Real, not an AsyncMock, so the prompt the model receives is a genuine
    artifact the test can read — the same thing step 4 of the manual shakedown
    checks by hand. ``client.complete`` is the process boundary and the only
    thing faked.
    """
    import json

    client = MagicMock()
    client.complete = AsyncMock(return_value=json.dumps(_story()))
    client.last_finish_reason = "stop"
    return StoryGenerator(llm_client=client), client


def _prompt_sent(client) -> str:
    return client.complete.await_args.args[0]


# ── the prompt needs no day ──────────────────────────────────────────────────


class TestThePromptNeedsNoDay:
    def test_it_builds_with_no_curriculum_day_at_all(self, seeded_db, language):
        prompts = build_review_session_prompts(language, "A2", srs_db=seeded_db)

        assert WORD in prompts.user_prompt
        assert prompts.review_words == (WORD,)

    def test_it_matches_what_the_day_shaped_path_produces(self, seeded_db, language):
        """The regression guard for the refactor.

        The dayless entry point and the strategy=REVIEW branch must render the
        same prompt, or there are two REVIEW prompts and one of them is stale.
        Measured before this was written: a REVIEW prompt is byte-identical
        across arbitrarily different days, which is what makes the delegation
        safe rather than merely convenient.
        """
        dayless = build_review_session_prompts(language, "A2", srs_db=seeded_db)
        day_shaped = build_story_prompts(_day(), language, ContentStrategy.REVIEW, "A2", srs_db=seeded_db)

        assert dayless.system_prompt == day_shaped.system_prompt
        assert dayless.user_prompt == day_shaped.user_prompt
        assert dayless.review_words == day_shaped.review_words

    def test_no_theme_leaks_in_from_anywhere(self, seeded_db, language):
        prompts = build_review_session_prompts(language, "A2", srs_db=seeded_db)

        assert "Café vocabulary" not in prompts.user_prompt
        assert "Ljubljana" not in prompts.user_prompt

    def test_the_cefr_level_reaches_the_prompt(self, seeded_db, language):
        prompts = build_review_session_prompts(language, "B1", srs_db=seeded_db)

        assert "B1" in prompts.user_prompt

    def test_nothing_due_refuses_rather_than_sending_an_empty_prompt(self, language):
        with SRSDatabase(":memory:") as empty, pytest.raises(NoReviewVocabularyError):
            build_review_session_prompts(language, "A2", srs_db=empty)


# ── the route ────────────────────────────────────────────────────────────────


class TestTheRoute:
    @pytest.fixture
    def stored(self):
        store = ContentStore(":memory:")
        app.state.content_store = store
        app.state.language = get_language("sl")
        return store

    def _with_curriculum(self, store, cefr_level="A2"):
        store.save_curriculum(
            "c1",
            Curriculum(id="c1", topic="t", language_code="sl", cefr_level=cefr_level, days=[_day()]),
        )

    async def _post(self, body=None):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/api/review-sessions", json={} if body is None else body)

    async def test_it_creates_a_session_and_stores_it(self, stored, seeded_db):
        self._with_curriculum(stored)
        app.state.srs_db = seeded_db
        app.state.story_generator = _generator_returning_a_story()[0]

        resp = await self._post()

        assert resp.status_code == 201
        body = resp.json()
        assert body["title"] == "A Missed Train"
        assert stored.get_review_session(body["id"]) is not None

    async def test_the_thing_it_stored_is_not_a_lesson(self, stored, seeded_db):
        """A session must not land in the lessons table under a borrowed day."""
        self._with_curriculum(stored)
        app.state.srs_db = seeded_db
        app.state.story_generator = _generator_returning_a_story()[0]

        resp = await self._post()

        assert stored.list_lessons() == []
        assert stored.get_lesson(resp.json()["id"]) is None

    async def test_it_records_what_was_asked_for_and_what_was_used(self, stored, seeded_db):
        self._with_curriculum(stored)
        app.state.srs_db = seeded_db
        app.state.story_generator = _generator_returning_a_story()[0]

        resp = await self._post()

        row = stored.get_review_session_row(resp.json()["id"])
        assert row["review_requested"] == [WORD]
        assert row["review_used"] is not None, "a generated session is always measured"

    async def test_the_session_date_is_a_calendar_date(self, stored, seeded_db):
        """Shape, not value. An oracle of ``date.today()`` would agree with the
        code by construction and still go red at a midnight boundary — and CI
        runs a job pinned to 04:xx in a shifted zone precisely to find that."""
        self._with_curriculum(stored)
        app.state.srs_db = seeded_db
        app.state.story_generator = _generator_returning_a_story()[0]

        resp = await self._post()

        row = stored.get_review_session_row(resp.json()["id"])
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["session_date"])

    async def test_it_refuses_a_curriculum_id(self, stored, seeded_db):
        """The interface IS the decision. Silently ignoring the field would let a
        caller believe it had scoped the session to one plan."""
        self._with_curriculum(stored)
        app.state.srs_db = seeded_db
        app.state.story_generator = _generator_returning_a_story()[0]

        resp = await self._post({"curriculum_id": "c1"})

        assert resp.status_code == 422

    async def test_it_refuses_a_day(self, stored, seeded_db):
        self._with_curriculum(stored)
        app.state.srs_db = seeded_db
        app.state.story_generator = _generator_returning_a_story()[0]

        resp = await self._post({"day": 1})

        assert resp.status_code == 422


# ── where the level comes from ───────────────────────────────────────────────


class TestTheLevel:
    @pytest.fixture
    def stored(self):
        store = ContentStore(":memory:")
        app.state.content_store = store
        app.state.language = get_language("sl")
        return store

    async def _post(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/api/review-sessions", json={})

    async def test_it_comes_from_the_most_recent_curriculum(self, stored, seeded_db):
        """Asserted on the prompt the model actually receives, not on a call
        shape — the same claim step 4 of the shakedown makes by hand."""
        stored.save_curriculum(
            "old", Curriculum(id="old", topic="t", language_code="sl", cefr_level="A1", days=[_day()])
        )
        stored.save_curriculum(
            "new", Curriculum(id="new", topic="t", language_code="sl", cefr_level="B2", days=[_day()])
        )
        app.state.srs_db = seeded_db
        generator, client = _generator_returning_a_story()
        app.state.story_generator = generator

        resp = await self._post()

        assert resp.status_code == 201
        assert "B2" in _prompt_sent(client)

    async def test_with_no_curricula_at_all_it_still_works(self, stored, seeded_db):
        """The proof that a review session lives outside any plan.

        If this fails, Option C has not actually been delivered — the feature
        would still require a curriculum to exist, just not to be named.
        """
        app.state.srs_db = seeded_db
        generator, client = _generator_returning_a_story()
        app.state.story_generator = generator

        resp = await self._post()

        assert resp.status_code == 201
        assert "A2" in _prompt_sent(client)


# ── refusals ─────────────────────────────────────────────────────────────────


class TestRefusals:
    @pytest.fixture
    def stored(self):
        store = ContentStore(":memory:")
        store.save_curriculum("c1", Curriculum(id="c1", topic="t", language_code="sl", cefr_level="A2", days=[_day()]))
        app.state.content_store = store
        app.state.language = get_language("sl")
        return store

    async def _post(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/api/review-sessions", json={})

    async def test_nothing_due_is_409_before_any_llm_call(self, stored):
        """Spending a Groq request to produce a contentless story is the thing
        this guard exists to prevent, so reaching the model at all is the
        failure — not merely the status code."""
        generator, client = _generator_returning_a_story()
        app.state.story_generator = generator

        with SRSDatabase(":memory:") as empty:
            app.state.srs_db = empty
            resp = await self._post()

        assert resp.status_code == 409
        assert client.complete.await_count == 0, "the LLM must not be called for a story with no content"

    async def test_the_409_says_nothing_is_due_in_this_language(self, stored):
        """Wording preserved from the deleted lesson-page handler (tunatale-q2np).
        'no vocabulary is due' is the part that tells the learner this is a
        normal Tuesday rather than a broken button."""
        generator, _ = _generator_returning_a_story()
        app.state.story_generator = generator

        with SRSDatabase(":memory:") as empty:
            app.state.srs_db = empty
            resp = await self._post()

        detail = resp.json()["detail"].lower()
        assert "nothing to review" in detail
        assert "due" in detail

    async def test_a_quota_refusal_is_429_not_502(self, stored, seeded_db):
        """A 502 here would be a lie with consequences: it reads as "the provider
        failed", which invites a retry, and a retry cannot succeed against an
        exhausted day budget. 429 says TT declined to call."""
        from app.llm.client import LLMQuotaExceededError

        client = MagicMock()
        client.complete = AsyncMock(side_effect=LLMQuotaExceededError("daily token budget exhausted"))
        app.state.story_generator = StoryGenerator(llm_client=client)
        app.state.srs_db = seeded_db

        resp = await self._post()

        assert resp.status_code == 429
        assert "budget" in resp.json()["detail"]

    async def test_an_upstream_llm_error_is_502_carrying_its_detail(self, stored, seeded_db):
        """The retry detail has to survive to the client, or the caller cannot
        tell a rate limit from an outage."""
        from app.llm.client import LLMError

        client = MagicMock()
        client.complete = AsyncMock(side_effect=LLMError("Groq returned 429 Too Many Requests (retry after 37s)"))
        app.state.story_generator = StoryGenerator(llm_client=client)
        app.state.srs_db = seeded_db

        resp = await self._post()

        assert resp.status_code == 502
        assert "retry after 37s" in resp.json()["detail"]

    async def test_a_malformed_story_is_still_502(self, stored, seeded_db):
        """The sibling guard from tunatale-q2np: ONLY the review refusal was
        reworded. A 502 must still read as an upstream failure, or the rewording
        has swallowed a genuinely different condition."""
        client = MagicMock()
        client.complete = AsyncMock(return_value="not json at all")
        client.last_finish_reason = "stop"
        app.state.story_generator = StoryGenerator(llm_client=client)
        app.state.srs_db = seeded_db

        resp = await self._post()

        assert resp.status_code == 502
