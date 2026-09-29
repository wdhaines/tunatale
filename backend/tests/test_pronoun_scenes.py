"""Pronoun pictures: who is in the conversation, and who the word points at.

A pronoun is the word class a photo search cannot serve at all. Asked for
"she" it returns a portrait of one person, which is a picture of *a* person
and not of the thing the word says — that the referent is somebody the
speaker is TALKING TO, and not the speaker. The referent of a pronoun is
defined by a relation between people (who stands inside the conversation
outline, who outside it, who is filled), and a photograph carries no such
relation in a way anyone can check. So these concepts get a picture that is
**drawn**, and that is the whole case rather than a preference: the relation is
in the coordinates, so "the other is lit and stands outside the outline" is a
number read off the SVG and compared with a table. Same argument, one word class
across, as ``app.cards.spatial_scenes`` — and the same discipline: nothing is
labelled, because a printed "she" would make the card answerable by reading it.

**Every number in the checker below comes off the parsed markup** — never out of
a module constant, and never out of a builder-supplied label. A person is found
by its *geometry* (a circle of the person head radius with an arc-bodied path
immediately beneath it) rather than by reading a ``class`` attribute, and the
gender of a mark is read from the *direction* of its first stroke. Both matter
more here than they did in the spatial work, because the cat's head is also a
12-radius circle and the two gender marks share one circle: a detector that
counted head circles, or trusted an id, would report three people in ``it`` and
a boy in ``she`` — and would then agree with a table written to match the
mistake.

**The derived properties, not the examples, are the oracle.** :func:`_row`
turns a render into a dict of counts and flags read off the geometry, and each
concept is matched against the brief's table. The discrimination tests at the
end then feed those same predicates *mutated* renders and require them to
reject them, so the checker is proven to discriminate rather than merely to
agree with the renderer that produced its input.

**A possessive is not a scene, it is a difference.** Tagalog ``siya`` ("he/she")
and ``niya`` ("his/her") are the same conversation, the same people, the same
who-is-filled — so a possessive concept is its nominative render with a bag added
to every lit referent and NOTHING else changed, and the tests check that as a
*difference*: strip the bags and the two renders must be byte-identical, with one
exception that is itself asserted (the cat has no hands, so its bag stands on
the ground beside it and the group has to move to stay centred). A bag is found
by its corner radius, not by its fill, because a bag in the WRONG COLOUR still
has to be found for the contrast check to reject it — keyed on the fill it would
vanish, and "no bags" would read as a scene with no bags rather than as a
palette mistake.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from typing import NamedTuple

import pytest

from app.cards.pronoun_scenes import PRONOUN_CONCEPTS, render_pronoun_svg
from app.cards.spatial_scenes import _ACCENT

_SVG = "{http://www.w3.org/2000/svg}"

# ── The brief's table, transcribed ──────────────────────────────────────────
# Roles: S = speaker, L = listener (inside the outline), O = other (outside it).
# "lit" is the filled reference; "pale" is the pale fill WITH its outline, which
# is the only reason a pale figure is visible. These are what a RENDER MUST
# SHOW — never what the builder is handed.
#
# "listeners" counts the people inside the outline OTHER THAN the speaker, which
# is what the brief's column means: every scene has at least one listener, so a
# count of everybody inside would be 2 for almost every concept and would carry
# none of the distinctions the table exists to pin.

_ROW_BASE: dict[str, object] = {"cats": 0, "cats_lit": 0, "pale_outlined": True, "bags": 0}

_EXPECTED: dict[str, dict[str, object]] = {
    # concept    speaker lit | listeners drawn/lit | others drawn/lit | extra
    "i": {
        **_ROW_BASE,
        "speaker_lit": True,
        "listeners": 1,
        "listeners_lit": 0,
        "outside": 0,
        "outside_lit": 0,
        "symbol": None,
    },
    "you_one": {
        **_ROW_BASE,
        "speaker_lit": False,
        "listeners": 1,
        "listeners_lit": 1,
        "outside": 0,
        "outside_lit": 0,
        "symbol": None,
    },
    "he": {
        **_ROW_BASE,
        "speaker_lit": False,
        "listeners": 1,
        "listeners_lit": 0,
        "outside": 1,
        "outside_lit": 1,
        "symbol": "m",
    },
    "she": {
        **_ROW_BASE,
        "speaker_lit": False,
        "listeners": 1,
        "listeners_lit": 0,
        "outside": 1,
        "outside_lit": 1,
        "symbol": "f",
    },
    "third_one": {
        **_ROW_BASE,
        "speaker_lit": False,
        "listeners": 1,
        "listeners_lit": 0,
        "outside": 1,
        "outside_lit": 1,
        "symbol": None,
    },
    "it": {
        **_ROW_BASE,
        "speaker_lit": False,
        "listeners": 1,
        "listeners_lit": 0,
        "outside": 0,
        "outside_lit": 0,
        "symbol": None,
        "cats": 1,
        "cats_lit": 1,
    },
    "we_two": {
        **_ROW_BASE,
        "speaker_lit": True,
        "listeners": 1,
        "listeners_lit": 1,
        "outside": 0,
        "outside_lit": 0,
        "symbol": None,
    },
    "we_many": {
        **_ROW_BASE,
        "speaker_lit": True,
        "listeners": 1,
        "listeners_lit": 1,
        "outside": 1,
        "outside_lit": 1,
        "symbol": None,
    },
    "we_incl": {
        **_ROW_BASE,
        "speaker_lit": True,
        "listeners": 1,
        "listeners_lit": 1,
        "outside": 0,
        "outside_lit": 0,
        "symbol": None,
    },
    "we_excl": {
        **_ROW_BASE,
        "speaker_lit": True,
        "listeners": 1,
        "listeners_lit": 0,
        "outside": 1,
        "outside_lit": 1,
        "symbol": None,
    },
    "you_two": {
        **_ROW_BASE,
        "speaker_lit": False,
        "listeners": 2,
        "listeners_lit": 2,
        "outside": 0,
        "outside_lit": 0,
        "symbol": None,
    },
    "you_many": {
        **_ROW_BASE,
        "speaker_lit": False,
        "listeners": 3,
        "listeners_lit": 3,
        "outside": 0,
        "outside_lit": 0,
        "symbol": None,
    },
    "they_two": {
        **_ROW_BASE,
        "speaker_lit": False,
        "listeners": 1,
        "listeners_lit": 0,
        "outside": 2,
        "outside_lit": 2,
        "symbol": None,
    },
    "they_many": {
        **_ROW_BASE,
        "speaker_lit": False,
        "listeners": 1,
        "listeners_lit": 0,
        "outside": 3,
        "outside_lit": 3,
        "symbol": None,
    },
}

# ── The fourteen possessives ───────────────────────────────────────────────
# A possessive is its nominative scene plus a bag in the hands of every LIT
# referent, so its row is the nominative row and one number: how many bags.
# The counts are transcribed here rather than derived from the row above, so
# that a scene which quietly lost a referent could not agree with a table
# computed from itself.

_BAGS: dict[str, int] = {
    "i": 1,  # the speaker
    "you_one": 1,  # the one listener
    "he": 1,
    "she": 1,
    "third_one": 1,
    "it": 1,  # the cat, whose bag stands on the ground beside it
    "we_two": 2,  # speaker and listener
    "we_many": 3,  # speaker, listener, other
    "we_incl": 2,
    "we_excl": 2,  # speaker and other
    "you_two": 2,  # two listeners
    "you_many": 3,
    "they_two": 2,  # two others
    "they_many": 3,
}

#: The nominative concepts, and the possessive ids built from them by suffix —
#: the one rule the whole family follows, so the pair is written once.
_NOMINATIVES: tuple[str, ...] = (
    "i",
    "you_one",
    "he",
    "she",
    "third_one",
    "it",
    "we_two",
    "we_many",
    "we_incl",
    "we_excl",
    "you_two",
    "you_many",
    "they_two",
    "they_many",
)
_POSSESSIVES: tuple[str, ...] = tuple(f"{concept}_poss" for concept in _NOMINATIVES)

_EXPECTED.update({f"{concept}_poss": {**_EXPECTED[concept], "bags": count} for concept, count in _BAGS.items()})


# ── Path reading ────────────────────────────────────────────────────────────
# Two things come out of path data, and both are numbers rather than ids: the
# DIRECTION of a gender mark's first stroke (its circle is identical for ♀ and
# ♂, so the direction is the whole of the difference), and where a figure's body
# begins (which is what tells a person's head from the cat's).

#: How many parameters each command takes.
_ARITY = {"M": 2, "L": 2, "T": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "A": 7, "Z": 0}
#: Which of a command's parameters are coordinates, control points included.
_XY = {
    "M": ((0, 1),),
    "L": ((0, 1),),
    "T": ((0, 1),),
    "H": ((0, None),),
    "V": ((None, 0),),
    "C": ((0, 1), (2, 3), (4, 5)),
    "S": ((0, 1), (2, 3)),
    "Q": ((0, 1), (2, 3)),
    "A": ((5, 6),),
    "Z": (),
}
_TOKEN = re.compile(r"[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def _paths(d: str) -> tuple[list[list[tuple[float, float]]], tuple[float, float, float, float]]:
    """*d* as subpaths of endpoint points, plus the extent of every coordinate.

    Relative moves accumulate and absolute ones set. Control points count
    toward the extent, because a curve can leave its endpoints behind it, and
    this is a bounding box rather than a renderer.
    """
    tokens = _TOKEN.findall(d)
    subpaths: list[list[tuple[float, float]]] = []
    xs: list[float] = []
    ys: list[float] = []
    x = y = 0.0
    i = 0
    while i < len(tokens):
        command = tokens[i]
        i += 1
        if not command.isalpha():
            continue
        kind = command.upper()
        params = [float(v) for v in tokens[i : i + _ARITY[kind]]]
        i += _ARITY[kind]
        if kind == "M":
            x, y = (x + params[0], y + params[1]) if command.islower() else (params[0], params[1])
            xs.append(x)
            ys.append(y)
            subpaths.append([(x, y)])
            continue
        for x_at, y_at in _XY[kind]:
            if x_at is not None:
                xs.append(x + params[x_at] if command.islower() else params[x_at])
            if y_at is not None:
                ys.append(y + params[y_at] if command.islower() else params[y_at])
        if kind == "H":
            x = x + params[0] if command.islower() else params[0]
        elif kind == "V":
            y = y + params[0] if command.islower() else params[0]
        elif kind in ("L", "T", "A") or kind in ("C", "S", "Q"):
            x, y = (x + params[-2], y + params[-1]) if command.islower() else (params[-2], params[-1])
        subpaths[-1].append((x, y))
    return subpaths, (min(xs), min(ys), max(xs), max(ys))


def _arc_bodied(d: str) -> bool:
    """True if *d* closes a rounded top with an elliptical arc.

    A person's body is the one path in these pictures drawn with ``a``; the
    cat's is drawn with quadratics. So this is what says "a person is here"
    without asking the builder to label one.
    """
    return bool(re.search(r"a[\d.]", d))


# ── WCAG, for the palette read off the render ───────────────────────────────


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(colour: str) -> float:
    """WCAG relative luminance of the ``#rrggbb`` the SVG actually carries."""
    assert colour.startswith("#") and len(colour) == 7, f"{colour!r} is not a hex colour"
    r, g, b = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _contrast(one: str, other: str) -> float:
    high, low = sorted((_luminance(one), _luminance(other)), reverse=True)
    return (high + 0.05) / (low + 0.05)


# ── The parsed render ───────────────────────────────────────────────────────


class Figure(NamedTuple):
    """One person, read off the markup and placed in viewBox units."""

    cx: float
    cy: float
    r: float
    fill: str
    stroke: str
    body_top: float  # topmost y of the arc-bodied path beneath the head
    body_bottom: float  # its bottommost y — the ground, since a body stands on it
    inside: bool
    symbol: str | None  # "m" | "f" — the mark's DIRECTION, not an id
    mark_cx: float | None  # the mark circle's x, for the "sits over it" check

    @property
    def lit(self) -> bool:
        return self.fill == _LIT

    @property
    def pale_outlined(self) -> bool:
        """A pale fill is 1.10:1 on the background; the outline is the figure."""
        return self.lit or (self.stroke is not None and self.stroke.lower() != "none")


class Cat(NamedTuple):
    left: float
    right: float
    cx: float
    fill: str
    inside: bool
    #: The x-range of each shape the cat is MADE OF, in viewBox units. The tail
    #: is not one of them: it is the one piece with no fill, a stroked line
    #: rather than a shape, so a bag is never checked against it.
    filled: tuple[tuple[float, float], ...]

    @property
    def lit(self) -> bool:
        return self.fill == _LIT


class Bag(NamedTuple):
    """One bag, found by its corner radius and placed in viewBox units."""

    cx: float
    left: float
    right: float
    top: float
    bottom: float
    fill: str
    stroke: str


class Picture(NamedTuple):
    width: float
    height: float
    ground: float
    panel: tuple[float, float]
    panel_stroke: str
    speaker: Figure
    tail_tip: tuple[float, float]
    bubbles: int
    figures: list[Figure]
    cats: list[Cat]
    bags: list[Bag]
    extent: tuple[float, float]
    root: ET.Element


def _root(svg: bytes) -> ET.Element:
    return ET.fromstring(svg.decode("utf-8"))


def _num(el: ET.Element, name: str) -> float:
    return float(el.get(name, "0"))


def _dx(svg: bytes) -> float:
    """The group's own translate: every figure's x is read through it."""
    group = next(el for el in _root(svg).iter() if el.tag == f"{_SVG}g" and el.get("transform"))
    match = re.fullmatch(r"translate\(([-\d.]+)[ ,][-\d.]+\)", group.get("transform"))
    assert match is not None, f"group transform is {group.get('transform')!r}"
    return float(match.group(1))


def _mark_kind(subpaths: list[list[tuple[float, float]]]) -> str | None:
    """ "m", "f" or ``None``, read from the direction of the mark's first stroke.

    ♀ is a vertical stroke DOWN from the circle with a horizontal crossbar; ♂
    is a stroke UP AND RIGHT with an arrowhead. A renderer that drew the wrong
    one would pass every count in this file, so the direction is read from the
    path rather than from anything the builder recorded.
    """
    if not subpaths or len(subpaths[0]) < 2:
        return None
    (start_x, start_y), (end_x, end_y) = subpaths[0][0], subpaths[0][1]
    if abs(end_x - start_x) < 1e-6 and end_y - start_y > 0:
        return "f"
    if end_x - start_x > 0 and end_y - start_y < 0:
        return "m"
    return None


def _digest(svg: bytes) -> str:
    """A short, stable fingerprint of a render's exact bytes.

    Twelve hex digits is 48 bits. This is a *change* detector for a table read
    off history, not an integrity claim about an adversary, and a collision
    would have to also produce a renderer that draws the same scene differently
    — at which point the properties elsewhere in this file are what would fire.
    """
    return hashlib.sha256(svg).hexdigest()[:12]


def _parse(svg: bytes) -> Picture:
    root = _root(svg)
    _, _, width, height = (float(v) for v in root.get("viewBox").split())
    lines = [el for el in root.iter() if el.tag == f"{_SVG}line"]
    assert len(lines) == 1, "one ground line"
    group = next(el for el in root.iter() if el.tag == f"{_SVG}g" and el.get("transform"))
    dx = _dx(svg)
    children = list(group)

    dashed = [el for el in children if el.tag == f"{_SVG}rect" and el.get("stroke-dasharray")]
    assert len(dashed) == 1, "the conversation outline is the one dashed rect"
    panel_rect = dashed[0]
    panel = (dx + _num(panel_rect, "x"), dx + _num(panel_rect, "x") + _num(panel_rect, "width"))

    bubbles = [el for el in children if el.tag == f"{_SVG}rect" and el.get("fill") == "#ffffff" and el.get("stroke")]
    tails = [
        el
        for el in children
        if el.tag == f"{_SVG}path" and el.get("fill") == "#ffffff" and el.get("stroke") is not None
    ]
    assert len(bubbles) == 1, "one speech bubble"
    assert len(tails) == 1, "one tail, pointing at the speaker"
    bubble = bubbles[0]
    tip = max(_paths(tails[0].get("d"))[0][0], key=lambda point: point[1])
    tail_tip = (dx + tip[0], tip[1])

    figures: list[Figure] = []
    cats: list[Cat] = []
    for index, el in enumerate(children):
        if el.tag != f"{_SVG}circle" or _num(el, "r") != 12 or index == 0:
            continue
        below = children[index - 1]
        if below.tag != f"{_SVG}path":
            continue
        cx = dx + _num(el, "cx")
        if _arc_bodied(below.get("d")):
            _, body_top, _, body_bottom = _paths(below.get("d"))[1]
            figures.append(
                Figure(
                    cx=cx,
                    cy=_num(el, "cy"),
                    r=_num(el, "r"),
                    fill=el.get("fill"),
                    stroke=el.get("stroke"),
                    body_top=body_top,
                    body_bottom=body_bottom,
                    inside=panel[0] < cx < panel[1],
                    symbol=None,
                    mark_cx=None,
                )
            )
            continue
        # A 12-radius circle with no arc beneath it: the cat, whose head is the
        # same size as a person's and whose body is drawn with curves instead.
        # Its tail and body reach past the head, so the extent is the group's.
        reach = [_num(el, "cx") - _num(el, "r"), _num(el, "cx") + _num(el, "r")]
        filled = [(dx + _num(el, "cx") - _num(el, "r"), dx + _num(el, "cx") + _num(el, "r"))]
        for part in children[max(0, index - 3) : index]:
            if part.tag == f"{_SVG}path":
                x0, _, x1, _ = _paths(part.get("d"))[1]
                reach += [x0, x1]
                if part.get("fill") != "none":
                    filled.append((dx + x0, dx + x1))
        cats.append(
            Cat(
                left=dx + min(reach),
                right=dx + max(reach),
                cx=cx,
                fill=el.get("fill"),
                inside=panel[0] < cx < panel[1],
                filled=tuple(filled),
            )
        )

    # A bag is the one rounded-corner rect in the picture — the conversation
    # outline rounds at 16, the bubble at 14, the bag at 4. Keyed on the SHAPE
    # rather than the fill, deliberately: a bag in the wrong colour is still a
    # bag, and a checker that looked for the approved fill would report NO bags
    # and pass a palette mistake as a scene with nothing in its hands.
    bags = [
        Bag(
            cx=dx + _num(el, "x") + _num(el, "width") / 2,
            left=dx + _num(el, "x"),
            right=dx + _num(el, "x") + _num(el, "width"),
            top=_num(el, "y"),
            bottom=_num(el, "y") + _num(el, "height"),
            fill=el.get("fill"),
            stroke=el.get("stroke"),
        )
        for el in children
        if el.tag == f"{_SVG}rect" and el.get("rx") == "4"
    ]

    # A mark belongs to the head it is geometrically ABOVE, not to whatever it
    # happens to be drawn after: the brief's rule is "a circle directly above
    # one head", and reading it off document order instead would let a mark
    # slid sideways onto a listener still count as a mark on the other.
    rings = [el for el in children if el.tag == f"{_SVG}circle" and el.get("stroke") == _ACCENT]
    strokes = [
        el for el in children if el.tag == f"{_SVG}path" and el.get("stroke") == _ACCENT and el.get("fill") == "none"
    ]
    for ring, mark in zip(rings, strokes, strict=True):
        mx, my = dx + _num(ring, "cx"), _num(ring, "cy")
        hosts = [f for f in figures if abs(f.cx - mx) <= 2 and my < f.cy]
        assert len(hosts) <= 1, "a mark is over at most one head"
        if hosts:
            at = figures.index(hosts[0])
            figures[at] = hosts[0]._replace(symbol=_mark_kind(_paths(mark.get("d"))[0]), mark_cx=mx)

    figures.sort(key=lambda figure: figure.cx)
    speaker = min(figures, key=lambda f: (f.cx - tail_tip[0]) ** 2 + (f.cy - tail_tip[1]) ** 2)
    edges = [dx + _num(bubble, "x"), dx + _num(bubble, "x") + _num(bubble, "width"), *panel]
    for figure in figures:
        edges += [figure.cx - figure.r, figure.cx + figure.r]
    for cat in cats:
        edges += [cat.left, cat.right]
    # The bags are part of the drawn group, so they are part of what "centred"
    # measures — the cat's bag is what moves that scene off centre, and a
    # centring check that ignored it would be checking the wrong extent.
    for bag in bags:
        edges += [bag.left, bag.right]
    return Picture(
        width=width,
        height=height,
        ground=_num(lines[0], "y1"),
        panel=panel,
        panel_stroke=panel_rect.get("stroke"),
        speaker=speaker,
        tail_tip=tail_tip,
        bubbles=len(bubbles),
        figures=figures,
        cats=cats,
        bags=bags,
        extent=(min(edges), max(edges)),
        root=root,
    )


# The two head fills, told apart by the property the palette exists for: the
# reference is the DARKER of the two, because a pale fill has to be outlined to
# be seen at all. Read off a render rather than imported from the builder — a
# checker holding the builder's own constants would only be asserting that the
# builder agrees with itself.
_HEAD_FILLS = {
    el.get("fill")
    for el in _root(render_pronoun_svg("we_excl")).iter()
    if el.tag == f"{_SVG}circle" and el.get("r") == "12"
}
assert len(_HEAD_FILLS) == 2, "every scene uses exactly two head fills"
_LIT, _PALE = sorted(_HEAD_FILLS, key=_luminance)


def _inside(p: Picture) -> list[Figure]:
    return [f for f in p.figures if f.inside]


def _outside(p: Picture) -> list[Figure]:
    return [f for f in p.figures if not f.inside]


def _listeners(p: Picture) -> list[Figure]:
    """The people inside the outline other than the speaker.

    Split out because the brief's column counts LISTENERS: every scene has at
    least one, so a count of everyone inside would be a constant.
    """
    return [f for f in _inside(p) if f is not p.speaker]


def _row(p: Picture) -> dict[str, object]:
    """The whole table, as numbers read off one render.

    ``symbol`` is the mark sitting over an OUTSIDE, LIT figure — so a mark
    moved onto a listener reads as absent, which is what a listener wearing a
    gender mark means. ``pale_outlined`` is the legibility property the pale
    fill cannot carry on its own.
    """
    inside, outside = _inside(p), _outside(p)
    listeners = _listeners(p)
    symbol = None
    for mark in ("m", "f"):
        if any(f.symbol == mark for f in outside if f.lit):
            symbol = mark
    return {
        "speaker_lit": p.speaker.lit,
        "listeners": len(listeners),
        "listeners_lit": sum(1 for f in listeners if f.lit),
        "outside": len(outside),
        "outside_lit": sum(1 for f in outside if f.lit),
        "symbol": symbol,
        "cats": len(p.cats),
        "cats_lit": sum(1 for c in p.cats if c.lit),
        "pale_outlined": all(f.pale_outlined for f in inside + outside),
        "bags": len(p.bags),
    }


def _matches(p: Picture, expected: dict[str, object]) -> bool:
    return _row(p) == expected


# ── The table, read row by row ─────────────────────────────────────────────

#: The pairs the drawing is SUPPOSED to collapse, one nominative and one
#: possessive. Asserted both ways: the renders are identical, and they are the
#: only identical rows in the family.
_COLLAPSED: tuple[tuple[str, str], ...] = (("we_two", "we_incl"), ("we_two_poss", "we_incl_poss"))

#: The concepts that carry a gender mark, and which one. ``he_poss``/``she_poss``
#: are in it because a possessive is its nominative scene plus bags: the mark
#: rides along with the referent, and "his" drawn without the ♂ would be a
#: different man.
_GENDER_MARK: dict[str, str] = {"he": "m", "she": "f", "he_poss": "m", "she_poss": "f"}
_GENDERED = tuple(_GENDER_MARK)

#: The concepts whose one referent is the cat and not a person.
_CAT_ONLY = ("it", "it_poss")


def test_the_concept_list_is_the_one_the_cards_route_on() -> None:
    assert PRONOUN_CONCEPTS == _NOMINATIVES + _POSSESSIVES
    assert set(_EXPECTED) == set(PRONOUN_CONCEPTS)
    assert _POSSESSIVES[0] == "i_poss" and _POSSESSIVES[-1] == "they_many_poss"


#: SHA-256 (truncated) of every render as it stood BEFORE the possessives were
#: added, taken from ``git show HEAD:backend/app/cards/pronoun_scenes.py``.
#:
#: This table is a **pin, not a measurement** — nothing here describes a
#: property a reader could have predicted, and a digest is not one. It exists
#: because the brief makes adding fourteen concepts an *additive* change, and
#: "additive" is not a thing the rest of this file can see.
#:
#: ⚠️ The relative test below cannot catch a regression in the base renderer.
#: Oracle A4 compares ``<c>_poss`` against ``<c>``: if a change to the shared
#: figure/outline/bubble code moved both, the two still agree with each other
#: and the test is green over a picture every existing card ships. Only an
#: oracle taken from *before the change* can tell the two apart, and the only
#: such artifact in a repository is history.
#:
#: ``we_two``/``we_incl`` share a digest because the pair renders identically by
#: design (``_COLLAPSED``) — one row fewer here is the collapse, not an omission.
_BASE_RENDERS_AT_HEAD: dict[str, str] = {
    "i": "9c40bf84971d",
    "you_one": "0f2d8e5294b5",
    "he": "be9ebb5ad3df",
    "she": "3d9f884dc58c",
    "third_one": "9f9af7a1d14c",
    "it": "c2938fdf9209",
    "we_two": "953517cafe4a",
    "we_many": "83be6f263a7a",
    "we_incl": "953517cafe4a",
    "we_excl": "6057b23d5aa4",
    "you_two": "e2f4c1df78c3",
    "you_many": "fbff1be152f8",
    "they_two": "7570f97e263c",
    "they_many": "5df66a7f9448",
}


@pytest.mark.parametrize("concept", _NOMINATIVES)
def test_adding_the_possessives_did_not_move_a_single_existing_picture(concept: str) -> None:
    """The fourteen pre-existing renders are byte-for-byte what they were.

    Every other test in this file reads a *property* out of the SVG, and a
    property is the wrong instrument for this claim: a figure shifted 4 units
    left still draws the right number of people in the right fills at a
    sensible centring, so every property test here would stay green over a
    picture that had visibly moved. Only the bytes can say no.
    """
    assert _digest(render_pronoun_svg(concept)) == _BASE_RENDERS_AT_HEAD[concept], (
        f"{concept} no longer renders as it did before the possessives were added"
    )


@pytest.mark.parametrize("concept", PRONOUN_CONCEPTS)
def test_the_concept_draws_exactly_the_people_its_name_claims(concept: str) -> None:
    """The whole table, off the markup. Every number read from the SVG itself."""
    picture = _parse(render_pronoun_svg(concept))
    assert _matches(picture, _EXPECTED[concept]), f"{concept} renders as {_row(picture)}"


@pytest.mark.parametrize(("first", "second"), _COLLAPSED)
def test_the_two_words_that_need_no_distinction_render_the_same_picture(first: str, second: str) -> None:
    """``we_two`` and ``we_incl`` are identical ON PURPOSE — no language has both.

    A contrast the drawing cannot show is not a distinction worth drawing, and
    separate art would invent a difference a learner could not see. Asserted
    byte-for-byte so that splitting them later is a decision somebody makes on
    purpose rather than an accident that survives. The same collapse holds of
    their possessives, which is the same decision once more.
    """
    assert render_pronoun_svg(first) == render_pronoun_svg(second)


def test_every_other_pair_of_concepts_renders_different_bytes() -> None:
    """Identical bytes would point two words at one picture."""
    distinct = {render_pronoun_svg(c) for c in PRONOUN_CONCEPTS}
    assert len(distinct) == len(PRONOUN_CONCEPTS) - len(_COLLAPSED)


# ── The speaker, the outline, and the referents ────────────────────────────


@pytest.mark.parametrize("concept", PRONOUN_CONCEPTS)
def test_the_speaker_is_the_person_the_bubble_points_at(concept: str) -> None:
    """Found by proximity to the tail tip — and it must also be leftmost.

    The tail is drawn over the speaker's head, so proximity is a geometric fact
    about the render. Being leftmost is a *different* fact, and the two
    agreeing is the check: a scene whose speaker drifted right of a listener
    would satisfy one and not the other.
    """
    picture = _parse(render_pronoun_svg(concept))
    assert picture.bubbles == 1
    inside = _inside(picture)
    assert inside, "there is always somebody inside: there must be an outside to be outside of"
    assert picture.speaker is inside[0]
    assert picture.speaker.cx == min(f.cx for f in inside)


@pytest.mark.parametrize("concept", PRONOUN_CONCEPTS)
def test_nobody_straddles_the_outline(concept: str) -> None:
    """A head half in and half out of the conversation reads as neither."""
    picture = _parse(render_pronoun_svg(concept))
    x0, x1 = picture.panel
    for figure in picture.figures:
        if figure.inside:
            assert x0 <= figure.cx - figure.r and figure.cx + figure.r <= x1
        else:
            assert figure.cx - figure.r > x1
    for cat in picture.cats:
        assert cat.left > x1


@pytest.mark.parametrize("concept", PRONOUN_CONCEPTS)
def test_a_person_head_sits_clear_above_its_own_body(concept: str) -> None:
    """The gap is what makes a figure read as a person rather than a blob — and
    it is the other half of what tells the cat's head from a person's."""
    for figure in _parse(render_pronoun_svg(concept)).figures:
        assert figure.cy + figure.r < figure.body_top


@pytest.mark.parametrize("concept", PRONOUN_CONCEPTS)
def test_there_is_always_a_listener_even_when_none_is_the_referent(concept: str) -> None:
    """ "We" needs an inside to be outside of; a lone speaker would have none."""
    assert len(_inside(_parse(render_pronoun_svg(concept)))) >= 1


@pytest.mark.parametrize("concept", PRONOUN_CONCEPTS)
def test_a_pale_person_carries_the_outline_colour(concept: str) -> None:
    """The pale fill is 1.10:1 on the background — invisible without a stroke.

    Checked against the outline's own stroke, so "outlined" means one shared
    structural colour rather than a grey invented per figure.
    """
    picture = _parse(render_pronoun_svg(concept))
    for figure in picture.figures:
        if not figure.lit:
            assert figure.stroke == picture.panel_stroke


@pytest.mark.parametrize("concept", _GENDERED)
def test_the_gender_mark_sits_over_the_other_and_says_which_gender(concept: str) -> None:
    """The mark is over the OUTSIDE, lit figure, and its direction is the gender."""
    picture = _parse(render_pronoun_svg(concept))
    marked = [f for f in picture.figures if f.symbol is not None]
    assert len(marked) == 1
    host = marked[0]
    assert not host.inside and host.lit
    assert host.symbol == _GENDER_MARK[concept]
    assert host.mark_cx is not None and abs(host.mark_cx - host.cx) <= 2, "the circle is above ITS head"


@pytest.mark.parametrize("concept", [c for c in PRONOUN_CONCEPTS if c not in _GENDERED])
def test_no_other_concept_draws_a_gender_mark(concept: str) -> None:
    """``third_one`` is exactly the concept that must NOT guess: no language
    says whether its ``siya`` is a man or a woman, and a mark would teach a
    fact the word does not carry. ``third_one_poss`` is the same argument one
    word along: ``niya`` is just as gender-neutral as ``siya``."""
    assert all(f.symbol is None for f in _parse(render_pronoun_svg(concept)).figures)


def test_only_it_draws_a_cat_and_the_cat_is_lit() -> None:
    """The one non-person referent: a lit cat standing outside, facing in."""
    for concept in PRONOUN_CONCEPTS:
        assert len(_parse(render_pronoun_svg(concept)).cats) == (1 if concept in _CAT_ONLY else 0)
    for concept in _CAT_ONLY:
        picture = _parse(render_pronoun_svg(concept))
        assert picture.cats[0].lit
        assert not _outside(picture), f"'{concept}' has no person outside the outline"


def test_the_cat_is_not_mistaken_for_a_person() -> None:
    """Its head is a 12-radius circle, exactly like a person's.

    A detector that counted head circles would report a third person in ``it``
    — and would then agree with a table written to match the mistake. So the
    count is asserted directly: two people, one of them the listener, and the
    cat counted separately.
    """
    picture = _parse(render_pronoun_svg("it"))
    assert len(picture.figures) == 2
    assert len(picture.cats) == 1
    assert not _outside(picture), "the cat is the only referent, and it is not a person"
    assert not picture.cats[0].inside, "it stands outside, with the others"


# ── Everything every picture must satisfy ──────────────────────────────────


@pytest.mark.parametrize("concept", PRONOUN_CONCEPTS)
def test_is_well_formed_xml_on_a_svg_root_with_a_viewbox(concept: str) -> None:
    root = _root(render_pronoun_svg(concept))
    assert root.tag == f"{_SVG}svg"
    parts = (root.get("viewBox") or "").split()
    assert len(parts) == 4, f"viewBox is {root.get('viewBox')!r}"
    x, y, width, height = (float(v) for v in parts)
    assert (x, y) == (0.0, 0.0)
    assert width > 0 and height > 0


@pytest.mark.parametrize("concept", PRONOUN_CONCEPTS)
def test_paints_its_own_opaque_background_first(concept: str) -> None:
    """An ``<img>`` cannot inherit currentColor, and night mode would erase it."""
    first = _root(render_pronoun_svg(concept))[0]
    assert first.tag == f"{_SVG}rect"
    assert first.get("fill", "").startswith("#")
    assert (first.get("x"), first.get("y")) == ("0", "0")
    assert (_num(first, "width"), _num(first, "height")) == (260.0, 220.0)


@pytest.mark.parametrize("concept", PRONOUN_CONCEPTS)
def test_carries_no_word_that_would_answer_the_card(concept: str) -> None:
    """A printed "she" would make the card answerable by reading it."""
    assert not [el for el in _root(render_pronoun_svg(concept)).iter() if el.tag == f"{_SVG}text"]


@pytest.mark.parametrize("concept", PRONOUN_CONCEPTS)
def test_nothing_but_the_ground_line_is_faded_or_hidden(concept: str) -> None:
    """The checker reads "lit" off a fill, so a lit referent at opacity 0.1 would
    still read as lit while being invisible on the card. Only the ground line is
    ever drawn faded; no other element may carry a visibility attribute."""
    hiders = ("opacity", "fill-opacity", "stroke-opacity", "visibility", "display")
    offenders = [
        el.tag
        for el in _root(render_pronoun_svg(concept)).iter()
        if el.tag != f"{_SVG}line" and any(el.get(a) is not None for a in hiders)
    ]
    assert offenders == []


@pytest.mark.parametrize("concept", PRONOUN_CONCEPTS)
def test_has_exactly_one_horizontal_ground_line_and_one_dashed_outline(concept: str) -> None:
    picture = _parse(render_pronoun_svg(concept))
    line = next(el for el in picture.root.iter() if el.tag == f"{_SVG}line")
    assert line.get("y1") == line.get("y2")
    assert 0 < picture.ground < picture.height
    assert len([el for el in picture.root.iter() if el.get("stroke-dasharray")]) == 1


@pytest.mark.parametrize("concept", PRONOUN_CONCEPTS)
def test_the_group_is_centred_in_the_frame(concept: str) -> None:
    """Centred, so a card scaled to its width does not put the cast off-centre."""
    picture = _parse(render_pronoun_svg(concept))
    left, right = picture.extent
    assert abs((left + right) / 2 - picture.width / 2) <= 6


@pytest.mark.parametrize("concept", PRONOUN_CONCEPTS)
def test_every_shape_is_inside_the_viewbox(concept: str) -> None:
    """A card crops nothing, so a shape past the edge is a shape somebody cut."""
    root = _root(render_pronoun_svg(concept))
    _, _, width, height = (float(v) for v in root.get("viewBox").split())
    for el in root.iter():
        if el.tag == f"{_SVG}circle":
            cx, cy, r = (_num(el, k) for k in ("cx", "cy", "r"))
            assert cx - r >= 0 and cx + r <= width
            assert cy - r >= 0 and cy + r <= height
        elif el.tag == f"{_SVG}rect" and el.get("fill") != "none":
            x, y = _num(el, "x"), _num(el, "y")
            assert x >= 0 and x + _num(el, "width") <= width
            assert y >= 0 and y + _num(el, "height") <= height


@pytest.mark.parametrize("concept", PRONOUN_CONCEPTS)
def test_the_picture_keeps_an_aspect_a_card_can_scale(concept: str) -> None:
    _, _, width, height = (float(v) for v in _root(render_pronoun_svg(concept)).get("viewBox").split())
    assert 0.75 <= width / height <= 2.0


@pytest.mark.parametrize("concept", PRONOUN_CONCEPTS)
def test_is_deterministic(concept: str) -> None:
    """The filename is the content hash; a jittered render re-stages forever."""
    assert render_pronoun_svg(concept) == render_pronoun_svg(concept)


@pytest.mark.parametrize("concept", ["sideways", "", "I", "we two", "we_two ", "they", "he ", "on", "čez"])
def test_refuses_a_concept_it_cannot_draw(concept: str) -> None:
    """An empty picture is indistinguishable from a render that failed, so a
    concept this module cannot draw is refused rather than approximated."""
    with pytest.raises(ValueError, match="renderable"):
        render_pronoun_svg(concept)


# ── Possessives: the same scene, and a bag in every lit pair of hands ──────
#
# A possessive is not a second cast and not a second layout. It is its
# nominative render with a bag added to every lit referent and nothing else
# changed — which is exactly what keeps Tagalog `siya` ("he/she") and `niya`
# ("his/her") two different words pointing at two different pictures, and the
# property worth testing is a DIFFERENCE rather than a geometry: strip the bags
# and the two renders must be byte-identical.

#: The bag palette the user approved, 2026-09-28. Held as a literal here rather
#: than imported from the renderer, because "the renderer agrees with itself" is
#: not what pins a colour — the first mockup's orange was 1.40:1 on the lit body
#: it hangs against, which is a number no structural test would have complained
#: about.
_BAG_FILL = "#ffd23f"
_BAG_STROKE = "#6b4a00"

#: A bag is two elements: the handle arc above it and the rounded rect. The rect
#: is the one with ``rx="4"`` (the outline rounds at 16, the bubble at 14); the
#: handle is the one path stroked at 2.4, a width nothing else in these pictures
#: uses. Both are stripped together — a bag missing its handle is still a bag.
_BAG_ELEMENT = re.compile(r'<rect [^>]*\brx="4"[^>]*/>|<path [^>]*\bstroke-width="2\.4"[^>]*/>')


def _strip_bags(svg: bytes) -> bytes:
    """*svg* with every bag element removed, and nothing else touched."""
    text = svg.decode()
    assert _BAG_ELEMENT.search(text), "this render has no bag to strip"
    return _BAG_ELEMENT.sub("", text).encode()


def _translate(svg: bytes) -> str:
    return next(el.get("transform") for el in _root(svg).iter() if el.tag == f"{_SVG}g" and el.get("transform"))


def _retarget(svg: bytes, other: bytes) -> bytes:
    """*svg* carrying the group translate *other* has."""
    mine, theirs = _translate(svg), _translate(other)
    assert svg.decode().count(mine) == 1, "one group, one translate"
    return svg.decode().replace(mine, theirs).encode()


def _hung_on(picture: Picture, bag: Bag) -> Figure | None:
    """The lit body *bag* hangs on, or ``None`` if it hangs on nobody.

    "On" is a POSITION, read off the two centres: a bag belongs to exactly one
    figure, and that figure is a filled one. A bag over a pale listener has no
    owner, which is a different card from "my".
    """
    hosts = [f for f in picture.figures if abs(f.cx - bag.cx) <= 1]
    return hosts[0] if len(hosts) == 1 and hosts[0].lit else None


def _in_front_of(bag: Bag, host: Figure) -> bool:
    """A carried bag is in front of the body, not hovering over the head."""
    return host.body_top <= bag.top and bag.bottom <= host.body_bottom


@pytest.mark.parametrize("concept", _POSSESSIVES)
def test_every_lit_referent_carries_exactly_one_bag(concept: str) -> None:
    """One bag per lit referent — the whole difference from the nominative scene.

    The count is read off the markup and compared with the transcribed number
    rather than with whatever the renderer happened to emit. WHICH figure holds
    it is a separate check (:func:`_hung_on`), because a bag on the wrong figure
    leaves the count right.
    """
    picture = _parse(render_pronoun_svg(concept))
    lit = [f for f in picture.figures if f.lit] + [c for c in picture.cats if c.lit]
    assert len(picture.bags) == len(lit) == _BAGS[concept[: -len("_poss")]]


@pytest.mark.parametrize("concept", [c for c in _POSSESSIVES if c != "it_poss"])
def test_each_bag_hangs_on_the_body_it_belongs_to(concept: str) -> None:
    """Centred on a LIT head, inside that body's own y-range, one bag per figure."""
    picture = _parse(render_pronoun_svg(concept))
    for bag in picture.bags:
        host = _hung_on(picture, bag)
        assert host is not None, f"a bag at x={bag.cx:.1f} hangs on no lit body"
        assert host.body_bottom == pytest.approx(picture.ground, abs=0.01), "a body stands on the ground"
        assert _in_front_of(bag, host), f"the bag at y={bag.top:.1f} is not in front of its body"


@pytest.mark.parametrize("concept", [c for c in _POSSESSIVES if c != "it_poss"])
def test_a_possessive_is_its_nominative_scene_with_bags_and_nothing_else(concept: str) -> None:
    """Strip the bags and the two renders are the same bytes.

    Derived by stripping, not by trusting the code path: a renderer that
    quietly re-laid-out the possessive scene would pass a test that only compared
    the two calls, and would show up here as a difference.
    """
    nominative = render_pronoun_svg(concept[: -len("_poss")])
    assert _strip_bags(render_pronoun_svg(concept)) == nominative
    assert _translate(render_pronoun_svg(concept)) == _translate(nominative)


def test_the_cats_possessive_moves_its_group_and_nothing_else() -> None:
    """The one scene that cannot be a bare addition, and its exception is proved.

    A bag wider than the cat stands beside it, so the group has to be centred a
    little wider — a different translate. Stripping the bags leaves exactly that
    and nothing else: unequal first, equal once the translate is put back.
    """
    possessive, nominative = render_pronoun_svg("it_poss"), render_pronoun_svg("it")
    stripped = _strip_bags(possessive)
    assert _translate(possessive) != _translate(nominative)
    assert stripped != nominative
    assert _retarget(stripped, nominative) == nominative


def test_the_cats_bag_stands_on_the_ground_clear_of_the_cat() -> None:
    """The one referent with no hands: the bag is ON the ground, to its right.

    Right, because the cat faces left into the conversation. Clear of every
    shape the cat is made of, so the two do not read as one object. The tail is
    not one of those shapes — it is a stroked line with no fill — which is
    recorded in ``Cat.filled`` rather than assumed here.
    """
    picture = _parse(render_pronoun_svg("it_poss"))
    assert len(picture.bags) == 1
    bag, cat = picture.bags[0], picture.cats[0]
    assert abs(bag.bottom - picture.ground) <= 0.5, (
        f"the bag's bottom is at y={bag.bottom}, the ground at {picture.ground}"
    )
    assert bag.left > cat.cx, "the bag is to the RIGHT of the cat"
    for left, right in cat.filled:
        assert bag.left > right or bag.right < left, f"the bag overlaps the cat's shape at x={left}..{right}"
    # And clear of the tail too, stroke included (width 5): an overlapping bag hides
    # the tail's tip and reads as the cat holding it (orchestrator audit, 2026-09-28).
    assert bag.left > cat.right + 2.5, f"the bag (left x={bag.left}) covers the cat's tail (reach x={cat.right})"


@pytest.mark.parametrize("concept", _POSSESSIVES)
def test_the_bag_is_painted_in_the_approved_palette(concept: str) -> None:
    """The user rejected the first mockup's orange; the two colours are pinned."""
    for bag in _parse(render_pronoun_svg(concept)).bags:
        assert (bag.fill, bag.stroke) == (_BAG_FILL, _BAG_STROKE)


@pytest.mark.parametrize("concept", _POSSESSIVES)
def test_every_bag_is_drawn_on_top_of_the_figures(concept: str) -> None:
    """A bag painted under a body would be a body wearing a bag, not carrying one."""
    group = next(el for el in _root(render_pronoun_svg(concept)).iter() if el.tag == f"{_SVG}g")
    children = list(group)
    bags = [i for i, el in enumerate(children) if el.tag == f"{_SVG}rect" and el.get("rx") == "4"]
    handles = [i for i, el in enumerate(children) if el.tag == f"{_SVG}path" and el.get("stroke-width") == "2.4"]
    heads = [i for i, el in enumerate(children) if el.tag == f"{_SVG}circle" and el.get("r") == "12"]
    assert len(bags) == len(handles), "every bag has its handle"
    assert min(bags) > max(heads), "every head is painted before every bag"
    assert min(handles) > max(heads), "and every handle with it"


# ── Contrast: the measured palette, recomputed from the markup ─────────────

#: The brief's measured WCAG ratios. Each is RE-COMPUTED from the fills and
#: strokes the SVG actually carries, held to >= 3.0, and additionally held to
#: within 0.01 of the figure measured before this renderer was written — a
#: palette drifting toward the background would clear a bare floor for a while.
#: The three "bag" rows are the possessive's own colours; a scene with no bags
#: has no such roles and is skipped, and the reachability test below is what
#: stops them from quietly never being measured at all.
_MEASURED: dict[tuple[str, str], float] = {
    ("lit", "bg"): 6.23,
    ("lit-stroke", "bg"): 9.80,
    ("line", "bg"): 3.85,
    ("line", "pale"): 3.50,
    ("accent", "bg"): 4.46,
    ("line", "bubble"): 4.08,
    ("bag", "lit"): 4.57,
    ("bag-stroke", "bg"): 7.60,
    ("bag-stroke", "bag"): 5.58,
}


def _palette(svg: bytes) -> dict[str, str]:
    """The palette roles actually present in this picture, off its attributes."""
    root = _root(svg)
    roles = {
        "bg": root[0].get("fill"),
        "bubble": next(el for el in root.iter() if el.tag == f"{_SVG}rect" and el.get("fill") == "#ffffff").get("fill"),
        "line": next(el for el in root.iter() if el.get("stroke-dasharray")).get("stroke"),
    }
    for head in (el for el in root.iter() if el.tag == f"{_SVG}circle" and el.get("fill") in (_LIT, _PALE)):
        lit = head.get("fill") == _LIT
        roles["lit" if lit else "pale"] = head.get("fill")
        roles["lit-stroke" if lit else "pale-stroke"] = head.get("stroke")
    marks = {el.get("stroke") for el in root.iter() if el.get("stroke") == _ACCENT}
    if marks:
        roles["accent"] = marks.pop()
    # The bag, found the same way the picture's own tests find it: by its shape.
    # A recoloured bag is still the bag, so the contrast check gets to see it.
    bag = next((el for el in root.iter() if el.tag == f"{_SVG}rect" and el.get("rx") == "4"), None)
    if bag is not None:
        roles["bag"] = bag.get("fill")
        roles["bag-stroke"] = bag.get("stroke")
    return roles


def _contrast_floor(svg: bytes) -> float:
    """The worst ratio among the pairs this picture actually uses."""
    roles = _palette(svg)
    return min(_contrast(roles[one], roles[two]) for one, two in _MEASURED if one in roles and two in roles)


@pytest.mark.parametrize("concept", PRONOUN_CONCEPTS)
def test_every_colour_pair_in_this_picture_is_legible(concept: str) -> None:
    """The pale figures are why this exists: at 1.10:1 on the background an
    unlit person is a shape nobody can see, which no structural test catches.
    The bag is why it grew: the first mockup's orange was 1.40:1 on the lit
    body it hangs against, and the user asked for better."""
    roles = _palette(render_pronoun_svg(concept))
    for (one, two), measured in _MEASURED.items():
        if one not in roles or two not in roles:
            continue
        ratio = _contrast(roles[one], roles[two])
        assert ratio >= 3.0, f"{concept}: {one} on {two} is only {ratio:.2f}:1"
        assert ratio == pytest.approx(measured, abs=0.01), f"{concept}: {one} on {two} drifted off its measurement"


def test_every_measured_pair_is_still_reachable_in_some_picture() -> None:
    """Otherwise a colour could be dropped and the floor test would never know."""
    present = {
        (one, two)
        for concept in PRONOUN_CONCEPTS
        for (one, two) in _MEASURED
        if {one, two} <= set(_palette(render_pronoun_svg(concept)))
    }
    assert present == set(_MEASURED)


def test_a_pale_person_and_the_outline_share_one_structural_colour() -> None:
    """``pale-stroke`` and ``line`` are the same colour wherever both appear, so
    the palette holds exactly one structural grey."""
    for concept in PRONOUN_CONCEPTS:
        roles = _palette(render_pronoun_svg(concept))
        if "pale-stroke" in roles:
            assert roles["pale-stroke"] == roles["line"]


def test_the_darker_head_fill_is_the_one_that_can_be_seen() -> None:
    """Which fill the checker calls "lit" is settled by legibility, not by name."""
    bg = _root(render_pronoun_svg("he"))[0].get("fill")
    assert _contrast(_LIT, bg) >= 3.0
    assert _contrast(_PALE, bg) < 2.0, "a pale head needs its outline, which is why the two are told apart"


# ── Discrimination: the reason the oracle table exists ─────────────────────
#
# Every pair of concepts with a different row must be told apart by their own
# row, or two words would share one picture. The same check is then run against
# MUTATED renders, so the checker is proven to discriminate rather than merely
# to agree with the renderer that produced its input.

_DIFFERENT = [(a, b) for a in PRONOUN_CONCEPTS for b in PRONOUN_CONCEPTS if a < b and _EXPECTED[a] != _EXPECTED[b]]
_SAME = [(a, b) for a in PRONOUN_CONCEPTS for b in PRONOUN_CONCEPTS if a < b and _EXPECTED[a] == _EXPECTED[b]]


@pytest.mark.parametrize(("first", "second"), _DIFFERENT)
def test_neighbour_concepts_are_told_apart_by_their_own_row(first: str, second: str) -> None:
    picture = _parse(render_pronoun_svg(first))
    assert _matches(picture, _EXPECTED[first])
    assert not _matches(picture, _EXPECTED[second]), f"the {first} render also shows {second}"


def test_the_only_duplicate_rows_are_the_deliberate_ones() -> None:
    """Otherwise the list above could be vacuous — ``we_two``/``we_incl`` and
    their possessives are the pairs the drawing is *supposed* to collapse.

    Compared unordered: the list above walks the concepts in render order and so
    hands the two members back the other way round from the table above.
    """
    assert {frozenset(pair) for pair in _SAME} == {frozenset(pair) for pair in _COLLAPSED}
    assert len(_SAME) == len(_COLLAPSED)


# ── The probes: each checker, shown to reject a real near-miss ─────────────


def _mutate(svg: bytes, old: str, new: str) -> bytes:
    """A render with one piece of its own markup replaced."""
    assert svg.count(old.encode()) == 1, f"{old!r} is not in this render exactly once"
    return svg.replace(old.encode(), new.encode())


def _own(svg: bytes, cx: float) -> int:
    """A figure's x in the render's own coordinates, before the group translate."""
    return round(cx - _dx(svg))


def _person_markup(cx: int, fill: str, stroke: str) -> str:
    """A whole person, in the markup the renderer emits, for splicing."""
    return (
        f'<path d="M{cx - 12} 198 V146 a12 12 0 0 1 24 0 V198 Z" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="2"/>'
        f'<circle cx="{cx}" cy="119" r="12" fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
    )


#: The gender mark's path ELEMENT, which is how the swap probe carries one mark
#: into another's render: the two marks differ in their stroke data AND in the
#: ``stroke-linecap`` the diagonal brings with it, so swapping only ``d`` would
#: leave a mark that is neither.
_MARK_ELEMENT = re.compile(rf'<path d="[^"]*" stroke="{re.escape(_ACCENT)}"[^>]*/>')


def _bag_markup(cx: int, top: int) -> str:
    """A whole bag, in the markup the renderer emits, for splicing.

    Written out rather than captured from a render, so a probe says where the
    bag is instead of asserting whatever the builder happened to put there. The
    handle is a wide, low tote arc (12 x 7 over a 3-unit stem): the mockup's narrow
    6-radius shackle read as a PADLOCK once a gender mark sat in the bag like a
    keyhole (orchestrator audit, 2026-09-28). It is written out here for the same
    reason as before: a checker that read its expectations out of the renderer
    would pass a renderer that grew a third unit of stem.
    """
    return (
        f'<path d="M{cx - 12} {top} v-3 a12 7 0 0 1 24 0 v3" fill="none" '
        f'stroke="{_BAG_STROKE}" stroke-width="2.4"/>'
        f'<rect x="{cx - 14}" y="{top}" width="28" height="26" rx="4" fill="{_BAG_FILL}" '
        f'stroke="{_BAG_STROKE}" stroke-width="2"/>'
    )


def _own_bag(svg: bytes, bag: Bag) -> tuple[int, int]:
    """A bag's centre and top in the render's own coordinates, for splicing."""
    return round(bag.cx - _dx(svg)), round(bag.top)


def _mark_path(svg: bytes) -> str:
    """The whole gender-mark path element, as it appears in the render's bytes."""
    found = _MARK_ELEMENT.findall(svg.decode())
    assert len(found) == 1, "one gender mark, drawn once"
    return found[0]


def test_the_checker_rejects_a_referent_moved_inside_the_outline() -> None:
    """``we_excl`` drawn as if its other were in the conversation.

    Which side of the outline a figure stands on is the whole difference
    between "we, and not them" and "we", so widening the outline is exactly
    the near-miss a reader would miss.
    """
    svg = render_pronoun_svg("we_excl")
    width = f'width="{_parse(svg).panel[1] - _dx(svg):g}"'
    mutated = _mutate(svg, width, f'width="{_parse(svg).panel[1] - _dx(svg) + 60:g}"')
    before, after = _row(_parse(svg)), _row(_parse(mutated))
    assert (before["outside"], before["listeners"]) == (1, 1)
    assert (after["outside"], after["listeners"]) == (0, 2)
    assert not _matches(_parse(mutated), _EXPECTED["we_excl"])
    assert not _matches(_parse(mutated), _EXPECTED["we_incl"])


def test_the_checker_rejects_a_lit_referent_drawn_pale() -> None:
    """``we_excl`` with its other no longer lit: the referent has gone."""
    svg = render_pronoun_svg("we_excl")
    other = _own(svg, _outside(_parse(svg))[0].cx)
    head = f'cx="{other}" cy="119" r="12" fill="{_LIT}" stroke='
    mutated = _mutate(svg, head, f'cx="{other}" cy="119" r="12" fill="{_PALE}" stroke=')
    assert _row(_parse(mutated))["outside_lit"] == 0
    assert not _matches(_parse(mutated), _EXPECTED["we_excl"])


def test_the_checker_rejects_a_pale_person_with_no_outline() -> None:
    """The legibility failure, structurally perfect: 1.10:1 on the background.

    Only the stroke is touched, so every count in the row still holds and the
    picture is rejected for being invisible rather than for being wrong.
    """
    svg = render_pronoun_svg("you_one")
    pale = [f for f in _inside(_parse(svg)) if not f.lit][-1]
    head = f'cx="{_own(svg, pale.cx)}" cy="119" r="12" fill="{_PALE}" stroke="'
    mutated = _mutate(svg, head, f'{head}none" data-was="')
    after = _parse(mutated)
    assert _row(after)["listeners_lit"] == 1, "only the outline changed, not who is lit"
    assert _row(after)["pale_outlined"] is False
    assert not _matches(after, _EXPECTED["you_one"])


def test_the_checker_rejects_a_symbol_over_the_wrong_figure() -> None:
    """``he`` with the mark moved onto the listener: inside, pale, not a referent."""
    svg = render_pronoun_svg("he")
    listener = _own(svg, _inside(_parse(svg))[0].cx)
    other = _own(svg, _outside(_parse(svg))[0].cx)
    mutated = _mutate(svg, f'cx="{other}" cy="91"', f'cx="{listener}" cy="91"')
    assert _row(_parse(mutated))["symbol"] is None
    assert not _matches(_parse(mutated), _EXPECTED["he"])


def test_a_female_mark_swapped_into_he_turns_the_render_into_she() -> None:
    """Same circle, same host, same everything else — only the stroke direction.

    The two scenes have identical geometry, so substituting one mark's path for
    the other's reproduces the other concept byte for byte. That is the
    strongest form of the probe: the checker is not choosing between two similar
    pictures, it is reading the one thing that differs.
    """
    he, she = render_pronoun_svg("he"), render_pronoun_svg("she")
    assert _mark_path(he) != _mark_path(she)
    swapped = _mutate(he, _mark_path(he), _mark_path(she))
    assert swapped == she
    assert _row(_parse(swapped))["symbol"] == "f"
    assert not _matches(_parse(swapped), _EXPECTED["he"])


def test_a_male_mark_swapped_into_she_turns_the_render_into_he() -> None:
    """The other direction, so neither mark is a special case in the checker."""
    she, he = render_pronoun_svg("she"), render_pronoun_svg("he")
    swapped = _mutate(she, _mark_path(she), _mark_path(he))
    assert swapped == he
    assert _row(_parse(swapped))["symbol"] == "m"
    assert not _matches(_parse(swapped), _EXPECTED["she"])


def test_one_listener_too_many_is_exactly_you_many() -> None:
    """The count is the whole of "the two of you", so a third is a different word."""
    svg = render_pronoun_svg("you_two")
    picture = _parse(svg)
    right = _own(svg, _inside(picture)[-1].cx)
    person = _person_markup(right, _LIT, _inside(picture)[-1].stroke)
    width = f'width="{picture.panel[1] - _dx(svg):g}"'
    text = svg.decode()
    assert text.count(person) == 1 and text.count(width) == 1
    mutated = (
        text.replace(person, person + _person_markup(right + 30, _LIT, _inside(picture)[-1].stroke))
        .replace(width, f'width="{picture.panel[1] - _dx(svg) + 30:g}"')
        .encode()
    )
    after = _parse(mutated)
    assert _row(after)["listeners"] == 3
    assert not _matches(after, _EXPECTED["you_two"])
    assert _matches(after, _EXPECTED["you_many"])


def test_the_checker_rejects_a_cat_mistaken_for_a_listener() -> None:
    """The probe the brief calls out: give the cat a person's body.

    The cat is excluded from the person count because its body is drawn with
    curves rather than an arc, so pasting an arc-bodied path in front of its
    head makes the checker report a person. Had it still reported a cat, the
    exclusion would be doing nothing and ``it`` would be three people.
    """
    svg = render_pronoun_svg("it")
    head = f'<circle cx="{_own(svg, _parse(svg).cats[0].cx)}" cy="148" r="12"'
    impostor = f'<path d="M9 198 V146 a12 12 0 0 1 24 0 V198 Z" fill="{_LIT}" stroke="{_LIT}" stroke-width="2"/>'
    mutated = _mutate(svg, head, impostor + head)
    after = _row(_parse(mutated))
    assert after["cats"] == 0
    assert after["outside"] == 1
    assert not _matches(_parse(mutated), _EXPECTED["it"])


# ── The bag probes: the same five near-misses, in the possessive half ──────
#
# Each of these is a mutation a renderer could plausibly ship and a learner
# would have no way to report: a bag in the wrong hands, a missing bag, a bag
# that vanishes into the body it hangs on, a bag floating where nothing is, and
# the palette the user already rejected once. Every one is a real render's own
# markup, spliced.


def test_the_checker_rejects_a_bag_handed_to_somebody_it_does_not_belong_to() -> None:
    """``i_poss`` with the bag passed to the pale listener: "his", not "my".

    The count is untouched — one bag, one lit referent — so only the OWNERSHIP
    check can catch it, which is the point of reading the position.
    """
    svg = render_pronoun_svg("i_poss")
    picture = _parse(svg)
    listener = next(f for f in picture.figures if not f.lit)
    assert _hung_on(picture, picture.bags[0]) is not None
    mutated = _mutate(svg, _bag_markup(*_own_bag(svg, picture.bags[0])), _bag_markup(_own(svg, listener.cx), 162))
    after = _parse(mutated)
    assert _row(after)["bags"] == 1, "the count still holds; only who holds it changed"
    assert _hung_on(after, after.bags[0]) is None
    assert _hung_on(picture, picture.bags[0]) is not None, "and the real render's bag does have an owner"
    # The oracle ROW is deliberately not asked to catch this one: it counts bags,
    # and a bag in the wrong hands is still one bag. Ownership is a position, so
    # it is a predicate of its own — and this probe is the reason it exists.


def test_the_checker_rejects_a_lit_referent_carrying_no_bag() -> None:
    """``they_many_poss`` with one bag gone: "their", short one referent.

    The scene is untouched — three lit others, as before — so nothing but the
    count can see it.
    """
    svg = render_pronoun_svg("they_many_poss")
    picture = _parse(svg)
    assert (len(picture.bags), len([f for f in picture.figures if f.lit])) == (3, 3)
    mutated = _mutate(svg, _bag_markup(*_own_bag(svg, picture.bags[-1])), "")
    after = _parse(mutated)
    assert _row(after)["bags"] == 2
    assert len([f for f in after.figures if f.lit]) == 3, "only a bag is missing, not a referent"
    assert not _matches(after, _EXPECTED["they_many_poss"])


def test_the_contrast_check_rejects_the_bag_the_user_already_rejected() -> None:
    """The first mockup's orange, back again: 1.40:1 on the body it hangs on.

    The bag is still a bag, so it is still found — a checker that looked for the
    approved fill would report no bags and wave this through.
    """
    svg = render_pronoun_svg("i_poss")
    assert _contrast_floor(svg) >= 3.0
    mutated = _mutate(svg, f'fill="{_BAG_FILL}"', 'fill="#b8572f"')
    after = _parse(mutated)
    assert len(after.bags) == 1, "a recoloured bag is a wrong bag, not no bag"
    assert _contrast(_palette(mutated)["bag"], _LIT) == pytest.approx(1.40, abs=0.01)
    assert _contrast_floor(mutated) < 3.0


def test_the_checker_rejects_a_bag_hovering_above_the_head() -> None:
    """A bag carried at head height: still on the right figure, still one bag.

    Ownership survives the move, so the check that has to catch it is the one
    that reads the bag's y-range against the body's — which is why that is a
    separate predicate and not a consequence of the x-match.
    """
    svg = render_pronoun_svg("i_poss")
    cx, _ = _own_bag(svg, _parse(svg).bags[0])
    assert _in_front_of(_parse(svg).bags[0], _hung_on(_parse(svg), _parse(svg).bags[0]))
    mutated = _mutate(svg, _bag_markup(cx, 162), _bag_markup(cx, 104))
    after = _parse(mutated)
    host = _hung_on(after, after.bags[0])
    assert host is not None, "it is still on the lit figure; only its height moved"
    assert host.body_top > after.bags[0].bottom
    assert not _in_front_of(after.bags[0], host)


def test_the_checker_rejects_the_cats_bag_floating_off_the_ground() -> None:
    """``it_poss`` with the bag at carrying height instead of standing height.

    Still right of the cat and still clear of it, so again only the ground check
    can see it.
    """
    svg = render_pronoun_svg("it_poss")
    cx, top = _own_bag(svg, _parse(svg).bags[0])
    assert top == 172
    mutated = _mutate(svg, _bag_markup(cx, top), _bag_markup(cx, top - 26))
    after = _parse(mutated)
    assert after.bags[0].left > after.cats[0].cx, "it is still to the right of the cat"
    assert abs(after.bags[0].bottom - after.ground) > 0.5


# ── Part C: the gender of the OWNED thing, as a mark inside the bag ─────────
#
# A Norwegian possessive says more than its nominative does: ``min`` is "my",
# ``mi`` "my" of a feminine thing, ``mitt`` of a neuter one and ``mine`` of a
# plural — four words on one picture family, and the only thing that tells them
# apart is a mark drawn INSIDE the bag (tunatale-l1ba). It is drawn in the bag's
# own stroke colour rather than the accent on purpose: a referent's own gender
# mark is drawn in the accent, above a head, so the same glyph in both places
# would read as one fact about one person — which is the mistake the whole split
# exists to prevent.
#
# The marks are read the way the referent marks are: by the DIRECTION of the
# first stroke and by the presence of a horizontal subpath, never by an id. ♀ and
# ⚲ are the pair that forces it — the same ring, the same vertical stem, and one
# crossbar between them — so a checker that keyed on the ring alone would call a
# neuter thing feminine, and agree with a table written to match.

#: SHA-256 (truncated) of every render as it stood BEFORE this half of the work,
#: read off ``fcfb879`` (Part A, tunatale-uy38). A pin, not a measurement, and for
#: the same reason as ``_BASE_RENDERS_AT_HEAD`` above: a property test cannot see
#: a picture that has moved, and adding an argument to a renderer is exactly the
#: change that could move it by accident. The ``_poss`` rows were re-pinned
#: 2026-09-29 when the bag and its mark grew about 1.45x (the user's pick of the
#: marks being too small to read), bag width held to 28; every nominative row
#: is untouched.
_OWNED_NONE_AT_HEAD: dict[str, str] = {
    "i": "9c40bf84971d",
    "you_one": "0f2d8e5294b5",
    "he": "be9ebb5ad3df",
    "she": "3d9f884dc58c",
    "third_one": "9f9af7a1d14c",
    "it": "c2938fdf9209",
    "we_two": "953517cafe4a",
    "we_many": "83be6f263a7a",
    "we_incl": "953517cafe4a",
    "we_excl": "6057b23d5aa4",
    "you_two": "e2f4c1df78c3",
    "you_many": "fbff1be152f8",
    "they_two": "7570f97e263c",
    "they_many": "5df66a7f9448",
    "i_poss": "7862b3800699",
    "you_one_poss": "1ddd51f36731",
    "he_poss": "e28cb4caf173",
    "she_poss": "75ec093cc41b",
    "third_one_poss": "4689b7dc0ed5",
    "it_poss": "dfa6f3d55844",
    "we_two_poss": "ec9146800005",
    "we_many_poss": "55922d07cce3",
    "we_incl_poss": "ec9146800005",
    "we_excl_poss": "98e5b7275976",
    "you_two_poss": "c8524e2d5c4d",
    "you_many_poss": "b71399c27af9",
    "they_two_poss": "cc58f3361bb0",
    "they_many_poss": "2b8f5dadc860",
}

#: The gender of the owned thing, as the two things a checker can read off the
#: markup: the direction of its first stroke, and whether it carries a crossbar.
#: The KIND is what the render is asked for; the pair is what comes back, and the
#: two tables have to agree on every row.
_OWNED_MARK: dict[str, tuple[str, bool]] = {
    "m": ("up_right", False),  # ♂ — a stroke up and to the right, with an arrowhead
    "f": ("down", True),  # ♀ — a stroke down, CROSSED
    "n": ("down", False),  # ⚲ — a stroke down, NOT crossed
}

#: A plural owned thing is a SECOND bag, behind the first and up to the right:
#: two shapes where the singular has one, and no mark at all, because a plural is
#: not a gender. Transcribed from the brief's geometry rather than derived — the
#: offset is a drawing decision, and a renderer that computed a "nicer" one would
#: pass every other check in this section.
_OWNED_RADIUS = 5
_OWNED_STROKE_WIDTH = "2.2"
_PLURAL = "pl"
_PLURAL_DX = 9
_PLURAL_DY = -7
_OWNED_KINDS = ("m", "f", "n", _PLURAL)
_OWNED_GENDERS = ("m", "f", "n")


class BagMark(NamedTuple):
    """The mark inside a bag, read off the markup and placed in viewBox units."""

    cx: float
    cy: float
    r: float
    stroke: str
    width: str
    direction: str  # the direction of the FIRST stroke: "up_right" | "down" | ""
    crossed: bool  # a horizontal subpath of its own — the ♀/⚲ difference


def _bag_marks(svg: bytes) -> list[BagMark]:
    """Every mark drawn inside a bag, found by its own radius and stroke width.

    Each mark is a ring and a set of strokes, and the strokes START on the ring —
    so the pair is made geometrically rather than by document order, the same
    rule this file already applies to a referent's mark. A mark whose ring had
    been slid away from its strokes would otherwise be read as two marks.

    The tolerance is 0.1 rather than a hair because the ♂ arrow begins at the
    ring's CORNER — the 3.5/3.5 offset is the chord point, 4.95 from the centre
    against a 5 ring — so the association is a neighbourhood and not an equality.
    It is still far tighter than the 30 units between two neighbouring bags'
    centres, which is what makes it a check.
    """
    root = _root(svg)
    dx = _dx(svg)
    rings = [el for el in root.iter() if el.tag == f"{_SVG}circle" and _num(el, "r") == _OWNED_RADIUS]
    strokes = [el for el in root.iter() if el.tag == f"{_SVG}path" and el.get("stroke-width") == _OWNED_STROKE_WIDTH]
    marks: list[BagMark] = []
    for stroke in strokes:
        subpaths, _ = _paths(stroke.get("d"))
        (sx, sy), (ex, ey) = subpaths[0][0], subpaths[0][1]
        # Compared in the GROUP's own coordinates — a path's `d` is written where
        # the element sits inside the translate, which is where the ring's `cx` is
        # written too. Adding the translate to one side and not the other is a
        # mismatch of 87 units, not of a hair.
        hosts = [
            el
            for el in rings
            if abs(((_num(el, "cx") - sx) ** 2 + (_num(el, "cy") - sy) ** 2) ** 0.5 - _OWNED_RADIUS) <= 0.1
        ]
        assert len(hosts) == 1, "a bag mark's strokes start on its own ring"
        ring = hosts[0]
        # A crossbar is a horizontal segment that is a WHOLE subpath: the arrowhead
        # of ♂ is horizontal for one step and is not a crossbar, and ♂ is told
        # apart by its direction anyway.
        crossed = any(
            len(part) == 2 and abs(part[0][1] - part[1][1]) < 1e-6 and abs(part[0][0] - part[1][0]) > 1e-6
            for part in subpaths
        )
        direction = ""
        if abs(ex - sx) < 1e-6 and ey - sy > 0:
            direction = "down"
        elif ex - sx > 0 and ey - sy < 0:
            direction = "up_right"
        marks.append(
            BagMark(
                cx=dx + _num(ring, "cx"),
                cy=_num(ring, "cy"),
                r=_OWNED_RADIUS,
                stroke=stroke.get("stroke"),
                width=stroke.get("stroke-width"),
                direction=direction,
                crossed=crossed,
            )
        )
    return marks


def _held_by(picture: Picture, mark: BagMark) -> Bag | None:
    """The one bag *mark* sits inside, or ``None`` if it sits in none of them.

    "Inside" is the ring's CENTRE strictly within the bag's own rectangle — the
    mark is a small glyph, and a glyph whose centre is outside the bag is a
    mark somewhere else entirely.
    """
    hosts = [bag for bag in picture.bags if bag.left < mark.cx < bag.right and bag.top < mark.cy < bag.bottom]
    return hosts[0] if len(hosts) == 1 else None


def _accented_from_the_bags_onward(svg: bytes) -> list[str]:
    """Which elements from the first bag onwards are stroked in the accent colour.

    Scoped to the tail of the group because ``he_poss``/``she_poss`` legitimately
    carry an accent-stroked mark over a head — the thing this must not collide
    with is the SAME colour on the SAME picture, inside the bag.
    """
    group = next(el for el in _root(svg).iter() if el.tag == f"{_SVG}g")
    children = list(group)
    start = next(i for i, el in enumerate(children) if el.get("stroke-width") == "2.4")
    return [el.tag for el in children[start:] if el.get("stroke") == _ACCENT]


@pytest.mark.parametrize("concept", PRONOUN_CONCEPTS)
def test_asking_for_no_gender_draws_exactly_the_picture_it_drew_before(concept: str) -> None:
    """All twenty-eight renders, byte for byte, with the new argument unused.

    The parameter is a keyword with a default, so the ordinary call is unchanged
    textually — which is exactly why only the bytes can say it. Twenty-eight
    cards already exist in the deck and the file name of each is its content
    hash, so a render that moved would stage a second copy of a picture nobody
    asked to change.
    """
    assert _digest(render_pronoun_svg(concept)) == _OWNED_NONE_AT_HEAD[concept]
    assert _digest(render_pronoun_svg(concept, owned=None)) == _OWNED_NONE_AT_HEAD[concept]


@pytest.mark.parametrize("concept", _POSSESSIVES)
@pytest.mark.parametrize("owned", _OWNED_GENDERS)
def test_every_bag_carries_the_gender_of_the_thing_in_it(concept: str, owned: str) -> None:
    """One mark per bag, inside it, in the bag's own colour, saying what it must.

    The count is against the transcribed number of lit referents, not against
    whatever the renderer emitted, and it runs over the cat's ground-standing bag
    as well as the carried ones — a mark that fitted a hanging bag and not a
    standing one would be a mark missing from one card.
    """
    svg = render_pronoun_svg(concept, owned)
    picture = _parse(svg)
    marks = _bag_marks(svg)
    assert len(marks) == len(picture.bags) == _BAGS[concept[: -len("_poss")]]
    for mark in marks:
        assert (mark.direction, mark.crossed) == _OWNED_MARK[owned], f"{concept}:{owned} drew {mark.direction}"
        assert (mark.stroke, mark.width) == (_BAG_STROKE, _OWNED_STROKE_WIDTH)
        assert _held_by(picture, mark) is not None, f"the mark at {mark.cx:.1f},{mark.cy:.1f} is in no bag"


@pytest.mark.parametrize("concept", _POSSESSIVES)
def test_a_gender_mark_is_painted_over_the_bag_it_is_in(concept: str) -> None:
    """A mark drawn under the bag body is a mark nobody can see: the body is opaque.

    Per mark rather than for the picture as a whole, because with several lit
    referents the body of the SECOND bag is painted after the first bag's mark —
    so "every mark comes after every body" would be false of a correct render.
    What has to hold is the local claim: the mark is inside one bag's x-range, and
    it is painted after THAT body.
    """
    svg = render_pronoun_svg(concept, "f")
    group = next(el for el in _root(svg).iter() if el.tag == f"{_SVG}g")
    children = list(group)
    rings = [i for i, el in enumerate(children) if el.tag == f"{_SVG}circle" and _num(el, "r") == _OWNED_RADIUS]
    bodies = [
        (i, _num(el, "x"), _num(el, "x") + _num(el, "width"))
        for i, el in enumerate(children)
        if el.tag == f"{_SVG}rect" and el.get("rx") == "4"
    ]
    assert len(rings) == len(bodies) > 0
    for ring in rings:
        cx = _num(children[ring], "cx")
        hosts = [i for i, left, right in bodies if left < cx < right]
        assert len(hosts) == 1, "a mark is over exactly one bag"
        assert hosts[0] < ring, "and it is painted after that bag's body"


@pytest.mark.parametrize("concept", _POSSESSIVES)
def test_the_three_genders_are_three_different_marks(concept: str) -> None:
    """The property, not three examples: no two of ♂ ♀ ⚲ read the same.

    ♀ and ⚲ share a ring, a radius and a vertical stem, so "they are different
    marks" is only true if the CROSSBAR is part of what a checker reads — which is
    what this asserts, for every scene rather than for one hand-picked bag.
    """
    read = {}
    for owned in _OWNED_GENDERS:
        marks = _bag_marks(render_pronoun_svg(concept, owned))
        read[owned] = {(m.direction, m.crossed) for m in marks}
        assert read[owned] == {_OWNED_MARK[owned]}
    assert len(set().union(*read.values())) == 3, "two of the three genders read alike"


@pytest.mark.parametrize("concept", _POSSESSIVES)
@pytest.mark.parametrize("owned", (None, *_OWNED_GENDERS))
def test_neighbouring_referents_bags_do_not_touch(concept: str, owned: str | None) -> None:
    """Each lit referent's bag stands clear of the next one's.

    The three-person scenes stand their people 30 units apart, so a bag grown
    wider than that runs into its neighbour, and a row of joined bags reads as
    one bundle — or as the plural's deliberate second bag. Found when the bag
    grew for legible marks (2026-09-29): nothing else in this file measures the
    space BETWEEN bags. The plural is left out on purpose; its overlap is the
    design.
    """
    bags = sorted(_parse(render_pronoun_svg(concept, owned)).bags, key=lambda bag: bag.left)
    for left_bag, right_bag in zip(bags, bags[1:], strict=False):
        assert right_bag.left - left_bag.right >= 2, f"bags at x={left_bag.cx} and x={right_bag.cx} touch"


@pytest.mark.parametrize("concept", _POSSESSIVES)
def test_a_plural_owned_thing_is_a_second_bag_behind_the_first(concept: str) -> None:
    """Two bags per lit referent, the back one up and to the right, and no mark.

    "Behind" is DOCUMENT ORDER: the bags are read off the group in the order they
    were emitted, so a back bag that came second would be a front bag paired
    with a front bag and this offset check would fail. A plural carries no mark
    because a plural is not a gender, and a mark on it would be a fact about
    nothing.
    """
    svg = render_pronoun_svg(concept, _PLURAL)
    picture = _parse(svg)
    lit = _BAGS[concept[: -len("_poss")]]
    assert len(picture.bags) == 2 * lit
    assert _bag_marks(svg) == [], "a plural is not a gender"
    for index in range(0, len(picture.bags), 2):
        back, front = picture.bags[index], picture.bags[index + 1]
        assert back.left - front.left == _PLURAL_DX, f"bag {index} is not {_PLURAL_DX} to the right of its pair"
        assert back.top - front.top == _PLURAL_DY, f"bag {index} is not {_PLURAL_DY} above its pair"


@pytest.mark.parametrize("concept", _POSSESSIVES)
@pytest.mark.parametrize("owned", _OWNED_KINDS)
def test_nothing_in_the_bag_is_drawn_in_the_referent_mark_colour(concept: str, owned: str) -> None:
    """The bag's mark is in ``BAG_STROKE``; a head's is in ``_ACCENT``.

    Not a style note: the same glyph in the accent above a head is how these
    pictures say *she*, so an accent mark inside a bag would be read as the same
    fact said twice about one person rather than as the gender of a house.
    """
    assert _accented_from_the_bags_onward(render_pronoun_svg(concept, owned)) == []


@pytest.mark.parametrize("concept", _POSSESSIVES)
@pytest.mark.parametrize("owned", _OWNED_KINDS)
def test_a_gendered_bag_is_still_centred_in_the_frame(concept: str, owned: str) -> None:
    """A second bag up and to the right widens the cast, so the cast moves.

    Every other render in this file is held to this, and an owned render is a
    card like any other — including the cat's, whose ground-standing bag is the
    one thing that already widened its scene.
    """
    picture = _parse(render_pronoun_svg(concept, owned))
    left, right = picture.extent
    assert abs((left + right) / 2 - picture.width / 2) <= 6


@pytest.mark.parametrize("owned", _OWNED_KINDS)
@pytest.mark.parametrize("concept", _NOMINATIVES)
def test_refuses_a_gender_for_a_thing_that_is_not_owned(concept: str, owned: str) -> None:
    """A nominative scene has no bag to put a mark in, so it is refused.

    ``sin/si/sitt`` are the words this protects: they are the REFLEXIVE ("his
    own"), they have no clean scene, and the cheapest way to give them one would
    have been to mark a bag on a scene that carries no bag.
    """
    with pytest.raises(ValueError, match="owned"):
        render_pronoun_svg(concept, owned)


@pytest.mark.parametrize("owned", ["x", "M", "", "plural", "f:m", ":m", "n:"])
def test_refuses_a_gender_it_cannot_draw(owned: str) -> None:
    """An unknown kind is refused rather than approximated with no mark at all.

    A typo in a data file that drew the same bag it always drew would be a
    picture confidently showing the wrong word — the same argument as refusing an
    unrenderable concept.
    """
    with pytest.raises(ValueError, match="owned"):
        render_pronoun_svg("i_poss", owned)


def test_an_unknown_concept_is_still_refused_before_the_gender_is_read() -> None:
    """The concept check comes first, so the refusal still says what it is."""
    with pytest.raises(ValueError, match="renderable"):
        render_pronoun_svg("sideways", "m")


# ── The owned-mark probes: each checker, shown to reject a real near-miss ────
#
# The same five near-misses the brief names, spliced into real renders. Three of
# them are the crossbar in one direction or the other, which is the whole reason
# the mark is read by direction and not by identity.

#: The three marks as they appear in ``i_poss``, whose single bag is centred at
#: x=24 with its top at y=162 in the render's own coordinates. Written out
#: literally, for the reason :func:`_bag_markup` is: a probe that built its
#: expectations out of the renderer would agree with whatever it fed it.
_OWN_MARKUP: dict[str, str] = {
    "m": (
        '<circle cx="21" cy="178" r="5" fill="none" stroke="#6b4a00" stroke-width="2.2"/>'
        '<path d="M24.5 174.5 L30 169 h-4 m4 0 v4" fill="none" stroke="#6b4a00" stroke-width="2.2"/>'
    ),
    "f": (
        '<circle cx="24" cy="172" r="5" fill="none" stroke="#6b4a00" stroke-width="2.2"/>'
        '<path d="M24 177 V185 M20.5 181.5 H27.5" fill="none" stroke="#6b4a00" stroke-width="2.2"/>'
    ),
    "n": (
        '<circle cx="24" cy="172" r="5" fill="none" stroke="#6b4a00" stroke-width="2.2"/>'
        '<path d="M24 177 V185" fill="none" stroke="#6b4a00" stroke-width="2.2"/>'
    ),
}
#: The same three, forty units up — clear of the bag and its handle, still on its
#: own ring, so the "inside the bag" check is the only one that can see it.
_OWN_MARKUP_OUTSIDE = (
    '<circle cx="24" cy="132" r="5" fill="none" stroke="#6b4a00" stroke-width="2.2"/>'
    '<path d="M24 137 V145 M20.5 141.5 H27.5" fill="none" stroke="#6b4a00" stroke-width="2.2"/>'
)
#: And the feminine mark again, in the colour a referent's own gender uses. The
#: shape is untouched, so the ring and the strokes are still a mark — found by
#: radius and stroke width, neither of which says anything about colour.
_OWN_MARKUP_ACCENT = _OWN_MARKUP["f"].replace(_BAG_STROKE, _ACCENT)


def test_the_neuter_mark_given_a_crossbar_reads_as_the_feminine_one() -> None:
    """``mitt`` drawn as ``mi``: the one mistake that teaches the wrong gender.

    The two marks are the same ring at the same place and differ ONLY in the
    crossbar, so this is the checker reading the single thing that differs — not
    choosing between two pictures that merely resemble each other.
    """
    svg = render_pronoun_svg("i_poss", "n")
    assert _OWN_MARKUP["n"] in svg.decode() and _OWN_MARKUP["f"] not in svg.decode()
    swapped = _mutate(svg, _OWN_MARKUP["n"], _OWN_MARKUP["f"])
    after = _bag_marks(swapped)
    assert [(m.direction, m.crossed) for m in after] == [_OWNED_MARK["f"]]
    assert _held_by(_parse(swapped), after[0]) is not None, "it is still the same bag; only the gender changed"


def test_the_feminine_mark_losing_its_crossbar_reads_as_the_neuter_one() -> None:
    """The other direction, so neither mark is a special case in the checker."""
    svg = render_pronoun_svg("i_poss", "f")
    swapped = _mutate(svg, _OWN_MARKUP["f"], _OWN_MARKUP["n"])
    after = _bag_marks(swapped)
    assert [(m.direction, m.crossed) for m in after] == [_OWNED_MARK["n"]]


def test_the_checker_rejects_a_bag_mark_drawn_in_the_referent_mark_colour() -> None:
    """The mark is still a mark, so it is still found — and still refused.

    Keyed on the stroke WIDTH rather than the colour, deliberately: a mark in the
    accent is the wrong mark, not no mark, and a reader that looked for the
    approved colour would report an empty bag and wave this through.
    """
    svg = render_pronoun_svg("i_poss", "f")
    assert _accented_from_the_bags_onward(svg) == []
    mutated = _mutate(svg, _OWN_MARKUP["f"], _OWN_MARKUP_ACCENT)
    after = _bag_marks(mutated)
    assert len(after) == 1, "a recoloured mark is a wrong mark, not no mark"
    assert (after[0].stroke, after[0].width) != (_BAG_STROKE, _OWNED_STROKE_WIDTH)
    assert _accented_from_the_bags_onward(mutated) != []


def test_the_checker_rejects_a_bag_mark_outside_the_bag() -> None:
    """``mi`` marked on the person instead of in the bag: the same fact twice.

    Ownership and containment both survive the move except containment, which is
    why it is its own predicate rather than a consequence of the bag count.
    """
    svg = render_pronoun_svg("i_poss", "f")
    picture = _parse(svg)
    assert _held_by(picture, _bag_marks(svg)[0]) is not None
    mutated = _mutate(svg, _OWN_MARKUP["f"], _OWN_MARKUP_OUTSIDE)
    after = _parse(mutated)
    assert len(after.bags) == len(_bag_marks(mutated)) == 1, "nothing was lost but its place"
    assert _held_by(after, _bag_marks(mutated)[0]) is None


def test_the_checker_rejects_a_lit_referent_carrying_one_bag_of_a_plural() -> None:
    """``they_many_poss:pl`` with a back bag gone: "their" rather than "theirs'".

    The count is the only thing that can see it — the remaining bags are all
    correctly placed, so the pairing check passes on the two bags left over and
    fails on the count.
    """
    svg = render_pronoun_svg("they_many_poss", _PLURAL)
    picture = _parse(svg)
    lit = len([f for f in picture.figures if f.lit])
    assert (len(picture.bags), lit) == (2 * lit, 3)
    own = [_own_bag(svg, bag) for bag in picture.bags]
    back, front = own[0], own[1]
    mutated = _mutate(svg, _bag_markup(*back), "")
    after = _parse(mutated)
    assert len(after.bags) == 2 * lit - 1
    assert len([f for f in after.figures if f.lit]) == 3, "only a bag is missing, not a referent"
    survivors = [_own_bag(mutated, bag) for bag in after.bags]
    assert survivors[:2] == [front, own[2]], "the two left over are still a correctly placed pair"
