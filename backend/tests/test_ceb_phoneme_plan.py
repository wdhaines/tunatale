"""The Cebuano chunk planner: breakdown chunk → IPA, read off the spelling.

Cebuano has no pronunciation lexicon, so the planner in
``app.plugins.languages.ceb.phoneme_plan`` is a thin language-specific
configuration of the shared spelling logic in ``app.audio.spelled_ipa``. The
tables are the only thing Cebuano brings to it, and the two entries that are not
plain Latin letters are the ones worth reading twice:

* ``-`` → ``ʔ``. In Cebuano the hyphen is not punctuation on a word but a
  letter: it marks the glottal stop of a final vowel (kanus-a, pag-anhi,
  mag-ampo). Tagalog's planner strips ``\\W+`` off both ends of a word and would
  reduce ``-a`` to ``a``; this one strips only the sentence-punctuation set, and
  the two tables differ precisely so the hyphen survives.
* ``'`` → silent. A contraction mark, not a sound: napulo'g is three syllables
  and the apostrophe contributes nothing between them.

The syllabifier is the shared one (``syllabify_word`` falls back to the core
default for a language with no ``syllabifier_fn`` of its own) because that is
the function the breakdown's spans come from — see the last test, which pins
that rather than trusting it.
"""

from __future__ import annotations

import pytest

from app.audio.spelled_ipa import SpelledPhonemePlanner
from app.languages import get_phoneme_planner
from app.plugins.languages.ceb.phoneme_plan import create_phoneme_planner


@pytest.fixture
def planner() -> SpelledPhonemePlanner:
    return create_phoneme_planner()


@pytest.mark.parametrize(
    ("word", "span", "expected"),
    [
        ("inyong", (0, 2), "ʔinjoŋ"),
        ("inyong", (0, 1), "ˈʔin"),
        ("inyong", (1, 2), "ˈjoŋ"),
        ("akong", (0, 1), "ˈʔa"),
        ("akong", (1, 2), "ˈkoŋ"),
        ("pamilya", (0, 3), "pamilja"),
        ("pamilya", (1, 2), "ˈmil"),
        ("pamilya", (1, 3), "milja"),
        ("pahasubo", (2, 4), "subo"),
        ("sa", (0, 1), "ˈsa"),
        # The hyphen rows are what the Cebuano ``"-"`` entry buys: without it
        # ``kanus-a`` would come out ``kanusa``, and the final syllable would
        # open on a glottal stop the spelling never mentions.
        ("kanus-a", (0, 3), "kanusʔa"),
        ("kanus-a", (2, 3), "ˈʔa"),
        # The source word carries the question mark the lesson wrote; the
        # stripping takes it off, and the pieces come back the same as for the
        # bare word.
        ("lubong?", (1, 2), "ˈboŋ"),
    ],
)
def test_a_fragment_is_read_off_the_spelling(planner, word, span, expected):
    assert planner.plan_chunk(word, span) == expected


@pytest.mark.parametrize("span", [(0, 3), (1, 3), (0, 0), (2, 1)])
def test_a_span_that_does_not_fit_the_word_gets_no_ipa(planner, span):
    assert planner.plan_chunk("inyong", span) is None


def test_a_caption_naming_other_letters_gets_no_ipa(planner):
    """A lesson stored before the boundaries moved: the span says "in", the
    caption says "yong", and the audio would play a syllable it does not name."""
    assert planner.plan_chunk("inyong", (0, 1), chunk_text="yong") is None
    assert planner.plan_chunk("inyong", (1, 2), chunk_text="inyong") is None


def test_a_caption_naming_the_right_letters_is_accepted(planner):
    assert planner.plan_chunk("inyong", (0, 1), chunk_text="in") == "ˈʔin"
    assert planner.plan_chunk("inyong", (0, 2), chunk_text="inyong") == "ʔinjoŋ"
    assert planner.plan_chunk("inyong", (1, 2), chunk_text="Yong") == "ˈjoŋ"


def test_the_apostrophe_is_a_silent_contraction_mark(planner):
    # napulo'g: three syllables, and the mark is not a sound between them.
    assert planner.plan_chunk("napulo'g", (0, 3)) == "napuloɡ"


def test_a_punctuation_marked_source_word_still_splits_the_same_way(planner):
    assert planner.plan_chunk('"inyong,"', (1, 2)) == "ˈjoŋ"


def test_the_registry_serves_a_cebuano_planner():
    planner = get_phoneme_planner("ceb")
    assert isinstance(planner, SpelledPhonemePlanner)
    assert planner.plan_chunk("inyong", (1, 2)) == "ˈjoŋ"


def test_the_planner_cuts_words_the_way_the_breakdown_does(planner):
    """The spans are only meaningful if the syllabifier is the breakdown's.

    A planner splitting a word differently from ``build_word_breakdown_spans``
    would point every span at the wrong letters, and every row above would still
    pass — so this is the test that makes the others mean anything.
    """
    from app.generation.section_builder import build_word_breakdown_spans
    from app.generation.syllabify import syllabify_word

    for word in ("inyong", "pamilya", "kanus-a", "pahasubo"):
        assert syllabify_word(word, "ceb") == planner._syllabify(word), word
        chunks = [c for c in build_word_breakdown_spans(word, "ceb") if c.span is not None]
        assert chunks, f"{word} carries no provenance at all — the fixture stopped testing the planner"
        for chunk in chunks:
            assert planner.plan_chunk(word, chunk.span, chunk_text=chunk.text) is not None, (word, chunk)


def test_mga_is_said_manga_not_as_spelled(planner):
    """The plural marker is spelled "mga" and said "manga": the spelling does
    not write the vowel or the velar nasal, so no letter rule can produce it.
    Found auditing the planner on 2026-09-25 (it gave "ˈmɡa"), and it is one of
    the most frequent words in any lesson, so the drill would teach it wrong."""
    assert planner.plan_chunk("mga", (0, 1)) == "maˈŋa"
    assert planner.plan_chunk("Mga", (0, 1), chunk_text="Mga") == "maˈŋa"


def test_a_word_without_an_override_is_still_spelled(planner):
    """Control: the override table is an exception list, not a lookup that
    replaces the letter rules."""
    assert planner.plan_chunk("sa", (0, 1)) == "ˈsa"


# ---------------------------------------------------------------------------
# plan_word: the WHOLE word, for a multi-word drill step
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        # "ilubong ugma", the phrase the user heard as "ilubong uglak" — the row
        # this method exists for. A vowel-initial first syllable opens on a
        # glottal stop; more than one syllable carries no stress mark.
        ("ilubong", "ʔiluboŋ"),
        ("ugma", "ʔuɡma"),
        # One syllable: the lone-syllable stress mark, and the glottal onset.
        ("ang", "ˈʔaŋ"),
        # The lesson's punctuation is stripped, and a consonant-initial word has
        # no glottal onset to write.
        ("lubong?", "luboŋ"),
        # The override still applies: it is keyed on a span covering the whole
        # word, which is exactly what this is.
        ("mga", "maˈŋa"),
    ],
)
def test_a_whole_word_is_read_off_its_spelling(planner, word, expected):
    assert planner.plan_word(word) == expected


@pytest.mark.parametrize("word", ["", "?"], ids=["empty", "all_punctuation"])
def test_a_word_with_nothing_to_read_gets_no_ipa(planner, word):
    """Nothing to spell: an empty or all-punctuation word is plain text, and a
    caller that treated it as an empty reading would speak a silence."""
    assert planner.plan_word(word) is None
