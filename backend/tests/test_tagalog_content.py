"""Tagalog content files: style guide, function words, numbers (tunatale-w4m7.4).

User decisions, 2026-09-23: no Taglish code-switching but everyday loans stay;
"po" the way a courteous visitor uses it, not a hospitality worker; numbers the
way Filipinos use them, both systems. The style guide's effect was measured by a
blind A/B on the production story path (see the bead); these tests pin that the
files are WIRED and that the function-word and number policies hold.
"""

from __future__ import annotations

import pytest

from app.cards.number_image import number_value
from app.generation.prompts import build_story_system_prompt
from app.languages import get_language, get_lemma_table_path, get_style_notes
from app.srs.function_words import is_function_word
from app.srs.lemma_table import TableLemmatizer


def test_the_style_guide_reaches_the_story_system_prompt():
    notes = get_style_notes("tl")
    assert "Taglish" in notes and '"po"' in notes
    prompt = build_story_system_prompt(get_language("tl"))
    assert notes[:80] in prompt
    # Not the generic fallback a language without a guide gets.
    assert "as a native speaker would write and speak" not in prompt


@pytest.mark.parametrize(
    ("word", "upos"),
    [("ang", None), ("ng", None), ("sa", "ADP"), ("si", "NOUN"), ("mga", None), ("nang", None), ("ba", "PART")],
)
def test_markers_and_particles_are_function_words(word, upos):
    # `si` is listed by Wiktionary only as a NOUN; `include` must win regardless.
    assert is_function_word(word, "tl", upos=upos)


@pytest.mark.parametrize(("word", "upos"), [("po", "PART"), ("opo", None), ("ako", "PRON"), ("bahay", "NOUN")])
def test_register_particles_pronouns_and_content_words_are_not(word, upos):
    # po/opo are a register choice the style guide governs; a blank that "po"
    # answers teaches nothing. PRON is kept out of `pos` so every "ako" is not a cloze.
    assert not is_function_word(word, "tl", upos=upos)


@pytest.mark.parametrize(
    ("word", "value"),
    [("dalawa", 2), ("tatlo", 3), ("sampu", 10), ("labing-isa", 11), ("dalawampu", 20), ("isandaan", 100)],
)
def test_native_numbers_get_the_counting_picture(word, value):
    assert number_value(word, "tl") == value


@pytest.mark.parametrize("word", ["tres", "otso", "bente", "singkuwenta", "isa", "sandaan", "isanlibo"])
def test_spanish_numbers_one_and_doublets_do_not(word):
    # Spanish-derived numbers belong to clock time and prices (tunatale-w4m7.10);
    # `isa` is also "a/an"; `sandaan` is isandaan's doublet; 1000 is out of range.
    assert number_value(word, "tl") is None


@pytest.fixture(scope="module")
def table():
    lemmatizer = TableLemmatizer("tl", get_lemma_table_path("tl"))
    yield lemmatizer
    lemmatizer.close()


def test_the_committed_table_tags_function_words_as_closed_class(table):
    got = [(a.surface, a.upos) for a in table.analyze_sentence("Kumain ako sa bahay ng kaibigan ko.", "tl")]
    assert ("ako", "PRON") in got and ("sa", "ADP") in got and ("ko", "PRON") in got
