"""Which personal pronouns get a drawn picture, and which picture (tunatale-3rxu).

A pronoun is the word class a photo search cannot serve at all: asked for "she"
it returns a portrait of one person, which pictures *a* person and not the thing
the word says — that the referent is somebody the speaker is talking to. So
these words are drawn, like the numbers and the spatial words: a conversation
with the referent filled, rendered by ``app.cards.pronoun_scenes``.

The user decided (2026-09-28) that they are **picture cards**, not clozes. That
is why ``is_pronoun_word`` exists as well as ``pronoun_picture``: the closed-class
router (``app.srs.function_words.is_function_word``) asks it first, so a listed
word cards as vocab on every add path and at the mint, instead of going down the
cloze route its UPOS would otherwise send it. That route matters more here than
for the spatial words: a pronoun is ``PRON``, and the case-marker cloze is
exactly the wrong drill for it.

**Which word means which concept is data**, in each language's ``pronouns.json``;
this module holds no word of any language. The English keywords below are the
other half: they are the concept's own names, used to check a card's GLOSS.
The possessive keywords are where that guard earns its keep twice over, since
``siya`` and ``niya`` are two words whose pictures differ only by the bags.

**The gloss guard, which carries more weight in this family than the spatial
one.** A listed pronoun is vetoed by TOKEN, so the router cannot see a sense —
Norwegian ``de`` is listed as "they" and is also the definite article, Slovene
``on`` is "he" and is also a relative-clause particle. Once listed, *every* sense
of the word is off the cloze route, so the picture is drawn only when the card's
English gloss names the referent. ``de`` glossed "the" gets no filled figures at
all; it takes the route it was already on. An empty gloss refuses too — a
picture that cannot be confirmed is not drawn.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import cache

from app.cards.number_picture import NumberPicture
from app.cards.pronoun_scenes import render_pronoun_svg
from app.languages import get_pronouns_path

#: The English words that name each concept. A gloss matches when one of its
#: whole words is in the set — "young" does not confirm "you", and "its" does not
#: confirm "it". ``third_one`` takes all four of "he/she/him/her" because it is
#: the gender-neutral referent (Tagalog and Cebuano ``siya``) and the gloss is
#: the only place the card can say which is meant.
_CONCEPT_GLOSSES: dict[str, frozenset[str]] = {
    "i": frozenset({"i", "me"}),
    "you_one": frozenset({"you"}),
    "you_two": frozenset({"you"}),
    "you_many": frozenset({"you"}),
    "he": frozenset({"he", "him"}),
    "she": frozenset({"she", "her"}),
    "third_one": frozenset({"he", "she", "him", "her"}),
    "it": frozenset({"it"}),
    "we_two": frozenset({"we", "us"}),
    "we_many": frozenset({"we", "us"}),
    "we_incl": frozenset({"we", "us"}),
    "we_excl": frozenset({"we", "us"}),
    "they_two": frozenset({"they", "them"}),
    "they_many": frozenset({"they", "them"}),
    # The possessives, which is what this guard is for. A possessive is its
    # nominative scene plus a bag, so the two halves of this table decide
    # whether Tagalog `siya` ("he/she") and `niya` ("his/her") are two words on
    # two pictures or one word drawn twice — and only the card's gloss can say
    # which. Hence `he` does NOT confirm `third_one_poss` and `me` does NOT
    # confirm `i_poss`: a nominative gloss is about a different word.
    "i_poss": frozenset({"my", "mine"}),
    "you_one_poss": frozenset({"your", "yours"}),
    "you_two_poss": frozenset({"your", "yours"}),
    "you_many_poss": frozenset({"your", "yours"}),
    "he_poss": frozenset({"his"}),
    "she_poss": frozenset({"her", "hers"}),
    "third_one_poss": frozenset({"his", "her", "hers"}),
    "it_poss": frozenset({"its"}),
    "we_two_poss": frozenset({"our", "ours"}),
    "we_many_poss": frozenset({"our", "ours"}),
    "we_incl_poss": frozenset({"our", "ours"}),
    "we_excl_poss": frozenset({"our", "ours"}),
    "they_two_poss": frozenset({"their", "theirs"}),
    "they_many_poss": frozenset({"their", "theirs"}),
}

_WORD = re.compile(r"[^\W\d_]+")


@cache
def load_pronoun_words(language_code: str) -> dict[str, str]:
    """*language_code*'s pronoun words, casefolded, each mapped to its concept.

    Empty when the language registers no file: it has no drawn pronouns.
    """
    path = get_pronouns_path(language_code)
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(word).casefold(): str(concept) for word, concept in data.get("words", {}).items()}


def is_pronoun_word(text: str, language_code: str) -> bool:
    """True if *text* is one of *language_code*'s drawn pronoun words."""
    return text.strip().casefold() in load_pronoun_words(language_code)


def gloss_matches(concept: str, gloss: str) -> bool:
    """True if the English *gloss* names *concept* in one of its whole words."""
    return bool(_CONCEPT_GLOSSES[concept] & {w.casefold() for w in _WORD.findall(gloss)})


def pronoun_picture(text: str, language_code: str, gloss: str) -> NumberPicture | None:
    """The drawn picture for pronoun word *text*, or ``None``.

    ``None`` means "take the route you were already on": not a pronoun word, or
    one whose *gloss* does not confirm the personal sense.
    """
    concept = load_pronoun_words(language_code).get(text.strip().casefold())
    if concept is None or not gloss_matches(concept, gloss):
        return None
    svg = render_pronoun_svg(concept)
    return NumberPicture(f"pronoun_{concept}_{hashlib.sha256(svg).hexdigest()[:8]}.svg", svg)
