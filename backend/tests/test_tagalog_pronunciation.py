"""Tagalog pronunciation readings: syllable boundaries and IPA from ONE source.

tunatale-w4m7.16. The key-phrase breakdown cuts a word into syllables and plays
each fragment as IPA. Both halves must come from the same Wiktionary reading:
a caption cut by spelling rules while the audio comes from a reading with a
different syllable count names a sound the audio does not make (the Norwegian
lesson, ``lexicon_syllables``).

The expected splits are an ORACLE written before the code, from two sources:
the user's ear (2026-09-23: ``kailan`` is kai-lan, not ka-i-lan; ``siya`` is one
syllable) and the reading-choice rule those two cases forced. Wiktionary lists
the careful form FIRST in 155 of the 239 words whose readings disagree on
syllable count, so neither "first reading" nor "fewest syllables" works: the
rule is that every written vowel keeps its syllable unless the spelling marks it
as a glide.
"""

from __future__ import annotations

import pytest

from app.plugins.languages.tl.pronunciation import (
    Reading,
    align_spelling,
    choose_reading,
    ipa_syllables,
    resolve_reading,
    whole_word_ipa,
)

# ── the committed table, read through the chooser ────────────────────────────

SPLITS = {
    # The user's two corrections.
    "kailan": ["kai", "lan"],  # a+i before a consonant is a diphthong: [k̠aɪ̯ˈlan̪]
    "siya": ["siya"],  # consonant + i + y is a glide: [ˈʃa], one syllable
    # The same glide rule, the other readings it must choose.
    "niya": ["niya"],  # [ˈɲa]
    "diyan": ["diyan"],  # [ˈd͡ʒan]
    "buwan": ["buwan"],  # consonant + u + w: [ˈbwan]
    # Every other written vowel keeps its syllable, even where Wiktionary also
    # lists a contraction (saan [ˈsan], doon [ˈdon], paano [ˈpaːno], iyan [ˈjan]).
    "saan": ["sa", "an"],
    "doon": ["do", "on"],
    "paano": ["pa", "a", "no"],
    "iyan": ["i", "yan"],  # word-initial i has no consonant to glide onto
    "mayroon": ["may", "ro", "on"],
    # Ordinary words, where the reading agrees with the spelling rules.
    "salamat": ["sa", "la", "mat"],
    "pangalan": ["pa", "nga", "lan"],
    "kumain": ["ku", "ma", "in"],
    "abuloy": ["a", "bu", "loy"],
    "mangga": ["mang", "ga"],
    # Pronunciation moves a boundary the spelling rules put elsewhere: the
    # rules say is|tas|yon, but [ʔɪs.t̪ɐˈʃon̪] says the sy is one sound, ʃ.
    "istasyon": ["is", "ta", "syon"],
}


@pytest.mark.parametrize(("word", "expected"), SPLITS.items())
def test_reading_splits_the_spelling_where_the_pronunciation_does(word, expected):
    reading = resolve_reading(word)
    assert reading is not None
    assert list(reading.spelling) == expected
    assert "".join(reading.spelling) == word


def test_the_ipa_syllables_are_the_chosen_readings_own():
    reading = resolve_reading("kailan")
    assert reading == Reading(ipa="k̠aɪ̯ˈlan̪", spelling=("kai", "lan"), syllables=("k̠aɪ̯", "ˈlan̪"))


def test_the_default_part_of_speech_picks_the_reading():
    # ako the pronoun is [ʔɐˈx̠o]; the noun homograph [ˈʔaː.x̠oʔ] must not win.
    assert resolve_reading("ako").ipa == "ʔɐˈx̠o"
    assert resolve_reading("ako", upos="NOUN").ipa == "ˈʔaː.x̠oʔ"


def test_an_unknown_part_of_speech_falls_back_to_the_default():
    assert resolve_reading("ako", upos="VERB").ipa == "ʔɐˈx̠o"


def test_a_word_wiktionary_does_not_list_has_no_reading():
    assert resolve_reading("nakikiramay") is None


def test_a_reading_with_more_syllables_than_written_vowels_does_not_align():
    # mga is /maˈŋa/: two syllables from one written vowel. No split of the
    # spelling rejoins to "mga" with two nuclei, so there is no syllable reading.
    assert resolve_reading("mga") is None


def test_lookup_is_case_insensitive():
    assert resolve_reading("Salamat") == resolve_reading("salamat")


# ── whole words ────────────────────────────────────────────────────────────


def test_whole_word_ipa_uses_the_chosen_reading():
    assert whole_word_ipa("kailan") == "k̠aɪ̯ˈlan̪"
    assert whole_word_ipa("ng") == "n̪ɐŋ"  # the particle, not the letter name


def test_whole_word_ipa_needs_no_syllable_alignment():
    # mga has no syllable reading, but the whole word is still one utterance.
    assert whole_word_ipa("mga") == "mɐˈŋa"


def test_whole_word_ipa_is_none_for_an_unlisted_word():
    assert whole_word_ipa("nakikiramay") is None


# ── the pieces, on literal readings ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("ipa", "expected"),
    [
        ("sɐˈlaː.mɐt̪̚", ["sɐ", "ˈlaː", "mɐt̪̚"]),  # a stress mark opens a syllable
        ("mɐɡ̠ˈk̠aː.n̪o", ["mɐɡ̠", "ˈk̠aː", "n̪o"]),  # ...even inside a cluster
        ("ˌn̪aː.sɐˈʔan̪", ["ˌn̪aː", "sɐ", "ˈʔan̪"]),  # secondary stress too
        ("ˈʃa", ["ˈʃa"]),
    ],
)
def test_ipa_syllables(ipa, expected):
    assert ipa_syllables(ipa) == expected


def test_align_reports_glides_the_spelling_licenses():
    # kai|lan drops the i as a syllable, and the spelling says it may.
    assert align_spelling("kailan", "k̠aɪ̯ˈlan̪") == (("kai", "lan"), 0, 1)
    assert align_spelling("kailan", "k̠ɐ.ʔɪˈlan̪") == (("ka", "i", "lan"), 0, 0)


def test_align_reports_a_dropped_vowel_the_spelling_does_not_license():
    # iyan as [ˈjan] drops a word-initial i: nothing before it to glide from.
    assert align_spelling("iyan", "ˈjan̪") == (("iyan",), 1, 0)


def test_a_reading_that_swallows_a_written_a_does_not_align():
    # Only i/u/y/w/h may be silent. A contraction that loses an a (saan read
    # [ˈsan]) must not set the boundaries; the spelling rules do instead.
    assert align_spelling("saan", "ˈsan̪") is None


def test_align_refuses_sounds_the_spelling_cannot_produce():
    assert align_spelling("salamat", "kʊˈmʊs.t̪a") is None


def test_the_chooser_prefers_a_licensed_glide_over_the_careful_form():
    assert choose_reading("kailan", ["k̠ɐ.ʔɪˈlan̪", "k̠aɪ̯ˈlan̪"]).spelling == ("kai", "lan")


def test_the_chooser_prefers_the_careful_form_over_a_contraction():
    assert choose_reading("iyan", ["ˈjan̪", "ʔɪˈjan̪"]).spelling == ("i", "yan")


def test_the_chooser_keeps_wiktionarys_order_on_a_tie():
    assert choose_reading("siya", ["ˈʃa", "ˈsja"]).ipa == "ˈʃa"


def test_the_chooser_takes_an_unlicensed_glide_when_nothing_else_aligns():
    assert choose_reading("iyan", ["ˈjan̪"]).spelling == ("iyan",)


def test_the_chooser_returns_none_when_no_reading_aligns():
    assert choose_reading("mga", ["mɐˈŋa"]) is None


def test_align_counts_a_non_glide_vowel_read_as_a_glide_as_unlicensed():
    # Only i and u can be glides; an e read as the ɪ̯ of a diphthong cannot.
    assert align_spelling("kaelan", "kaɪ̯ˈlan") == (("kae", "lan"), 1, 0)


def test_align_reads_x_as_two_sounds():
    assert align_spelling("taxi", "ˈtak.si") == (("tax", "i"), 0, 0)


def test_align_refuses_a_symbol_it_does_not_know():
    assert align_spelling("ewan", "ʔɛ|wan") is None


def test_align_refuses_a_syllable_with_two_nuclei():
    # A missing syllable dot: "sɐlaː" is two syllables written as one.
    assert align_spelling("salamat", "sɐlaː.mɐt") is None
