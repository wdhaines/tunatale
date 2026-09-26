"""Cebuano chunk planner: breakdown chunk → IPA, read off the spelling.

The Cebuano drill is voiced by a Gemini 2.5 flash TTS voice, and a lone drill
fragment rendered badly by ear: extra words, clipped endings, English readings
(tunatale-u8nz, the user's listening test). Cloud TTS rejects SSML
``<phoneme>`` for Gemini voices, so the fragment's IPA reaches the model the
only way it can — as an instruction, in ``input.prompt`` (see
``app.audio.gemini_tts``). This module is where that IPA comes from.

Cebuano has no pronunciation lexicon and, so far, no syllabifier of its own, so
the reading is the spelling's. Two entries in the table below are not plain
Latin letters, and both are about marks that are letters here rather than
punctuation on a word:

* ``"-"`` → ``ʔ``. The hyphen marks the glottal stop of a final vowel —
  kanus-a, pag-anhi, mag-ampo — and Tagalog's planner strips ``\\W+`` off both
  ends of a word, which would reduce ``-a`` to ``a`` and lose the stop. The
  shared planner strips only the sentence-punctuation set instead.
* ``"'"`` → silent. A contraction mark, not a sound: napulo'g is three
  syllables and the mark contributes nothing between them.

The rest is the same Latin table Tagalog uses. It is duplicated rather than
imported because a plugin may not import another plugin
(``tests/test_plugin_isolation.py``), and the shared *logic* — the glottal stop,
digraph-before-letter, the lone syllable's stress mark — is a core class
(``app.audio.spelled_ipa``) for the same reason.
"""

from __future__ import annotations

from app.audio.spelled_ipa import SpelledPhonemePlanner
from app.generation.syllabify import syllabify_word

# r is the trill, matching Tagalog's table: nothing in Cebuano marks the tap,
# and a trill is the sound the table already claims to write.
_LETTER_IPA = {
    "a": "a",
    "b": "b",
    "c": "k",
    "d": "d",
    "e": "ɛ",
    "f": "f",
    "g": "ɡ",
    "h": "h",
    "i": "i",
    "j": "dʒ",
    "k": "k",
    "l": "l",
    "m": "m",
    "n": "n",
    "o": "o",
    "p": "p",
    "q": "k",
    "r": "r",
    "s": "s",
    "t": "t",
    "u": "u",
    "v": "v",
    "w": "w",
    "x": "ks",
    "y": "j",
    "z": "z",
    "ñ": "ɲ",
    # The two Cebuano marks, and the only difference from Tagalog's table.
    "-": "ʔ",
    "'": "",
}
_DIGRAPH_IPA = {"ng": "ŋ", "ts": "tʃ"}


# Whole words the spelling cannot produce. "mga", the plural marker, is said
# "manga": neither the vowel nor the velar nasal is written. Found auditing this
# planner on 2026-09-25, when the letter rules gave "ˈmɡa" for one of the most
# frequent words in any lesson. Add a word here only with the same evidence:
# a reading the letters cannot yield, not a preferred variant.
_WHOLE_WORD_IPA = {"mga": "maˈŋa"}


def create_phoneme_planner() -> SpelledPhonemePlanner:
    """Zero-arg factory registered on the plugin's ``LanguageConfig``.

    The syllabifier is ``syllabify_word`` rather than Cebuano's own, because
    Cebuano registers none: that is the function ``section_builder`` cut the
    breakdown with, so a span indexes exactly the pieces this planner reads. A
    second splitter here would point every span at the wrong letters while every
    unit test still passed.
    """
    return SpelledPhonemePlanner(
        letter_ipa=_LETTER_IPA,
        digraph_ipa=_DIGRAPH_IPA,
        syllabify=lambda word: syllabify_word(word, "ceb"),
        whole_word_ipa=_WHOLE_WORD_IPA,
    )
