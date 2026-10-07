"""Does a lesson's gloss name the same sense as a card's translation?

A deliberately small test: the two share a word. It exists to CHOOSE
among the cards one spelling has (``transcript.py::choose_lemma_card``), which
it does well — ``ura`` glossed "the clock" shares a word with exactly one of
"hour" / "clock". It is NOT a judge of whether a single card is right: a probe
over 11 live Norwegian lessons (2026-10-06) flagged 149 headwords whose gloss
shares nothing with their card, and about 20 were real sense conflicts — the
rest were inflection ("went" / "go") and synonym ("the city" / "town"). Do not
reach for this to answer that question (bd tunatale-en6n).

Glosses and translations are the learner's L1, so the stop list is English.
"""

from __future__ import annotations

import re

# Articles are never evidence: "to the" and "the hall" are not one meaning.
_DROP = frozenset({"a", "an", "the"})
# Grammar words are WEAK evidence — they count only when no content word
# decides. They cannot simply be dropped: for a function-word card they ARE the
# sense. Norwegian `vår` is "spring" (noun) / "our" (determinative), and with
# "our" discarded the gloss "our" matched neither card (live deck, 2026-10-06).
# But "to go" must pick the card "go" over the card "to", hence two tiers.
_WEAK = frozenset(
    [
        "to",
        "of",
        "in",
        "on",
        "at",
        "for",
        "from",
        "by",
        "with",
        "and",
        "or",
        "not",
        "be",
        "is",
        "am",
        "are",
        "was",
        "were",
        "been",
        "being",
        "i",
        "you",
        "he",
        "she",
        "it",
        "we",
        "they",
        "me",
        "him",
        "her",
        "us",
        "them",
        "my",
        "your",
        "his",
        "its",
        "our",
        "their",
        "this",
        "that",
        "these",
        "those",
        "do",
        "does",
        "did",
        "will",
        "would",
        "shall",
        "should",
        "can",
        "could",
        "may",
        "might",
        "must",
        "have",
        "has",
        "had",
    ]
)
# What a gloss may open with that is not part of the meaning's name.
_LEADING = _DROP | {"to"}
_SUFFIXES = ("ing", "ed", "es", "s", "d")
_MIN_STEM = 3


def _stems(word: str) -> frozenset[str]:
    """*word*, and every shorter form it might be a suffixed version of.

    Enough to join "hours" to "hour" and "wondered" to "wonder" — nothing more.
    A SET, because English does not say which suffix a word carries: "times" is
    "time" + "s" and "boxes" is "box" + "es". Taking the longest suffix alone
    made "times" the stem "tim", so a line glossed "three times" could not
    find the card translated "time" (2026-10-06).
    """
    cut = {word[: -len(suffix)] for suffix in _SUFFIXES if word.endswith(suffix)}
    return frozenset({word, *(stem for stem in cut if len(stem) >= _MIN_STEM)})


def _words(text: str) -> tuple[frozenset[str], frozenset[str]]:
    """(content words, grammar words) of a gloss or translation, casefolded,
    articles gone."""
    tokens = [w for w in re.findall(r"[^\W\d_]+", text.casefold()) if w not in _DROP]
    return (
        frozenset(w for w in tokens if w not in _WEAK),
        frozenset(w for w in tokens if w in _WEAK),
    )


def sense_overlap(gloss: str, translation: str) -> tuple[int, int]:
    """(content words shared, grammar words shared) — compare as a tuple, so a
    shared content word outranks any number of shared grammar words.
    ``(0, 0)`` is no evidence at all. A content word of the gloss is shared
    when some content word of the translation could be the same word under a
    suffix (``_stems``)."""
    gloss_content, gloss_weak = _words(gloss)
    card_content, card_weak = _words(translation)
    card_stems = frozenset().union(*(_stems(w) for w in card_content))
    shared = sum(1 for word in gloss_content if _stems(word) & card_stems)
    return shared, len(gloss_weak & card_weak)


def names_same_sense(gloss: str, translation: str) -> bool:
    """Does a card translated *translation* already HAVE the meaning *gloss* names?

    Stricter than "any overlap", because here a yes means "make no new card":
    a shared grammar word alone is not a shared meaning ("to wonder" and "to
    trick" share "to") — unless grammar words are all the gloss has, which is
    the function-word case ``_WEAK`` exists for (``vår`` glossed "our").
    """
    content, weak = sense_overlap(gloss, translation)
    return content > 0 or (weak > 0 and not _words(gloss)[0])


def sense_label(gloss: str) -> str:
    """The key a second-sense card is told apart by: its meaning, named plainly.

    The decks already key same-class homographs this way — Slovene ``barva`` is
    two cards keyed "color" and "paint" — so a card minted for a second sense
    (bd tunatale-ceuc) takes the same shape rather than a new one. Casefolded,
    a gloss's parenthetical aside dropped unless it is all there is, and the
    leading article or infinitive "to" dropped: "the time (den gangen = back
    then)" names the sense "time". Empty when nothing is left to name it by.
    """
    text = " ".join(gloss.casefold().split())
    text = " ".join(re.sub(r"\([^)]*\)", " ", text).split()) or text
    words = text.split(" ")
    while len(words) > 1 and words[0] in _LEADING:
        words.pop(0)
    return "" if words[0] in _DROP else " ".join(words)
