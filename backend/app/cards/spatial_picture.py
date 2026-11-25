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
    # Going vs being there, boundary and slope words (tunatale-fsyd). Broad on
    # purpose, like the sets above: the guard exists to refuse a homograph's
    # OTHER sense, not to pick between spatial senses — the word's concept in
    # spatial.json already did that.
    "into": frozenset({"in", "into", "inside", "inward", "inwards"}),
    "within": frozenset({"within", "inside", "in", "inner"}),
    "outside_of": frozenset({"outside", "out", "beyond", "outer"}),
    "outdoors": frozenset({"outdoors", "outdoor", "outside", "out"}),
    "up_there": frozenset({"up", "upstairs", "high", "above"}),
    "down_there": frozenset({"down", "downstairs", "low", "below"}),
    "further_down": frozenset({"below", "down", "beneath", "lower", "under", "further"}),
    "further_up": frozenset({"above", "up", "higher", "over", "further"}),
    "side": frozenset({"side", "edge", "flank"}),
    "middle": frozenset({"middle", "centre", "center", "mid"}),
    # A place in a line. "latest" and "final" are `last` said another way; the
    # verb "to last" is a different word's gloss, and that word is not listed.
    "first": frozenset({"first", "firstly"}),
    "last": frozenset({"last", "latest", "final", "lastly"}),
    # Movement relative to a place. "going" is in `toward` because that is how
    # the lessons gloss padulong ("Asa ka padulong?", where are you going).
    "from": frozenset({"from"}),
    "toward": frozenset({"toward", "towards", "heading", "headed", "going", "bound"}),
    # Distance from the people talking. The guard only confirms the sense
    # (a thing word says "this"/"that", a place word "here"/"there"); which of
    # the four distances is the word's own entry in spatial.json.
    "this_both": frozenset({"this"}),
    "this_mine": frozenset({"this"}),
    "that_yours": frozenset({"that"}),
    "that_far": frozenset({"that", "yonder"}),
    "here_both": frozenset({"here"}),
    "here_mine": frozenset({"here"}),
    "there_yours": frozenset({"there"}),
    "there_far": frozenset({"there", "yonder"}),
    # Being there: the lessons gloss nia "is here (near you and me)", and the
    # base list glosses the long forms anía / atúa "there exists (...)".
    "is_here_both": frozenset({"here", "exists"}),
    "is_there_far": frozenset({"there", "yonder"}),
    "is_there_yours": frozenset({"there"}),
    # Past/future/genitive (tunatale-tofg.5)
    "is_here_mine": frozenset({"here"}),
    "was_here_mine": frozenset({"was", "were", "past"}),
    "was_here_both": frozenset({"was", "were", "past"}),
    "was_there_yours": frozenset({"was", "were", "past"}),
    "was_there_far": frozenset({"was", "were", "past"}),
    "will_be_here_mine": frozenset({"will", "future", "come", "go", "goes"}),
    "will_be_here_both": frozenset({"will", "future", "come", "go", "goes"}),
    "will_be_there_yours": frozenset({"will", "future", "come", "go", "goes"}),
    "will_be_there_far": frozenset({"will", "future", "come", "go", "goes"}),
    "of_this_mine": frozenset({"of"}),
    "of_this_both": frozenset({"of"}),
    "of_that_yours": frozenset({"of"}),
    "of_that_far": frozenset({"of"}),
}

_WORD = re.compile(r"[^\W\d_]+")


@cache
def load_spatial_words(language_code: str) -> dict[str, str]:
    """*language_code*'s spatial words, casefolded, each mapped to its concept.

    Empty when the language registers no file: it has no drawn spatial words.
    """
    return {word: senses[0] for word, senses in load_spatial_senses(language_code).items()}


@cache
def load_spatial_senses(language_code: str) -> dict[str, tuple[str, ...]]:
    """Every word's concepts, in priority order: a string value is a one-sense word."""
    path = get_spatial_path(language_code)
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, tuple[str, ...]] = {}
    for word, concept in data.get("words", {}).items():
        senses = concept if isinstance(concept, list) else [concept]
        result[str(word).casefold()] = tuple(str(c) for c in senses)
    return result


@cache
def _load_data(language_code: str) -> dict:
    path = get_spatial_path(language_code)
    if path is None or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def pair_cue(text: str, language_code: str, disambig: str = "") -> tuple[str, str] | None:
    """``(sentence, "short" | "long")`` for a card in a demonstrative pair, else ``None`` (tunatale-avcl).

    The pair's cards are drawn with the table's ``pair_frame`` under the scene,
    its blank saying which form the card asks for.
    """
    card = (text.strip().casefold(), disambig.strip())
    frame = _load_data(language_code).get("pair_frame")
    for short, short_key, long, long_key in load_spatial_short_forms(language_code):
        if frame and card == (short, short_key):
            return frame, "short"
        if frame and card == (long, long_key):
            return frame, "long"
    return None


@cache
def load_spatial_second_senses(language_code: str) -> dict[tuple[str, str], str]:
    """Each word's second senses, ``(word, disambig_key)`` to its concept (tunatale-tofg.19).

    For a word whose main sense is NOT spatial, so it cannot be in ``words``
    (listing it would veto its cloze): Cebuano ``ni`` is the name marker first and
    the short form of ``kini`` second. Only a card carrying that key is drawn.
    """
    data = _load_data(language_code)
    return {
        (str(word).casefold(), str(key)): str(concept)
        for word, senses in data.get("senses", {}).items()
        for key, concept in senses.items()
    }


@cache
def load_spatial_short_forms(language_code: str) -> tuple[tuple[str, str, str, str], ...]:
    """The table's short demonstratives, ``(short, short_key, long, long_key)``, each with a card of its own."""
    data = _load_data(language_code)
    return tuple(
        (str(row["short"]), str(row.get("short_key", "")), str(row["long"]), str(row.get("long_key", "")))
        for row in data.get("short_forms", [])
    )


def is_spatial_word(text: str, language_code: str) -> bool:
    """True if *text* is one of *language_code*'s drawn spatial words."""
    return text.strip().casefold() in load_spatial_words(language_code)


def gloss_matches(concept: str, gloss: str) -> bool:
    """True if the English *gloss* names *concept* in one of its whole words."""
    return bool(_CONCEPT_GLOSSES[concept] & {w.casefold() for w in _WORD.findall(gloss)})


def spatial_picture(text: str, language_code: str, gloss: str, disambig: str = "") -> NumberPicture | None:
    """The drawn picture for spatial word *text*, or ``None``.

    ``None`` means "take the route you were already on": not a spatial word, or
    one whose *gloss* does not confirm the spatial sense. A card keyed with a
    second sense (:func:`load_spatial_second_senses`) is drawn as that sense.
    """
    key = text.strip().casefold()
    second = load_spatial_second_senses(language_code).get((key, disambig.strip()))
    senses = (second,) if second else load_spatial_senses(language_code).get(key, ())
    concept = next((c for c in senses if gloss_matches(c, gloss)), None)
    if concept is None:
        return None
    cue = pair_cue(key, language_code, disambig.strip() if second else "")
    svg = render_spatial_svg(concept, *cue) if cue else render_spatial_svg(concept)
    return NumberPicture(f"spatial_{concept}_{hashlib.sha256(svg).hexdigest()[:8]}.svg", svg)
