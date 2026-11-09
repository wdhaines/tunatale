"""Key-phrase build-up rules (tunatale-lat6, the owner 2026-11-09).

The build-up of a key phrase walks its words from the last: a word is either
BROKEN (syllable pieces or grouped rungs, then the word) or said WHOLE, and
after each word a backward rung joins it to the words already built
(baay -> ilang baay? -> niy ilang baay? -> ... -> the phrase). The owner tuned
which words break in the workbench artifact (its settings are the oracle
here):

- names and loanwords are never broken;
- a word is broken only from ``min_syllables`` (3 for a Wolff lesson, 2 for a
  generated one);
- a broken word of 3+ syllables is built root first when its prefix comes off
  cleanly (lisod, maglisod), else two syllables at a time from the end
  (ila, ilaila, ipailaila);
- a word is broken once per lesson;
- the backward build is never cut ("if a word doesn't get broken down, it still
  contributes to the backwards buildup of the full phrase"); an unbroken
  word's lone rung goes, except the last word's, where the build starts.

Every rung kept is one the full build already had, so a reshaped lesson asks
the TTS for nothing new.
"""

from __future__ import annotations

import pytest

from app.generation.buildup_rules import BuildupRules, capitalized_names, reshape_breakdown
from app.generation.section_builder import build_key_phrases_section, build_word_breakdown_spans
from app.generation.story import build_lesson_from_story
from app.languages import get_language, get_prefix_fn
from app.models.lesson import SectionType


def _ceb_prefix(word: str) -> str | None:
    return get_prefix_fn("ceb")(word)


def _texts(phrase: str, code: str, rules: BuildupRules, built: set[str] | None = None, prefix_fn=None) -> list[str]:
    chunks = build_word_breakdown_spans(phrase, code)
    out = reshape_breakdown(phrase, chunks, rules=rules, built=set() if built is None else built, prefix_fn=prefix_fn)
    return [c.text for c in out]


WOLFF = BuildupRules(min_syllables=3)


# --- the owner's examples, from the workbench ------------------------------------------


def test_a_phrase_with_no_word_long_enough_keeps_its_whole_backward_build():
    phrase = "Mao ba niy ilang baay?"
    assert _texts(phrase, "ceb", WOLFF) == [
        phrase,
        "baay",
        "ilang baay?",
        "niy ilang baay?",
        "ba niy ilang baay?",
        phrase,
    ]


def test_a_prefixed_verb_is_built_root_first_and_a_hyphen_is_a_syllable_edge():
    phrase = "Wa mo maglisod pag-anhi nganhi?"
    assert _texts(phrase, "ceb", WOLFF, prefix_fn=_ceb_prefix) == [
        phrase,
        "nganhi",
        "-anhi",
        "pag-anhi",
        "pag-anhi nganhi?",
        "lisod",
        "maglisod",
        "maglisod pag-anhi nganhi?",
        "mo maglisod pag-anhi nganhi?",
        phrase,
    ]


def test_a_long_word_with_no_clean_prefix_is_built_two_syllables_at_a_time():
    phrase = "Gusto nakong ipailaila nimo."
    assert _texts(phrase, "ceb", WOLFF, prefix_fn=_ceb_prefix) == [
        phrase,
        "nimo",
        "ila",
        "ilaila",
        "ipailaila",
        "ipailaila nimo.",
        "nakong ipailaila nimo.",
        phrase,
    ]


def test_pairs_are_used_when_the_language_has_no_prefix_function():
    phrase = "Wa mo maglisod."
    # mag·li·sod: the pair from the end is li·sod (here the same rung a prefix would give)
    assert _texts(phrase, "ceb", WOLFF, prefix_fn=None) == [phrase, "lisod", "maglisod", "mo maglisod.", phrase]


def test_a_two_syllable_word_breaks_into_single_syllables_when_the_limit_is_two():
    phrase = "Mao ba niy ilang baay?"
    texts = _texts(phrase, "ceb", BuildupRules(min_syllables=2))
    assert texts[:4] == [phrase, "ay", "ba", "baay"]
    assert "lang" in texts and "ilang" in texts


def test_names_are_never_broken():
    phrase = "Hain man si Doktor Fernandez?"
    texts = _texts(phrase, "ceb", BuildupRules(min_syllables=2, skip_words=capitalized_names(phrase)))
    assert texts == [
        phrase,
        "fernandez",
        "Doktor Fernandez?",
        "si Doktor Fernandez?",
        "man si Doktor Fernandez?",
        "in",
        "ha",
        "hain",
        phrase,
    ]


def test_capitalized_names_skip_the_sentence_opening_word():
    assert capitalized_names("Hain man si Doktor Fernandez?") == frozenset({"doktor", "fernandez"})
    assert capitalized_names("Jeg skal møte Kari. Hun kommer.") == frozenset({"kari"})
    assert capitalized_names("hello") == frozenset()


def test_a_skipped_word_is_said_whole_and_starts_the_build_when_it_is_last():
    phrase = "Pila bay pliti?"
    texts = _texts(phrase, "ceb", BuildupRules(min_syllables=2, skip_words=frozenset({"pliti"})))
    assert texts[:3] == [phrase, "pliti", "bay pliti?"]
    assert "ti" not in texts and "pli" not in texts


def test_a_word_is_broken_once_per_lesson():
    built: set[str] = set()
    first = _texts("Wa mo maglisod pag-anhi.", "ceb", WOLFF, built, _ceb_prefix)
    assert "lisod" in first
    second = _texts("Maglisod ka ba?", "ceb", WOLFF, built, _ceb_prefix)
    assert "lisod" not in second
    assert second == ["Maglisod ka ba?", "ba", "ka ba?", "Maglisod ka ba?"]


def test_an_unbroken_one_word_phrase_is_said_once():
    assert _texts("Diay!", "ceb", WOLFF) == ["Diay!"]


def test_norwegian_follows_the_same_rules():
    phrase = "Jeg skal ta bussen til sentrum i morgen."
    texts = _texts(phrase, "no", BuildupRules(min_syllables=2))
    assert texts == [
        phrase,
        "rgen",
        "mo",
        "morgen",
        "i morgen.",
        "trum",
        "sen",
        "sentrum",
        "sentrum i morgen.",
        "til sentrum i morgen.",
        "ssen",
        "bu",
        "bussen",
        "bussen til sentrum i morgen.",
        "ta bussen til sentrum i morgen.",
        "skal ta bussen til sentrum i morgen.",
        phrase,
    ]


def test_every_kept_rung_is_one_the_full_build_already_had():
    for phrase, code in [
        ("Wa mo maglisod pag-anhi nganhi?", "ceb"),
        ("Gusto nakong ipailaila nimo.", "ceb"),
        ("Jeg skal ta bussen til sentrum i morgen.", "no"),
    ]:
        full = build_word_breakdown_spans(phrase, code)
        kept = reshape_breakdown(phrase, full, rules=WOLFF, built=set(), prefix_fn=_ceb_prefix)
        assert set(kept) <= set(full)


def test_chunks_it_cannot_read_are_returned_unchanged():
    phrase = "Mao ba niy ilang baay?"
    chunks = build_word_breakdown_spans(phrase, "ceb")
    odd = [c for c in chunks if c.text != "ilang baay?"]  # a backward rung missing
    assert reshape_breakdown(phrase, odd, rules=WOLFF, built=set(), prefix_fn=None) == odd


# --- the Cebuano prefix function -------------------------------------------------------


@pytest.mark.parametrize(
    ("word", "prefix"),
    [("maglisod", "mag"), ("moabot", "mo"), ("nilakaw", "ni"), ("pagdala", "pag"), ("pag-anhi", "pag")],
)
def test_the_cebuano_prefix_function_finds_a_prefix_whose_root_is_the_rest(word, prefix):
    assert _ceb_prefix(word) == prefix


@pytest.mark.parametrize("word", ["manakay", "ipailaila", "pangita", "dili"])
def test_the_cebuano_prefix_function_declines_what_does_not_come_off_cleanly(word):
    # manakay is maN- + sakay: the prefix changed the root's first sound
    assert _ceb_prefix(word) is None


def test_a_language_without_a_prefix_function_has_none():
    assert get_prefix_fn("no") is None


# --- where the rules apply ------------------------------------------------------------


def test_the_section_builder_without_rules_keeps_the_full_build():
    kps = [{"phrase": "Mao ba niy ilang baay?", "translation": "Is this their house?"}]
    section = build_key_phrases_section(kps, {"female-1": "v"}, "n", "ceb")
    assert [p.text for p in section.phrases][3:] == [
        c.text for c in build_word_breakdown_spans("Mao ba niy ilang baay?", "ceb")
    ]


def _story(**extra) -> dict:
    return {
        "title": "Test",
        "key_phrases": [
            {"phrase": "Hain man si Doktor Fernandez?", "translation": "Where is Dr. Fernandez?"},
            {"phrase": "Pila bay pliti?", "translation": "How much is the fare?"},
        ],
        "scenes": [
            {
                "label": "s",
                "lines": [
                    {"speaker": "male-1", "text": "Hain man si Doktor Fernandez?", "translation": "x"},
                    {"speaker": "male-1", "text": "Pila bay pliti?", "translation": "y"},
                ],
            }
        ],
        "dialogue_glosses": [
            {"word": "pliti", "translation": "fare", "kind": "loan"},
            {"word": "hain", "translation": "where"},
        ],
        **extra,
    }


def _kp_texts(lesson) -> list[str]:
    section = next(s for s in lesson.sections if s.section_type == SectionType.KEY_PHRASES)
    return [p.text for p in section.phrases]


def test_a_story_breaks_two_syllable_words_by_default_but_never_names_or_loans():
    texts = _kp_texts(build_lesson_from_story(_story(), language=get_language("ceb")))
    assert "hain" in texts and "ha" in texts  # hai·n: 2 syllables, new
    assert "pli" not in texts and "ti" not in texts  # pliti: a loan
    assert "fer" not in texts and "dok" not in texts  # names (capitalized mid-phrase)
    assert "pila" in texts and "la" in texts


def test_a_story_can_raise_the_shortest_word_broken():
    texts = _kp_texts(build_lesson_from_story(_story(buildup={"min_syllables": 3}), language=get_language("ceb")))
    assert "ha" not in texts and "la" not in texts
    assert texts.count("Pila bay pliti?") == 3  # head, the build's first rung, its last


def test_a_gloss_marked_as_a_name_is_never_broken():
    story = _story()
    story["dialogue_glosses"].append({"word": "hain", "translation": "where", "kind": "name"})
    texts = _kp_texts(build_lesson_from_story(story, language=get_language("ceb")))
    assert "ha" not in texts


# --- what it refuses, and what it falls back on ------------------------------------------


def _drop(chunks, *texts):
    return [c for c in chunks if c.text not in texts]


@pytest.mark.parametrize(
    ("phrase", "edit"),
    [
        ("Mao ba niy ilang baay?", lambda cs: [cs[1], *cs[1:]]),  # does not open on the phrase
        ("Mao ba niy ilang baay?", lambda cs: _drop(cs, "ay", "ba", "baay")),  # a rung before its word
        ("Mao ba niy ilang baay?", lambda cs: cs[:3]),  # the build stops before a word is said
        ("Pangita lang.", lambda cs: cs),  # a control: this one reads
    ],
)
def test_a_build_it_cannot_read_is_kept_as_it_was(phrase, edit):
    chunks = edit(build_word_breakdown_spans(phrase, "ceb"))
    out = reshape_breakdown(phrase, chunks, rules=WOLFF, built=set(), prefix_fn=None)
    if phrase == "Pangita lang.":
        assert out != chunks
    else:
        assert out == chunks


def test_a_one_word_build_that_does_not_close_on_the_phrase_is_kept_as_it_was():
    chunks = build_word_breakdown_spans("Pangitaa!", "ceb")[:-1]
    assert reshape_breakdown("Pangitaa!", chunks, rules=WOLFF, built=set(), prefix_fn=None) == chunks


def test_a_prefix_that_ends_inside_a_syllable_falls_back_to_pairs():
    phrase = "Wa mo maglisod."
    texts = _texts(phrase, "ceb", WOLFF, prefix_fn=lambda w: "ma")  # mag·li·sod: no edge after "ma"
    assert texts == [phrase, "lisod", "maglisod", "mo maglisod.", phrase]


def test_a_long_word_missing_the_rung_to_group_by_keeps_its_whole_build():
    phrase = "Wa mo maglisod."
    chunks = _drop(build_word_breakdown_spans(phrase, "ceb"), "lisod")
    texts = [c.text for c in reshape_breakdown(phrase, chunks, rules=WOLFF, built=set(), prefix_fn=None)]
    assert texts == [phrase, "sod", "li", "mag", "maglisod", "mo maglisod.", phrase]


def test_a_dash_between_words_does_not_end_the_sentence_for_capitals():
    assert capitalized_names("Ayaw — Inday.") == frozenset({"inday"})


def test_a_suffixed_word_has_no_clean_prefix():
    assert _ceb_prefix("sakyan") is None  # sakay + -an
