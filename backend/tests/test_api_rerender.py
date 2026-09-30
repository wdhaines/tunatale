"""Re-render audio from the tools menus: the estimate and the render (bd tunatale-9paa).

User, 2026-09-30: a "re-render audio" option in the tools of lessons and review
sessions, with the sections as checkboxes, and the cost shown before the click.

The estimate's oracle is the tracked instrument itself: it must equal
``price_lessons`` for the same scope with the app's own renderer configuration
(the app renders WITHOUT the slicer, see main.py, so the estimate prices none).
A second estimate would be a second opinion that drifts.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import app_state
from app.audio.azure_tts import AzureTTSService
from app.audio.render_cost import price_lessons
from app.audio.slicer import PARENT_RATE
from app.config import settings
from app.languages import get_phoneme_planner, get_preprocessor, get_tts_locale
from app.main import app
from app.models.language import NARRATOR_VOICE
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.storage.store import ContentStore
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401
from tests.test_reassemble_lesson import (
    _build_test_lesson,
    _CountingTTS,
    _make_fake_renderer,
    _populate_store,
    _sha256,
)

FINN = "nb-NO-FinnNeural"


@pytest.fixture(autouse=True)
def _dirs_follow_the_test_tree(tmp_path, monkeypatch):
    """Audio rows resolve against settings.audio_dir; the estimate reads the TTS
    cache at settings.tts_cache_dir. Both point into the test tree, and the cache
    starts EMPTY, so every clip is a miss unless a test writes it."""
    monkeypatch.setattr(settings, "audio_dir", tmp_path / "audio")
    monkeypatch.setattr(settings, "tts_cache_dir", tmp_path / "tts-cache")


def _no_lesson() -> Lesson:
    return Lesson(
        title="Sporet ender i hagen",
        language_code="no",
        sections=[
            Section(
                section_type=SectionType.KEY_PHRASES,
                phrases=[Phrase(text="Hei", voice_id=FINN, language_code="no", role="female-1")],
            ),
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text="Hello", voice_id=NARRATOR_VOICE, language_code="en", role="narrator")],
            ),
        ],
    )


def _instrument(lesson: Lesson, cache_dir: Path):
    code = lesson.language_code
    return price_lessons(
        [lesson],
        language_code=code,
        preprocessor=get_preprocessor(code),
        planner=get_phoneme_planner(code),
        target_locale=get_tts_locale(code),
        syllabify_fn=None,
        slicer_enabled=False,
        parent_rate=PARENT_RATE,
        cache_dir=cache_dir,
    )


async def _post(path: str, body: dict | None = None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(path, json=body or {})


# ── the estimate ─────────────────────────────────────────────────────────


class TestLessonEstimate:
    @pytest.fixture
    def store(self):
        store = ContentStore(":memory:")
        store.save_lesson("l1", "c1", 1, _no_lesson())
        app.state.content_store = store
        return store

    async def test_whole_lesson_matches_the_instrument(self, store, tmp_path):
        r = await _post("/api/audio/render-estimate", {"lesson_id": "l1"})
        assert r.status_code == 200, r.text
        cost = _instrument(_no_lesson(), tmp_path / "tts-cache")
        body = r.json()
        assert body["billable_chars"] == cost.phrase.billable_chars + cost.slicer.billable_chars
        assert body["new_clips"] == cost.phrase.misses + cost.gemini_phrase.misses
        assert body["cached_clips"] == 0
        assert body["monthly_allowance"] == 500_000

    async def test_a_section_subset_prices_only_those_sections(self, store):
        """Davis reading "Hello": the <lang en-US> rule's 65 billable chars
        (test_report_render_cost's oracle), and nothing from KEY_PHRASES."""
        r = await _post("/api/audio/render-estimate", {"lesson_id": "l1", "section_types": ["natural_speed"]})
        assert r.status_code == 200, r.text
        assert r.json()["billable_chars"] == 65
        assert r.json()["new_clips"] == 1

    async def test_a_cached_clip_costs_nothing(self, store, tmp_path):
        tts = AzureTTSService(cache_dir=tmp_path / "tts-cache")
        cached = tts._cache_path("Hello", NARRATOR_VOICE, "+0%", None, "en-US")
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(b"mp3")
        r = await _post("/api/audio/render-estimate", {"lesson_id": "l1", "section_types": ["natural_speed"]})
        assert r.json()["billable_chars"] == 0
        assert (r.json()["new_clips"], r.json()["cached_clips"]) == (0, 1)

    @pytest.mark.parametrize(
        ("section_types", "status"),
        [
            ([], 422),  # nothing to render
            (["not_a_section"], 422),
            (["slow_speed"], 422),  # a real type this lesson does not have
        ],
        ids=["empty", "unknown", "absent"],
    )
    async def test_a_bad_selection_is_refused(self, store, section_types, status):
        r = await _post("/api/audio/render-estimate", {"lesson_id": "l1", "section_types": section_types})
        assert r.status_code == status, r.text

    async def test_a_missing_lesson_is_404(self, store):
        r = await _post("/api/audio/render-estimate", {"lesson_id": "nope"})
        assert (r.status_code, r.json()["detail"]) == (404, "Lesson not found")


# ── the render ───────────────────────────────────────────────────────────


@pytest.fixture
def seeded(tmp_path):
    """A rendered 4-section lesson (KEY_PHRASES, NATURAL_SPEED, TRANSLATED,
    SLOW_SPEED) with real decodable section files, as the reassemble tests seed it."""
    store = ContentStore(":memory:")
    lesson = _build_test_lesson()
    store.save_lesson(lesson.title, "c1", 1, lesson)
    _populate_store(store, lesson, tmp_path / "audio", [2.0, 5.0, 8.0, 5.0])
    app.state.content_store = store
    app.state.audio_dir = tmp_path / "audio"
    app.state.renderer = _make_fake_renderer()
    app.state.tts = _CountingTTS()
    return store, lesson


def _rows(store, lesson_id):
    return {r["section_index"]: r for r in store.list_audio_files_for_lesson(lesson_id)}


@pytest.mark.ffmpeg
class TestLessonRerender:
    async def test_chosen_sections_are_re_rendered_and_the_rest_kept_byte_for_byte(self, seeded):
        from app.audio.paths import resolve_audio_path

        store, lesson = seeded
        before = _rows(store, lesson.title)
        kept_sha = _sha256(resolve_audio_path(before[0]["file_path"]))

        r = await _post("/api/audio/rerender", {"lesson_id": lesson.title, "section_types": ["slow_speed"]})

        assert r.status_code == 200, r.text
        after = _rows(store, lesson.title)
        assert after[3]["file_path"] != before[3]["file_path"]
        assert after[0]["file_path"] == before[0]["file_path"]
        assert _sha256(resolve_audio_path(after[0]["file_path"])) == kept_sha
        # The fake's render() raises: a subset never re-renders the whole lesson.
        assert app.state.renderer.sections_rendered == [3]

    async def test_no_selection_renders_the_whole_lesson(self, seeded, tmp_path):
        store, lesson = seeded
        whole = AsyncMock()
        whole.render = AsyncMock(return_value=[])
        app.state.renderer = whole
        r = await _post("/api/audio/rerender", {"lesson_id": lesson.title})
        # render() returning no cues is the fake's business; what matters is that
        # the whole-lesson path, not the reassemble, was taken.
        assert whole.render.await_count == 1, r.text

    async def test_a_second_click_while_rendering_is_refused(self, seeded):
        store, lesson = seeded
        app_state.lesson_renders(app).add(lesson.title)
        try:
            r = await _post("/api/audio/rerender", {"lesson_id": lesson.title, "section_types": ["slow_speed"]})
        finally:
            app_state.lesson_renders(app).discard(lesson.title)
        assert r.status_code == 409
        assert app.state.renderer.sections_rendered == []

    async def test_the_in_flight_marker_is_released_after_a_render(self, seeded):
        store, lesson = seeded
        await _post("/api/audio/rerender", {"lesson_id": lesson.title, "section_types": ["slow_speed"]})
        assert lesson.title not in app_state.lesson_renders(app)

    async def test_a_subset_of_a_lesson_with_no_audio_is_a_conflict(self, seeded):
        """Nothing to keep byte-for-byte: the reassemble refuses, and the message
        tells the user to render the whole lesson."""
        store, lesson = seeded
        other = replace(lesson, title="Never rendered")
        store.save_lesson(other.title, "c1", 2, other)
        r = await _post("/api/audio/rerender", {"lesson_id": other.title, "section_types": ["slow_speed"]})
        assert r.status_code == 409
        assert "not found" in r.json()["detail"].lower()

    async def test_a_tts_failure_is_503_and_releases_the_marker(self, seeded):
        """The adapter's RuntimeError (e.g. Azure refusing the key) is the /render
        route's 503; the finally still drops the marker so the button recovers."""
        store, lesson = seeded
        failing = AsyncMock()
        failing.render = AsyncMock(side_effect=RuntimeError("Azure TTS refused the request"))
        app.state.renderer = failing
        r = await _post("/api/audio/rerender", {"lesson_id": lesson.title})
        assert (r.status_code, r.json()["detail"]) == (503, "Azure TTS refused the request")
        assert lesson.title not in app_state.lesson_renders(app)

    async def test_a_missing_lesson_is_404(self, seeded):
        r = await _post("/api/audio/rerender", {"lesson_id": "nope"})
        assert (r.status_code, r.json()["detail"]) == (404, "Lesson not found")

    async def test_a_bad_selection_is_refused_before_any_work(self, seeded):
        store, lesson = seeded
        r = await _post("/api/audio/rerender", {"lesson_id": lesson.title, "section_types": ["en_translated"]})
        assert r.status_code == 422
        assert app.state.renderer.sections_rendered == []


# ── review sessions: the same two actions on their own ids ─────────────────


@pytest.mark.ffmpeg
class TestReviewSessionRerender:
    @pytest.fixture
    def session(self, tmp_path):
        store = ContentStore(":memory:")
        lesson = _build_test_lesson(title="A Fishing Trip in Lofoten")
        store.save_review_session("s1", lesson.language_code, "2026-09-30", lesson)
        _populate_store(store, lesson, tmp_path / "audio", [2.0, 5.0, 8.0, 5.0])
        # _populate_store keys the rows on the title; a session's rows key on its id.
        with store._get_conn() as conn:
            conn.execute("UPDATE audio_files SET lesson_id = ? WHERE lesson_id = ?", ("s1", lesson.title))
        app.state.content_store = store
        app.state.audio_dir = tmp_path / "audio"
        app.state.renderer = _make_fake_renderer()
        app.state.tts = _CountingTTS()
        return store, lesson

    async def test_estimate_prices_the_session_like_a_lesson(self, session, tmp_path):
        """Real voices (the reassemble fixture's ids are placeholders pricing
        rightly refuses), so the Davis "Hello" oracle applies here too."""
        store, _ = session
        store.save_review_session("s2", "no", "2026-09-30", _no_lesson())
        r = await _post("/api/review-sessions/s2/render-estimate", {"section_types": ["natural_speed"]})
        assert r.status_code == 200, r.text
        assert (r.json()["billable_chars"], r.json()["new_clips"]) == (65, 1)

    async def test_chosen_sections_are_re_rendered(self, session):
        store, _ = session
        before = _rows(store, "s1")
        r = await _post("/api/review-sessions/s1/rerender", {"section_types": ["slow_speed"]})
        assert r.status_code == 200, r.text
        after = _rows(store, "s1")
        assert after[3]["file_path"] != before[3]["file_path"]
        assert after[0]["file_path"] == before[0]["file_path"]

    async def test_a_session_already_rendering_is_refused(self, session):
        """Shares the review-session render marker, so a re-render and a render
        of the same session exclude each other."""
        app_state.review_renders(app).add("s1")
        try:
            r = await _post("/api/review-sessions/s1/rerender", {"section_types": ["slow_speed"]})
        finally:
            app_state.review_renders(app).discard("s1")
        assert r.status_code == 409

    async def test_a_missing_session_is_404(self, session):
        # The detail, not just the code: an unrouted path is a 404 too
        # ("Not Found"), so the status alone passes before the route exists.
        for path in ("/api/review-sessions/nope/render-estimate", "/api/review-sessions/nope/rerender"):
            r = await _post(path, {})
            assert (r.status_code, r.json()["detail"]) == (404, "Review session not found"), path
