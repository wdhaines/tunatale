"""Tests for Tagalog affix-hyphen normalization (tunatale-w4m7.11).

The LLM writes ``mag‑kape`` (often with U+2011 NON-BREAKING HYPHEN) where
standard Tagalog spelling is ``magkape``. A hyphen after a prefix is correct
only before a vowel (``mag-aral``) or a capital letter (a loan proper noun:
``nag-Facebook``).
"""

from __future__ import annotations

import copy

import pytest

from app.languages import get_language, get_story_text_normalizer
from app.models.lesson import Lesson, SectionType
from app.plugins.languages.tl.hyphenation import normalize_affix_hyphens

NBH = "\u2011"  # NON-BREAKING HYPHEN
SHY = "\u2010"  # HYPHEN


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"mag{NBH}kape", "magkape"),
        ("mag-lakad", "maglakad"),
        (f"nag{NBH}shopping", "nagshopping"),
        (f"pag{SHY}luto", "pagluto"),
        ("mang-isda", "mang-isda"),
        (f"Mag{NBH}kape tayo.", "Magkape tayo."),
        ("mag-aral", "mag-aral"),
        (f"mag{NBH}aral", "mag-aral"),
        ("pag-ibig", "pag-ibig"),
        ("mag-asawa", "mag-asawa"),
        ("nag-Grab", "nag-Grab"),
        (f"nag{NBH}Facebook", "nag-Facebook"),
        ("mag-Ingles", "mag-Ingles"),
        ("magkano", "magkano"),
        ("araw-araw", "araw-araw"),
        ("damag-kape", "damag-kape"),  # prefix not at word start
        ("mag-3", "mag-3"),
        ("mag-", "mag-"),
        # A bare prefix-shaped WORD at end of text (nang/mang/nag are common
        # standalone words) once raised IndexError reading the char after it.
        ("mag", "mag"),
        ("Kumain ka nang", "Kumain ka nang"),
        ("Tara, mang", "Tara, mang"),
        ("MAG-kape", "MAGkape"),
        ("(mag-kape)", "(magkape)"),
        (f"Kumain ka, tapos mag{NBH}lakad at nag-aral.", "Kumain ka, tapos maglakad at nag-aral."),
    ],
)
def test_normalize_affix_hyphens(text: str, expected: str) -> None:
    assert normalize_affix_hyphens(text) == expected


def test_get_story_text_normalizer_is_tl_only() -> None:
    assert get_story_text_normalizer("tl") is normalize_affix_hyphens
    assert get_story_text_normalizer("no") is None
    assert get_story_text_normalizer("sl") is None


def _minimal_tl_story() -> dict:
    return {
        "title": "Sa kapehan",
        "key_phrases": [{"phrase": f"mag{NBH}lakad", "translation": "to walk"}],
        "scenes": [
            {
                "label": "Sa kapehan",
                "lines": [
                    {"speaker": "female-1", "text": f"Mag{NBH}kape tayo.", "translation": "Let's have coffee."},
                ],
            }
        ],
        "dialogue_glosses": [{"word": "kape", "lemma": "kape", "translation": "coffee"}],
        "morphology_focus": [],
    }


def _section_phrases(lesson: Lesson, section_type: SectionType) -> list[str]:
    return [p.text for s in lesson.sections if s.section_type is section_type for p in s.phrases]


def test_build_lesson_from_story_normalizes_tl_fields() -> None:
    from app.generation.story import build_lesson_from_story

    story = _minimal_tl_story()
    story_snapshot = copy.deepcopy(story)
    lesson = build_lesson_from_story(story, language=get_language("tl"))

    assert "maglakad" in _section_phrases(lesson, SectionType.KEY_PHRASES)
    assert "Magkape tayo." in _section_phrases(lesson, SectionType.NATURAL_SPEED)

    # No U+2011 anywhere in the built lesson, including the stashed story source.
    all_text = " ".join(p.text for s in lesson.sections for p in s.phrases)
    assert NBH not in all_text
    assert NBH not in str(lesson.generation_metadata["story"])
    assert lesson.generation_metadata["story"]["key_phrases"][0]["phrase"] == "maglakad"
    assert lesson.generation_metadata["story"]["scenes"][0]["lines"][0]["text"] == "Magkape tayo."

    # The caller's dict is untouched.
    assert story == story_snapshot


def test_norwegian_story_passes_through_hyphen_unchanged() -> None:
    from app.generation.story import build_lesson_from_story

    story = {
        "title": "En kopp kaffe",
        "key_phrases": [],
        "scenes": [
            {
                "label": "På kaféen",
                "lines": [
                    {"speaker": "female-1", "text": "mag-kape", "translation": "Let's have coffee."},
                ],
            }
        ],
        "dialogue_glosses": [],
        "morphology_focus": [],
    }
    lesson = build_lesson_from_story(story, language=get_language("no"))
    assert "mag-kape" in _section_phrases(lesson, SectionType.NATURAL_SPEED)


def test_build_lesson_from_story_tolerates_partial_tl_entries() -> None:
    """Entries the builder already tolerates (a non-dict key phrase, a line with
    no text, a gloss with only one of word/lemma) survive the normalizer too."""
    from app.generation.story import build_lesson_from_story

    story = _minimal_tl_story()
    story["key_phrases"].insert(0, "not-a-dict")
    story["key_phrases"].append({"translation": "no phrase"})
    story["scenes"][0]["lines"].append({"speaker": "male-1", "translation": "(silence)"})
    story["dialogue_glosses"] = [{"word": f"mag{NBH}kape", "translation": "have coffee"}]

    lesson = build_lesson_from_story(story, language=get_language("tl"))

    assert "maglakad" in _section_phrases(lesson, SectionType.KEY_PHRASES)
    assert lesson.generation_metadata["token_glosses"]["magkape"] == "have coffee"
