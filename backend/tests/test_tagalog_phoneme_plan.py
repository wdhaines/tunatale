"""The Tagalog chunk planner: breakdown chunk → IPA for the key-phrase voice.

tunatale-w4m7.16. Azure's fil-PH voices ignore IPA entirely (byte-identical
audio for "salamat" under /saˈlamat/, [sɐˈlaː.mɐt̪̚] and the IPA of "kumusta",
measured 2026-09-23), so the breakdown is voiced by a Multilingual voice that
honours it. The literals below are the chosen Wiktionary reading's syllables
(``tests/test_tagalog_pronunciation.py``) after the planner's voice
adaptations, each one a measured merge:

* ``ɾ`` → ``r``: the voice renders the tap byte-identically to ``d``, so
  "nakikiramay" split up said "da" (the user heard it; the hash confirmed it).
* the dental, retracted and unreleased marks are dropped: byte-identical with
  and without them, so they only make the input noisier.
"""

from __future__ import annotations

import pytest

from app.languages import get_phoneme_planner
from app.plugins.languages.tl.phoneme_plan import TagalogPhonemePlanner, for_voice


@pytest.fixture
def planner() -> TagalogPhonemePlanner:
    return TagalogPhonemePlanner()


@pytest.mark.parametrize(
    ("word", "span", "expected"),
    [
        ("salamat", (1, 2), "ˈlaː"),  # the stressed syllable keeps its length
        ("salamat", (0, 1), "sɐ"),
        ("salamat", (1, 3), "ˈlaːmɐt"),  # t̪̚ → t
        ("ako", (1, 2), "ˈxo"),  # the k between vowels is the fricative [x]
        ("kailan", (0, 1), "kaɪ̯"),  # k̠ → k
        ("kailan", (1, 2), "ˈlan"),
        ("sigurado", (2, 3), "ˈraː"),  # ɾ → r: the voice says the tap as d
        ("libing", (1, 2), "ˈbɪŋ"),
    ],
)
def test_a_fragment_is_the_chosen_readings_syllables(planner, word, span, expected):
    assert planner.plan_chunk(word, span) == expected


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("salamat", "sɐˈlaː.mɐt"),
        ("ako", "ʔɐˈxo"),
        ("kailan", "kaɪ̯ˈlan"),  # kai-lan, the user's correction
    ],
)
def test_a_whole_word_is_the_whole_reading(planner, word, expected):
    # Norwegian leaves whole words to the TTS; a Tagalog word said by a German
    # voice as text would come out German, so the whole word is IPA too.
    n = len(planner.syllables(word))
    assert planner.plan_chunk(word, (0, n)) == expected


def test_a_one_syllable_function_word_is_read_as_what_it_abbreviates(planner):
    # ng is the particle "nang" and mga is "manga": written abbreviations.
    assert planner.plan_chunk("ng", (0, 1)) == "nɐŋ"
    assert planner.plan_chunk("mga", (0, 1)) == "mɐˈŋa"


def test_the_part_of_speech_picks_the_whole_word_reading(planner):
    assert planner.plan_chunk("ako", (0, 2), upos="NOUN") == "ˈʔaː.xoʔ"


def test_a_word_wiktionary_does_not_list_gets_its_fragments_from_the_spelling(planner):
    # nakikiramay is inflected and not in the table: the spelling rules give
    # na|ki|ki|ra|may, and each fragment is read letter by letter.
    assert planner.plan_chunk("nakikiramay", (3, 4)) == "ˈra"
    assert planner.plan_chunk("nakikiramay", (2, 5)) == "kiramaj"
    assert planner.plan_chunk("magdadala", (0, 1)) == "ˈmaɡ"


def test_an_unlisted_whole_word_is_left_to_the_tts(planner):
    # Spelling rules know no stress, and a whole word read as text is fine.
    assert planner.plan_chunk("nakikiramay", (0, 5)) is None


def test_edge_punctuation_on_the_source_word_is_ignored(planner):
    assert planner.plan_chunk("libing?", (1, 2), chunk_text="bing?") == "ˈbɪŋ"
    assert planner.plan_chunk("Salamat!", (0, 3)) == "sɐˈlaː.mɐt"


def test_a_stale_caption_gets_no_ipa(planner):
    # A lesson stored before the boundaries moved says "i" where kailan now
    # splits kai|lan: IPA for the new split would play a syllable the caption
    # does not name, so the chunk stays plain text.
    assert planner.plan_chunk("kailan", (1, 2), chunk_text="i") is None
    assert planner.plan_chunk("kailan", (1, 2), chunk_text="lan") == "ˈlan"


def test_a_span_outside_the_word_gets_no_ipa(planner):
    assert planner.plan_chunk("ako", (1, 3)) is None
    assert planner.plan_chunk("ako", (1, 1)) is None


def test_a_word_with_no_letters_gets_no_ipa(planner):
    assert planner.plan_chunk("?", (0, 1)) is None


def test_for_voice_rewrites_only_what_the_voice_merges():
    assert for_voice("ˈɾaː") == "ˈraː"
    assert for_voice("mɐt̪̚") == "mɐt"
    assert for_voice("ʔɐˈx̠o") == "ʔɐˈxo"
    assert for_voice("kaɪ̯") == "kaɪ̯"  # the glide mark is meaningful and stays


def test_the_registry_serves_a_tagalog_planner():
    assert isinstance(get_phoneme_planner("tl"), TagalogPhonemePlanner)


def test_an_unlisted_words_digraphs_are_one_sound(planner):
    # ts and ng are single sounds in Tagalog spelling ("tsinelas", "ngayon").
    assert planner.plan_chunk("tsangzing", (0, 1)) == "ˈtʃaŋ"
