"""Tests for story prompt templates."""

from app.generation.prompts import (
    STORY_PROMPT_DEEPER_TEMPLATE,
    STORY_PROMPT_TEMPLATE,
    STORY_PROMPT_WIDER_TEMPLATE,
    SYSTEM_PROMPT,
    get_strategy_prompt,
)
from app.models.strategy import ContentStrategy


def test_system_prompt_contains_voice_protocol():
    assert "female-1" in SYSTEM_PROMPT
    assert "male-1" in SYSTEM_PROMPT


def test_system_prompt_contains_json_schema():
    assert "key_phrases" in SYSTEM_PROMPT
    assert "scenes" in SYSTEM_PROMPT


def test_story_prompt_has_template_variables():
    assert "{learning_objective}" in STORY_PROMPT_TEMPLATE
    assert "{focus}" in STORY_PROMPT_TEMPLATE


def test_wider_prompt_mentions_scenario_expansion():
    assert "scenario" in STORY_PROMPT_WIDER_TEMPLATE.lower() or "wider" in STORY_PROMPT_WIDER_TEMPLATE.lower()


def test_deeper_prompt_mentions_source_transcript():
    assert "transcript" in STORY_PROMPT_DEEPER_TEMPLATE.lower() or "source" in STORY_PROMPT_DEEPER_TEMPLATE.lower()


def test_get_strategy_prompt_returns_correct_template():
    assert get_strategy_prompt(ContentStrategy.WIDER) is STORY_PROMPT_WIDER_TEMPLATE
    assert get_strategy_prompt(ContentStrategy.DEEPER) is STORY_PROMPT_DEEPER_TEMPLATE
