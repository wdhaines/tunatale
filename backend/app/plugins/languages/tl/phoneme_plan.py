"""Tagalog chunk planner: a breakdown chunk → IPA for the key-phrase voice.

Azure's fil-PH voices ignore ``<phoneme>`` entirely: "salamat" came back
byte-identical with no IPA, with ``/saˈlamat/``, ``[sɐˈlaː.mɐt̪̚]`` and the IPA
of "kumusta" (measured 2026-09-23, tunatale-w4m7.16). So the Tagalog key-phrase
breakdown is voiced by a Multilingual voice that honours IPA (the plugin's
``key-phrases`` role), and this planner hands it the chosen Wiktionary reading
(``app.plugins.languages.tl.pronunciation``).

Unlike Norwegian's planner, WHOLE words get IPA too. A native voice reads its
own language's words correctly as text; a German voice reading Tagalog text
guesses, and a whole word is where the reading's stress (vowel length) and the
particles' real sounds (``ng`` = ``[n̪ɐŋ]``) matter most.

A fragment of a word Wiktionary does not list is read letter by letter
(:func:`_spelled`): Tagalog spelling is close enough to phonemic for that, and
leaving it to the voice as text was what sounded bad in the first place.
"""

from __future__ import annotations

import re

from app.plugins.languages.tl.pronunciation import resolve_reading, whole_word_ipa
from app.plugins.languages.tl.syllabify import syllabify_tagalog_word

# Dental, retracted and unreleased marks: byte-identical audio with and without
# them on the key-phrase voice, so they only make the input noisier.
_IGNORED_MARKS = re.compile("[\u032a\u0320\u031a]")

_EDGES = re.compile(r"^\W+|\W+$")

# Letter → IPA for a word with no reading. r is the trill, not the tap, for the
# same reason for_voice rewrites the tap.
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
}
_DIGRAPH_IPA = {"ng": "ŋ", "ts": "tʃ"}


def for_voice(ipa: str) -> str:
    """Adapt Wiktionary's IPA to what the key-phrase voice can actually say.

    Every rewrite here is a measured merge on the voice, not a preference: the
    tap ``ɾ`` came back byte-identical to ``d`` (the user heard "nakikiramay"
    split up as "da"), so it becomes the trill.
    """
    return _IGNORED_MARKS.sub("", ipa).replace("ɾ", "r")


def _bare(text: str) -> str:
    return _EDGES.sub("", text.lower())


def _spelled(syllable: str) -> str:
    """IPA read off the spelling of one syllable of an unlisted word."""
    out = "ʔ" if syllable[:1] in "aeiou" else ""
    i = 0
    while i < len(syllable):
        if syllable[i : i + 2] in _DIGRAPH_IPA:
            out += _DIGRAPH_IPA[syllable[i : i + 2]]
            i += 2
        else:
            out += _LETTER_IPA.get(syllable[i], "")
            i += 1
    return out


class TagalogPhonemePlanner:
    """IPA for Tagalog breakdown chunks, from the same reading that cut them."""

    def syllables(self, word: str) -> list[str]:
        """The syllables *span* indexes: the breakdown's own split of *word*."""
        return syllabify_tagalog_word(word)

    def plan_chunk(
        self,
        source_word: str,
        span: tuple[int, int],
        upos: str | None = None,
        chunk_text: str | None = None,
    ) -> str | None:
        """IPA for syllables ``span`` of *source_word*, or ``None`` for plain synthesis.

        ``None`` when the span does not fit the word, when *chunk_text* names
        other letters than the span now does (a lesson stored before the
        boundaries moved: IPA for the new split would play a syllable its
        caption does not name), and for a whole word Wiktionary does not list.

        *upos* picks among a whole word's readings. A fragment always uses the
        reading the SYLLABIFIER chose, which takes no part of speech, because
        that is the reading its boundaries came from.
        """
        pieces = [_bare(p) for p in self.syllables(source_word)]
        start, stop = span
        if not 0 <= start < stop <= len(pieces) or not all(pieces):
            return None
        if chunk_text is not None and _bare(chunk_text) != "".join(pieces[start:stop]):
            return None

        word = "".join(pieces)
        if (start, stop) == (0, len(pieces)):
            ipa = whole_word_ipa(word, upos)
            return None if ipa is None else for_voice(ipa)

        reading = resolve_reading(word)
        if reading is not None:
            return for_voice("".join(reading.syllables[start:stop]))
        spelled = "".join(_spelled(p) for p in pieces[start:stop])
        return "ˈ" + spelled if stop - start == 1 else spelled


def create_phoneme_planner() -> TagalogPhonemePlanner:
    """Zero-arg factory registered on the plugin's ``LanguageConfig``."""
    return TagalogPhonemePlanner()
