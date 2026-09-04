"""Language-neutral machinery for scoring which Anki field holds the L2 text.

The SCORER lives in each language plugin; only the algorithm lives here. That
split is the fix for tunatale-yaan, where a Slovene-shaped scorer was applied to
a Norwegian note: ``ø`` and ``å`` were in no character set, so the headword
``snøm`` scored 0.0 while an example sentence scored 0.5 on the single ``æ`` in
``være`` (``æ`` counts only by accident, as an IPA symbol). The sentence won by
half a point and was written to Anki as a headword.

A shared algorithm with per-language character sets keeps the one thing that is
genuinely language-specific — which letters mark this language — in the plugin
that owns it, per the repo's no-hardcoded-language-logic rule.
"""

from __future__ import annotations

from collections.abc import Callable

#: Phonetic symbols that appear on pronunciation cards in any language. Worth
#: half a point: they mark "not English", but they are not evidence for a
#: PARTICULAR language, so they must never outweigh a real L2 letter.
IPA_CHARS = frozenset("ɛəɔɪʊæθðŋɲʃʒɕʑɯɰʔˈˌːˈ́")

#: Function words that mark a field as an English gloss or a phonics prompt.
ENGLISH_STOPWORDS = frozenset(
    {
        "what",
        "where",
        "when",
        "how",
        "why",
        "is",
        "are",
        "does",
        "do",
        "did",
        "was",
        "were",
        "the",
        "a",
        "an",
        "per",
        "after",
        "before",
        "of",
        "in",
        "on",
        "to",
        "with",
        "for",
    }
)


def make_l2_scorer(l2_chars: frozenset[str]) -> Callable[[str], float]:
    """Build a scorer that prefers text written in a language's own letters.

    One point per character in *l2_chars*, half a point per IPA symbol, minus two
    per English stopword. Pass the letters that distinguish this language from
    English — a language whose set is empty scores every field 0.0 and the first
    non-empty field wins, which is the documented forward-layout default.
    """

    def score(clean: str) -> float:
        total = 0.0
        for ch in clean:
            if ch in l2_chars:
                total += 1
            elif ch in IPA_CHARS:
                total += 0.5
        for word in clean.lower().split():
            if word.strip("?,.!:;") in ENGLISH_STOPWORDS:
                total -= 2
        # DENSITY, not a raw count. A raw count is length-biased, and the field
        # we want is a HEADWORD — the shortest thing on the card. Counting made
        # a 54-character example sentence (two Norwegian letters) beat the
        # headword `snøm` (one), because longer text simply contains more
        # letters. Per-character, `snøm` scores 0.25 and the sentence 0.037.
        return total / max(1, len(clean))

    return score
