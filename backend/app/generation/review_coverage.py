"""Did the generated lesson actually USE the review words the prompt asked for?

The prompt carries the learner's decaying vocabulary and explicitly licenses the
model to skip what does not fit (see ``prompts.build_review_block``). So a miss
is not an error — at the default NATURAL pressure, skipping a word IS a correct
answer. This module exists to make the outcome VISIBLE rather than to police it:
"we asked the model" is not "it happened", and omission is an already-observed
failure mode on this exact call.

Pure: no I/O, no clock, no logging. The caller owns what to do with the numbers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def review_word_usage(
    review_words: Sequence[str],
    surface_lemma: Mapping[str, str],
    dialogue: str,
) -> tuple[list[str], list[str]]:
    """Split *review_words* into (used, unused), preserving the given order.

    *surface_lemma* is the surface→in-context-lemma map the lesson builder
    already computes from the generated dialogue; *dialogue* is that dialogue's
    L2 text joined together.

    ⚠️ SINGLE WORDS MATCH ON TOKENS AND LEMMAS, NEVER ON SUBSTRINGS. Slovene and
    Norwegian inflect, so a substring check fails in both directions at once: it
    misses a requested lemma that appears only as an inflected surface (the
    COMMON case — "kava" written as "kavo"), and it invents uses where a short
    word happens to sit inside a longer unrelated one ("dan" inside "danes").
    Checking the surfaces AND their lemmas gets the first right; requiring a
    whole-token match gets the second right.

    A multi-word collocation has no entry in a token map, so it falls back to a
    phrase search over *dialogue*. That under-counts an inflected collocation,
    and under-counting is the safe direction here: the number is advisory, and a
    meter that flatters itself is worse than one that is slightly pessimistic.
    """
    forms = {surface.casefold() for surface in surface_lemma}
    forms |= {lemma.casefold() for lemma in surface_lemma.values()}
    haystack = dialogue.casefold()

    used: list[str] = []
    unused: list[str] = []
    for word in review_words:
        needle = word.casefold().strip()
        hit = needle in haystack if " " in needle else needle in forms
        (used if hit else unused).append(word)
    return used, unused
