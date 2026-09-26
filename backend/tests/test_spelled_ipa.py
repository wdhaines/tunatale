"""``SpelledPhonemePlanner`` — IPA read off a word's SPELLING.

The core half of ``app/audio/spelled_ipa.py``, exercised with SYNTHETIC letter
tables and a synthetic syllabifier rather than a real language: the table is
the plugin's business, and the thing worth pinning here is the shape every
plugin's table is dropped into (glottal stop, digraph-before-letter, the
one-syllable stress mark, and the three refusals).

The one rule that is not obviously safe is the stripping set. Tagalog's own
planner strips ``\\W+`` off both ends of a word, which would take the hyphen off
``-a`` and silently lose the glottal stop; this class strips only the sentence
punctuation set, so ``-`` and ``'`` survive as letters.
"""

from __future__ import annotations

import pytest

from app.audio.spelled_ipa import SpelledPhonemePlanner

# A deliberately small alphabet: everything the assertions below need, and
# nothing that would make a test pass for the wrong reason.
_LETTER = {
    "a": "a",
    "b": "b",
    "e": "e",
    "g": "ɡ",
    "k": "k",
    "l": "l",
    "m": "m",
    "n": "n",
    "o": "o",
    "s": "s",
    "t": "t",
    "u": "u",
    "y": "j",
    "'": "",
    "-": "ʔ",
}
_DIGRAPH = {"ng": "ŋ", "ts": "tʃ"}


def _planner(*pieces: str, letter_ipa=None, digraph_ipa=None, onset_vowels: str = "aeiou") -> SpelledPhonemePlanner:
    """A planner whose syllabifier always cuts *word* into exactly *pieces*."""
    return SpelledPhonemePlanner(
        letter_ipa=_LETTER if letter_ipa is None else letter_ipa,
        digraph_ipa=_DIGRAPH if digraph_ipa is None else digraph_ipa,
        syllabify=lambda word: list(pieces),
        onset_vowels=onset_vowels,
    )


# ---------------------------------------------------------------------------
# The transcription rules
# ---------------------------------------------------------------------------


def test_a_vowel_initial_syllable_opens_on_a_glottal_stop():
    assert _planner("a", "ko").plan_chunk("ako", (0, 1)) == "ˈʔa"
    assert _planner("a", "ko").plan_chunk("ako", (1, 2)) == "ˈko"


def test_a_consonant_initial_syllable_has_no_glottal_stop():
    assert _planner("ny", "ko").plan_chunk("nyko", (0, 1)) == "ˈnj"
    assert _planner("ny", "ko").plan_chunk("nyko", (0, 2)) == "njko"


def test_a_digraph_is_one_sound_and_beats_two_letters():
    """``ng`` is one sound: read as two letters it would be ``nɡ``."""
    assert _planner("yong").plan_chunk("yong", (0, 1)) == "ˈjoŋ"
    assert _planner("tsa").plan_chunk("tsa", (0, 1)) == "ˈtʃa"


def test_an_unmapped_letter_contributes_nothing_and_is_not_skipped():
    # ``q`` is absent from the table: the scan moves on ONE character, so the
    # ``a`` after it is still read. A digraph straddle survives it too.
    letters = {"a": "a", "n": "n"}
    assert _planner("qa", letter_ipa=letters, digraph_ipa={}).plan_chunk("qan", (0, 1)) == "ˈa"
    assert _planner("nq", letter_ipa=letters, digraph_ipa={}).plan_chunk("nqa", (0, 1)) == "ˈn"
    assert _planner("ang", letter_ipa=letters, digraph_ipa={"ng": "ŋ"}).plan_chunk("ang", (0, 1)) == "ˈʔaŋ"


def test_exactly_one_syllable_is_stressed_and_more_than_one_is_not():
    planner = _planner("a", "ko")
    assert planner.plan_chunk("ako", (0, 1)) == "ˈʔa"
    assert planner.plan_chunk("ako", (1, 2)) == "ˈko"
    assert planner.plan_chunk("ako", (0, 2)) == "ʔako"


def test_the_onset_vowels_are_the_plugins_to_choose():
    """A language whose only vowel is ``e`` glottal-stops on ``e`` alone."""
    planner = _planner("e", "o", onset_vowels="e")
    assert planner.plan_chunk("eo", (0, 1)) == "ˈʔe"
    assert planner.plan_chunk("eo", (1, 2)) == "ˈo"


# ---------------------------------------------------------------------------
# The stripping rule: sentence punctuation only
# ---------------------------------------------------------------------------


def test_a_hyphen_and_an_apostrophe_are_letters_not_punctuation():
    # The Tagalog planner strips ``\\W+``, which would reduce "-a" to "a" and
    # lose the glottal stop the hyphen is there to mark.
    assert _planner("-a").plan_chunk("-a", (0, 1)) == "ˈʔa"
    assert _planner("lo'").plan_chunk("lo'", (0, 1)) == "ˈlo"


def test_a_contraction_mark_is_silent_but_still_stops_the_scan():
    planner = _planner("lo'", "g", letter_ipa={**_LETTER, "'": ""})
    assert planner.plan_chunk("lo'g", (0, 2)) == "loɡ"


def test_leading_and_trailing_sentence_punctuation_is_stripped_from_the_source_word():
    # The pieces come back unchanged whatever the caller wrapped the word in.
    assert _planner("a", "ko").plan_chunk('"ako?"', (0, 1)) == "ˈʔa"
    assert _planner("a", "ko").plan_chunk("(ako)", (0, 2)) == "ʔako"


# ---------------------------------------------------------------------------
# The three refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "span",
    [(0, 3), (2, 3), (1, 1), (1, 0), (-1, 1)],
    ids=["past_the_end", "starts_past_the_end", "empty_span", "reversed", "negative"],
)
def test_a_span_that_does_not_fit_the_word_gets_no_ipa(span):
    assert _planner("a", "ko").plan_chunk("ako", span) is None


def test_an_empty_piece_gets_no_ipa():
    # Nothing to read off: an empty piece would otherwise open on the glottal
    # stop, since ``"" in "aeiou"``.
    assert _planner("a", "").plan_chunk("ako", (0, 2)) is None
    assert _planner("").plan_chunk("a", (0, 1)) is None


def test_a_word_with_no_syllables_gets_no_ipa():
    assert _planner().plan_chunk("ako", (0, 1)) is None


def test_a_caption_naming_other_letters_gets_no_ipa():
    # A lesson stored before the boundaries moved: IPA for the new split would
    # play a syllable the caption does not name.
    assert _planner("a", "ko").plan_chunk("ako", (1, 2), chunk_text="a") is None


def test_a_caption_naming_the_right_letters_is_accepted_whatever_its_case_or_punctuation():
    assert _planner("a", "ko").plan_chunk("ako", (0, 2), chunk_text="AKO") == "ʔako"
    assert _planner("a", "ko").plan_chunk("ako", (0, 1), chunk_text="a?") == "ˈʔa"
    # A multi-piece span is matched by its CONCATENATION, not by one piece.
    assert _planner("a", "ko").plan_chunk("ako", (0, 2), chunk_text="ako") == "ʔako"


def test_no_caption_at_all_is_accepted():
    assert _planner("a", "ko").plan_chunk("ako", (0, 1), chunk_text=None) == "ˈʔa"


def test_the_syllabifier_sees_the_stripped_word_and_the_pieces_are_lowercased():
    seen: list[str] = []

    def _spy(word: str) -> list[str]:
        seen.append(word)
        return ["A", "KO"]

    planner = SpelledPhonemePlanner(letter_ipa=_LETTER, digraph_ipa=_DIGRAPH, syllabify=_spy, onset_vowels="aeiou")

    assert planner.plan_chunk('"AKO?"', (0, 1)) == "ˈʔa"
    assert seen == ["AKO"]


def test_a_part_of_speech_changes_nothing():
    # The spelling has no readings to choose between, so ``upos`` is accepted
    # and ignored rather than a reason to refuse.
    assert _planner("a", "ko").plan_chunk("ako", (0, 1), upos="VERB") == "ˈʔa"


def test_a_whole_word_override_wins_only_for_the_whole_word():
    """An override names a WHOLE word's reading; a span inside a word is
    still spelled, since the override says nothing about its pieces."""
    from app.audio.spelled_ipa import SpelledPhonemePlanner

    p = SpelledPhonemePlanner(
        letter_ipa={c: c for c in "abcdefghijklmnopqrstuvwxyz"},
        digraph_ipa={},
        syllabify=lambda w: [w[:2], w[2:]] if len(w) > 2 else [w],
        whole_word_ipa={"abcd": "XYZ"},
    )
    assert p.plan_chunk("abcd", (0, 2)) == "XYZ"
    assert p.plan_chunk("abcd", (0, 1)) == "ˈʔab"
    assert p.plan_chunk("abce", (0, 2)) == "ʔabce"
