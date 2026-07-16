"""Multi-turn cassette-backed planner scenarios.

Turn 1 proposes 3 days, they are committed inline, feedback is added,
then turn 2 proposes the next batch starting at day 4.
Norwegian-context regression: 2 committed days in Norwegian + chat
history, then proposes 2 more days — JSON fields must stay English.

Record with:
    uv run pytest tests/test_planner_llm.py --llm-mode=record
"""

from app.generation.planner import CurriculumPlanner
from app.languages import get_language
from app.models.curriculum import Curriculum, CurriculumDay


def _empty_curriculum() -> Curriculum:
    return Curriculum(
        id="planner-llm-test",
        topic="Visiting Ljubljana",
        language_code="sl",
        cefr_level="A2",
        metadata={"planner": {"chat": [], "proposed": None, "feedback": []}},
    )


SNAPSHOT = "(no tracked vocabulary yet \u2014 assume a beginner at the stated CEFR level)"
LANGUAGE = get_language("sl")

_NORWEGIAN_CHARS = frozenset("æøåÆØÅ")


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

    async def test_norwegian_context_regression(self, cassette_llm):
        """2 committed Norwegian days + chat → propose 2 more; JSON fields must stay English."""
        planner = CurriculumPlanner(llm=cassette_llm)
        curriculum = Curriculum(
            id="planner-no-test",
            topic="En reise til Bergen",
            language_code="no",
            cefr_level="A2",
            metadata={
                "planner": {
                    "chat": [
                        {"role": "user", "content": "Jeg vil lære norsk for en tur til Bergen"},
                        {"role": "planner", "content": "Flott! La oss begynne med det grunnleggende."},
                        {"role": "event", "content": "Committed days 1-2."},
                    ],
                    "proposed": None,
                    "feedback": [],
                }
            },
            days=[
                CurriculumDay(
                    day=1,
                    title="Politiintervjuet – første vitne",
                    focus="Enkel vitneforklaring og høflighet",
                    collocations=["vitne", "forklare", "hendelsen", "bekreft"],
                    learning_objective="Kunne gjengi enkle fakta om en hendelse i høflig form.",
                    story_guidance="En politibetjent spør deg hva du så. Bruk enkle setninger for å forklare.",
                ),
                CurriculumDay(
                    day=2,
                    title="Politiintervjuet – andre vitne",
                    focus="Bekrefte informasjon og stille oppfølgingsspørsmål",
                    collocations=["bekrefte", "stemmer", "kan du gjenta", "usikker"],
                    learning_objective="Kunne bekrefte eller avkrefte påstander og be om gjentakelse.",
                    story_guidance="Du blir bedt om å bekrefte opplysninger. Spør om du er usikker.",
                ),
            ],
        )

        turn = await planner.turn(
            curriculum=curriculum,
            user_message="Legg til to dager med mer avansert ordforråd om restaurant og shopping",
            batch_size=2,
            learner_snapshot=SNAPSHOT,
            language=get_language("no"),
        )
        assert turn.proposed_days is not None, "Should propose days"
        assert len(turn.proposed_days) == 2
        assert turn.proposed_days[0].day == 3
        assert turn.proposed_days[1].day == 4
        for d in turn.proposed_days:
            for c in d.collocations:
                assert "(" not in c, f"Collocation must be bare target-language, got: {c!r}"
            # Mechanical proxy for "JSON fields in English": no Norwegian chars
            assert not _NORWEGIAN_CHARS & set(d.title), f"title should be English, got: {d.title!r}"
            assert not _NORWEGIAN_CHARS & set(d.focus), f"focus should be English, got: {d.focus!r}"
            assert not _NORWEGIAN_CHARS & set(d.learning_objective), (
                f"learning_objective should be English, got: {d.learning_objective!r}"
            )
            # story_guidance may legitimately quote target-language text — no assertion
