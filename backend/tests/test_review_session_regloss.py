"""Re-gloss a stored review session (tunatale-y0bk.4).

A session can be stored with zero hover translations because
``ensure_dialogue_glosses`` degrades rather than raising. This route repairs
sessions already stored — the button and the live-database repair are the
orchestrator's.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.languages import get_language
from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.storage.store import ContentStore
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

pytestmark = pytest.mark.anyio


def _lesson(*, story: dict | None = None, title: str = "Norwegian Lesson") -> Lesson:
    metadata: dict = {}
    if story is not None:
        metadata["story"] = story
    return Lesson(
        title=title,
        language_code="no",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text="Hei!", voice_id="test-voice", language_code="no")],
            )
        ],
        generation_metadata=metadata,
    )


def _story(*, glosses: list | None = None) -> dict:
    data: dict = {
        "title": "Norwegian Lesson",
        "key_phrases": [{"phrase": "hei", "translation": "hi"}],
        "scenes": [
            {
                "label": "At the cafe",
                "lines": [
                    {"speaker": "female-1", "text": "Hei!", "translation": "Hi!"},
                    {"speaker": "male-1", "text": "Takk.", "translation": "Thanks."},
                ],
            }
        ],
    }
    if glosses is not None:
        data["dialogue_glosses"] = glosses
    return data


_GLOSS_REPLY = json.dumps(
    [
        {"word": "hei", "translation": "hi"},
        {"word": "takk", "translation": "thanks"},
    ]
)


def _mock_llm(reply: str = _GLOSS_REPLY) -> MagicMock:
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=reply)
    return llm


async def _post(url: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(url)


async def _get(url: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(url)


@pytest.fixture
def stored():
    store = ContentStore(":memory:")
    app.state.content_store = store
    app.state.language = get_language("no")
    return store


class TestReglossReviewSession:
    async def test_no_glosses_gets_new_ones(self, stored):
        """A session stored with NO glosses, LLM returns 2 entries -> 200 with count 2."""
        story = _story()
        stored.save_review_session(
            "unglossed",
            "no",
            "2026-09-09",
            _lesson(story=story),
        )
        app.state.llm = _mock_llm()

        resp = await _post("/api/review-sessions/unglossed/regloss")

        assert resp.status_code == 200
        body = resp.json()
        assert body["gloss_entry_count"] == 2

        # Re-read: gloss_entry_count persists (token_glosses is internal to
        # generation_metadata and not exposed by serialize_lesson).
        resp2 = await _get("/api/review-sessions/unglossed")
        assert resp2.status_code == 200
        assert resp2.json()["gloss_entry_count"] == 2

    async def test_already_glossed_is_re_glossed_not_skipped(self, stored):
        """Seed a story WITH dialogue_glosses, return a DIFFERENT array, assert the new one landed.

        This is the step-4 pop; without it, ensure_dialogue_glosses early-returns.
        """
        # ⚠️ The old and new arrays must differ in BOTH size and content. An
        # earlier version of this test seeded one entry and returned one entry,
        # so `gloss_entry_count == 1` held whether the pop ran or not — the whole
        # suite passed with `story.pop(...)` deleted. Size alone discriminates
        # here; the translation assertion below discriminates even if a future
        # edit makes the counts match again.
        old_glosses = [
            {"word": "hei", "translation": "hello"},
            {"word": "takk", "translation": "thanks"},
        ]
        new_gloss_reply = json.dumps([{"word": "hei", "translation": "hey"}])
        story = _story(glosses=old_glosses)
        stored.save_review_session(
            "pre-glossed",
            "no",
            "2026-09-09",
            _lesson(story=story),
        )
        app.state.llm = _mock_llm(new_gloss_reply)

        resp = await _post("/api/review-sessions/pre-glossed/regloss")

        assert resp.status_code == 200
        assert resp.json()["gloss_entry_count"] == 1

        # Re-read: gloss_entry_count reflects the NEW gloss (not skipped).
        resp2 = await _get("/api/review-sessions/pre-glossed")
        assert resp2.status_code == 200
        assert resp2.json()["gloss_entry_count"] == 1

        # And the stored gloss is the NEW translation, not the seeded one. The
        # count is a size check; this is the one that says the array was replaced
        # rather than merely re-counted.
        reglossed = stored.get_review_session("pre-glossed")
        assert reglossed.generation_metadata["token_glosses"]["hei"] == "hey"

    async def test_coverage_pair_survives(self, stored):
        """Seed review_requested / review_used, re-gloss, both still non-empty.

        This is the save_review_session trap: using it instead of
        update_review_session_data writes NULL over the coverage pair.
        """
        story = _story()
        stored.save_review_session(
            "with-coverage",
            "no",
            "2026-09-09",
            _lesson(story=story),
            review_requested=["hei", "takk"],
            review_used=["hei"],
        )
        app.state.llm = _mock_llm()

        resp = await _post("/api/review-sessions/with-coverage/regloss")

        assert resp.status_code == 200

        # ⚠️ ASSERT THE COLUMNS, NOT THE GET PAYLOAD. An earlier version read
        # review_requested/review_used off `GET /api/review-sessions/{id}`, which
        # serialize_lesson takes from the lesson's generation_metadata — and
        # build_lesson_from_story REBUILDS that from the `review_words` argument
        # on every re-gloss. So it was populated whichever store method ran, and
        # the whole suite passed with `save_review_session` swapped in. The
        # denormalised COLUMNS are what that method NULLs, and what the dated
        # list reads.
        row2 = stored.get_review_session_row("with-coverage")
        assert row2["review_requested"] == ["hei", "takk"]
        assert row2["review_used"] == ["hei"]

    async def test_409_no_story_in_metadata(self, stored):
        """A session predating stored Story-JSON has no source to re-gloss -> 409."""
        stored.save_review_session(
            "no-story",
            "no",
            "2026-09-09",
            _lesson(),
        )

        resp = await _post("/api/review-sessions/no-story/regloss")

        assert resp.status_code == 409
        assert "no stored story" in resp.json()["detail"]

        # Row is unchanged
        row = stored.get_review_session_row("no-story")
        assert row is not None
        assert row["title"] == "Norwegian Lesson"

    async def test_404_unknown_id(self, stored):
        resp = await _post("/api/review-sessions/nonexistent/regloss")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Review session not found"

    async def test_gloss_pass_fails_returns_200_with_warning(self, stored):
        """ensure_dialogue_glosses never raises -> 200, gloss_entry_count unchanged, warning."""
        story = _story()
        stored.save_review_session(
            "gloss-fail",
            "no",
            "2026-09-09",
            _lesson(story=story),
        )
        app.state.llm = None  # ensure_dialogue_glosses is a no-op

        resp = await _post("/api/review-sessions/gloss-fail/regloss")

        assert resp.status_code == 200
        body = resp.json()
        assert body["gloss_entry_count"] == 0
        assert any("hover translations" in w for w in body["warnings"])

    async def test_422_malformed_story(self, stored):
        """A story missing required structure -> 422, session unchanged."""
        bad_story = {"title": "empty", "key_phrases": [], "scenes": []}
        stored.save_review_session(
            "bad-story",
            "no",
            "2026-09-09",
            _lesson(story=bad_story),
        )
        app.state.llm = None

        resp = await _post("/api/review-sessions/bad-story/regloss")

        assert resp.status_code == 422

        # Session row is unchanged
        row = stored.get_review_session_row("bad-story")
        assert row is not None
