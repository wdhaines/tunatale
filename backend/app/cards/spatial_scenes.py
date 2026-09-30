"""Spatial-word pictures: a box, a ball, and the relation between them.

**A photo search does its worst work on this word class.** Asked for "under" it
returns a photograph of a ball and a box that share no relation at all — and the
relation *is* the content of the word. A picture is a poor fit for a preposition
the way five dots are a poor fit for "alas singko": true of nothing that was
asked. So these concepts get a picture that is **drawn**, and that is the whole
case rather than a preference. In a render the relation is in the coordinates,
so "the ball is on the box" is a number a test reads off the SVG and compares
with a table — where in a photograph it is a judgement about pixels that nobody
can check, and so is not a test at all. Same argument, one word class down, as
``app.cards.number_scenes``: the pictures look like diagrams, and the cost of
that was accepted for the number work too.

**One visual convention for every concept**, so a learner meets the same
two objects every time and only the relation changes: a **box** (the reference
object), a **ball** (the thing that is somewhere relative to it), an **arrow**
for the four direction words, and a **highlight band** for the two that name a
part of the box rather than a place. Nothing is labelled: a printed "under"
would let the card be answered by reading it, which is the one thing a picture
on a vocabulary card must not do.

**The y axis grows downward**, and that is where this file could have gone
quietly wrong. Up/down, on/under and above/under are three pairs of concepts
that differ only in the sign of a difference, so a mirrored render passes every
test that checks a symmetric member and fails the language. The four direction
concepts are therefore drawn as exact reflections of each other about the
picture's centre, and the pairs are asserted from the markup in both directions.

**The palette is shared with the counting pictures** — background, ball and box
fill imported rather than redeclared, so all of them read as one deck — plus two
colours new here. The box outline is deliberately *not* ``number_image``'s
``_ROD_STROKE``: that is 2.26:1 against the background, which is the exact
"present but invisible" failure the number work already paid for, where the
coin labels were drawn in a colour every structural test approved. Contrast is
therefore a property here and not a style note: the tests recompute the WCAG
ratio of every colour pair present in each picture off the fills and strokes
that picture actually carries, and hold it to the figures measured before this
was written.

Nothing here is per-language. The concept ids are the *picture's* key, and a
language's own words are mapped onto them by the caller — which is where the
language lives, and what keeps this module clear of one.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from app.cards.number_image import _BG, _DOT_FILL, _ROD_FILL

#: The concepts this module knows how to draw, and the only ones: an id outside
#: this list is refused rather than approximated, because a picture that is
#: almost the right relation teaches the wrong one.
SPATIAL_CONCEPTS: tuple[str, ...] = (
    "up",
    "down",
    "left",
    "right",
    "in",
    "out",
    "on",
    "under",
    "above",
    "between",
    "beside",
    "in_front",
    "behind",
    "top",
    "bottom",
    # Norwegian separates GOING somewhere (inn, ut, opp, ned) from BEING there
    # (inni, ute, oppe, nede), and has boundary (innenfor, utenfor) and slope
    # (nedenfor, ovenfor) words; with fifteen concepts six groups of its words
    # shared one picture ("inn and innenfor are identical", the user, 2026-09-29).
    # These eight give each its own, chosen by eye from drafts (tunatale-fsyd).
    "into",
    "within",
    "outside_of",
    "outdoors",
    "up_there",
    "down_there",
    "further_down",
    "further_up",
    # Two more parts of the box, beside top and bottom: Cebuano kilid ("side")
    # shared tupad's `beside` and tunga ("middle") shared taliwala's `between`
    # (the user's eye, 2026-09-29).
    "side",
    "middle",
)

#: The box outline, and every structural line with it. Chosen over the counting
#: picture's ``_ROD_STROKE`` because that one is 2.26:1 on the background: an
#: outline you cannot see is not an outline, and the test suite cannot tell.
_BOX_STROKE = "#6b7f9c"
#: Arrows and highlight bands. Drawn on the background *and* over the box fill
#: in several concepts, so both of those pairs are measured and asserted.
_ACCENT = "#b8572f"

# ── One frame for every concept ─────────────────────────────────────────────
# A near-square picture: the card scales an image to its width, so anything much
# wider than tall shrinks the very relation the card is about. Twenty units of
# margin all round, and the ground line 20 above the bottom edge, so nothing is
# ever drawn to the boundary.

_W = 260
_H = 220
_MARGIN = 20
_G = 200  # the ground line's y, in every concept

#: The ball. One radius of clearance from anything it must not touch, which is
#: what makes `on` (no gap) distinguishable from `above` (a full radius of air).
_BALL_R = 22

#: How far the arrow's tip sits from the ball's centre: one radius plus a hair,
#: so the tip lands against the ball's edge instead of under it.
_ARROW_TIP = 24
#: The shaft's length. The tests hold it at 3 * _BALL_R = 66 — a shaft shorter
#: than three ball widths reads as a tick, not as a direction.
_SHAFT = 72
_ARROW_HEAD_LEN = 18
_ARROW_HEAD_HALF = 12

#: The solid block: on the ground, its bottom at :data:`_G`, so every concept
#: that has one starts from the same object and differs only in the ball.
_BLOCK_W = 90
_BLOCK_H = 70
_BLOCK_Y = _G - _BLOCK_H  # 130

#: `under`'s table: a slab raised off the ground with room for the ball to stand
#: underneath it, which is the only way "under" differs from "beside".
_SLAB_H = 20
_SLAB_Y = 90
_SLAB_W = 140
_LEG_INSET = 10

#: The band on `top` and `bottom`, kept to a third of the block's height so it
#: reads as a band on the block rather than as a second block.
_BAND_H = 30
_BLOCK_TALL_H = 100  # `top` and `bottom`'s block, so _BAND_H <= h / 3 holds
_BLOCK_TALL_Y = _G - _BLOCK_TALL_H

#: Centres for the four direction concepts. Each pair is an exact reflection
#: about the picture's middle, so no pair can drift out of symmetry — the
#: failure that would mirror "up" into "down" while leaving "left" correct.
_CX = 130.0
_MID = 100.0
_HIGH = 70.0  # `up`'s ball, high in the frame
_LOW = 130.0  # `down`'s ball: _MID mirrored
_SIDE = 60.0  # how far left/right of centre those two balls sit


def _n(value: float) -> str:
    """Format a coordinate, trimmed so the markup carries no ``48.000000``."""
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _ground() -> str:
    """The floor every concept stands on, horizontal by construction."""
    return (
        f'<line class="ground" x1="{_MARGIN}" y1="{_G}" x2="{_W - _MARGIN}" y2="{_G}" '
        f'stroke="{_BOX_STROKE}" stroke-width="5" stroke-linecap="round"/>'
    )


def _ball(cx: float, cy: float) -> str:
    return f'<circle class="ball" cx="{_n(cx)}" cy="{_n(cy)}" r="{_BALL_R}" fill="{_DOT_FILL}"/>'


def _block(
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    table: bool = False,
    band: float | None = None,
    open_top: bool = False,
    side_band: bool = False,
) -> str:
    """The reference object: one body, plus whatever else the concept hangs on it.

    Document order is the spec, not a detail. The body comes first so the legs
    and the band that follow are painted OVER it, and the caller chooses whether
    the ball or the whole block is emitted last — which for ``in_front`` and
    ``behind`` is the only thing in the markup that says which is in front.

    ``table`` adds the two legs that make a raised slab a table; ``band`` is the
    band's top edge, for the two concepts that name a part of the box rather
    than a place by it.

    ``open_top`` draws a container instead of a block: the body keeps its fill
    but loses its outline, and a ``rim`` polyline outlines the two sides and the
    floor — no top edge. That missing edge is the whole of what ``in`` says and
    ``in_front`` does not (a ball inside a CLOSED box reads as a ball in front of
    it; found by eye on 2026-09-28, with every predicate green).
    """
    stroke = "none" if open_top else _BOX_STROKE
    parts = [
        f'<rect class="box-body" x="{_n(x)}" y="{_n(y)}" width="{_n(width)}" height="{_n(height)}" '
        f'rx="{0 if open_top else 6}" fill="{_ROD_FILL}" stroke="{stroke}" stroke-width="3"/>'
    ]
    if open_top:
        rim = f"{_n(x)},{_n(y)} {_n(x)},{_n(y + height)} {_n(x + width)},{_n(y + height)} {_n(x + width)},{_n(y)}"
        parts.append(
            f'<polyline class="rim" points="{rim}" fill="none" stroke="{_BOX_STROKE}" '
            f'stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>'
        )
    if table:
        for leg in (x + _LEG_INSET, x + width - _LEG_INSET):
            parts.append(
                f'<line class="leg" x1="{_n(leg)}" y1="{_n(y + height)}" x2="{_n(leg)}" y2="{_G}" '
                f'stroke="{_BOX_STROKE}" stroke-width="6" stroke-linecap="round"/>'
            )
    if band is not None:
        parts.append(
            f'<rect class="highlight" x="{_n(x)}" y="{_n(band)}" width="{_n(width)}" height="{_BAND_H}" fill="{_ACCENT}"/>'
        )
    if side_band:
        parts.append(
            f'<rect class="highlight" x="{_n(x + width - _BAND_H)}" y="{_n(y)}" width="{_BAND_H}" '
            f'height="{_n(height)}" fill="{_ACCENT}"/>'
        )
    return f'<g class="box">{"".join(parts)}</g>'


def _arrow(x1: float, y1: float, x2: float, y2: float) -> str:
    """A shaft from tail ``(x1, y1)`` to a head whose TIP is at ``(x2, y2)``.

    The tip is the head's position, so the arrow's direction is exactly
    ``(x2 - x1, y2 - y1)`` — the number the tests read, and the one that would be
    wrong if the tip were the head's centre.
    """
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    base_x, base_y = x2 - _ARROW_HEAD_LEN * ux, y2 - _ARROW_HEAD_LEN * uy
    left_x, left_y = base_x - uy * _ARROW_HEAD_HALF, base_y + ux * _ARROW_HEAD_HALF
    right_x, right_y = base_x + uy * _ARROW_HEAD_HALF, base_y - ux * _ARROW_HEAD_HALF
    return (
        '<g class="arrow">'
        f'<line class="shaft" x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}" '
        f'stroke="{_ACCENT}" stroke-width="6" stroke-linecap="round"/>'
        f'<polygon class="head" points="{_n(left_x)},{_n(left_y)} {_n(x2)},{_n(y2)} {_n(right_x)},{_n(right_y)}" '
        f'fill="{_ACCENT}"/>'
        "</g>"
    )


# ── The four directions ─────────────────────────────────────────────────────
# No box in any of them: a word that names a movement has nothing to be relative
# to, so an arrow and a ball is all the picture can honestly carry.


def _up() -> list[str]:
    """The ball up in the frame, with a shaft rising to it from below."""
    tip = _HIGH + _ARROW_TIP
    return [_ball(_CX, _HIGH), _arrow(_CX, tip + _SHAFT, _CX, tip)]


def _down() -> list[str]:
    """:func:`_up` reflected, the ball floating clear of the ground it is heading for."""
    tip = _LOW - _ARROW_TIP
    return [_ball(_CX, _LOW), _arrow(_CX, tip - _SHAFT, _CX, tip)]


def _left() -> list[str]:
    tip = _CX - _SIDE + _ARROW_TIP
    return [_ball(_CX - _SIDE, _MID), _arrow(tip + _SHAFT, _MID, tip, _MID)]


def _right() -> list[str]:
    tip = _CX + _SIDE - _ARROW_TIP
    return [_ball(_CX + _SIDE, _MID), _arrow(tip - _SHAFT, _MID, tip, _MID)]


# ── The ball against a box ──────────────────────────────────────────────────


def _in() -> list[str]:
    """The ball wholly inside an OPEN container, low down, with air on all sides.

    Two cues, and it needs both: containment keeps it out of ``in_front`` by
    the predicates, and the open top keeps it out by eye — a ball drawn inside
    a closed box looks like a ball in front of one.
    """
    return [
        _block(_CX - _BLOCK_W / 2, _BLOCK_Y, _BLOCK_W, _BLOCK_H, open_top=True),
        _ball(_CX, _G - _BALL_R - 4.0),
    ]


def _out() -> list[str]:
    """The ball out on the ground, and the arrow that gets it there from inside."""
    block_x, block_y = 30.0, _BLOCK_Y
    return [
        _block(block_x, block_y, _BLOCK_W, _BLOCK_H),
        # The tail starts INSIDE the body and points at the ball, so the picture
        # shows the relation leaving the box rather than merely standing near it.
        _arrow(block_x + _BLOCK_W / 2, _G - _BALL_R, 150.0, _G - _BALL_R),
        _ball(190.0, _G - _BALL_R),
    ]


def _on() -> list[str]:
    """The ball's underside on the box's top edge: touching, with no gap at all.

    The one ball that also breaks the ground line's frame — it is drawn above the
    box, and the box is drawn on the ground, so "on" is legible as contact.
    """
    block_x = _CX - _BLOCK_W / 2
    return [_block(block_x, _BLOCK_Y, _BLOCK_W, _BLOCK_H), _ball(_CX, _BLOCK_Y - _BALL_R)]


def _under() -> list[str]:
    """A table, and the ball standing on the ground beneath its slab.

    The legs are what make the position mean "under": without them a raised slab
    is a box in mid-air, and with them the ball is visibly *inside* the table's
    footprint. They are inset far enough that the ball clears both, and that
    clearance is asserted — a leg crossing the ball would read as the ball being
    caught rather than under.
    """
    return [
        _block(_CX - _SLAB_W / 2, _SLAB_Y, _SLAB_W, _SLAB_H, table=True),
        _ball(_CX, _G - _BALL_R),
    ]


def _above() -> list[str]:
    """The ball floating, a full radius clear of the box's top.

    The gap is the whole point: one radius of air is what a person reads as
    "above" rather than "on", so it is drawn with a margin on top of that radius
    and asserted as at least one.
    """
    height = 50.0
    block_y = _G - height
    return [
        _block(_CX - _BLOCK_W / 2, block_y, _BLOCK_W, height),
        _ball(_CX, block_y - 2 * _BALL_R - 8.0),
    ]


def _between() -> list[str]:
    """Two blocks of one height, and the ball in the gap with air on both sides."""
    width = 70.0
    return [
        _block(20.0, _BLOCK_Y, width, _BLOCK_H),
        _block(_W - _MARGIN - width, _BLOCK_Y, width, _BLOCK_H),
        _ball(_CX, _G - _BALL_R),
    ]


def _beside() -> list[str]:
    """The ball on the ground, a ball's width clear of the block's side."""
    return [
        _block(30.0, _BLOCK_Y, _BLOCK_W, _BLOCK_H),
        _ball(160.0, _G - _BALL_R),
    ]


def _in_front() -> list[str]:
    """The ball painted OVER the block, and crossing its edge.

    Depth has to be drawn somehow, and with opaque fills the only honest cue is
    which shape occludes which: the ball here covers part of the block's face,
    so it is the nearer of the two. It also crosses the block's left edge, so the
    pair reads as two objects sharing space rather than one inside the other —
    which is what tells it apart from ``in``.
    """
    block_x = _CX - _BLOCK_W / 2
    return [_block(block_x, _BLOCK_Y, _BLOCK_W, _BLOCK_H), _ball(block_x + 15.0, _BLOCK_Y + 42.0)]


def _behind() -> list[str]:
    """:func:`_in_front` with the paint order reversed, and the ball higher.

    Painted FIRST, so the block covers it; and high enough that a crescent of
    ball still shows above the block's top edge, or the ball would be invisible
    and the picture would carry no evidence of the claim at all.
    """
    block_x = _CX - _BLOCK_W / 2
    return [_ball(_CX, _BLOCK_Y - 5.0), _block(block_x, _BLOCK_Y, _BLOCK_W, _BLOCK_H)]


def _top() -> list[str]:
    """The band hard against the block's top edge, full width, a third its height.

    No ball: with nothing to place, the band's position against the block IS the
    answer, and a third of the height keeps it a band rather than a second block.
    """
    return [_block(_CX - _BLOCK_W / 2, _BLOCK_TALL_Y, _BLOCK_W, _BLOCK_TALL_H, band=_BLOCK_TALL_Y)]


def _bottom() -> list[str]:
    """:func:`_top` reflected: the band hard against the block's bottom edge."""
    return [_block(_CX - _BLOCK_W / 2, _BLOCK_TALL_Y, _BLOCK_W, _BLOCK_TALL_H, band=_G - _BAND_H)]


# ── Going and being there, boundaries and slopes (tunatale-fsyd) ──────────────
# The eight concepts that give Norwegian's going/being, boundary and slope words
# a picture each. The same box, ball, arrow and palette, plus four props that
# carry the relation the box alone cannot: a dashed boundary (an area, not a
# container), a roof (a house: "outdoors"), a ladder (a climb, so a ledge's top
# and foot read as up there and down there rather than `on` and `beside`), and
# steps with a marker (further up or down than a reference point).

_LEDGE_X = 140.0
_LEDGE_W = 90.0
_LEDGE_H = 120.0
_RAILS = (95.0, 125.0)
_RUNG_GAP = 22

_STEP_X0 = 35.0
_STEP_W = 63.0
_STEP_RISE = 42.0


def _boundary(x: float, width: float, top: float) -> str:
    """A fenced area seen from the side: a dashed outline standing on the ground."""
    return (
        f'<rect class="boundary" x="{_n(x)}" y="{_n(top)}" width="{_n(width)}" height="{_n(_G - top)}" rx="10" '
        f'fill="none" stroke="{_BOX_STROKE}" stroke-width="4" stroke-dasharray="12 9"/>'
    )


def _house(x: float, width: float, wall_h: float, roof_h: float) -> str:
    """A body with a roof and a door: the one prop that says "a building"."""
    y = _G - wall_h
    roof = f"{_n(x - 8)},{_n(y)} {_n(x + width / 2)},{_n(y - roof_h)} {_n(x + width + 8)},{_n(y)}"
    return (
        '<g class="house">'
        f'<rect class="box-body" x="{_n(x)}" y="{_n(y)}" width="{_n(width)}" height="{_n(wall_h)}" '
        f'fill="{_ROD_FILL}" stroke="{_BOX_STROKE}" stroke-width="3"/>'
        f'<polygon class="roof" points="{roof}" fill="{_ROD_FILL}" stroke="{_BOX_STROKE}" stroke-width="3" '
        'stroke-linejoin="round"/>'
        f'<rect class="door" x="{_n(x + width / 2 - 11)}" y="{_n(_G - 36)}" width="22" height="36" fill="none" '
        f'stroke="{_BOX_STROKE}" stroke-width="3"/>'
        "</g>"
    )


def _ledge_with_ladder() -> list[str]:
    """A tall ledge on the ground, a ladder against its left face reaching past its top."""
    top = _G - _LEDGE_H
    rails = [
        f'<line class="rail" x1="{_n(x)}" y1="{_G}" x2="{_n(x)}" y2="{_n(top - 10)}" stroke="{_BOX_STROKE}" '
        'stroke-width="4" stroke-linecap="round"/>'
        for x in _RAILS
    ]
    rungs = [
        f'<line class="rung" x1="{_n(_RAILS[0])}" y1="{_n(y)}" x2="{_n(_RAILS[1])}" y2="{_n(y)}" '
        f'stroke="{_BOX_STROKE}" stroke-width="4" stroke-linecap="round"/>'
        for y in range(_G - 18, int(top) - 10, -_RUNG_GAP)
    ]
    return [_block(_LEDGE_X, top, _LEDGE_W, _LEDGE_H), f'<g class="ladder">{"".join(rails + rungs)}</g>']


def _steps() -> tuple[str, list[tuple[float, float]]]:
    """Three steps rising to the right; returns the markup and each tread's (centre x, top y)."""
    bodies, treads = [], []
    for i in range(3):
        x = _STEP_X0 + i * _STEP_W
        height = (i + 1) * _STEP_RISE
        bodies.append(
            f'<rect class="box-body" x="{_n(x)}" y="{_n(_G - height)}" width="{_n(_STEP_W)}" height="{_n(height)}" '
            f'fill="{_ROD_FILL}" stroke="{_BOX_STROKE}" stroke-width="3"/>'
        )
        treads.append((x + _STEP_W / 2, _G - height))
    return f'<g class="steps">{"".join(bodies)}</g>', treads


def _marker(x: float, base_y: float) -> str:
    """The reference point on the middle step: a pole and a pennant in the accent."""
    top = base_y - 44
    return (
        '<g class="marker">'
        f'<line class="pole" x1="{_n(x)}" y1="{_n(base_y)}" x2="{_n(x)}" y2="{_n(top)}" stroke="{_BOX_STROKE}" '
        'stroke-width="4" stroke-linecap="round"/>'
        f'<polygon class="pennant" points="{_n(x)},{_n(top)} {_n(x + 26)},{_n(top + 9)} {_n(x)},{_n(top + 18)}" '
        f'fill="{_ACCENT}"/>'
        "</g>"
    )


def _into() -> list[str]:
    """GOING in: the ball above an open container, an arrow carrying it down into it.

    ``in`` (the ball resting inside) is the BEING-there half of the same pair.
    """
    ball_y = 40.0
    return [
        _block(_CX - _BLOCK_W / 2, _BLOCK_Y, _BLOCK_W, _BLOCK_H, open_top=True),
        _ball(_CX, ball_y),
        _arrow(_CX, ball_y + _BALL_R + 6, _CX, _BLOCK_Y + 40.0),
    ]


def _within() -> list[str]:
    """Inside an AREA (a boundary line), not inside a container: no walls, no floor."""
    return [_boundary(55.0, 150.0, 70.0), _ball(_CX, _G - _BALL_R)]


def _outside_of() -> list[str]:
    """:func:`_within`'s boundary, with the ball standing clear of it."""
    return [_boundary(25.0, 130.0, 70.0), _ball(200.0, _G - _BALL_R)]


def _outdoors() -> list[str]:
    """Out of doors: a house, and the ball out on the ground well clear of it."""
    return [_house(30.0, 100.0, 80.0, 50.0), _ball(195.0, _G - _BALL_R)]


def _up_there() -> list[str]:
    """BEING up: resting on the ledge the ladder climbs. No arrow — a place, not a movement.

    The ladder is load-bearing: without it the picture is ``on`` a tall box
    (the first draft, judged by eye 2026-09-29).
    """
    return [*_ledge_with_ladder(), _ball(_LEDGE_X + _LEDGE_W / 2, _G - _LEDGE_H - _BALL_R)]


def _down_there() -> list[str]:
    """BEING down: on the ground at the ladder's foot. Without the ladder, ``beside``."""
    return [*_ledge_with_ladder(), _ball(55.0, _G - _BALL_R)]


def _further_down() -> list[str]:
    """On a step BELOW the marker's step: lower down than a reference point."""
    stair, treads = _steps()
    (x0, y0), (x1, y1), _ = treads
    return [stair, _marker(x1 - 8, y1), _ball(x0, y0 - _BALL_R)]


def _further_up() -> list[str]:
    """On a step ABOVE the marker's step."""
    stair, treads = _steps()
    _, (x1, y1), (x2, y2) = treads
    return [stair, _marker(x1 - 8, y1), _ball(x2, y2 - _BALL_R)]


def _side() -> list[str]:
    """:func:`_top`'s band stood upright against the block's right edge: "the side of it"."""
    return [_block(_CX - _BLOCK_W / 2, _BLOCK_TALL_Y, _BLOCK_W, _BLOCK_TALL_H, side_band=True)]


def _middle() -> list[str]:
    """The band centred on the block's height, clear of both edges: "the middle of it"."""
    band_top = _BLOCK_TALL_Y + (_BLOCK_TALL_H - _BAND_H) / 2
    return [_block(_CX - _BLOCK_W / 2, _BLOCK_TALL_Y, _BLOCK_W, _BLOCK_TALL_H, band=band_top)]


_SCENES: dict[str, Callable[[], list[str]]] = {
    "up": _up,
    "down": _down,
    "left": _left,
    "right": _right,
    "in": _in,
    "out": _out,
    "on": _on,
    "under": _under,
    "above": _above,
    "between": _between,
    "beside": _beside,
    "in_front": _in_front,
    "behind": _behind,
    "top": _top,
    "bottom": _bottom,
    "into": _into,
    "within": _within,
    "outside_of": _outside_of,
    "outdoors": _outdoors,
    "up_there": _up_there,
    "down_there": _down_there,
    "further_down": _further_down,
    "further_up": _further_up,
    "side": _side,
    "middle": _middle,
}


def render_spatial_svg(concept: str) -> bytes:
    """Draw the relation *concept* names, as SVG bytes.

    One box, one ball, one floor and — for the four directions — one arrow, in a
    single frame; the relation is carried by where the ball is and by which
    shape is painted over which. That makes the claim checkable: the tests read
    every number below off the emitted markup, and a relation that is true by
    construction is a thing a unit test can assert instead of a picture a human
    has to squint at.

    Raises ``ValueError`` for a concept this module cannot draw, rather than
    emitting a near-miss: a picture showing the ball beside the box when the card
    asked for "under" is worse than no picture, because it is confidently wrong.
    """
    if concept not in SPATIAL_CONCEPTS:
        raise ValueError(
            f"{concept!r} is not a renderable spatial concept; expected one of {', '.join(SPATIAL_CONCEPTS)}"
        )
    parts = [_ground(), *_SCENES[concept]()]
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_W} {_H}" width="{_W}" height="{_H}">'
        f'<rect class="bg" x="0" y="0" width="{_W}" height="{_H}" fill="{_BG}"/>'
        f"{''.join(parts)}</svg>"
    )
    return svg.encode("utf-8")
