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

import unicodedata

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
#
# "sais", six, is Wolff's sayís (2026-10-19, the owner: "go with what Wolff
# says"): the letters give /sa.ʔis/, a glottal stop no speaker says.
_WHOLE_WORD_IPA = {"mga": "maˈŋa", "sais": "sa.ˈjis"}

# Names the letter rules misread, as said inside a LINE (the Enunciated
# sections give the voice the whole line's reading; tunatale-tyfk). A borrowed
# name keeps its own spelling: Spanish j and z (Jimenez), an English vowel
# (Paul), a z that Cebuano says as s (Liza). The rules gave dʒimɛnɛz, paʔul and
# liza, and these three are the corrections the user heard in every round of
# the listening test on 2026-10-06.
#
# It is a list and not a rule on purpose: no rule reads "Paul", and one that
# turned every j into h would be wrong for the next English name. It applies to
# lines only, so the key-phrase drill is untouched. A name missing from it is
# read as spelled, which is audible; add it here with the reading you heard.
_LINE_WORD_IPA = {"jimenez": "himɛnɛs", "paul": "pol", "liza": "lisa"}


# Every reading shows where its syllables part. Run together, a glottal stop
# between two like vowels was lost on the voice these readings go to: miinom
# sent as /miʔinom/ ran its two i's into one in 9 takes of 9, and as plain text
# kept them apart in 7 of 7; sent as /mi.ʔi.nom/ the stop was there in 6 of 8
# (2026-10-13, tunatale-ve4p.24; the owner's ear on seven of those clips agreed
# with the measure on every one). The owner's call, the same day: "I'd do it
# everywhere", so it is the planner's, and a drill piece, a drilled form and a
# line at natural speed all get it. An Enunciated line is the one caller that
# asks for its words run together: see ``synth_plan``.
_SYLLABLE_BREAK = "."


# Wolff marks a stressed vowel with an acute, or a circumflex where a glottal
# stop follows it; the grave alone is a glottal stop and no stress.
_STRESS_MARKS = frozenset("\u0301\u0302")


def _letters(word: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", word.lower()) if c.isalpha()).translate(_EO)


_EO = str.maketrans("eo", "iu")


def _stressed_letter(marked: str) -> int | None:
    """Which letter of *marked* carries its one stress mark, or ``None``."""
    letters, stressed = 0, []
    for c in unicodedata.normalize("NFD", marked):
        if c in _STRESS_MARKS:
            stressed.append(letters - 1)
        elif c.isalpha():
            letters += 1
    return stressed[0] if len(stressed) == 1 else None


def wolff_stress(word: str) -> int | None:
    """The stressed syllable of *word* as Wolff prints it, or ``None``.

    Read off his Handbook spelling laid onto the word's own letters
    (``onto_card``). ``None`` when he lists the word under two stresses
    (baláy, bálay: there is no telling which is meant) and when the word is
    not in him. A spelling that says nothing is passed over rather than
    counted against the others: one with no stress mark (dyos beside diyós),
    and one whose letters are not the word's (aníyo beside ányo for anyo),
    where an index into his letters would land on the wrong syllable. Without a
    stress, the voice falls back on the penult, which is wrong for every
    final-stressed word: lubóng and simbahán, as the owner heard them read
    (2026-10-19); with this, the takes were "closer".
    """
    from app.plugins.languages.ceb.wolff_cards import load_lexicon, onto_card

    pieces = syllabify_word(word, "ceb")
    owner = [i for i, piece in enumerate(pieces) for c in piece if c.isalpha()]
    found = set()
    for surface in load_lexicon().surfaces(word):
        marked = onto_card(word, surface)
        letter = _stressed_letter(marked) if _letters(marked) == _letters(word) else None
        if letter is not None:
            found.add(owner[letter])
    return found.pop() if len(found) == 1 else None


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
        line_word_ipa=_LINE_WORD_IPA,
        syllable_break=_SYLLABLE_BREAK,
        stress=wolff_stress,
    )
