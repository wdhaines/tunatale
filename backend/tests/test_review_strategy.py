"""The themeless REVIEW story strategy (bd tunatale-j1hr).

Not merely the pressure dial at maximum, which is why it is a strategy and not a
setting: WIDER and DEEPER are both ABOUT a scenario and take Theme/Focus/Story
Guidance. A review story has no theme to trade against — its content IS the
learner's decaying vocabulary.

⚠️ WITH THE THEME GONE, COHERENCE IS THE ONLY CONSTRAINT LEFT, and nothing stops
a model from emitting a word list wearing a thin coat of dialogue. "Reasonably"
in the user's phrasing does real work: the result still has to be a scene someone
could plausibly overhear.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.common.guid import compute_guid
from app.generation.prompts import COHERENCE_FLOOR, get_strategy_prompt
from app.generation.story import NoReviewVocabularyError, build_story_prompts
from app.languages import get_language
from app.main import app
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.strategy import ContentStrategy, ReviewPressure
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

WORD = "zapadla_beseda"


def _day() -> CurriculumDay:
    return CurriculumDay(
        day=1,
        title="Ordering Coffee",
        focus="Café vocabulary",
        collocations=["dober dan"],
        learning_objective="Order a coffee",
        story_guidance="Scene at a Ljubljana café",
    )


@pytest.fixture
def seeded_db():
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


class TestTheTemplate:
    def test_it_carries_no_theme(self):
        """The distinguishing property. If any of these survive, REVIEW is just
        WIDER with a louder review section and should not exist."""
        template = get_strategy_prompt(ContentStrategy.REVIEW)
        for placeholder in ("{focus}", "{story_guidance}", "{learning_objective}", "{new_collocations}"):
            assert placeholder not in template

    def test_it_still_carries_the_review_slot_and_the_cefr_block(self):
        template = get_strategy_prompt(ContentStrategy.REVIEW)
        assert "{review_collocations}" in template
        assert "{cefr_block}" in template

    def test_it_says_the_scene_must_still_be_plausible(self):
        """Coherence is the only constraint left once the theme is gone, so the
        template has to carry it in its own rules, not only inherit it from the
        review block's shared floor."""
        assert "plausibly" in get_strategy_prompt(ContentStrategy.REVIEW)


class TestBuilding:
    def test_the_prompt_contains_the_due_words(self, seeded_db, language):
        prompts = build_story_prompts(_day(), language, ContentStrategy.REVIEW, "A2", srs_db=seeded_db)
        assert WORD in prompts.user_prompt
        assert prompts.review_words == (WORD,)

    def test_the_day_theme_does_not_leak_in(self, seeded_db, language):
        prompts = build_story_prompts(_day(), language, ContentStrategy.REVIEW, "A2", srs_db=seeded_db)
        assert "Café vocabulary" not in prompts.user_prompt
        assert "Ljubljana" not in prompts.user_prompt

    def test_review_forces_insistent_whatever_the_dial_says(self, seeded_db, language):
        """The strategy sets both axes. NATURAL's wording — "candidates, not
        requirements", "including none of them is a correct answer" — is
        self-contradictory in a story whose only content IS those words."""
        prompts = build_story_prompts(
            _day(), language, ContentStrategy.REVIEW, "A2", srs_db=seeded_db, review_pressure=ReviewPressure.NATURAL
        )
        assert "candidates, not requirements" not in prompts.user_prompt
        assert "matters MORE than staying" in prompts.user_prompt
        assert COHERENCE_FLOOR in prompts.user_prompt


class TestNothingToReview:
    """The one case that cannot reuse the empty-set rule the other strategies
    hold to: there is no "today's behaviour" for REVIEW to be identical to, and
    a review story with nothing to review is a prompt with no content at all."""

    def test_an_empty_set_refuses_rather_than_sending_an_empty_prompt(self, language):
        with SRSDatabase(":memory:") as empty, pytest.raises(NoReviewVocabularyError):
            build_story_prompts(_day(), language, ContentStrategy.REVIEW, "A2", srs_db=empty)

    def test_no_db_at_all_also_refuses(self, language):
        """A caller that forgets the db must not get a themeless, contentless
        prompt — it must get an error naming the reason."""
        with pytest.raises(NoReviewVocabularyError):
            build_story_prompts(_day(), language, ContentStrategy.REVIEW, "A2")

    def test_the_other_strategies_still_accept_an_empty_set(self, language):
        """WIDER and DEEPER have a theme to fall back on, so nothing due is
        normal for them and must stay silent."""
        prompts = build_story_prompts(_day(), language, ContentStrategy.WIDER, "A2")
        assert "(none yet)" in prompts.user_prompt


class TestTheApi:
    @pytest.fixture
    def stored(self):
        store = ContentStore(":memory:")
        curriculum = Curriculum(id="c1", topic="t", language_code="sl", cefr_level="A2", days=[_day()])
        store.save_curriculum("c1", curriculum)
        app.state.content_store = store
        app.state.language = get_language("sl")
        return store

    async def test_the_prompt_export_accepts_review(self, stored, seeded_db):
        app.state.srs_db = seeded_db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/story/prompt?curriculum_id=c1&day=1&strategy=REVIEW")
        assert resp.status_code == 200
        assert WORD in resp.json()["user_prompt"]

    async def test_generate_with_nothing_due_is_409_before_any_llm_call(self, stored):
        """The same refusal on the AUTO route, and it must fire BEFORE the model
        is called — spending a Groq request to produce a contentless story is the
        thing this guard exists to avoid. A MagicMock generator would still have
        been *reached* under the defect; the real StoryGenerator raises inside
        build_story_prompts, which runs before the first `complete()`."""
        from unittest.mock import AsyncMock, MagicMock

        from app.generation.story import StoryGenerator

        client = MagicMock()
        client.complete = AsyncMock(return_value="{}")
        app.state.story_generator = StoryGenerator(llm_client=client)

        with SRSDatabase(":memory:") as empty:
            app.state.srs_db = empty
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client_http:
                resp = await client_http.post(
                    "/api/story/generate", json={"curriculum_id": "c1", "day": 1, "strategy": "REVIEW"}
                )

        assert resp.status_code == 409
        assert client.complete.await_count == 0, "the LLM must not be called for a story with no content"

    async def test_review_with_nothing_due_is_409_not_500(self, stored):
        """A valid request the current state cannot satisfy. A 500 would read as
        a bug and a 502 as an upstream failure; neither is true."""
        with SRSDatabase(":memory:") as empty:
            app.state.srs_db = empty
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/api/story/prompt?curriculum_id=c1&day=1&strategy=REVIEW")
        assert resp.status_code == 409
        assert "review" in resp.json()["detail"].lower()
