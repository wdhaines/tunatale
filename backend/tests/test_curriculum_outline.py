"""What a curriculum day holds, for the curriculum page's rows (tunatale-cs3p slice 2).

A row opens in place to show its lesson's parts (key phrases, dialogue, drill)
and, for each drill a story authored, its model sentence and step count, without
the page loading every lesson. Oracle C4 in
.beads-tasks/briefs/design-curriculum-page-2026-11.md: Wolff 1 practice 2 shows
"Dia siya diri." 11 and "Nia siya dinhis Cebu." 11, the lesson's stored drills.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.lesson import Lesson, Phrase, Section, SectionType, lesson_outline
from app.storage.store import ContentStore


def _section(kind: SectionType) -> Section:
    return Section(section_type=kind, phrases=[Phrase(text="Hei.", voice_id="v", language_code="no")])


def _drill(model: str, steps: int) -> dict:
    return {"kind": "substitution", "model": model, "steps": [{"cue": "x", "answer": "y"}] * steps}


def _out(model: str, steps: int, kind: str = "substitution", instruction: str = "") -> dict:
    return {"kind": kind, "model": model, "instruction": instruction, "steps": steps}


DIALOGUE = [
    SectionType.KEY_PHRASES,
    SectionType.NATURAL_SPEED,
    SectionType.SLOW_SPEED,
    SectionType.TRANSLATED,
    SectionType.SLOW_TRANSLATED,
    SectionType.EN_TRANSLATED,
    SectionType.SLOW_EN_TRANSLATED,
]


def _lesson(sections: list[SectionType], drills: list[dict] | None = None, kind: str = "thematic") -> Lesson:
    story: dict = {"title": "t"}
    if drills is not None:
        story["drills"] = drills
    return Lesson(
        title="t",
        language_code="no",
        sections=[_section(s) for s in sections],
        generation_metadata={"story": story},
        kind=kind,
    )


class TestLessonOutline:
    def test_a_dialogue_lesson_has_key_phrases_and_a_dialogue(self):
        assert lesson_outline(_lesson(DIALOGUE)) == {"parts": ["key_phrases", "dialogue"], "drills": []}

    def test_a_practice_lesson_lists_its_drills_with_model_and_step_count(self):
        """C4's shape: the stored drills, in order, each with its own step count."""
        lesson = _lesson([SectionType.AFFIX_DRILL], [_drill("Dia siya diri.", 11), _drill("Nia siya dinhis Cebu.", 11)])
        assert lesson_outline(lesson) == {
            "parts": ["drill"],
            "drills": [_out("Dia siya diri.", 11), _out("Nia siya dinhis Cebu.", 11)],
        }

    def test_a_dialogue_that_ends_in_a_drill_has_all_three_parts(self):
        lesson = _lesson([*DIALOGUE, SectionType.AFFIX_DRILL], [_drill("Nia siya dinhi.", 16)])
        assert lesson_outline(lesson)["parts"] == ["key_phrases", "dialogue", "drill"]

    def test_a_rule_built_drill_has_the_part_but_no_authored_drills(self):
        """A grammar day's affix drill comes from rules, not a story: nothing to name it by."""
        lesson = Lesson(title="t", language_code="no", sections=[_section(SectionType.AFFIX_DRILL)], kind="grammar")
        assert lesson_outline(lesson) == {"parts": ["drill"], "drills": []}

    def test_every_kind_of_authored_drill_counts(self):
        """The live Wolff kinds (2026-11-23): only substitution has a model sentence.

        Wolff 2 practice 5 is a transformation and two question-and-answer
        drills; a filter on ``model`` showed it as having none.
        """
        transformation = {"kind": "transformation", "instruction": "Put the owner first.", "steps": [{}] * 7}
        question = {"kind": "question_answer", "speaker": "male-2", "answer_speaker": "male-1", "steps": [{}] * 9}
        lesson = _lesson([SectionType.AFFIX_DRILL], [transformation, question])
        assert lesson_outline(lesson)["drills"] == [
            _out("", 7, "transformation", "Put the owner first."),
            _out("", 9, "question_answer"),
        ]

    def test_non_text_fields_read_as_empty(self):
        drills = [{"kind": 1, "model": 3, "instruction": None, "steps": [{}]}]
        assert lesson_outline(_lesson([SectionType.AFFIX_DRILL], drills))["drills"] == [_out("", 1, "")]

    @pytest.mark.parametrize(
        "drills",
        [
            "not a list",
            ["not a dict"],
            [{"model": "Hei.", "steps": "many"}],
            [{"model": "Hei."}],
        ],
    )
    def test_a_malformed_stored_drill_is_skipped_not_fatal(self, drills):
        """generation_metadata is free-form JSON; a bad entry must not 500 the curriculum page."""
        lesson = _lesson([SectionType.AFFIX_DRILL], drills)
        assert lesson_outline(lesson) == {"parts": ["drill"], "drills": []}

    def test_no_story_at_all(self):
        lesson = Lesson(title="t", language_code="no", sections=[_section(SectionType.KEY_PHRASES)])
        assert lesson_outline(lesson) == {"parts": ["key_phrases"], "drills": []}


def _day(day: int, order: int | None = None) -> CurriculumDay:
    return CurriculumDay(day=day, title=f"t{day}", focus="f", collocations=["hei"], learning_objective="o", order=order)


@pytest.mark.asyncio
class TestOutlineRoute:
    async def _get(self, store: ContentStore, cid: str):
        app.state.content_store = store
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(f"/api/curriculum/{cid}/outline")

    async def test_each_day_with_a_lesson_gets_its_outline_in_display_order(self):
        store = ContentStore(":memory:")
        c = Curriculum(
            id="c", topic="t", language_code="no", cefr_level="A1", days=[_day(1, 1), _day(2, 3), _day(3, 2), _day(4)]
        )
        store.save_curriculum("c", c)
        store.save_lesson("l1", "c", 1, _lesson(DIALOGUE))
        store.save_lesson("l2", "c", 2, _lesson([SectionType.AFFIX_DRILL], [_drill("Dia siya diri.", 11)]))
        store.save_lesson("l3", "c", 3, _lesson(DIALOGUE))
        # day 4 has no lesson yet: no row, and the page shows no outline toggle for it

        response = await self._get(store, "c")

        assert response.status_code == 200
        assert response.json() == [
            {"day": 1, "parts": ["key_phrases", "dialogue"], "drills": []},
            {"day": 3, "parts": ["key_phrases", "dialogue"], "drills": []},
            {"day": 2, "parts": ["drill"], "drills": [_out("Dia siya diri.", 11)]},
        ]

    async def test_the_latest_lesson_of_a_regenerated_day_wins(self):
        store = ContentStore(":memory:")
        store.save_curriculum("c", Curriculum(id="c", topic="t", language_code="no", cefr_level="A1", days=[_day(1)]))
        store.save_lesson("old", "c", 1, _lesson(DIALOGUE))
        store.save_lesson("new", "c", 1, _lesson([SectionType.AFFIX_DRILL], [_drill("Ny.", 4)]))

        response = await self._get(store, "c")

        assert response.json() == [{"day": 1, "parts": ["drill"], "drills": [_out("Ny.", 4)]}]

    async def test_unknown_curriculum_is_404(self):
        response = await self._get(ContentStore(":memory:"), "missing")
        assert response.status_code == 404
