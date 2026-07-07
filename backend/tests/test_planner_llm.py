"""Multi-turn cassette-backed planner scenario.

Turn 1 proposes 3 days, they are committed inline, feedback is added,
then turn 2 proposes the next batch starting at day 4.

Record with:
    uv run pytest tests/test_planner_llm.py --llm-mode=record
"""

from app.generation.planner import CurriculumPlanner
from app.models.curriculum import Curriculum
from app.models.language import Language


def _empty_curriculum() -> Curriculum:
    return Curriculum(
        id="planner-llm-test",
        topic="Visiting Ljubljana",
        language_code="sl",
        cefr_level="A2",
        metadata={"planner": {"chat": [], "proposed": None, "feedback": []}},
    )


SNAPSHOT = "(no tracked vocabulary yet \u2014 assume a beginner at the stated CEFR level)"
LANGUAGE = Language.slovene()


class TestPlannerLLM:
    async def test_two_turn_scenario(self, cassette_llm):
        planner = CurriculumPlanner(llm=cassette_llm)
        curriculum = _empty_curriculum()

        # ── Turn 1: propose 3 days ──────────────────────────────────
        turn1 = await planner.turn(
            curriculum=curriculum,
            user_message="Plan 3 days about visiting Ljubljana, starting with arrival and basic greetings",
            batch_size=3,
            learner_snapshot=SNAPSHOT,
            language=LANGUAGE,
        )
        assert turn1.proposed_days is not None, "Turn 1 should propose days"
        assert len(turn1.proposed_days) == 3
        assert turn1.proposed_days[0].day == 1
        assert turn1.proposed_days[1].day == 2
        assert turn1.proposed_days[2].day == 3
        for d in turn1.proposed_days:
            for c in d.collocations:
                assert "(" not in c, f"Collocation must be bare target-language, got: {c!r}"

        # Commit inline
        curriculum.days.extend(turn1.proposed_days)
        curriculum.metadata["planner"]["chat"].extend(
            [
                {
                    "role": "user",
                    "content": "Plan 3 days about visiting Ljubljana, starting with arrival and basic greetings",
                },
                {"role": "planner", "content": turn1.reply},
                {"role": "event", "content": f"Committed days 1-{len(turn1.proposed_days)}."},
            ]
        )

        # ── Add feedback ────────────────────────────────────────────
        curriculum.metadata["planner"]["feedback"].append(
            {"day": 1, "note": "Great first day, maybe add more food vocabulary"}
        )

        # ── Turn 2: propose next batch (should start at day 4) ─────────
        turn2 = await planner.turn(
            curriculum=curriculum,
            user_message="Plan 3 more days with more advanced vocabulary, including restaurant and shopping",
            batch_size=3,
            learner_snapshot=SNAPSHOT,
            language=LANGUAGE,
        )
        assert turn2.proposed_days is not None, "Turn 2 should propose days"
        assert len(turn2.proposed_days) == 3
        assert turn2.proposed_days[0].day == 4
        assert turn2.proposed_days[1].day == 5
        assert turn2.proposed_days[2].day == 6
        for d in turn2.proposed_days:
            for c in d.collocations:
                assert "(" not in c, f"Collocation must be bare target-language, got: {c!r}"
