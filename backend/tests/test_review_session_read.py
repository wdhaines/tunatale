"""Reading and rendering review sessions (bd tunatale-uv55, under tunatale-9p9d).

The parallel path a separate `review_sessions` table costs. It is smaller than
the epic feared, and the measurement is why:

    render_lesson_audio(store, renderer, audio_dir, lesson_id, lesson) -> dict

already takes NO curriculum_id and NO day, and keys on ``lesson_id`` alone. So
rendering a session reuses the audio pipeline verbatim; only the LOOKUP had to
learn about sessions. ``test_the_audio_is_reachable_through_the_existing_route``
is the test that proves the reuse rather than asserting it in a comment — the
rendered audio comes back from ``GET /api/audio/lesson/{id}``, which was never
touched.

⚠️ ``test_the_body_has_no_day_field`` is the one that keeps the epic honest at
the API boundary. ``serialize_lesson`` omits ``day`` when it is not passed, so a
session read is shaped exactly like a lesson read MINUS the field it has no
right to. A day appearing here would mean something upstream invented one.

The create route lives here too, at POST /api/review-sessions, having moved off
/api/story. A review session is not a story about a curriculum day, and hanging
it under the curriculum-shaped router was the same placement error the epic
exists to correct — one layer down and in a URL rather than a button.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.languages import get_language
from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.storage.store import ContentStore
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401
from tests.test_api_audio import _fake_render, _make_mock_lesson_with_sections  # noqa: E402


def _lesson(title: str = "A Missed Train") -> Lesson:
    return Lesson(
        title=title,
        language_code="sl",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text="Dober dan!", voice_id="test-voice", language_code="sl")],
            )
        ],
        generation_metadata={"review_requested": ["kavo"], "review_used": ["kavo"]},
    )


@pytest.fixture
def stored():
    store = ContentStore(":memory:")
    app.state.content_store = store
    app.state.language = get_language("sl")
    return store


async def _get(url: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(url)


# ── the dated list ───────────────────────────────────────────────────────────


class TestTheList:
    async def test_it_is_dated_and_newest_first(self, stored):
        stored.save_review_session("older", "sl", "2026-08-28", _lesson("Cold Platform"))
        stored.save_review_session(
            "newer", "sl", "2026-09-02", _lesson(), review_requested=["kavo", "vlak"], review_used=["kavo"]
        )

        resp = await _get("/api/review-sessions")

        assert resp.status_code == 200
        items = resp.json()["sessions"]
        assert [i["id"] for i in items] == ["newer", "older"]
        assert items[0]["session_date"] == "2026-09-02"
        assert items[0]["title"] == "A Missed Train"
        assert items[0]["review_requested"] == ["kavo", "vlak"]
        assert items[0]["review_used"] == ["kavo"]

    async def test_no_sessions_is_an_empty_list_not_a_404(self, stored):
        """A learner who has never made one is in a normal state, not an error."""
        resp = await _get("/api/review-sessions")

        assert resp.status_code == 200
        assert resp.json()["sessions"] == []

    async def test_an_unmeasured_session_reports_null_not_zero(self, stored):
        """Empty means unmeasurable, not zero — carried all the way to the wire so
        the list can render no readout rather than 'reused 0 of 0'."""
        stored.save_review_session("sess-1", "sl", "2026-09-02", _lesson())

        item = (await _get("/api/review-sessions")).json()["sessions"][0]

        assert item["review_requested"] is None
        assert item["review_used"] is None


# ── reading one ──────────────────────────────────────────────────────────────


class TestReadingOne:
    async def test_it_comes_back_shaped_like_a_lesson(self, stored):
        stored.save_review_session("sess-1", "sl", "2026-09-02", _lesson())

        resp = await _get("/api/review-sessions/sess-1")

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "sess-1"
        assert body["title"] == "A Missed Train"
        assert body["sections"][0]["phrases"][0]["text"] == "Dober dan!"

    async def test_the_body_has_no_day_field(self, stored):
        """The epic's decision, asserted at the API boundary.

        A session read is a lesson read minus the one field it has no right to.
        If ``day`` appears here, something upstream invented one.
        """
        stored.save_review_session("sess-1", "sl", "2026-09-02", _lesson())

        body = (await _get("/api/review-sessions/sess-1")).json()

        assert "day" not in body

    async def test_it_carries_its_date(self, stored):
        stored.save_review_session("sess-1", "sl", "2026-09-02", _lesson())

        body = (await _get("/api/review-sessions/sess-1")).json()

        assert body["session_date"] == "2026-09-02"

    async def test_an_unknown_session_is_404(self, stored):
        resp = await _get("/api/review-sessions/nope")

        assert resp.status_code == 404

    async def test_a_lesson_id_is_not_a_session_id(self, stored):
        """The two stores of content stay separate in both directions."""
        stored.save_lesson("lesson-1", "c1", 1, _lesson())

        resp = await _get("/api/review-sessions/lesson-1")

        assert resp.status_code == 404


# ── rendering ────────────────────────────────────────────────────────────────


class TestRendering:
    @pytest.fixture
    def renderable(self, stored, tmp_path):
        renderer = AsyncMock()
        renderer.render = AsyncMock(side_effect=_fake_render)
        app.state.renderer = renderer
        app.state.audio_dir = tmp_path
        stored.save_review_session("sess-1", "sl", "2026-09-02", _make_mock_lesson_with_sections())
        return stored

    async def _render(self, session_id: str):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(f"/api/review-sessions/{session_id}/render")

    async def test_it_renders(self, renderable):
        resp = await self._render("sess-1")

        assert resp.status_code == 202
        assert renderable.get_audio_file_row(resp.json()["audio_id"]) is not None

    async def test_the_audio_is_reachable_through_the_existing_route(self, renderable):
        """The payoff of the separate table costing so little.

        `audio_files` joins on an id and never on a curriculum or a day, so once
        the rows exist under the session id, GET /api/audio/lesson/{id} serves
        them with no change at all. If this fails, the reuse claim in the
        commit message is wrong.
        """
        await self._render("sess-1")

        resp = await _get("/api/audio/lesson/sess-1")

        assert resp.status_code == 200
        assert resp.json()["lesson_id"] == "sess-1"
        assert resp.json()["sections"]

    async def test_rendering_an_unknown_session_is_404(self, renderable):
        resp = await self._render("nope")

        assert resp.status_code == 404

    async def test_an_unavailable_renderer_is_503(self, renderable, monkeypatch):
        """Mirrors POST /api/audio/render: a missing TTS binary is a service
        problem, not a bad request."""
        app.state.renderer.render = AsyncMock(side_effect=RuntimeError("ffmpeg not found"))

        resp = await self._render("sess-1")

        assert resp.status_code == 503
        assert "ffmpeg" in resp.json()["detail"]


# ── the transcript comes from the SHARED content route ───────────────────────


class TestTheTranscript:
    """A session's transcript is served by GET /api/srs/content/{id}/transcript.

    ⚠️ THERE IS NO SESSION-SPECIFIC TRANSCRIPT ROUTE, and there was briefly. The
    same obstacle — a handler whose only lesson-specific line was
    ``store.get_lesson(id)`` — appeared five times in this epic: render,
    transcript, listen, review-queue, listen-preview. Adding a twin per endpoint
    was the wrong shape; ``ContentStore.get_readable_content`` resolves either,
    and the route family was renamed to say so.
    """

    async def test_a_session_resolves_through_the_content_route(self, stored):
        from app.srs.database import SRSDatabase

        stored.save_review_session("sess-1", "sl", "2026-09-02", _lesson())
        app.state.srs_db = SRSDatabase(":memory:")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/content/sess-1/transcript")

        assert resp.status_code == 200
        assert resp.json()["lesson_id"] == "sess-1"

    async def test_a_lesson_still_resolves_through_the_same_route(self, stored):
        """The regression control: widening the lookup must not cost the lesson."""
        from app.srs.database import SRSDatabase

        stored.save_lesson("lesson-1", "c1", 1, _lesson())
        app.state.srs_db = SRSDatabase(":memory:")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/content/lesson-1/transcript")

        assert resp.status_code == 200

    async def test_an_unknown_id_is_404(self, stored):
        from app.srs.database import SRSDatabase

        app.state.srs_db = SRSDatabase(":memory:")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/content/nope/transcript")

        assert resp.status_code == 404
