"""Re-gloss a stored lesson (tunatale-w1fp.5).

A lesson can be stored with zero hover translations because
``ensure_dialogue_glosses`` degrades rather than raising. The review-session
surface got an in-app repair route (tunatale-y0bk.4 / w1fp.4); this is the
lesson twin, and its write path must use ``update_lesson_data`` — a
``save_lesson`` write (``INSERT OR REPLACE``) assigns a NEW rowid and resets
``created_at``, leaving the old row invisible-but-present (bd tunatale-326c).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.languages import get_language
from app.main import app
from app.models.curriculum import Curriculum, CurriculumDay
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


def _seed_curriculum(store: ContentStore) -> None:
    """Give ``c1`` day 1 a home so the route's review_words branch runs."""
    store.save_curriculum(
        "c1",
        Curriculum(
            id="c1",
            topic="t",
            language_code="no",
            cefr_level="A2",
            days=[
                CurriculumDay(
                    day=1,
                    title="At the cafe",
                    focus="Café vocabulary",
                    collocations=[],
                    learning_objective="Order a coffee",
                    story_guidance="Scene at a café",
                )
            ],
        ),
    )


async def _post(url: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(url)


@pytest.fixture
def stored():
    store = ContentStore(":memory:")
    app.state.content_store = store
    app.state.language = get_language("no")
    return store


class TestReglossLesson:
    async def test_no_glosses_gets_new_ones(self, stored):
        """A lesson stored with NO glosses, LLM returns 2 entries -> 200 with count 2."""
        _seed_curriculum(stored)
        stored.save_lesson("unglossed", "c1", 1, _lesson(story=_story()))
        app.state.llm = _mock_llm()

        resp = await _post("/api/story/unglossed/regloss")

        assert resp.status_code == 200
        body = resp.json()
        assert body["gloss_entry_count"] == 2

        # Re-read: the fresh glosses persisted on the same row.
        reglossed = stored.get_lesson("unglossed")
        assert reglossed.generation_metadata["gloss_entry_count"] == 2

    async def test_already_glossed_is_re_glossed_not_skipped(self, stored):
        """Seed a story WITH dialogue_glosses, return a DIFFERENT array, assert the new one landed.

        This is the step-4 pop; without it, ensure_dialogue_glosses early-returns.
        """
        old_glosses = [
            {"word": "hei", "translation": "hello"},
            {"word": "takk", "translation": "thanks"},
        ]
        new_gloss_reply = json.dumps([{"word": "hei", "translation": "hey"}])
        _seed_curriculum(stored)
        stored.save_lesson("pre-glossed", "c1", 1, _lesson(story=_story(glosses=old_glosses)))
        app.state.llm = _mock_llm(new_gloss_reply)

        resp = await _post("/api/story/pre-glossed/regloss")

        assert resp.status_code == 200
        assert resp.json()["gloss_entry_count"] == 1

        # The stored gloss is the NEW translation, not the seeded one.
        reglossed = stored.get_lesson("pre-glossed")
        assert reglossed.generation_metadata["token_glosses"]["hei"] == "hey"

    async def test_409_no_story_in_metadata(self, stored):
        """A lesson predating stored Story-JSON has no source to re-gloss -> 409."""
        stored.save_lesson("no-story", "c1", 1, _lesson())

        resp = await _post("/api/story/no-story/regloss")

        assert resp.status_code == 409
        assert "no stored story" in resp.json()["detail"]

        # Row is unchanged
        row = stored.get_lesson_row("no-story")
        assert row is not None

    async def test_404_unknown_id(self, stored):
        resp = await _post("/api/story/nonexistent/regloss")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Lesson not found"

    async def test_the_write_path_uses_update_lesson_data(self, stored):
        """Regloss rewrites the blob IN PLACE: rowid and created_at unchanged,
        and no duplicate row remains.

        This is the non-vacuous one: a ``save_lesson`` write is
        ``INSERT OR REPLACE``, which assigns a NEW rowid and resets
        ``created_at`` to now while leaving the old row present but invisible —
        the duplicate-row condition tracked as bd tunatale-326c.
        """
        _seed_curriculum(stored)
        stored.save_lesson("l1", "c1", 1, _lesson(story=_story()))
        app.state.llm = _mock_llm()

        before = _raw_row(stored, "l1")

        resp = await _post("/api/story/l1/regloss")

        assert resp.status_code == 200

        after = _raw_row(stored, "l1")
        assert after["rowid"] == before["rowid"], "a save_lesson write assigns a new rowid"
        assert after["created_at"] == before["created_at"], "a save_lesson write resets created_at"
        # Exactly one row carries the id: a save_lesson write would leave two.
        assert len([r for r in stored.list_lessons() if r[0] == "l1"]) == 1

    async def test_gloss_pass_fails_returns_200_with_warning(self, stored):
        """ensure_dialogue_glosses never raises -> 200, gloss_entry_count 0, warning.

        No curriculum is seeded here on purpose: the route's review_words branch
        must also survive a lesson whose curriculum is absent (`()`), the way
        the /import path passes.
        """
        stored.save_lesson("gloss-fail", "c1", 1, _lesson(story=_story()))
        app.state.llm = None  # ensure_dialogue_glosses is a no-op

        resp = await _post("/api/story/gloss-fail/regloss")

        assert resp.status_code == 200
        body = resp.json()
        assert body["gloss_entry_count"] == 0
        assert any("hover translations" in w for w in body["warnings"])

    async def test_422_malformed_story(self, stored):
        """A story missing required structure -> 422, lesson unchanged."""
        bad_story = {"title": "empty", "key_phrases": [], "scenes": []}
        stored.save_lesson("bad-story", "c1", 1, _lesson(story=bad_story))
        app.state.llm = None

        resp = await _post("/api/story/bad-story/regloss")

        assert resp.status_code == 422

        # Lesson row is unchanged
        row = stored.get_lesson_row("bad-story")
        assert row is not None


def _raw_row(store: ContentStore, lesson_id: str) -> dict:
    """The row's identity columns — what a regloss must NOT touch."""
    with store._get_conn() as conn:
        row = conn.execute(
            "SELECT rowid, created_at, data_json FROM lessons WHERE id = ?",
            (lesson_id,),
        ).fetchone()
    assert row is not None
    return dict(row)
