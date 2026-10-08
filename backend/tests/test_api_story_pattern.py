"""A story lesson ends on its day's affix drill, however the story was made (tunatale-ve4p.7).

Two writers put a story into a curriculum day: the pipeline, after the model
writes it, and ``POST /api/story/import``, after the owner pastes one written
from the exported prompt. Both publish through ``CurriculumDayTarget``, and
that is where the closing drill is added, so neither can forget it.
``test_story_pattern.py`` pins the drill itself; this file pins that it arrives.

The real pipeline and the real import route run; only the story generator and
the renderer are stood in for.
"""

from __future__ import annotations

from app.models.lesson import SectionType
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401
from tests.test_api_grammar_lesson import _CID, _client, deck  # noqa: F401
from tests.test_grammar_lesson import _BURIAL, _WAKE, _story_lesson
from tests.test_pipeline import wait_for_job

_DRILL = {"pattern": "mo-mi", "roots": ["adto", "anhi", "uban"], "new_roots": []}


def _story(lines: list[tuple[str, str]]) -> dict:
    return {
        "title": "An Evening Wake",
        "key_phrases": [{"phrase": "maayong gabii", "translation": "good evening"}],
        "scenes": [
            {
                "label": "At the wake",
                "lines": [{"speaker": "male-1", "text": text, "translation": english} for text, english in lines],
            }
        ],
        # Present so the import does not ask a model for them.
        "dialogue_glosses": [{"word": "kape", "translation": "coffee"}],
    }


async def test_a_story_the_pipeline_generates_ends_on_the_days_drill(deck):  # noqa: F811
    store, pipeline, generator = deck
    generator.lesson_to_return = _story_lesson("An Evening Wake", _WAKE)

    pipeline.start()
    pipeline.enqueue("ceb", _CID, 1, "generate", user_id=None, force=True)
    await wait_for_job(pipeline, "ceb", _CID, 1, "ready")
    await pipeline.shutdown()

    _, lesson = store.get_latest_lesson_by_day(_CID, 1)
    # Day 1 is the curriculum's first thematic day: mo- / mi-.
    assert lesson.generation_metadata["affix_drill"] == _DRILL
    assert lesson.sections[-1].section_type is SectionType.AFFIX_DRILL
    assert [s.section_type for s in lesson.sections].count(SectionType.AFFIX_DRILL) == 1
    assert lesson.kind == "thematic"


async def test_a_story_imported_by_hand_ends_on_the_same_drill(deck):  # noqa: F811
    store, _, _ = deck
    async with _client() as client:
        response = await client.post(
            "/api/story/import", json={"curriculum_id": _CID, "day": 1, "story": _story(_WAKE)}
        )

    assert response.status_code == 201, response.text
    _, lesson = store.get_latest_lesson_by_day(_CID, 1)
    assert lesson.generation_metadata["affix_drill"] == _DRILL
    assert lesson.sections[-1].section_type is SectionType.AFFIX_DRILL


async def test_the_lesson_is_served_with_its_story_and_its_drill(deck):  # noqa: F811
    """Not a drill-only lesson: the reader keeps its dialogue and the player gains a Drill phase."""
    store, _, _ = deck
    async with _client() as client:
        await client.post("/api/story/import", json={"curriculum_id": _CID, "day": 1, "story": _story(_WAKE)})
        lesson_id, _ = store.get_latest_lesson_by_day(_CID, 1)
        served = (await client.get(f"/api/story/{lesson_id}")).json()
        transcript = (await client.get(f"/api/srs/content/{lesson_id}/transcript")).json()

    types = [section["type"] for section in served["sections"]]
    assert types[-1] == "affix_drill"
    assert "natural_speed" in types
    assert [root["root"] for root in served["drill"]["roots"]] == ["adto", "anhi", "uban"]
    assert len(transcript["dialogue_lines"]) == len(_WAKE)


async def test_a_story_that_barely_uses_the_pattern_is_stored_without_a_drill(deck):  # noqa: F811
    store, _, _ = deck
    async with _client() as client:
        # The burial says moadto and nothing else in mo- / mi-.
        response = await client.post(
            "/api/story/import", json={"curriculum_id": _CID, "day": 1, "story": _story(_BURIAL)}
        )

    assert response.status_code == 201, response.text
    _, lesson = store.get_latest_lesson_by_day(_CID, 1)
    assert "affix_drill" not in lesson.generation_metadata
    assert SectionType.AFFIX_DRILL not in [s.section_type for s in lesson.sections]
