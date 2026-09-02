"""LOCKED ORACLE for bd tunatale-fgeq.2 — the review sample reaching the prompt.

Three call sites reach ``build_story_prompts`` and every one of them must hand
over the request's own SRS db. The epic (tunatale-fgeq) names two traps here,
and both fail SILENTLY — a worse lesson, never an error:

  M1  BOTH MODES. `api/generation.py::get_story_prompt` (manual paste-out) and
      `StoryGenerator.generate` (auto), plus the pipeline worker that drives
      auto for committed planner days. Doing the selection inside
      `StoryGenerator.generate` is the obvious shortcut and leaves MANUAL mode
      sending "(none yet)" forever. A test that exercises only the auto path
      cannot see that, which is why `TestManualExportPath` exists and asserts on
      the /prompt endpoint's rendered user_prompt.

  M2  `request.state.srs_db`, NOT `request.app.state.srs_db`. The singular
      attribute is ONE db — the default language's. This deployment runs two, so
      the app-state form draws review words from the WRONG LANGUAGE's deck and
      produces entirely plausible output. tunatale-pf4i was exactly this bug,
      confirmed and fixed, in the neighbouring function a reader would copy.
      ⚠️ A SINGLE-LANGUAGE TEST CANNOT FAIL ON THIS — with one language the two
      attributes are the same object. `TestLanguageIsolation` is the only test
      here that discriminates, and it needs the two-language app state to do it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.common.guid import compute_guid
from app.generation.story import StoryGenerator, build_story_prompts
from app.languages import get_language
from app.main import app
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.strategy import ContentStrategy, ReviewPressure
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

SL_WORD = "slovenska_zapadla"
NO_WORD = "norsk_forfalt"


def _seed_overdue(db: SRSDatabase, text: str) -> None:
    """One collocation whose RECOGNITION direction is well overdue and decayed.

    Production stays NEW (add_collocation's default), which `get_due_items`
    excludes, so each collocation contributes exactly one candidate.
    """
    unit = SyntacticUnit(text=text, translation=f"gloss of {text}", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    db.update_direction(
        compute_guid(text, "sl", ""),
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


def _curriculum_day() -> CurriculumDay:
    return CurriculumDay(
        day=1,
        title="Ordering Coffee",
        focus="Café vocabulary",
        collocations=["dober dan"],
        learning_objective="Order a coffee",
        story_guidance="Scene at a café",
    )


def _story_json() -> str:
    return json.dumps(
        {
            "title": "T",
            "key_phrases": [{"phrase": "dober dan", "translation": "good day"}],
            "scenes": [
                {
                    "label": "S",
                    "lines": [
                        {"speaker": "female-1", "text": "Dober dan.", "translation": "Good day."},
                        {"speaker": "male-1", "text": "Prosim.", "translation": "Please."},
                    ],
                }
            ],
        }
    )


@pytest.fixture
def seeded_db():
    with SRSDatabase(":memory:") as db:
        _seed_overdue(db, SL_WORD)
        yield db


@pytest.fixture
def stored_curriculum():
    store = ContentStore(":memory:")
    curriculum_id = "curriculum-review-wiring"
    curriculum = Curriculum(id=curriculum_id, topic="t", language_code="sl", cefr_level="A2", days=[_curriculum_day()])
    store.save_curriculum(curriculum_id, curriculum)
    return store, curriculum_id


# ── The shared builder ─────────────────────────────────────────────────────


class TestBuildStoryPrompts:
    def test_a_db_puts_real_review_words_in_the_prompt(self, seeded_db, language):
        user = build_story_prompts(
            _curriculum_day(), language, ContentStrategy.WIDER, "A2", srs_db=seeded_db
        ).user_prompt
        assert SL_WORD in user
        assert "(none yet)" not in user

    def test_no_db_is_the_old_prompt_byte_for_byte(self, language):
        """Every story cassette was recorded without a db. `srs_db=None` must
        still key identically, or the whole recorded corpus is orphaned."""
        user = build_story_prompts(_curriculum_day(), language, ContentStrategy.WIDER, "A2").user_prompt
        assert "**Review Collocations to Include:**\n(none yet)\n" in user

    def test_pressure_reaches_the_rendered_block(self, seeded_db, language):
        natural = build_story_prompts(
            _curriculum_day(), language, ContentStrategy.WIDER, "A2", srs_db=seeded_db
        ).user_prompt
        insistent = build_story_prompts(
            _curriculum_day(),
            language,
            ContentStrategy.WIDER,
            "A2",
            srs_db=seeded_db,
            review_pressure=ReviewPressure.INSISTENT,
        ).user_prompt
        assert natural != insistent
        assert "candidates, not requirements" in natural
        assert "candidates, not requirements" not in insistent


# ── M1: the auto path ──────────────────────────────────────────────────────


class TestAutoGeneratePath:
    async def test_generate_sends_the_review_words(self, seeded_db, language):
        client = MagicMock()
        client.complete = AsyncMock(return_value=_story_json())
        await StoryGenerator(llm_client=client).generate(
            curriculum_day=_curriculum_day(),
            language=language,
            strategy=ContentStrategy.WIDER,
            srs_db=seeded_db,
        )
        sent_user_prompt = client.complete.call_args.args[0]
        assert SL_WORD in sent_user_prompt


# ── M1: the manual path — the one an auto-only test cannot see ─────────────


class TestManualExportPath:
    async def test_prompt_endpoint_shows_real_review_words(self, seeded_db, stored_curriculum):
        store, curriculum_id = stored_curriculum
        app.state.content_store = store
        app.state.language = get_language("sl")
        app.state.srs_db = seeded_db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"/api/story/prompt?curriculum_id={curriculum_id}&day=1")

        assert resp.status_code == 200
        assert SL_WORD in resp.json()["user_prompt"]

    async def test_prompt_endpoint_honours_review_pressure(self, seeded_db, stored_curriculum):
        store, curriculum_id = stored_curriculum
        app.state.content_store = store
        app.state.language = get_language("sl")
        app.state.srs_db = seeded_db

        base = f"/api/story/prompt?curriculum_id={curriculum_id}&day=1"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            default = await client.get(base)
            insistent = await client.get(f"{base}&review_pressure=INSISTENT")

        assert default.status_code == 200
        assert insistent.status_code == 200
        # Asserted POSITIVELY on both arms. "the insistent phrasing is absent"
        # is satisfied by a prompt with no review block at all, which is exactly
        # the pre-wiring state — a vacuous green.
        assert "candidates, not requirements" in default.json()["user_prompt"]
        assert "matters MORE than staying" in insistent.json()["user_prompt"]


# ── M2: the trap a single-language test is blind to ────────────────────────


class TestLanguageIsolation:
    """The request's db, not the app's default one.

    Both directions are asserted. Without the control, an implementation that
    simply hardcoded the non-default language would pass the first test.
    """

    @pytest.fixture
    def two_languages(self, stored_curriculum):
        store, curriculum_id = stored_curriculum
        db_sl, db_no = SRSDatabase(":memory:"), SRSDatabase(":memory:")
        _seed_overdue(db_sl, SL_WORD)
        _seed_overdue(db_no, NO_WORD)

        app.state.srs_dbs = {"sl": db_sl, "no": db_no}
        app.state.content_stores = {"sl": store, "no": store}
        app.state.languages = {"sl": get_language("sl"), "no": get_language("no")}
        # The singular attributes point at SLOVENE — exactly what an
        # `app.state.srs_db` read would pick up while a Norwegian request is in
        # flight, which is the defect this class exists to catch.
        app.state.srs_db = db_sl
        app.state.content_store = store
        app.state.language = get_language("sl")
        yield curriculum_id
        for attr in ("srs_dbs", "content_stores", "languages"):
            delattr(app.state, attr)
        db_sl.close()
        db_no.close()

    async def _prompt_for(self, curriculum_id: str, language_code: str) -> str:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                f"/api/story/prompt?curriculum_id={curriculum_id}&day=1",
                headers={"X-TT-Language": language_code},
            )
        assert resp.status_code == 200
        return resp.json()["user_prompt"]

    async def test_a_norwegian_request_reads_the_norwegian_deck(self, two_languages):
        prompt = await self._prompt_for(two_languages, "no")
        assert NO_WORD in prompt
        assert SL_WORD not in prompt, (
            "the Slovene word leaked into a Norwegian prompt — the route is reading "
            "app.state.srs_db (the DEFAULT language) instead of request.state.srs_db"
        )

    async def test_a_slovene_request_reads_the_slovene_deck_control(self, two_languages):
        prompt = await self._prompt_for(two_languages, "sl")
        assert SL_WORD in prompt
        assert NO_WORD not in prompt


# ── M1: the pipeline worker, the third call site ───────────────────────────
#
# Its guard lives in test_pipeline.py, not here:
# `TestPipelineHappyPath::test_generate_hands_the_per_language_srs_db_to_the_generator`.
# That file owns the pipeline fixtures, and its `FakeStoryGenerator` RECORDS the
# `srs_db` it was handed — a behavioural assertion, where anything written here
# could only have grepped the pipeline's source for the call. It enqueues a
# NORWEGIAN job against two distinct db sentinels, so it fails both on "passed
# nothing" and on "passed the default language's db".
