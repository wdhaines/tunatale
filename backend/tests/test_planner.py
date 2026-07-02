"""Tests for CurriculumPlanner.turn with a stub LLM (no patch, no cassette)."""

from dataclasses import dataclass

import pytest

from app.generation.planner import CurriculumPlanner, PlannerError, PlannerTurn
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.language import Language


@dataclass
class StubLLM:
    """Minimal async LLM stub — NOT a mock/patch, passes the boundary check."""

    response: str

    async def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 256,
    ) -> str:
        return self.response


def _day_dict(day: int, **overrides) -> dict:
    d = {
        "day": day,
        "title": f"Day {day}",
        "focus": f"Focus {day}",
        "collocations": ["coll_a", "coll_b"],
        "learning_objective": f"Objective {day}",
    }
    d.update(overrides)
    return d


def _empty_curriculum(**kw) -> Curriculum:
    defaults = {"id": "test", "topic": "test", "language_code": "sl", "cefr_level": "A2"}
    defaults.update(kw)
    return Curriculum(**defaults)


class TestCurriculumPlanner:
    async def test_pure_chat_reply(self):
        planner = CurriculumPlanner(StubLLM("That sounds great! Let's focus on that."))
        result = await planner.turn(
            curriculum=_empty_curriculum(),
            user_message="Let's plan!",
            batch_size=3,
            learner_snapshot="(no tracked vocabulary)",
            language=Language.slovene(),
        )
        assert result.proposed_days is None
        assert result.reply == "That sounds great! Let's focus on that."

    async def test_proposing_reply(self):
        prose = "Here's your next batch:"
        json_block = '{"days": [{"day": 999, "title": "Day One", "focus": "Focus 1", "collocations": ["a", "b"], "learning_objective": "Obj 1", "story_guidance": "G1"}, {"day": 888, "title": "Day Two", "focus": "Focus 2", "collocations": ["c", "d"], "learning_objective": "Obj 2", "story_guidance": "G2"}, {"day": 777, "title": "Day Three", "focus": "Focus 3", "collocations": ["e", "f"], "learning_objective": "Obj 3", "story_guidance": "G3"}]}'
        planner = CurriculumPlanner(StubLLM(f"{prose}\n\n```json\n{json_block}\n```"))
        result = await planner.turn(
            curriculum=_empty_curriculum(),
            user_message="Plan next 3 days",
            batch_size=3,
            learner_snapshot="(no tracked vocabulary)",
            language=Language.slovene(),
        )
        assert result.proposed_days is not None
        assert len(result.proposed_days) == 3
        assert result.proposed_days[0].day == 1  # renumbered by server
        assert result.proposed_days[1].day == 2
        assert result.proposed_days[2].day == 3
        assert result.reply == prose

    async def test_start_day_from_committed_empty(self):
        """Empty curriculum → start_day = 1."""
        planner = CurriculumPlanner(
            StubLLM(
                '```json\n{"days": [{"day": 0, "title": "D1", "focus": "F", "collocations": ["a"], "learning_objective": "O"}]}\n```'
            )
        )
        result = await planner.turn(
            curriculum=_empty_curriculum(),
            user_message="plan",
            batch_size=1,
            learner_snapshot="(none)",
            language=Language.slovene(),
        )
        assert result.proposed_days[0].day == 1

    async def test_start_day_from_committed_non_contiguous(self):
        """Days up to 5 → next batch starts at 6 (even if days are non-contiguous)."""
        curriculum = _empty_curriculum(
            days=[
                CurriculumDay(day=1, title="T1", focus="F1", collocations=["a"], learning_objective="O1"),
                CurriculumDay(day=5, title="T5", focus="F5", collocations=["b"], learning_objective="O5"),
            ]
        )
        planner = CurriculumPlanner(
            StubLLM(
                '```json\n{"days": [{"day": 99, "title": "D1", "focus": "F", "collocations": ["a"], "learning_objective": "O"}]}\n```'
            )
        )
        result = await planner.turn(
            curriculum=curriculum,
            user_message="plan",
            batch_size=1,
            learner_snapshot="(none)",
            language=Language.slovene(),
        )
        assert result.proposed_days[0].day == 6

    async def test_malformed_fenced_json(self):
        # Fence needs a newline after the language tag for the regex to match
        planner = CurriculumPlanner(StubLLM("Some text ```json\n{invalid\n```"))
        with pytest.raises(PlannerError):
            await planner.turn(
                curriculum=_empty_curriculum(),
                user_message="plan",
                batch_size=3,
                learner_snapshot="(none)",
                language=Language.slovene(),
            )

    async def test_wrong_day_count(self):
        json_block = (
            '{"days": [{"day": 1, "title": "D1", "focus": "F1", "collocations": ["a"], "learning_objective": "O1"}]}'
        )
        planner = CurriculumPlanner(StubLLM(f"```json\n{json_block}\n```"))
        with pytest.raises(PlannerError, match="Expected 3.*got 1"):
            await planner.turn(
                curriculum=_empty_curriculum(),
                user_message="plan",
                batch_size=3,
                learner_snapshot="(none)",
                language=Language.slovene(),
            )

    async def test_missing_days_key(self):
        json_block = '{"foo": "bar"}'
        planner = CurriculumPlanner(StubLLM(f"```json\n{json_block}\n```"))
        with pytest.raises(PlannerError, match="missing 'days'"):
            await planner.turn(
                curriculum=_empty_curriculum(),
                user_message="plan",
                batch_size=3,
                learner_snapshot="(none)",
                language=Language.slovene(),
            )

    async def test_invalid_day_fields(self):
        """Missing required field 'title' → PlannerError."""
        json_block = '{"days": [{"day": 1, "focus": "F", "collocations": ["a"], "learning_objective": "O"}]}'
        planner = CurriculumPlanner(StubLLM(f"```json\n{json_block}\n```"))
        with pytest.raises(PlannerError):
            await planner.turn(
                curriculum=_empty_curriculum(),
                user_message="plan",
                batch_size=1,
                learner_snapshot="(none)",
                language=Language.slovene(),
            )

    async def test_empty_collocations(self):
        """Empty collocations list → PlannerError via validate_plan_days."""
        json_block = (
            '{"days": [{"day": 1, "title": "D1", "focus": "F", "collocations": [], "learning_objective": "O"}]}'
        )
        planner = CurriculumPlanner(StubLLM(f"```json\n{json_block}\n```"))
        with pytest.raises(PlannerError):
            await planner.turn(
                curriculum=_empty_curriculum(),
                user_message="plan",
                batch_size=1,
                learner_snapshot="(none)",
                language=Language.slovene(),
            )

    async def test_non_dict_day_entries_raise_planner_error(self):
        """LLM emitting non-dict entries in 'days' must raise PlannerError, not TypeError.

        The server-side renumbering loop assigns d["day"] before
        validate_plan_days runs, so without a dict guard a list of strings
        escapes as a raw TypeError ('str' object does not support item
        assignment) — a 500 instead of the PlannerError→502 retry path.
        """
        json_block = '{"days": ["day one", "day two"]}'
        planner = CurriculumPlanner(StubLLM(f"```json\n{json_block}\n```"))
        with pytest.raises(PlannerError, match="days\\[0\\] must be an object"):
            await planner.turn(
                curriculum=_empty_curriculum(),
                user_message="plan",
                batch_size=2,
                learner_snapshot="(none)",
                language=Language.slovene(),
            )

    async def test_returns_planner_turn_type(self):
        planner = CurriculumPlanner(StubLLM("Just chatting."))
        result = await planner.turn(
            curriculum=_empty_curriculum(),
            user_message="hi",
            batch_size=3,
            learner_snapshot="(none)",
            language=Language.slovene(),
        )
        assert isinstance(result, PlannerTurn)

    async def test_prose_reply_without_json_preserved(self):
        """Prose-only reply with no JSON → proposed_days is None."""
        planner = CurriculumPlanner(StubLLM("I think we should focus on greetings first. What do you think?"))
        result = await planner.turn(
            curriculum=_empty_curriculum(),
            user_message="What should I learn first?",
            batch_size=3,
            learner_snapshot="(none)",
            language=Language.slovene(),
        )
        assert result.proposed_days is None
        assert "greetings" in result.reply

    async def test_prose_around_json_preserved(self):
        """Prose before and after the JSON block is preserved in reply."""
        prose_before = "Here is a proposal for the next few days:"
        prose_after = "Let me know if you'd like to adjust any of these."
        json_block = (
            '{"days": [{"day": 1, "title": "D1", "focus": "F", "collocations": ["a"], "learning_objective": "O"}]}'
        )
        full = f"{prose_before}\n\n```json\n{json_block}\n```\n\n{prose_after}"
        planner = CurriculumPlanner(StubLLM(full))
        result = await planner.turn(
            curriculum=_empty_curriculum(),
            user_message="plan",
            batch_size=1,
            learner_snapshot="(none)",
            language=Language.slovene(),
        )
        assert result.proposed_days is not None
        assert len(result.proposed_days) == 1
        assert prose_before in result.reply
        assert prose_after in result.reply
