"""Which spatial words get a drawn picture, and which picture (tunatale-hvj0).

A spatial word — ``under``, ``sulod``, ``gor`` — is what a photo search does
worst: Pixabay's "under" is a photo with no relation in it. So these words are
drawn, like the numbers (``app.cards.number_picture``): a box and a ball in the
relation the word names, rendered by ``app.cards.spatial_scenes``.

The user decided (2026-09-28) that they are **picture cards**, not clozes. That
is why ``is_spatial_word`` exists as well as ``spatial_picture``: the
closed-class router (``app.srs.function_words.is_function_word``) asks it first,
so a listed word cards as vocab on every add path and at the mint, instead of
going down the cloze route its UPOS would otherwise send it.

**Which word means which concept is data**, in each language's ``spatial.json``;
this module holds no word of any language. The English keywords below are the
other half: they are the concept's own names, used to check a card's GLOSS.

**The gloss guard.** A spatial word can be a homograph — Cebuano ``wala`` is
"left" and "none", ``tuo`` is "right" and "believe". The routing veto cannot see
a gloss (it is asked about a bare token), but the picture can: it is drawn only
when the card's English gloss names the relation. A card for ``wala`` glossed
"none" gets no arrow pointing left; it takes the route it was already on. An
empty gloss refuses too — a picture that cannot be confirmed is not drawn.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import cache

from app.cards.number_picture import NumberPicture
from app.cards.spatial_scenes import render_spatial_svg
from app.languages import get_spatial_path

#: The English words that name each concept. A gloss matches when one of its
#: whole words is in the set — "upset" does not name "up".
_CONCEPT_GLOSSES: dict[str, frozenset[str]] = {
    "up": frozenset({"up", "upward", "upwards", "upstairs"}),
    "down": frozenset({"down", "downward", "downwards", "downstairs"}),
    "left": frozenset({"left"}),
    "right": frozenset({"right"}),
    "in": frozenset({"in", "inside", "within", "into", "inner", "interior"}),
    "out": frozenset({"out", "outside", "outdoors", "outer", "exterior"}),
    "on": frozenset({"on", "onto", "upon"}),
    "under": frozenset({"under", "below", "beneath", "underneath", "low", "lower"}),
    "above": frozenset({"above", "over", "high", "overhead"}),
    "between": frozenset({"between", "among", "amid", "middle"}),
    "beside": frozenset({"beside", "next", "side", "alongside"}),
    "in_front": frozenset({"front", "ahead"}),
    "behind": frozenset({"behind", "back", "rear"}),
    "top": frozenset({"top"}),
    "bottom": frozenset({"bottom"}),
}

_WORD = re.compile(r"[^\W\d_]+")


@cache
def load_spatial_words(language_code: str) -> dict[str, str]:
    """*language_code*'s spatial words, casefolded, each mapped to its concept.

    Empty when the language registers no file: it has no drawn spatial words.
    """
    path = get_spatial_path(language_code)
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(word).casefold(): str(concept) for word, concept in data.get("words", {}).items()}


def is_spatial_word(text: str, language_code: str) -> bool:
    """True if *text* is one of *language_code*'s drawn spatial words."""
    return text.strip().casefold() in load_spatial_words(language_code)


def gloss_matches(concept: str, gloss: str) -> bool:
    """True if the English *gloss* names *concept* in one of its whole words."""
    return bool(_CONCEPT_GLOSSES[concept] & {w.casefold() for w in _WORD.findall(gloss)})


def spatial_picture(text: str, language_code: str, gloss: str) -> NumberPicture | None:
    """The drawn picture for spatial word *text*, or ``None``.

    ``None`` means "take the route you were already on": not a spatial word, or
    one whose *gloss* does not confirm the spatial sense.
    """
    concept = load_spatial_words(language_code).get(text.strip().casefold())
    if concept is None or not gloss_matches(concept, gloss):
        return None
    svg = render_spatial_svg(concept)
    return NumberPicture(f"spatial_{concept}_{hashlib.sha256(svg).hexdigest()[:8]}.svg", svg)
