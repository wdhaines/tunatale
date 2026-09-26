"""App-level exception handlers own the LLM-error → HTTP mapping (tunatale-ux66.4 item 1).

The 429-vs-502 ladder used to be copy-pasted into three routes. It now lives once,
in ``main.py``, so these tests pin it at the app: each class is registered, and
each reaches a real route's client with today's status and a byte-identical
``{"detail": ...}`` body — the shape the old ``HTTPException`` produced.
"""

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.generation.story import NoReviewVocabularyError
from app.languages import get_language
from app.llm.client import LLMError, LLMQuotaExceededError
from app.main import app
from app.models.curriculum import Curriculum, CurriculumDay
from app.storage.store import ContentStore
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401


@pytest.mark.parametrize("exc_class", [LLMQuotaExceededError, LLMError, NoReviewVocabularyError])
def test_handler_is_registered_on_the_real_app(exc_class):
    assert exc_class in app.exception_handlers


def _store_with_day_1() -> ContentStore:
    store = ContentStore(":memory:")
    store.save_curriculum(
        "c1",
        Curriculum(
            id="c1",
            topic="kaffe",
            language_code="no",
            cefr_level="A2",
            days=[
                CurriculumDay(
                    day=1,
                    title="Day 1",
                    focus="greetings",
                    learning_objective="greet",
                    story_guidance="kafé",
                    collocations=["hei"],
                )
            ],
        ),
    )
    return store


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        # The subclass must win over LLMError's 502: Starlette walks the MRO, so
        # this row is what fails if the 429 handler is ever lost or shadowed.
        (LLMQuotaExceededError("Daily Groq budget exhausted: tokens per day"), 429),
        (LLMError("Groq returned 503"), 502),
        (NoReviewVocabularyError("nothing is due to review"), 409),
    ],
)
async def test_generate_story_maps_each_error_through_the_app_handler(exc, status):
    generator = AsyncMock()
    generator.generate = AsyncMock(side_effect=exc)
    app.state.content_store = _store_with_day_1()
    app.state.story_generator = generator
    app.state.language = get_language("no")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.post(
            "/api/story/generate",
            json={"curriculum_id": "c1", "day": 1, "strategy": "WIDER"},
        )

    assert response.status_code == status
    assert response.content == b'{"detail":"' + str(exc).encode() + b'"}'
