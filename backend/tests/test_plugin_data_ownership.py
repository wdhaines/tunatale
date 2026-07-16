"""Guardrails: plugin-owned style notes and function-word config."""

from pathlib import Path

from app.languages import get_function_words_path, get_language, get_style_notes


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

    prompt = build_story_system_prompt(get_language("sl"))
    assert "oprostite" in prompt.lower()


def test_sl_style_file_lives_in_plugin_dir() -> None:
    path = Path(__file__).resolve().parent.parent / "app" / "plugins" / "languages" / "sl" / "data" / "style.md"
    assert path.exists()
    assert "Slovene" in path.read_text(encoding="utf-8")


# ── Function-word config ─────────────────────────────────────────────────


def test_sl_function_words_path_exists() -> None:
    path = get_function_words_path("sl")
    assert path is not None
    assert path.exists()
    assert "plugins/languages/sl/" in str(path)


def test_en_function_words_path_none() -> None:
    assert get_function_words_path("en") is None


def test_unknown_function_words_path_none() -> None:
    assert get_function_words_path("xx") is None


def test_sl_function_word_detection_still_works() -> None:
    from app.srs.function_words import is_function_word

    assert is_function_word("je", "sl", upos=None) is True


def test_sl_function_words_file_lives_in_plugin_dir() -> None:
    path = get_function_words_path("sl")
    assert path is not None
    assert path.parent.name == "data"
    assert path.parent.parent.name == "sl"
