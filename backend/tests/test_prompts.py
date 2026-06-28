"""Tests for story prompt templates."""

from app.generation.prompts import (
    STORY_PROMPT_DEEPER_TEMPLATE,
    STORY_PROMPT_WIDER_TEMPLATE,
    SYSTEM_PROMPT,
    _build_cefr_block,
    _load_style_notes,
    build_story_system_prompt,
    get_strategy_prompt,
)
from app.models.language import Language
from app.models.strategy import ContentStrategy

# ── Existing system prompt invariants ────────────────────────────────────


def test_system_prompt_contains_voice_protocol():
    assert "female-1" in SYSTEM_PROMPT
    assert "male-1" in SYSTEM_PROMPT


def test_system_prompt_contains_json_schema():
    assert "key_phrases" in SYSTEM_PROMPT
    assert "scenes" in SYSTEM_PROMPT


def test_system_prompt_contains_dialogue_density_floor():
    # Each scene must have 5-12 lines, not 2-3 stub exchanges
    assert "5-12" in SYSTEM_PROMPT or "5–12" in SYSTEM_PROMPT


# ── CEFR block ────────────────────────────────────────────────────────────


def test_cefr_block_contains_level():
    block = _build_cefr_block("B1")
    assert "B1" in block


def test_cefr_block_contains_calibration_table():
    block = _build_cefr_block("A2")
    assert "A1" in block
    assert "A2" in block
    assert "B1" in block
    assert "B2" in block


# ── User prompt templates ─────────────────────────────────────────────────


def test_story_prompt_has_cefr_block_placeholder():
    assert "{cefr_block}" in STORY_PROMPT_WIDER_TEMPLATE
    assert "{cefr_block}" in STORY_PROMPT_DEEPER_TEMPLATE


def test_wider_prompt_mentions_scenario_expansion():
    assert "scenario" in STORY_PROMPT_WIDER_TEMPLATE.lower() or "wider" in STORY_PROMPT_WIDER_TEMPLATE.lower()


def test_wider_prompt_has_dialogue_density_floor():
    assert "5-12" in STORY_PROMPT_WIDER_TEMPLATE


def test_deeper_prompt_mentions_source_transcript():
    assert "transcript" in STORY_PROMPT_DEEPER_TEMPLATE.lower() or "source" in STORY_PROMPT_DEEPER_TEMPLATE.lower()


def test_deeper_prompt_has_dialogue_density_floor():
    assert "5-12" in STORY_PROMPT_DEEPER_TEMPLATE


def test_get_strategy_prompt_returns_correct_template():
    assert get_strategy_prompt(ContentStrategy.WIDER) is STORY_PROMPT_WIDER_TEMPLATE
    assert get_strategy_prompt(ContentStrategy.DEEPER) is STORY_PROMPT_DEEPER_TEMPLATE


def test_get_strategy_prompt_raises_for_unknown_strategy():
    import pytest

    with pytest.raises(ValueError, match="Unknown strategy"):
        get_strategy_prompt(object())  # type: ignore[arg-type]


# ── Per-language style notes ──────────────────────────────────────────────


def test_load_style_notes_returns_string_for_slovene():
    notes = _load_style_notes("sl")
    assert isinstance(notes, str)
    assert len(notes) > 0


def test_load_style_notes_returns_empty_for_unknown_language():
    notes = _load_style_notes("xx")
    assert notes == ""


def test_build_story_system_prompt_slovene_contains_oprostite():
    """Regression: Slovene prompt must guard against izvinite (Serbian/Croatian contamination)."""
    prompt = build_story_system_prompt(Language.slovene())
    assert "oprostite" in prompt.lower()


def test_build_story_system_prompt_slovene_warns_against_izvinite():
    """The guardrail must explicitly name the word to avoid, so the LLM sees the contrast."""
    prompt = build_story_system_prompt(Language.slovene())
    assert "izvinite" in prompt.lower()


def test_build_story_system_prompt_slovene_warns_against_croatian_chars():
    prompt = build_story_system_prompt(Language.slovene())
    # Must warn about ć and đ which don't exist in Slovene
    assert "ć" in prompt or "Croatian" in prompt


def test_build_story_system_prompt_tagalog_contains_puwede():
    """Tagalog prompt must enforce 2007 standardized spelling."""
    tl = Language(
        code="tl",
        name="Tagalog",
        native_name="Tagalog",
        script="latin",
        tts_voice_map={},
    )
    prompt = build_story_system_prompt(tl)
    assert "puwede" in prompt.lower()


def test_build_story_system_prompt_fallback_for_language_without_style_file():
    """Languages without a style file should not raise; fallback text is used."""
    lang = Language.english()
    prompt = build_story_system_prompt(lang)
    assert "English" in prompt
    # Generic fallback phrase
    assert "native speaker" in prompt.lower() or "authentic" in prompt.lower()


def test_build_story_system_prompt_contains_language_name():
    prompt = build_story_system_prompt(Language.slovene())
    assert "Slovene" in prompt


def test_build_story_system_prompt_contains_density_floor():
    prompt = build_story_system_prompt(Language.slovene())
    assert "5-12" in prompt or "5–12" in prompt


# ── Conditional morphology block (Slavic case/dual only for Slovene) ──────


def test_slovene_prompt_contains_morphology_block():
    """Slovene keeps the full morphology-tagging block (cases + dual)."""
    prompt = build_story_system_prompt(Language.slovene())
    assert "morphology_focus" in prompt
    assert "Allowed cases for A1" in prompt
    assert "dual" in prompt.lower()


def test_norwegian_prompt_omits_slavic_morphology_block():
    """Norwegian has neither grammatical case nor dual number — the Slavic
    morphology-tagging instructions must not leak into its prompt."""
    prompt = build_story_system_prompt(Language.norwegian())
    assert "morphology_focus" not in prompt
    assert "Allowed cases for A1" not in prompt
    assert "noun:acc" not in prompt


def test_norwegian_prompt_contains_bokmal_style_notes():
    """The Norwegian style file (Bokmål rules) must be injected."""
    prompt = build_story_system_prompt(Language.norwegian())
    assert "Bokmål" in prompt
    # No T–V distinction is the headline Norwegian rule.
    assert "du" in prompt


def test_norwegian_prompt_keeps_shared_sections():
    """Dropping the morphology block must not drop the shared instructions."""
    prompt = build_story_system_prompt(Language.norwegian())
    assert "dialogue_glosses" in prompt
    assert "SCENE HEADER FORMAT" in prompt
    assert "5-12" in prompt or "5–12" in prompt
