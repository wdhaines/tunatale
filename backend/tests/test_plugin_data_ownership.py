"""Guardrails: plugin-owned style notes and function-word config."""

from pathlib import Path

from app.languages import get_style_notes
from app.models.language import Language


def test_sl_style_notes_non_empty() -> None:
    notes = get_style_notes("sl")
    assert notes
    assert "Slovene" in notes


def test_en_style_notes_empty() -> None:
    assert get_style_notes("en") == ""


def test_unknown_style_notes_empty() -> None:
    assert get_style_notes("xx") == ""


def test_build_story_prompt_wires_sl_style() -> None:
    from app.generation.prompts import build_story_system_prompt

    prompt = build_story_system_prompt(Language.slovene())
    assert "oprostite" in prompt.lower()


def test_sl_style_file_lives_in_plugin_dir() -> None:
    path = Path(__file__).resolve().parent.parent / "app" / "plugins" / "languages" / "sl" / "data" / "style.md"
    assert path.exists()
    assert "Slovene" in path.read_text(encoding="utf-8")
