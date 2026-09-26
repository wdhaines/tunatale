"""DEEPER is fed the real prior-day transcript (tunatale-g8mu).

Before this, every DEEPER prompt carried the literal "(not available)" inside a
fenced "SOURCE TRANSCRIPT TO ENHANCE" block, so the model was asked to enhance
nothing. The contract now:

* the source is the latest stored lesson of the PREVIOUS stored day, which is
  not ``day - 1``: days are stable keys with gaps (a deleted day 5 leaves 4 → 6);
* the transcript is that lesson's NATURAL_SPEED dialogue, scene labels included;
* with no source (the first day, or no stored lesson), the block is ABSENT, never
  a placeholder;
* WIDER and REVIEW prompts are byte-identical whether or not a store is passed.
"""

from __future__ import annotations

import logging

import pytest

from app.generation.section_builder import build_natural_speed_section, build_slow_speed_section
from app.generation.story import build_story_prompts, prior_day_transcript
from app.models.curriculum import CurriculumDay
from app.models.lesson import Lesson
from app.models.strategy import ContentStrategy
from app.storage.store import ContentStore

CID = "kaffe-kurs"
VOICES = {"female-1": "nb-NO-PernilleNeural", "male-1": "nb-NO-FinnNeural"}


def _lesson(*scenes: tuple[str, list[tuple[str, str]]]) -> Lesson:
    section = build_natural_speed_section(
        [{"label": label, "lines": [{"speaker": s, "text": t} for s, t in lines]} for label, lines in scenes],
        VOICES,
        "en-US-GuyNeural",
        "no",
    )
    return Lesson(title="Kaffe", language_code="no", sections=[section])


def _day(n: int) -> CurriculumDay:
    return CurriculumDay(
        day=n,
        title="Kaffe",
        focus="Kafé",
        collocations=["en kopp kaffe"],
        learning_objective="Bestille kaffe",
        story_guidance="På en kafé i Oslo",
    )


@pytest.fixture
def store() -> ContentStore:
    s = ContentStore(":memory:")
    s.save_lesson("l1", CID, 1, _lesson(("På kafeen", [("female-1", "Hei!"), ("male-1", "En kopp kaffe, takk.")])))
    s.save_lesson("l4", CID, 4, _lesson(("Ved disken", [("male-1", "Hva koster det?"), ("female-1", "Førti kroner.")])))
    return s


class TestPriorDayTranscript:
    def test_the_source_is_the_previous_STORED_day_not_day_minus_one(self, store):
        """Day 6 has no day 5 before it; the previous stored day is 4."""
        assert prior_day_transcript(store, CID, 6) == ("[Ved disken]\nmale-1: Hva koster det?\nfemale-1: Førti kroner.")

    def test_the_latest_regeneration_of_that_day_wins(self, store):
        store.save_lesson("l4b", CID, 4, _lesson(("Ny scene", [("male-1", "Takk skal du ha.")])))
        assert prior_day_transcript(store, CID, 6) == "[Ny scene]\nmale-1: Takk skal du ha."

    def test_the_first_day_has_no_source(self, store):
        assert prior_day_transcript(store, CID, 1) is None

    def test_another_curriculum_is_never_a_source(self, store):
        assert prior_day_transcript(store, "annen-kurs", 6) is None

    def test_only_the_natural_speed_section_is_the_source(self):
        """A real lesson also carries the slowed and translated replays of the same
        dialogue; feeding those in would repeat every line with "..." between words."""
        scenes = [{"label": "På kafeen", "lines": [{"speaker": "female-1", "text": "Hei!"}]}]
        slow = build_slow_speed_section(scenes, VOICES, "en-US-GuyNeural", "no")
        natural = build_natural_speed_section(scenes, VOICES, "en-US-GuyNeural", "no")
        s = ContentStore(":memory:")
        s.save_lesson("l1", CID, 1, Lesson(title="Kaffe", language_code="no", sections=[slow, natural]))
        assert prior_day_transcript(s, CID, 2) == "[På kafeen]\nfemale-1: Hei!"

    def test_a_lesson_without_dialogue_is_no_source(self):
        s = ContentStore(":memory:")
        s.save_lesson("l1", CID, 1, Lesson(title="Tom", language_code="no", sections=[]))
        assert prior_day_transcript(s, CID, 2) is None


class TestDeeperPrompt:
    def test_deeper_carries_the_prior_transcript(self, store, language_no):
        user = build_story_prompts(
            _day(6), language_no, ContentStrategy.DEEPER, "A2", content_store=store, curriculum_id=CID
        ).user_prompt
        assert "**SOURCE TRANSCRIPT TO ENHANCE:**\n```\n[Ved disken]\nmale-1: Hva koster det?" in user
        assert "(not available)" not in user

    def test_deeper_without_a_source_has_NO_block_not_a_placeholder(self, store, language_no):
        for kwargs in ({}, {"content_store": store, "curriculum_id": CID}):
            user = build_story_prompts(_day(1), language_no, ContentStrategy.DEEPER, "A2", **kwargs).user_prompt
            assert "SOURCE TRANSCRIPT" not in user
            assert "(not available)" not in user
            assert "```" not in user

    def test_the_rest_of_the_deeper_prompt_is_intact(self, store, language_no):
        user = build_story_prompts(_day(1), language_no, ContentStrategy.DEEPER, "A2").user_prompt
        assert "**Strategy:** DEEPER" in user
        assert "**New Collocations to Teach:**\n- en kopp kaffe" in user
        assert "**DEEPER STRATEGY RULES**" in user

    def test_an_oversized_transcript_is_trimmed_to_the_budget_and_says_so(self, language_no, caplog):
        from app.generation import story

        s = ContentStore(":memory:")
        long_lines = [("male-1", f"Dette er en lang setning nummer {i} om kaffe og kaker.") for i in range(600)]
        s.save_lesson("l1", CID, 1, _lesson(("Lang scene", long_lines)))
        with caplog.at_level(logging.WARNING, logger="app.generation.story"):
            prompts = build_story_prompts(
                _day(2), language_no, ContentStrategy.DEEPER, "A2", content_store=s, curriculum_id=CID
            )
        estimate = (len(prompts.system_prompt) + len(prompts.user_prompt)) // 4
        assert estimate <= story.PROMPT_TOKEN_BUDGET
        assert "nummer 0 " in prompts.user_prompt  # trimmed from the END
        assert "nummer 599 " not in prompts.user_prompt
        assert "[transcript trimmed to fit the request budget]" in prompts.user_prompt
        assert any("trimmed" in r.getMessage() for r in caplog.records)


class TestOtherStrategiesAreUntouched:
    def test_wider_is_byte_identical_with_or_without_a_store(self, store, language_no):
        without = build_story_prompts(_day(6), language_no, ContentStrategy.WIDER, "A2")
        with_store = build_story_prompts(
            _day(6), language_no, ContentStrategy.WIDER, "A2", content_store=store, curriculum_id=CID
        )
        assert with_store == without
