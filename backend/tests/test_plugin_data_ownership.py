"""Guardrails: plugin-owned style notes and function-word config."""

from pathlib import Path

from app.languages import (
    get_function_words_path,
    get_language,
    get_numbers_path,
    get_spatial_path,
    get_style_notes,
    known_language_codes,
)


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


# ── Cardinal-number vocabulary ───────────────────────────────────────────


def test_every_language_with_function_words_also_ships_numbers() -> None:
    """The two files are a pair.

    A language that curates a closed-class policy has, by that act, put its
    numerals on the cloze route (numerals are DET/NUM in every deck that labels
    them). Shipping the closed-class file without the number file is therefore
    the exact configuration that produced tunatale-elrj, and it would be silent.
    """
    # Derived from the registry, not a literal list: tunatale-w4m7.2 found this
    # loop pinned to ("sl", "no") while Tagalog shipped both files unchecked.
    with_function_words = sorted(c for c in known_language_codes() if get_function_words_path(c) is not None)
    assert {"sl", "no", "tl"} <= set(with_function_words)
    for code in with_function_words:
        assert get_numbers_path(code) is not None, code


def test_number_vocabulary_lives_in_the_plugin_dir() -> None:
    with_numbers = sorted(c for c in known_language_codes() if get_numbers_path(c) is not None)
    assert {"sl", "no", "tl"} <= set(with_numbers)
    for code in with_numbers:
        path = get_numbers_path(code)
        assert path.exists(), code
        assert path.parent.name == "data"
        assert path.parent.parent.name == code


def test_en_numbers_path_none() -> None:
    assert get_numbers_path("en") is None


def test_unknown_numbers_path_none() -> None:
    assert get_numbers_path("xx") is None


# ── Spatial-word vocabulary ──────────────────────────────────────────────


def test_every_language_with_function_words_also_ships_spatial_words() -> None:
    """The same pairing argument as numbers: a closed-class policy puts every
    preposition on the cloze route, and the spatial file is what takes the
    picturable ones off it (tunatale-hvj0)."""
    for code in (c for c in known_language_codes() if get_function_words_path(c) is not None):
        assert get_spatial_path(code) is not None, code


def test_spatial_vocabulary_lives_in_the_plugin_dir() -> None:
    with_spatial = sorted(c for c in known_language_codes() if get_spatial_path(c) is not None)
    assert {"sl", "no", "tl", "ceb"} <= set(with_spatial)
    for code in with_spatial:
        path = get_spatial_path(code)
        assert path.exists(), code
        assert (path.parent.name, path.parent.parent.name) == ("data", code)


def test_en_and_unknown_spatial_paths_are_none() -> None:
    assert get_spatial_path("en") is None
    assert get_spatial_path("xx") is None
