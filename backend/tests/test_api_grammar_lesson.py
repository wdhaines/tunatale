"""Previewing, reading and listening to a grammar lesson.

A grammar lesson is an affix drill with no story, and every learner-facing route
here was written for a lesson that has one: the transcript, the listen preview,
the listen itself, the review queue's grid. The day is no longer added over HTTP
— that feature was retired (tunatale-ve4p.27) — so the setup helper appends it
the way the retired POST route did (plan against the deck, name the day, save,
enqueue) and the real pipeline builds it. The story generator and renderer are
replaced, so "a day was appended" and "a drill lesson came out of it" are one
assertion chain rather than two halves.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest
from httpx import ASGITransport, AsyncClient

from app.api._serializers import serialize_lesson
from app.generation.grammar_lesson import find_pattern, pattern_title, plan_grammar_lesson
from app.generation.pipeline import LessonPipeline
from app.languages import get_language
from app.llm.activity import ActivityLog
from app.main import app
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.storage.store import ContentStore
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401
from tests._helpers.lemmatizer import StubLemmatizer
from tests.test_pipeline import FakeRenderer, FakeStoryGenerator, wait_for_job

_CID = "funeral"
_CEB = {"X-TT-Language": "ceb"}
_NARRATOR = "en-US-DavisMultilingualNeural"


def _understand(db, root: str) -> None:
    db.add_collocation(
        SyntacticUnit(text=root, translation=root, word_count=1, difficulty=1, source="test"), language_code="ceb"
    )
    db.update_direction(
        db.get_collocation(root).guid,
        Direction.RECOGNITION,
        DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            stability=5.0,
            difficulty=4.0,
            reps=5,
            state=SRSState.REVIEW,
        ),
    )


def _cards(db) -> int:
    with db._get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM collocations").fetchone()[0]


def _wake() -> Lesson:
    line = Phrase(text="Mouban ko ugma.", voice_id="ceb-PH-CharonGemini", language_code="ceb", role="male-1")
    english = Phrase(text="I will come along tomorrow.", voice_id=_NARRATOR, language_code="en", role="narrator")
    return Lesson(
        title="An Evening Wake",
        language_code="ceb",
        sections=[
            Section(section_type=SectionType.NATURAL_SPEED, phrases=[line]),
            Section(section_type=SectionType.TRANSLATED, phrases=[line, english]),
        ],
    )


@pytest.fixture
def deck(srs_db, tmp_path):
    """A Cebuano learner with one story day, three understood roots, and a live pipeline."""
    store = ContentStore(":memory:")
    store.save_curriculum(
        _CID,
        Curriculum(
            id=_CID,
            topic="Attending a funeral",
            language_code="ceb",
            cefr_level="A1",
            days=[CurriculumDay(day=1, title="A wake", focus="f", collocations=["haya"], learning_objective="o")],
        ),
    )
    store.save_lesson("wake-1", _CID, 1, _wake())
    for root in ("inom", "adto", "uban"):
        _understand(srs_db, root)
    generator = FakeStoryGenerator()
    pipeline = LessonPipeline(
        story_generator=generator,
        renderer=FakeRenderer(),
        audio_dir=tmp_path,
        content_stores={"ceb": store},
        languages={"ceb": get_language("ceb")},
        srs_dbs={"ceb": srs_db},
        activity_log=ActivityLog(maxlen=100),
        llm_client=None,
    )
    app.state.content_store = store
    app.state.srs_db = srs_db
    app.state.language = get_language("ceb")
    app.state.pipeline = pipeline
    return store, pipeline, generator


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_CEB)


def _append_grammar_day(store, srs_db, pipeline, *, pattern: str = "mo-mi", enqueue: bool = True) -> CurriculumDay:
    """Append *pattern*'s grammar day and queue it for the worker.

    The steps the retired ``POST .../grammar-lessons`` route took, minus the
    route: plan the drill against the learner's deck, name the day, save the
    plan, enqueue the build. Tests below wait for the pipeline to build it.
    """
    curriculum = store.get_curriculum(_CID)
    code = curriculum.language_code
    plan = plan_grammar_lesson(
        code,
        find_pattern(code, pattern),
        srs_db=srs_db,
        store=store,
        curriculum_id=_CID,
        lemmatizer=None,
    )
    day = CurriculumDay(
        day=max((d.day for d in curriculum.days), default=0) + 1,
        title=pattern_title(plan.pattern),
        focus="a drill",
        collocations=list(plan.forms),
        learning_objective="Say each form.",
        kind="grammar",
        pattern=plan.pattern.key,
    )
    curriculum.days.append(day)
    store.save_curriculum(_CID, curriculum)
    if enqueue:
        pipeline.enqueue(code, _CID, day.day, "generate", user_id=None)
    return day


async def _drill_lesson(store, srs_db, pipeline) -> str:
    """Append the mo-mi day, let the pipeline build it, and return its lesson id."""
    _append_grammar_day(store, srs_db, pipeline)
    await wait_for_job(pipeline, "ceb", _CID, 2, "ready")
    return store.get_latest_lesson_by_day(_CID, 2)[0]


def _contrast_rows(db) -> list[dict]:
    with db._get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.text, c.lemma, c.card_type, c.source_sentence, c.anki_note_id, d.direction, d.state,
                   d.anki_card_id, d.dirty_fsrs, d.last_review, d.introduced_at
            FROM collocations c JOIN collocation_directions d ON d.collocation_id = c.id
            WHERE c.disambig_key LIKE 'morph:pair-%' ORDER BY c.id
            """
        ).fetchall()
    return [dict(row) for row in rows]


async def test_appending_a_grammar_day_gets_the_pipeline_to_build_the_drill(deck, srs_db):
    store, pipeline, generator = deck
    # The other words of the story's line: without them it is not asked for
    # (the test below).
    for word in ("ko", "ugma"):
        _understand(srs_db, word)
    pipeline.start()

    day = _append_grammar_day(store, srs_db, pipeline)

    assert (day.day, day.kind, day.pattern) == (2, "grammar", "mo-mi")
    await wait_for_job(pipeline, "ceb", _CID, 2, "ready")
    _, lesson = store.get_latest_lesson_by_day(_CID, 2)
    assert lesson.kind == "grammar"
    assert [s.section_type for s in lesson.sections] == [SectionType.AFFIX_DRILL]
    assert generator.calls == []
    # The pipeline plans against the deck when it builds: the line the lesson
    # asks for is one whose every other word the learner holds.
    assert "Mouban ko ugma." in [p.text for p in lesson.sections[0].phrases if p.role == "answer"]


async def test_a_grammar_day_is_built_even_when_stories_are_made_by_hand(deck, srs_db):
    """Manual mode keeps the MODEL from being called unasked. A drill calls no
    model, so reconcile still queues a grammar day with no lesson: its job
    lives in memory, and a restart before it ran must not strand it."""
    store, pipeline, _ = deck
    curriculum = store.get_curriculum(_CID)
    curriculum.metadata["generation_mode"] = "manual"
    store.save_curriculum(_CID, curriculum)
    _append_grammar_day(store, srs_db, pipeline, enqueue=False)
    pipeline.start()

    pipeline.reconcile("ceb", _CID, user_id=None)

    await wait_for_job(pipeline, "ceb", _CID, 2, "ready")
    await pipeline.shutdown()


async def test_the_pipeline_reads_the_line_with_its_own_lemmatizer(deck, srs_db, tmp_path):
    """The pipeline plans the drill again when it builds, with the lemmatizer
    handed to it: here only a lemmatizer that knows `ko` is `ako` finds the
    card, so a pipeline that dropped its own would leave the line out."""
    store, _, generator = deck
    for word in ("ako", "ugma"):
        _understand(srs_db, word)
    stub = StubLemmatizer()
    stub.set_lemma("ko", "ako")
    pipeline = LessonPipeline(
        story_generator=generator,
        renderer=FakeRenderer(),
        audio_dir=tmp_path,
        content_stores={"ceb": store},
        languages={"ceb": get_language("ceb")},
        srs_dbs={"ceb": srs_db},
        activity_log=ActivityLog(maxlen=100),
        llm_client=None,
        lemmatizer=stub,
        model_version="stub-1",
    )
    pipeline.start()

    _append_grammar_day(store, srs_db, pipeline)

    await wait_for_job(pipeline, "ceb", _CID, 2, "ready")
    _, lesson = store.get_latest_lesson_by_day(_CID, 2)
    assert "Mouban ko ugma." in [p.text for p in lesson.sections[0].phrases if p.role == "answer"]
    await pipeline.shutdown()


async def test_a_grammar_lesson_does_not_end_on_a_line_with_an_unmet_word(deck, srs_db):
    """The build asks the learner's deck for the words of every candidate line
    (tunatale-ve4p.20): `ko` and `ugma` have no card here, so the story's only
    line is left out."""
    store, pipeline, _ = deck
    pipeline.start()

    await _drill_lesson(store, srs_db, pipeline)

    _, lesson = store.get_latest_lesson_by_day(_CID, 2)
    assert "Mouban ko ugma." not in [p.text for p in lesson.sections[0].phrases]
    await pipeline.shutdown()


async def test_the_lesson_is_served_with_the_table_of_what_it_drills(deck, srs_db):
    """The page under the player has no transcript to show; this is what it shows instead."""
    store, pipeline, _ = deck
    pipeline.start()
    async with _client() as client:
        await _drill_lesson(store, srs_db, pipeline)
        lesson = (await client.get(f"/api/curriculum/{_CID}/days/2/lesson")).json()
        story = (await client.get(f"/api/curriculum/{_CID}/days/1/lesson")).json()
    await pipeline.shutdown()

    assert lesson["drill"] == {
        "pattern": "mo-mi",
        "title": "Affix drill: mo- / mi-",
        "labels": ["not begun", "begun"],
        "roots": [
            {
                "root": "uban",
                "english": "accompany",
                "new": False,
                "forms": [
                    {"form": "mouban", "english": "will go along"},
                    {"form": "miuban", "english": "went along"},
                ],
            },
            {
                "root": "inom",
                "english": "drink",
                "new": False,
                "forms": [{"form": "moinom", "english": "will drink"}, {"form": "miinom", "english": "drank"}],
            },
            {
                "root": "adto",
                "english": "go",
                "new": False,
                "forms": [{"form": "moadto", "english": "will go"}, {"form": "miadto", "english": "went"}],
            },
        ],
        # How far the learner is with the pattern, for the meters under the
        # table (tunatale-ve4p.30). Nothing heard or reviewed yet.
        "progress": {
            "review_only": False,
            "drill_days": 0,
            "drill_days_needed": 2,
            "first_passes": 0,
            "first_passes_needed": 4,
            "reviews": 0,
            "passes": 0,
            "pass_rate_needed": 0.8,
            "met": False,
            "stuck": False,
        },
    }
    assert "drill" not in story


def test_a_drill_served_without_the_learners_deck_has_no_progress():
    """Nothing to count listens or cards from, so no meters rather than zeros."""
    lesson = _wake()
    lesson.generation_metadata["affix_drill"] = {"pattern": "mo-mi", "roots": ["adto"], "new_roots": []}
    served = serialize_lesson("wake", lesson, owner=True)
    assert served["drill"]["pattern"] == "mo-mi"
    assert "progress" not in served["drill"]


async def test_the_drills_progress_counts_a_listen_when_the_lesson_is_fetched_again(deck, srs_db):
    store, pipeline, _ = deck
    pipeline.start()
    async with _client() as client:
        lesson_id = await _drill_lesson(store, srs_db, pipeline)
        await client.post("/api/srs/listen", json={"content_id": lesson_id})
        by_day = (await client.get(f"/api/curriculum/{_CID}/days/2/lesson")).json()
        by_id = (await client.get(f"/api/story/{lesson_id}")).json()
    await pipeline.shutdown()

    assert by_day["drill"]["progress"]["drill_days"] == by_id["drill"]["progress"]["drill_days"] == 1


async def test_a_grammar_lesson_can_be_previewed_read_and_marked_listened(deck, srs_db):
    """It has no story, and every one of these routes was written for a lesson that does."""
    store, pipeline, _ = deck
    pipeline.start()
    async with _client() as client:
        lesson_id = await _drill_lesson(store, srs_db, pipeline)

        transcript = await client.get(f"/api/srs/content/{lesson_id}/transcript")
        preview = await client.get(f"/api/srs/content/{lesson_id}/listen-preview")
        listened = await client.post("/api/srs/listen", json={"content_id": lesson_id})
    await pipeline.shutdown()

    assert (transcript.status_code, preview.status_code, listened.status_code) == (200, 200, 200)
    assert (transcript.json()["key_phrases"], transcript.json()["dialogue_lines"]) == ([], [])
    assert srs_db.count_listens(lesson_id) == 1


async def test_marking_a_grammar_lesson_listened_adds_its_contrast_cards(deck, srs_db):
    """One card per understood root, in the shape ``sync_create_new`` expects of a card TT originated.

    The story says ``mouban`` and nothing else in this pattern, so uban is
    asked for the form it has not been heard in and the other two for the last.
    """
    store, pipeline, _ = deck
    pipeline.start()
    async with _client() as client:
        lesson_id = await _drill_lesson(store, srs_db, pipeline)
        cards_before = _cards(srs_db)
        listened = await client.post("/api/srs/listen", json={"content_id": lesson_id})
        queue = await client.get("/api/srs/review-queue")
    await pipeline.shutdown()

    rows = _contrast_rows(srs_db)
    assert sorted(row["text"] for row in rows) == ["miadto", "miinom", "miuban"]
    assert _cards(srs_db) == cards_before + 3
    assert listened.json()["created"] == 3
    # Added, not graded: NEW, production only, nothing for the sync to mistake
    # for a linked or reviewed card (.claude/rules/anki-sync.md, card-adding contract).
    for row in rows:
        assert (row["card_type"], row["direction"], row["state"]) == ("cloze", "production", "new")
        assert (row["anki_note_id"], row["anki_card_id"], row["last_review"], row["introduced_at"]) == (None,) * 4
        assert row["dirty_fsrs"] == 0
        assert row["source_sentence"] == f"{{{{c1::{row['text']}}}}}"
    # ...and it is in today's queue with no sync in between.
    assert {"miadto", "miinom", "miuban"} <= {entry["text"] for entry in queue.json()["queue"]}


async def test_the_review_queue_serves_the_grid_with_a_contrast_card(deck, srs_db):
    """The grid is not stored, so the queue has to hand the review screen its cells."""
    store, pipeline, _ = deck
    pipeline.start()
    async with _client() as client:
        lesson_id = await _drill_lesson(store, srs_db, pipeline)
        await client.post("/api/srs/listen", json={"content_id": lesson_id})
        queue = (await client.get("/api/srs/review-queue")).json()["queue"]
    await pipeline.shutdown()

    served = {entry["text"]: entry for entry in queue}
    assert served["miuban"]["paradigm"] == {
        "root": "uban",
        "english": "accompany",
        "cells": [
            {"form": "mouban", "english": "will go along", "blank": False},
            {"form": "miuban", "english": "went along", "blank": True},
        ],
    }
    # Every other card says it has none, the fixture's own root cards included.
    assert {entry["paradigm"] for entry in queue if entry["text"] not in ("miuban", "miinom", "miadto")} <= {None}
    assert served["inom"]["paradigm"] is None


async def test_listening_again_the_same_day_adds_nothing_more(deck, srs_db):
    store, pipeline, _ = deck
    pipeline.start()
    async with _client() as client:
        lesson_id = await _drill_lesson(store, srs_db, pipeline)
        await client.post("/api/srs/listen", json={"content_id": lesson_id})
        again = await client.post("/api/srs/listen", json={"content_id": lesson_id})
    await pipeline.shutdown()

    assert again.json()["created"] == 0
    assert len(_contrast_rows(srs_db)) == 3


async def test_contrast_cards_stop_at_the_days_new_card_budget(deck, srs_db):
    """Two left in the day means two cards, the drill's first two roots.

    A cap of five leaves two: the fixture's three root cards were created
    today and are still new on the production side, and a card created today
    holds its slot until it is introduced (``count_new_created_today``).
    """
    store, pipeline, _ = deck
    srs_db.set_anki_state_cache("daily_new_cap", "5")
    pipeline.start()
    async with _client() as client:
        lesson_id = await _drill_lesson(store, srs_db, pipeline)
        listened = await client.post("/api/srs/listen", json={"content_id": lesson_id})
    await pipeline.shutdown()

    assert listened.json()["created"] == 2
    assert [row["text"] for row in _contrast_rows(srs_db)] == ["miuban", "miinom"]


async def test_a_story_lesson_adds_no_contrast_cards(deck, srs_db):
    """Only a lesson with an affix drill does: the story says mouban and that is not a drill."""
    async with _client() as client:
        listened = await client.post("/api/srs/listen", json={"content_id": "wake-1"})

    assert listened.status_code == 200
    assert _contrast_rows(srs_db) == []


async def test_the_preview_lists_the_cards_the_listen_will_add(deck, srs_db):
    """Each row says what the card asks for and which form it shows as the model. Read-only."""
    store, pipeline, _ = deck
    pipeline.start()
    async with _client() as client:
        lesson_id = await _drill_lesson(store, srs_db, pipeline)
        preview = await client.get(f"/api/srs/content/{lesson_id}/listen-preview")
    await pipeline.shutdown()

    assert preview.json()["affix_cards"] == [
        {"form": "miuban", "english": "went along", "root": "uban", "model": ["mouban"], "will_create": True},
        {"form": "miinom", "english": "drank", "root": "inom", "model": ["moinom"], "will_create": True},
        {"form": "miadto", "english": "went", "root": "adto", "model": ["moadto"], "will_create": True},
    ]
    assert _contrast_rows(srs_db) == []


async def test_the_preview_marks_the_cards_past_the_budget(deck, srs_db):
    """The same cut the listen makes: two left in the day, so the third row is for a later listen."""
    store, pipeline, _ = deck
    srs_db.set_anki_state_cache("daily_new_cap", "5")  # the fixture's three root cards hold three slots
    pipeline.start()
    async with _client() as client:
        lesson_id = await _drill_lesson(store, srs_db, pipeline)
        preview = await client.get(f"/api/srs/content/{lesson_id}/listen-preview")
    await pipeline.shutdown()

    assert [(row["form"], row["will_create"]) for row in preview.json()["affix_cards"]] == [
        ("miuban", True),
        ("miinom", True),
        ("miadto", False),
    ]


async def test_a_story_lesson_previews_no_affix_cards(deck):
    async with _client() as client:
        preview = await client.get("/api/srs/content/wake-1/listen-preview")

    assert preview.json()["affix_cards"] == []


@pytest.mark.parametrize(
    ("cap", "body", "added"),
    [
        # A skipped card is left out, and offered again by the next listen.
        ("20", {"skipped_affix_cards": ["miinom"]}, ["miuban", "miadto"]),
        # A skip keeps its slot: the card past the budget is not promoted into it.
        ("5", {"skipped_affix_cards": ["miuban"]}, ["miinom"]),
        # One deliberate tap takes a card past the day's limit.
        ("5", {"over_cap_affix_cards": ["miadto"]}, ["miuban", "miinom", "miadto"]),
        # Skipped wins over opted in; neither names a card this listen does not offer.
        (
            "5",
            {"skipped_affix_cards": ["miadto", "nope"], "over_cap_affix_cards": ["miadto", "nope"]},
            ["miuban", "miinom"],
        ),
    ],
)
async def test_the_listen_honours_what_the_preview_rows_were_set_to(deck, srs_db, cap, body, added):
    store, pipeline, _ = deck
    srs_db.set_anki_state_cache("daily_new_cap", cap)
    pipeline.start()
    async with _client() as client:
        lesson_id = await _drill_lesson(store, srs_db, pipeline)
        listened = await client.post("/api/srs/listen", json={"content_id": lesson_id, **body})
    await pipeline.shutdown()

    assert [row["text"] for row in _contrast_rows(srs_db)] == added
    assert listened.json()["created"] == len(added)


async def test_a_review_session_adds_no_contrast_cards(deck, srs_db):
    """A review session is listened through the same route and belongs to no curriculum.

    The cards' lesson lines are read from a curriculum's stories, so there is
    nothing to read them from; the session is marked listened and that is all.
    """
    store, _, _ = deck
    store.save_review_session("review-1", "ceb", "2026-10-08", _wake())
    async with _client() as client:
        listened = await client.post("/api/srs/listen", json={"content_id": "review-1"})

    assert listened.status_code == 200
    assert srs_db.count_listens("review-1") == 1
    assert _contrast_rows(srs_db) == []


async def test_the_cards_audio_is_made_after_the_response_and_is_the_form(deck, srs_db, monkeypatch):
    """The form is voiced, never a table. Faked at the TTS vendor boundary."""
    import app.audio.cloze_tts as cloze_tts_mod

    said: list[str] = []

    async def _tts(text, voice=None):
        said.append(text)
        return b"fake-mp3"

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _tts)
    store, pipeline, _ = deck
    pipeline.start()
    async with _client() as client:
        lesson_id = await _drill_lesson(store, srs_db, pipeline)
        await client.post("/api/srs/listen", json={"content_id": lesson_id})
    await pipeline.shutdown()

    assert sorted(set(said)) == ["miadto", "miinom", "miuban"]


async def test_the_new_day_is_served_with_its_kind_and_pattern(deck, srs_db):
    store, pipeline, _ = deck
    _append_grammar_day(store, srs_db, pipeline)

    async with _client() as client:
        curriculum = (await client.get(f"/api/curriculum/{_CID}")).json()
        source = (await client.get(f"/api/curriculum/{_CID}/source")).json()

    assert [(d["position"], d["kind"], d["pattern"]) for d in curriculum["days"]] == [
        (1, "thematic", ""),
        (2, "grammar", "mo-mi"),
    ]
    assert [(d["kind"], d["pattern"]) for d in source["days"]] == [("thematic", ""), ("grammar", "mo-mi")]


async def test_an_exported_plan_with_a_grammar_day_imports_again(deck, srs_db):
    """The plan file is edited by hand and re-imported; a grammar day must survive the trip."""
    store, pipeline, _ = deck
    _append_grammar_day(store, srs_db, pipeline)

    async with _client() as client:
        source = (await client.get(f"/api/curriculum/{_CID}/source")).json()
        response = await client.post("/api/curriculum/import", json=source)

    assert response.status_code == 201, response.text
    assert [(d.kind, d.pattern) for d in store.get_curriculum(_CID).days] == [("thematic", ""), ("grammar", "mo-mi")]


# --- object focus: the focus card (tunatale-ve4p.9) -------------------------------


async def _focus_lesson(store, srs_db, pipeline) -> str:
    """The on-gi day for a learner who understands inom, kaon and dala, built by the pipeline."""
    for root in ("kaon", "dala"):
        _understand(srs_db, root)
    _append_grammar_day(store, srs_db, pipeline, pattern="on-gi")
    await wait_for_job(pipeline, "ceb", _CID, 2, "ready")
    return store.get_latest_lesson_by_day(_CID, 2)[0]


def _focus_rows(db) -> list[dict]:
    with db._get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.text, c.lemma, c.disambig_key, c.card_type, c.source_sentence, c.anki_note_id,
                   d.direction, d.state, d.anki_card_id, d.last_review
            FROM collocations c JOIN collocation_directions d ON d.collocation_id = c.id
            WHERE c.disambig_key LIKE 'morph:focus-%' ORDER BY c.id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _parts(*runs: str) -> list[dict]:
    """``"*bold*"`` and plain runs, as the review screen receives them."""
    return [{"text": run.strip("*"), "bold": run.startswith("*")} for run in runs]


async def test_marking_an_object_focus_lesson_listened_adds_one_focus_card_per_root(deck, srs_db):
    store, pipeline, _ = deck
    pipeline.start()
    async with _client() as client:
        lesson_id = await _focus_lesson(store, srs_db, pipeline)
        listened = await client.post("/api/srs/listen", json={"content_id": lesson_id})
        queue = await client.get("/api/srs/review-queue")
    await pipeline.shutdown()

    rows = _focus_rows(srs_db)
    assert [(row["lemma"], row["text"], row["disambig_key"]) for row in rows] == [
        ("inom", "Giinom ko ang tubig.", "morph:focus-verb-gi"),
        ("kaon", "Gikaon ko ang pan.", "morph:focus-verb-gi"),
        ("dala", "Gidala ko ang pagkaon.", "morph:focus-verb-gi"),
    ]
    assert listened.json()["created"] == 3
    for row in rows:
        assert (row["card_type"], row["direction"], row["state"]) == ("cloze", "production", "new")
        assert (row["anki_note_id"], row["anki_card_id"], row["last_review"]) == (None,) * 3
        assert row["source_sentence"] == f"{{{{c1::{row['text']}}}}}"
    assert {row["text"] for row in rows} <= {entry["text"] for entry in queue.json()["queue"]}


async def test_the_review_queue_serves_the_focus_with_a_focus_card(deck, srs_db):
    """The bold is not stored, so the queue hands the review screen its runs."""
    store, pipeline, _ = deck
    pipeline.start()
    async with _client() as client:
        lesson_id = await _focus_lesson(store, srs_db, pipeline)
        await client.post("/api/srs/listen", json={"content_id": lesson_id})
        queue = (await client.get("/api/srs/review-queue")).json()["queue"]
    await pipeline.shutdown()

    served = {entry["text"]: entry for entry in queue}
    assert served["Giinom ko ang tubig."]["focus"] == {
        "prompt": _parts("I drank ", "*the water*", "."),
        "answer": _parts("*Giinom*", " ko ", "*ang tubig*", "."),
    }
    assert served["Giinom ko ang tubig."]["paradigm"] is None
    focus_texts = {"Giinom ko ang tubig.", "Gikaon ko ang pan.", "Gidala ko ang pagkaon."}
    assert {entry["focus"] is None for entry in queue if entry["text"] not in focus_texts} == {True}


async def test_the_preview_lists_the_focus_cards_by_their_sentences(deck, srs_db):
    store, pipeline, _ = deck
    pipeline.start()
    async with _client() as client:
        lesson_id = await _focus_lesson(store, srs_db, pipeline)
        preview = await client.get(f"/api/srs/content/{lesson_id}/listen-preview")
    await pipeline.shutdown()

    assert preview.json()["affix_cards"] == [
        {
            "form": "Giinom ko ang tubig.",
            "english": "I drank the water.",
            "root": "inom",
            "model": [],
            "will_create": True,
        },
        {"form": "Gikaon ko ang pan.", "english": "I ate the bread.", "root": "kaon", "model": [], "will_create": True},
        {
            "form": "Gidala ko ang pagkaon.",
            "english": "I brought the food.",
            "root": "dala",
            "model": [],
            "will_create": True,
        },
    ]
    assert _focus_rows(srs_db) == []


async def test_a_skipped_focus_card_is_named_by_its_sentence(deck, srs_db):
    store, pipeline, _ = deck
    pipeline.start()
    async with _client() as client:
        lesson_id = await _focus_lesson(store, srs_db, pipeline)
        await client.post(
            "/api/srs/listen", json={"content_id": lesson_id, "skipped_affix_cards": ["Gikaon ko ang pan."]}
        )
    await pipeline.shutdown()

    assert [row["text"] for row in _focus_rows(srs_db)] == ["Giinom ko ang tubig.", "Gidala ko ang pagkaon."]
