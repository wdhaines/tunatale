"""Content-surface parity — the render obligation (Stage 1, tunatale-w1fp).

Seven code paths write lesson-shaped content by hand and drop different steps.
This module pins the ROUTE-level half of that matrix: every route on the two
content routers is inventoried in ``ROUTE_TABLE`` (§3 of the brief), and each of
the six routes that writes audio-bearing text is driven end-to-end to prove a
render is scheduled.

Stage 1 is the TDD red step and is SUPPOSED to fail: the four review-session
write routes schedule no render today, and the prewarm tasks created by
``import_story`` and ``LessonPipeline._generate`` are bare ``asyncio.create_task``
calls with no strong reference. ``app.generation.publishing`` (the module that
Stage 2 creates to hold the anchored task set) does not exist yet, so every
reference to its set behind ``_publishing_task_set`` is guarded: the tests must
fail on their ASSERTIONS, not on a missing module at collection.

Predicted (orchestrator-measured): 4 render-obligation tests red + 2 prewarm-
anchoring tests red; the completeness test green immediately.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path as _Path
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import generation, review_sessions
from app.config import settings
from app.generation import publishing
from app.generation.pipeline import LessonPipeline
from app.languages import get_language
from app.llm.activity import ActivityLog
from app.main import app
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.storage.store import ContentStore
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401
from tests.test_api_audio import _fake_render

# The §3 exemption string for regloss, verbatim: regloss pops only
# `story["dialogue_glosses"]` and rebuilds; glosses are hover text and are never
# synthesized, so no audio-bearing text changes.
_REGLOSS_EXEMPTION = (
    'regloss pops only `story["dialogue_glosses"]` and rebuilds;'
    " glosses are hover text and are never synthesized,"
    " so no audio-bearing text changes."
)

# (method, path, handler, writes_audio_bearing_text, must_schedule_render, exemption)
ROUTE_TABLE: list[tuple[str, str, str, bool, bool, str]] = [
    ("POST", "/api/story/generate", "generate_story", True, True, ""),
    ("POST", "/api/story/import", "import_story", True, True, ""),
    ("GET", "/api/story/prompt", "get_story_prompt", False, False, ""),
    ("GET", "/api/story/{lesson_id}/source", "get_lesson_source", False, False, ""),
    ("GET", "/api/story/{lesson_id}", "get_lesson", False, False, ""),
    ("POST", "/api/story/{lesson_id}/regloss", "regloss_lesson_story", False, False, _REGLOSS_EXEMPTION),
    ("POST", "/api/review-sessions", "create_review_session", True, True, ""),
    ("POST", "/api/review-sessions/import", "create_review_session_from_paste", True, True, ""),
    ("POST", "/api/review-sessions/{session_id}/regenerate", "regenerate_review_session", True, True, ""),
    ("POST", "/api/review-sessions/{session_id}/import", "import_review_session", True, True, ""),
    ("GET", "/api/review-sessions/prompt", "get_review_session_draft_prompt", False, False, ""),
    ("GET", "/api/review-sessions/{session_id}/prompt", "get_review_session_prompt", False, False, ""),
    ("GET", "/api/review-sessions/{session_id}/source", "get_review_session_source", False, False, ""),
    ("GET", "/api/review-sessions", "list_review_sessions", False, False, ""),
    ("GET", "/api/review-sessions/{session_id}", "get_review_session", False, False, ""),
    ("POST", "/api/review-sessions/{session_id}/render", "render_review_session", False, False, ""),
    ("GET", "/api/review-sessions/{session_id}/render-status", "get_review_session_render_status", False, False, ""),
    ("POST", "/api/review-sessions/{session_id}/regloss", "regloss_review_session", False, False, _REGLOSS_EXEMPTION),
    ("DELETE", "/api/review-sessions/{session_id}", "delete_review_session", False, False, ""),
]

_MUST_SCHEDULE = [row for row in ROUTE_TABLE if row[4]]


# ── fixtures and helpers ─────────────────────────────────────────────────────


def _day() -> CurriculumDay:
    return CurriculumDay(
        day=1,
        title="Ordering Coffee",
        focus="Café vocabulary",
        collocations=["dober dan"],
        learning_objective="Order a coffee",
        story_guidance="Scene at a Ljubljana café",
    )


def _seed_curriculum(store: ContentStore, curriculum_id: str = "c1") -> None:
    store.save_curriculum(
        curriculum_id,
        Curriculum(id=curriculum_id, topic="t", language_code="sl", cefr_level="A2", days=[_day()]),
    )


def _story() -> dict:
    """The Story-JSON shape the import/paste paths validate and rebuild."""
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
        generation_metadata={"review_requested": ["kavo"], "review_used": ["kavo"], "gloss_entry_count": 1},
    )


def _publishing_task_set() -> set[asyncio.Task]:
    """The publishing module's anchored task set.

    Guarded with a try/except during Stage 1, when the module did not exist yet.
    It exists now, so the guard is gone: a swallowed ImportError here would make
    every render assertion below pass vacuously against an empty set.
    """
    return publishing._background_tasks


async def _drain_publishing_tasks() -> None:
    """Await every task anchored in the publishing set — no sleep-based waits.

    Session renders run as anchored background tasks; asserting on their side
    effects requires the task to have finished, and deterministically draining
    the exposed set is the intended wait (brief §4c/§5).
    """
    tasks = list(_publishing_task_set())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _post(url: str, body: dict):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(url, json=body)


def _install_pipeline(store: ContentStore) -> LessonPipeline:
    pipeline = LessonPipeline(
        story_generator=None,
        renderer=None,
        audio_dir=None,
        content_stores={"sl": store},
        languages={"sl": get_language("sl")},
        srs_dbs={},
        activity_log=ActivityLog(maxlen=100),
        llm_client=None,
    )
    app.state.pipeline = pipeline
    return pipeline


def _install_renderer(tmp_path) -> AsyncMock:
    renderer = AsyncMock()
    renderer.render = AsyncMock(side_effect=_fake_render)
    app.state.renderer = renderer
    app.state.audio_dir = tmp_path
    return renderer


def _inject_lemmatizer_doubles() -> None:
    """Keep UPOS resolution out of the test: model_version "" short-circuits it.

    ``_generate_and_store`` runs the UPOS pass unconditionally (a session with
    no SRS DB can never reach that line in production), so the auto-route tests
    must inject the same doubles the API already knows how to read.
    """
    app.state.lemmatizer = object()
    app.state.model_version = ""


# ── 4a.1 completeness — the table IS the routers, both directions ────────────


def _real_routes() -> set[tuple[str, str, str]]:
    real: set[tuple[str, str, str]] = set()
    for router in (generation.router, review_sessions.router):
        for route in router.routes:
            for method in sorted(route.methods or []):
                real.add((method, route.path, route.endpoint.__name__))
    return real


def test_the_table_inventories_every_registered_route():
    """Completeness: no route may exist on a router without a table row, and no
    table row may describe a route that is not registered."""
    tabled = {(method, path, handler) for method, path, handler, _, _, _ in ROUTE_TABLE}
    real = _real_routes()
    assert tabled == real, (
        "ROUTE_TABLE and the routers diverged. Only in table: "
        f"{sorted(tabled - real)}. Only in router: {sorted(real - tabled)}."
    )


# ── 4a.2 render obligation — drive every writer, prove scheduling ────────────


async def _drive_lesson_route(handler: str) -> LessonPipeline:
    """Drive /api/story/* and assert on the pipeline's enqueued job record.

    Seam pinned in brief §4c: a real LessonPipeline with None collaborators on
    ``app.state.pipeline`` (pattern at test_api_story.py:357), and the assertion
    reads the queue/record structure of ``LessonPipeline.enqueue`` directly.
    """
    store = ContentStore(":memory:")
    app.state.content_store = store
    app.state.language = get_language("sl")
    _seed_curriculum(store)
    pipeline = _install_pipeline(store)
    generator = AsyncMock()
    generator.generate = AsyncMock(return_value=_lesson())
    app.state.story_generator = generator

    if handler == "generate_story":
        body = {"curriculum_id": "c1", "day": 1, "strategy": "WIDER"}
        url = "/api/story/generate"
    else:  # import_story
        body = {"curriculum_id": "c1", "day": 1, "story": _story()}
        url = "/api/story/import"

    resp = await _post(url, body)
    assert resp.status_code == 201

    record = pipeline._jobs[(None, "sl", "c1", 1)]
    assert record["kind"] == "render"
    assert record["state"] == "queued"
    return pipeline


async def _drive_session_route(handler: str, tmp_path) -> None:
    """Drive one of the four review-session write routes and prove a render ran.

    Seam pinned in brief §4c: an AsyncMock renderer on ``app.state.renderer``
    with ``_fake_render`` side effects, then a deterministic drain of the
    publishing task set before asserting the renderer was actually called.
    """
    store = ContentStore(":memory:")
    app.state.content_store = store
    app.state.language = get_language("sl")
    _install_renderer(tmp_path)

    if handler == "create_review_session":
        _inject_lemmatizer_doubles()
        generator = AsyncMock()
        generator.generate_review_session = AsyncMock(return_value=_lesson())
        app.state.story_generator = generator
        url, body, expected = "/api/review-sessions", {}, 201
    elif handler == "create_review_session_from_paste":
        url, body, expected = "/api/review-sessions/import", {"story": _story(), "review_words": ["kavo"]}, 201
    elif handler == "regenerate_review_session":
        store.save_review_session("sess-r", "sl", "2026-09-02", _lesson())
        _inject_lemmatizer_doubles()
        generator = AsyncMock()
        generator.generate_review_session = AsyncMock(return_value=_lesson())
        app.state.story_generator = generator
        url, body, expected = "/api/review-sessions/sess-r/regenerate", {}, 200
    else:  # import_review_session
        store.save_review_session("sess-i", "sl", "2026-09-02", _lesson())
        url, body, expected = "/api/review-sessions/sess-i/import", {"story": _story()}, 200

    resp = await _post(url, body)
    assert resp.status_code == expected

    await _drain_publishing_tasks()
    assert app.state.renderer.render.called, f"{handler} scheduled no render for its new content"


@pytest.mark.parametrize(
    "handler",
    [row[2] for row in _MUST_SCHEDULE],
    ids=[row[2] for row in _MUST_SCHEDULE],
)
async def test_the_route_schedules_a_render(handler: str, tmp_path) -> None:
    if handler in ("generate_story", "import_story"):
        await _drive_lesson_route(handler)
    else:
        await _drive_session_route(handler, tmp_path)


# ── 4b anchoring — every fire-and-forget task keeps a strong reference ───────
#
# ⚠️ REWRITTEN 2026-09-14, and the reason is the point. The first version of
# these two tests drove a route, then asserted the prewarm task was still in
# ``publishing._background_tasks`` afterwards. That measures the OPPOSITE of the
# contract: a task still sitting in the set after the request finished is one
# that never got a chance to run, and a task that ran to completion was never at
# risk of the bug. Both tests were timing-dependent, in opposite directions —
# the HTTP one went red because ``AsyncClient`` yields to the loop (the prewarm
# short-circuits synchronously under conftest's ``lemmatizer_type="lowercase"``
# pin, so it completed and self-discarded mid-request), and the pipeline one
# went GREEN ONLY BY ACCIDENT, because driving ``_generate`` directly happens
# not to yield before the snapshot. A false green is the worse half.
#
# The real invariant is structural: every ``asyncio.create_task`` on a publish
# path keeps a strong reference for the task's lifetime. That is what these two
# test — the mechanism directly, and then every call site — with no timing in
# either.


async def test_anchor_holds_a_strong_reference_until_the_task_completes():
    """``_anchor`` keeps a pending task, and releases it once it is done.

    Both directions, deterministically: the task blocks on an Event this test
    owns, so "still running" and "finished" are states the test chooses rather
    than races. This is the contract the GC bug violated — the event loop keeps
    only a weak reference, so an un-anchored in-flight task can vanish.
    """
    released = asyncio.Event()

    async def _blocked() -> None:
        await released.wait()

    task = asyncio.create_task(_blocked())
    publishing._anchor(task)

    assert task in publishing._background_tasks, (
        "a PENDING task is not strongly referenced — this is exactly the bug: "
        "the event loop holds only a weak reference, so it can be collected mid-flight"
    )

    released.set()
    await task

    assert task not in publishing._background_tasks, (
        "a COMPLETED task was never discarded — the set would grow without bound"
    )


def test_every_fire_and_forget_task_on_a_publish_path_is_strongly_referenced():
    """No bare ``asyncio.create_task`` may survive on a content-publish path.

    The original defect (generation.py:369, pipeline.py:371) was a bare
    ``asyncio.create_task(_prewarm_lesson(...))`` whose result nobody held. This
    asserts the PROPERTY rather than listing the two known sites: every created
    task must either be handed to ``_anchor`` or be assigned somewhere that
    outlives the call. Parsed, not grepped, so a reformat or a line split
    (publishing.py wraps one of these across lines) cannot fool it.
    """
    modules = [
        "app/api/generation.py",
        "app/api/review_sessions.py",
        "app/generation/pipeline.py",
        "app/generation/publishing.py",
    ]

    violations: list[str] = []
    for rel in modules:
        path = _Path(__file__).resolve().parents[1] / rel
        tree = ast.parse(path.read_text())

        anchored: set[int] = set()
        assigned: set[int] = set()
        for node in ast.walk(tree):
            # _anchor(asyncio.create_task(...)) — the intended shape.
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_anchor":
                for arg in node.args:
                    anchored.add(id(arg))
            # self._worker_task = asyncio.create_task(...) — a strong ref that
            # outlives the call, which is equally valid and is why this test
            # asserts the property instead of banning create_task outright.
            # ATTRIBUTE targets only: `x = asyncio.create_task(...)` binds a
            # local that dies at scope exit, which is the very bug this guards.
            if isinstance(node, ast.Assign) and all(isinstance(t, ast.Attribute) for t in node.targets):
                assigned.add(id(node.value))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "create_task"):
                continue
            if id(node) in anchored or id(node) in assigned:
                continue
            violations.append(f"{rel}:{node.lineno}")

    assert not violations, (
        "fire-and-forget task(s) with no strong reference — the event loop keeps "
        f"only a weak reference and may collect them mid-flight: {violations}"
    )


def test_invalidate_audio_without_a_prior_write_is_a_no_op():
    """``invalidate_audio`` on a target that never wrote anything does nothing.

    REWRITTEN when bd tunatale-c8a9 landed. It used to assert that
    ``CurriculumDayTarget.invalidate_audio`` was a no-op in ALL cases and
    predicted it "should fail and be rewritten" — it did not fail, because its
    assertion was only ever about the lesson ROW surviving, and that is now the
    user's explicit decision (2026-09-17) rather than an accident of the no-op.

    What it pins today is the None-side: ``write()`` was never called, so no
    superseded id was captured and there is nothing to invalidate. The
    regenerate behaviour itself is pinned by the tunatale-c8a9 tests below.
    """
    store = ContentStore(":memory:")
    _seed_curriculum(store)
    lesson_id = store.save_lesson("l1", "c1", 1, _lesson())
    target = publishing.CurriculumDayTarget(store, "sl", "c1", 1, None, user_id=None)

    target.invalidate_audio(lesson_id or "l1")

    assert store.get_lesson("l1") is not None, "invalidate_audio must not remove the lesson"


# ── stale-audio invalidation on regenerate (bd tunatale-c8a9) ───────────────
#
# A regenerate mints a FRESH lesson id (ids.py::mint_id = {slug}-{uuid4hex8}),
# so it inserts a second row instead of overwriting. The superseded row stays
# (the user's decision, 2026-09-17: cheap, and a possible undo) but its audio —
# rows and files — must go. These tests pin that split through the real
# publish_lesson seam, exactly the order production runs.


def _seed_day_lesson_with_audio(store: ContentStore, lesson_id: str, audio_files: list[_Path]) -> None:
    """A curriculum day that already owns *lesson_id* and audio rows for *audio_files*."""
    store.save_lesson(lesson_id, "c1", 1, _lesson())
    for i, path in enumerate(audio_files):
        store.save_audio_file(f"audio-{lesson_id}-{i}", lesson_id, str(path))


async def test_a_regenerate_deletes_the_superseded_lesson_audio_rows():
    """A regenerate (replace=True) empties the superseded lesson's audio ROWS."""
    store = ContentStore(":memory:")
    _seed_curriculum(store)
    _seed_day_lesson_with_audio(store, "l-superseded", [_Path("/audio/old-full.opus"), _Path("/audio/old-sec.opus")])

    await publishing.publish_lesson(
        _lesson(),
        target=publishing.CurriculumDayTarget(store, "sl", "c1", 1, None, user_id=None),
        srs_db=None,
        lemmatizer_kwargs={},
        replace=True,
        llm=None,
    )

    assert store.list_audio_files_for_lesson("l-superseded") == []


async def test_a_regenerate_unlinks_the_superseded_lesson_audio_files(tmp_path, monkeypatch):
    """A regenerate UNLINKS the superseded lesson's files — rows alone are not the fix."""
    monkeypatch.setattr(settings, "audio_dir", tmp_path)
    store = ContentStore(":memory:")
    _seed_curriculum(store)
    full = tmp_path / "full.opus"
    section = tmp_path / "section.opus"
    full.write_bytes(b"full-render")
    section.write_bytes(b"section-render")
    _seed_day_lesson_with_audio(store, "l-superseded", [full, section])

    await publishing.publish_lesson(
        _lesson(),
        target=publishing.CurriculumDayTarget(store, "sl", "c1", 1, None, user_id=None),
        srs_db=None,
        lemmatizer_kwargs={},
        replace=True,
        llm=None,
    )

    assert not full.exists(), "superseded lesson's full audio file must be unlinked"
    assert not section.exists(), "superseded lesson's section audio must be unlinked"


async def test_a_regenerate_keeps_the_superseded_lesson_row():
    """⚠️ The superseded lesson ROW survives a regenerate — the user's decision.

    Kept as a possible undo; only its audio goes. If a later change makes this
    fail, that change is wrong (decided 2026-09-17, do not re-open).
    """
    store = ContentStore(":memory:")
    _seed_curriculum(store)
    _seed_day_lesson_with_audio(store, "l-superseded", [_Path("/audio/old-full.opus")])

    await publishing.publish_lesson(
        _lesson(),
        target=publishing.CurriculumDayTarget(store, "sl", "c1", 1, None, user_id=None),
        srs_db=None,
        lemmatizer_kwargs={},
        replace=True,
        llm=None,
    )

    assert store.get_lesson_row("l-superseded") is not None


async def test_a_first_generate_deletes_no_audio(tmp_path, monkeypatch):
    """A first generate (force=False -> replace=False) deletes NOTHING.

    The control that stops the fix from eating a day's audio on ordinary
    generation: audio rows and files that already exist survive a non-replace
    publish, even though write() has a superseded candidate to remember.
    """
    monkeypatch.setattr(settings, "audio_dir", tmp_path)
    store = ContentStore(":memory:")
    _seed_curriculum(store)
    existing = tmp_path / "existing.opus"
    existing.write_bytes(b"existing-render")
    _seed_day_lesson_with_audio(store, "l-existing", [existing])

    await publishing.publish_lesson(
        _lesson(),
        target=publishing.CurriculumDayTarget(store, "sl", "c1", 1, None, user_id=None),
        srs_db=None,
        lemmatizer_kwargs={},
        replace=False,
        llm=None,
    )

    assert store.list_audio_files_for_lesson("l-existing"), "rows must survive a first generate"
    assert existing.exists(), "files must survive a first generate"


async def test_a_regenerate_tolerates_an_already_missing_audio_file(tmp_path, monkeypatch):
    """Unlink is tolerant: an already-gone render is the outcome we want, not a 500."""
    monkeypatch.setattr(settings, "audio_dir", tmp_path)
    store = ContentStore(":memory:")
    _seed_curriculum(store)
    ghost = tmp_path / "ghost.opus"  # recorded in the store, never on disk
    _seed_day_lesson_with_audio(store, "l-superseded", [ghost])

    await publishing.publish_lesson(
        _lesson(),
        target=publishing.CurriculumDayTarget(store, "sl", "c1", 1, None, user_id=None),
        srs_db=None,
        lemmatizer_kwargs={},
        replace=True,
        llm=None,
    )

    assert store.list_audio_files_for_lesson("l-superseded") == []


async def test_invalidate_audio_guards_against_the_captured_superseded_id():
    """The ``!= content_id`` guard: invalidating the captured id itself deletes nothing.

    publish_lesson always passes the freshly written id, which a fresh mint can
    never collide with; this pins the guard the brief requires so a degenerate
    same-id case cannot delete audio either.
    """
    store = ContentStore(":memory:")
    _seed_curriculum(store)
    _seed_day_lesson_with_audio(store, "l-superseded", [_Path("/audio/old-full.opus")])
    target = publishing.CurriculumDayTarget(store, "sl", "c1", 1, None, user_id=None)
    target.write(_lesson())  # captures "l-superseded" as the superseded id

    target.invalidate_audio("l-superseded")  # the captured id itself

    assert store.list_audio_files_for_lesson("l-superseded"), "same-id invalidation must be a no-op"


async def test_a_replace_publish_with_nothing_to_supersede_deletes_nothing():
    """A day with no prior lesson: write() remembers nothing, invalidate no-ops.

    Covers invalidate_audio's empty-side through the real seam (replace=True,
    but there is nothing to supersede) and the store method's empty-rows return.
    """
    store = ContentStore(":memory:")
    _seed_curriculum(store)

    lesson_id = await publishing.publish_lesson(
        _lesson(),
        target=publishing.CurriculumDayTarget(store, "sl", "c1", 1, None, user_id=None),
        srs_db=None,
        lemmatizer_kwargs={},
        replace=True,
        llm=None,
    )

    assert store.get_lesson_row(lesson_id) is not None
    assert store.delete_audio_files_for_lesson(lesson_id) == []
