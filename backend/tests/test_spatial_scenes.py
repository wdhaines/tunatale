"""Spatial-word pictures: the ball, the box, and the relation between them.

A photo search does its worst work here. Asked for "under" it returns a
photograph of a ball and a box sharing no relation at all — and the relation IS
the content of the word, so a photo cannot be asked to carry one. These concepts
therefore get a picture that is *drawn*, and that is the whole case: in a render
the relation is in the coordinates, so "the ball is on the box" is a number read
off the SVG and compared with a table, rather than a judgement about a
photograph that has no oracle at all.

**Every number in the predicates below comes off the parsed markup** — never out
of a module constant, because a test that compares a renderer to itself asserts
only that the two agree. Each predicate is a plain function of a :class:`Picture`,
which is what lets the discrimination test ask whether one concept's render
satisfies its *neighbour's* predicate; that discrimination is the reason the
oracle exists, and it is the test that fails when a predicate has been written
loosely enough to accept two different words.

One predicate needed a reading the brief's prose does not spell out, and it is
flagged at its definition (``_is_in_front``): read strictly literally, ``in``'s
own render satisfies ``in_front``'s clause, so the two cards would be
indistinguishable by the oracle meant to tell them apart.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections.abc import Callable
from typing import NamedTuple

import pytest

from app.cards.spatial_scenes import SPATIAL_CONCEPTS, render_spatial_svg

_SVG = "{http://www.w3.org/2000/svg}"

#: "Touches" and "clear gap", in viewBox units — the brief's own tolerances.
_TOUCH = 1.0
_CLEAR = 2.0

#: The brief's element-count columns: (blocks, a ball?, an arrow?). ``top`` and
#: ``bottom`` are the two with no ball, and ``under`` is the one whose block is a
#: table rather than a solid box.
_SHAPES: dict[str, tuple[int, bool, bool]] = {
    "up": (0, True, True),
    "down": (0, True, True),
    "left": (0, True, True),
    "right": (0, True, True),
    "in": (1, True, False),
    "out": (1, True, True),
    "on": (1, True, False),
    "under": (1, True, False),
    "above": (1, True, False),
    "between": (2, True, False),
    "beside": (1, True, False),
    "in_front": (1, True, False),
    "behind": (1, True, False),
    "top": (1, False, False),
    "bottom": (1, False, False),
}

#: The brief's measured WCAG ratios. Each is RE-COMPUTED from the fills and
#: strokes in the SVG and held to >= 3.0, and additionally held to within 0.05 of
#: the number measured before this renderer was written — a palette that drifted
#: toward the background would still clear a bare floor for a while.
_MEASURED: dict[tuple[str, str], float] = {
    ("ball", "bg"): 6.23,
    ("ball", "box-fill"): 5.66,
    ("box-stroke", "bg"): 3.85,
    ("accent", "bg"): 4.46,
    ("accent", "box-fill"): 4.06,
}

#: The pairs whose render must fail the other's predicate. The reason the
#: oracle table exists at all: two neighbours that both satisfy one predicate
#: are two words on one picture.
_DISCRIMINATED: tuple[tuple[str, str], ...] = (
    ("on", "above"),
    ("above", "on"),
    ("in", "in_front"),
    ("in_front", "in"),
    ("in_front", "behind"),
    ("behind", "in_front"),
    ("under", "on"),
    ("up", "down"),
    ("left", "right"),
    ("top", "bottom"),
    ("beside", "out"),
)


# ── Parsing ─────────────────────────────────────────────────────────────────
# The markup contract is the spec, so the tests read it the way a renderer
# would: parse the document, find elements by class, take the numbers off the
# geometry. A regex over the source would pass on malformed XML.


class Box(NamedTuple):
    x: float
    y: float
    w: float
    h: float


class Ball(NamedTuple):
    cx: float
    cy: float
    r: float


class Arrow(NamedTuple):
    x1: float
    y1: float
    x2: float
    y2: float


class Highlight(NamedTuple):
    x: float
    y: float
    w: float
    h: float


class Picture(NamedTuple):
    """One render, read into the numbers the predicates talk about.

    ``painted`` is the document order of the block and the ball, because "in
    front of" and "behind" are decided by *which one is painted over which*, and
    no other part of the markup says so.
    """

    width: float
    height: float
    ground: float
    blocks: list[Box]
    legs: list[Arrow]
    ball: Ball | None
    arrow: Arrow | None
    bands: list[Highlight]
    painted: list[str]
    root: ET.Element
    rims: list[list[tuple[float, float]]]  # an open container's outline: sides and floor, no top


def _root(svg: bytes) -> ET.Element:
    return ET.fromstring(svg.decode("utf-8"))


def _classes(el: ET.Element) -> list[str]:
    return (el.get("class") or "").split()


def _by_class(svg: bytes, name: str) -> list[ET.Element]:
    return [el for el in _root(svg).iter() if name in _classes(el)]


def _viewbox(root: ET.Element) -> tuple[float, float, float, float]:
    """The four numbers off ``viewBox``, or a loud failure — a missing one is a
    picture nobody can scale, and it is the first thing to be dropped by hand."""
    parts = (root.get("viewBox") or "").split()
    assert len(parts) == 4, f"viewBox is {root.get('viewBox')!r}"
    x, y, width, height = (float(v) for v in parts)
    return x, y, width, height


def _rect_of(el: ET.Element) -> Box:
    return Box(*(float(el.get(k, "0")) for k in ("x", "y", "width", "height")))


def _arrow_of(el: ET.Element) -> Arrow:
    return Arrow(*(float(el.get(k, "0")) for k in ("x1", "y1", "x2", "y2")))


def _ball_of(el: ET.Element) -> Ball:
    return Ball(*(float(el.get(k, "0")) for k in ("cx", "cy", "r")))


def _lines(svg: bytes, cls: str) -> list[Arrow]:
    return [_arrow_of(el) for el in _by_class(svg, cls) if el.tag == f"{_SVG}line"]


def _painted(root: ET.Element) -> list[str]:
    """The order the block and the ball are drawn in, which is the depth cue."""
    order: list[str] = []
    for el in root.iter():
        for name in _classes(el):
            if name in ("box", "ball"):
                order.append(name)
    return order


def _parse(svg: bytes) -> Picture:
    root = _root(svg)
    _, _, width, height = _viewbox(root)
    circles = [el for el in _by_class(svg, "ball") if el.tag == f"{_SVG}circle"]
    shafts = _lines(svg, "shaft")
    return Picture(
        width=width,
        height=height,
        ground=_lines(svg, "ground")[0].y1,
        blocks=[_rect_of(el) for el in _by_class(svg, "box-body")],
        legs=_lines(svg, "leg"),
        ball=_ball_of(circles[0]) if len(circles) == 1 else None,
        arrow=shafts[0] if len(shafts) == 1 else None,
        bands=[_rect_of(el) for el in _by_class(svg, "highlight")],
        painted=_painted(root),
        root=root,
        rims=[
            [tuple(float(v) for v in pt.split(",")) for pt in el.get("points").split()] for el in _by_class(svg, "rim")
        ],
    )


# ── Geometry helpers ────────────────────────────────────────────────────────

Rect = tuple[float, float, float, float]


def _body(block: Box) -> Rect:
    return (block.x, block.y, block.x + block.w, block.y + block.h)


def _disc(ball: Ball) -> Rect:
    return (ball.cx - ball.r, ball.cy - ball.r, ball.cx + ball.r, ball.cy + ball.r)


def _disjoint(a: Rect, b: Rect) -> bool:
    return a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]


def _intersects(a: Rect, b: Rect) -> bool:
    return not _disjoint(a, b)


def _contains(outer: Rect, inner: Rect) -> bool:
    return outer[0] <= inner[0] and inner[2] <= outer[2] and outer[1] <= inner[1] and inner[3] <= outer[3]


def _dx(arrow: Arrow) -> float:
    return arrow.x2 - arrow.x1


def _dy(arrow: Arrow) -> float:
    return arrow.y2 - arrow.y1


def _length(arrow: Arrow) -> float:
    return math.hypot(_dx(arrow), _dy(arrow))


def _touches(a: float, b: float) -> bool:
    return abs(a - b) <= _TOUCH


def _one_block(p: Picture) -> Box | None:
    """The single block, or ``None`` — so a two-block render fails a one-block
    predicate instead of quietly using the left one."""
    return p.blocks[0] if len(p.blocks) == 1 else None


def _leg_hits_ball(leg: Arrow, ball: Ball) -> bool:
    """A leg crossing the ball would make the ball look caught by the table."""
    return ball.cx - ball.r <= leg.x1 <= ball.cx + ball.r and leg.y1 <= ball.cy + ball.r and leg.y2 >= ball.cy - ball.r


def _side_gap(block: Box, ball: Ball) -> float:
    """Clear horizontal space between the two, 0 when they touch or overlap."""
    if ball.cx - ball.r >= block.x + block.w:
        return (ball.cx - ball.r) - (block.x + block.w)
    if ball.cx + ball.r <= block.x:
        return block.x - (ball.cx + ball.r)
    return 0.0


# ── The predicates: one per row of the brief's table ───────────────────────
# Plain functions of a Picture, so the discrimination test can point one
# concept's predicate at another concept's render.


def _is_up(p: Picture) -> bool:
    """A vertical arrow rising to the ball, long enough to read as a direction."""
    if p.blocks or p.ball is None or p.arrow is None:
        return False
    ball, arrow = p.ball, p.arrow
    return (
        abs(_dx(arrow)) <= _TOUCH
        and _dy(arrow) < 0
        and _length(arrow) >= 3 * ball.r
        and math.hypot(ball.cx - arrow.x2, ball.cy - arrow.y2) <= 2 * ball.r
        and ball.cy < arrow.y2
    )


def _is_down(p: Picture) -> bool:
    """The mirror of :func:`_is_up`; the ball is off the ground, heading for it."""
    if p.blocks or p.ball is None or p.arrow is None:
        return False
    ball, arrow = p.ball, p.arrow
    return (
        abs(_dx(arrow)) <= _TOUCH
        and _dy(arrow) > 0
        and _length(arrow) >= 3 * ball.r
        and math.hypot(ball.cx - arrow.x2, ball.cy - arrow.y2) <= 2 * ball.r
        and ball.cy > arrow.y2
        and ball.cy + ball.r <= p.ground
    )


def _is_left(p: Picture) -> bool:
    if p.blocks or p.ball is None or p.arrow is None:
        return False
    ball, arrow = p.ball, p.arrow
    return (
        abs(_dy(arrow)) <= _TOUCH
        and _dx(arrow) < 0
        and _length(arrow) >= 3 * ball.r
        and math.hypot(ball.cx - arrow.x2, ball.cy - arrow.y2) <= 2 * ball.r
        and ball.cx < arrow.x2
    )


def _is_right(p: Picture) -> bool:
    if p.blocks or p.ball is None or p.arrow is None:
        return False
    ball, arrow = p.ball, p.arrow
    return (
        abs(_dy(arrow)) <= _TOUCH
        and _dx(arrow) > 0
        and _length(arrow) >= 3 * ball.r
        and math.hypot(ball.cx - arrow.x2, ball.cy - arrow.y2) <= 2 * ball.r
        and ball.cx > arrow.x2
    )


def _is_open(p: Picture) -> bool:
    """The one block is a container: an unstroked body and a rim with no top edge.

    The rim runs top-left -> bottom-left -> bottom-right -> top-right, so its
    only horizontal segment is the floor. Added by the orchestrator after the
    renders were looked at (2026-09-28): a ball inside a CLOSED box satisfied
    every predicate and still read, by eye, as a ball in front of the box.
    """
    block = _one_block(p)
    if (
        block is None
        or len(p.rims) != 1
        or next(el for el in p.root.iter() if "box-body" in _classes(el)).get("stroke") != "none"
    ):
        return False
    x0, y0, x1, y1 = _body(block)
    return p.rims[0] == [(x0, y0), (x0, y1), (x1, y1), (x1, y0)]


def _is_in(p: Picture) -> bool:
    """The ball inside an open container, air on all four sides, painted over it."""
    block, ball = _one_block(p), p.ball
    if block is None or ball is None or p.painted != ["box", "ball"] or not _is_open(p):
        return False
    return (
        block.x + _CLEAR <= ball.cx - ball.r
        and ball.cx + ball.r + _CLEAR <= block.x + block.w
        and block.y + _CLEAR <= ball.cy - ball.r
        and ball.cy + ball.r + _CLEAR <= block.y + block.h
    )


def _is_out(p: Picture) -> bool:
    """The ball outside on the ground, with an arrow leaving the body for it."""
    block, ball, arrow = _one_block(p), p.ball, p.arrow
    if block is None or ball is None or arrow is None:
        return False
    return (
        _disjoint(_body(block), _disc(ball))
        and _touches(ball.cy + ball.r, p.ground)
        and block.x < arrow.x1 < block.x + block.w
        and block.y < arrow.y1 < block.y + block.h
        and _dx(arrow) * (ball.cx - arrow.x1) + _dy(arrow) * (ball.cy - arrow.y1) > 0
    )


def _is_on(p: Picture) -> bool:
    """The ball resting on the body's top edge, with no gap at all."""
    block, ball = _one_block(p), p.ball
    if block is None or ball is None or p.painted != ["box", "ball"]:
        return False
    return _touches(ball.cy + ball.r, block.y) and block.x <= ball.cx <= block.x + block.w


def _is_under(p: Picture) -> bool:
    """A table: the ball is on the ground *beneath* the slab, between the legs."""
    block, ball = _one_block(p), p.ball
    if block is None or ball is None or len(p.legs) != 2:
        return False
    return (
        ball.cy - ball.r >= block.y + block.h + _CLEAR
        and _touches(ball.cy + ball.r, p.ground)
        and block.x <= ball.cx <= block.x + block.w
        and not any(_leg_hits_ball(leg, ball) for leg in p.legs)
    )


def _is_above(p: Picture) -> bool:
    """Floating with a full radius of air — the gap is what is not ``on``."""
    block, ball = _one_block(p), p.ball
    if block is None or ball is None:
        return False
    return ball.cy + ball.r <= block.y - ball.r and block.x <= ball.cx <= block.x + block.w


def _is_between(p: Picture) -> bool:
    """Two blocks of one height, and the ball in the gap with air on both sides."""
    ball = p.ball
    if ball is None or len(p.blocks) != 2:
        return False
    left, right = sorted(p.blocks, key=lambda b: b.x)
    return (
        _touches(left.y + left.h, p.ground)
        and _touches(right.y + right.h, p.ground)
        and left.h == right.h
        and _touches(ball.cy + ball.r, p.ground)
        and left.x + left.w + _CLEAR <= ball.cx - ball.r
        and ball.cx + ball.r + _CLEAR <= right.x
    )


def _is_beside(p: Picture) -> bool:
    """Clear of the block, and near enough to be beside rather than merely near."""
    block, ball = _one_block(p), p.ball
    if block is None or ball is None:
        return False
    return (
        _touches(ball.cy + ball.r, p.ground)
        and _disjoint(_body(block), _disc(ball))
        and 0 < _side_gap(block, ball) <= ball.r
    )


def _is_in_front(p: Picture) -> bool:
    """The ball painted OVER the body, and crossing its edge.

    ⚠️ The crossing clause is the one predicate here the brief does not spell
    out, and it is load-bearing: the brief's own discrimination list demands
    that ``in``'s render FAIL this predicate, and ``in`` draws its ball inside
    the body — overlapping it, centred in it, painted over it, which satisfies
    every literal clause of the brief's ``in_front`` row. As a strict subset test
    ``in_front`` would accept the ``in`` render and the oracle would not separate
    the two cards at all. So "in front of" is read as the occlusion reading: the
    ball is nearer the viewer, hence NOT contained by the block it stands in
    front of. That is the same "part of it showing, part of it hidden" cue
    ``behind`` states explicitly for the other side of the same depth pair.
    """
    block, ball = _one_block(p), p.ball
    if block is None or ball is None or p.painted != ["box", "ball"]:
        return False
    return (
        _intersects(_body(block), _disc(ball))
        and block.x < ball.cx < block.x + block.w
        and not _contains(_body(block), _disc(ball))
    )


def _is_behind(p: Picture) -> bool:
    """The body painted OVER the ball, which therefore has to poke out above."""
    block, ball = _one_block(p), p.ball
    if block is None or ball is None or p.painted != ["ball", "box"]:
        return False
    return (
        block.x < ball.cx < block.x + block.w
        and ball.cy + ball.r > block.y
        and block.y - (ball.cy - ball.r) >= ball.r / 2
    )


def _is_top(p: Picture) -> bool:
    """No ball: where the band sits against the body is the whole answer."""
    block = _one_block(p)
    if block is None or p.ball is not None or len(p.bands) != 1:
        return False
    band = p.bands[0]
    return _touches(band.y, block.y) and _touches(band.w, block.w) and band.h <= block.h / 3


def _is_bottom(p: Picture) -> bool:
    block = _one_block(p)
    if block is None or p.ball is not None or len(p.bands) != 1:
        return False
    band = p.bands[0]
    return _touches(band.y + band.h, block.y + block.h) and _touches(band.w, block.w) and band.h <= block.h / 3


_PREDICATES: dict[str, Callable[[Picture], bool]] = {
    "up": _is_up,
    "down": _is_down,
    "left": _is_left,
    "right": _is_right,
    "in": _is_in,
    "out": _is_out,
    "on": _is_on,
    "under": _is_under,
    "above": _is_above,
    "between": _is_between,
    "beside": _is_beside,
    "in_front": _is_in_front,
    "behind": _is_behind,
    "top": _is_top,
    "bottom": _is_bottom,
}


# ── The table, read row by row ─────────────────────────────────────────────


def test_the_concept_list_is_the_one_the_cards_route_on() -> None:
    """Fifteen ids, and the renderer refuses everything outside this list."""
    assert SPATIAL_CONCEPTS == (
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
    )
    assert set(_SHAPES) == set(SPATIAL_CONCEPTS)
    assert set(_PREDICATES) == set(SPATIAL_CONCEPTS)


@pytest.mark.parametrize("concept", SPATIAL_CONCEPTS)
def test_the_concept_draws_exactly_the_relation_its_name_claims(concept: str) -> None:
    """The whole table, off the markup. Every number read from the SVG itself."""
    picture = _parse(render_spatial_svg(concept))
    assert _PREDICATES[concept](picture), f"{concept} does not show {concept}"


@pytest.mark.parametrize("concept", ["up", "down", "left", "right"])
def test_a_direction_is_a_ball_and_an_arrow_and_no_box(concept: str) -> None:
    """With nothing to be relative to, a direction word can only be a movement."""
    picture = _parse(render_spatial_svg(concept))
    assert picture.blocks == []
    assert picture.ball is not None
    assert _length(picture.arrow) >= 3 * picture.ball.r


@pytest.mark.parametrize("concept", ["up", "down", "left", "right"])
def test_the_arrow_shaft_is_axis_aligned_and_reaches_the_ball(concept: str) -> None:
    """A diagonal shaft would make "up" and "left" the same picture."""
    picture = _parse(render_spatial_svg(concept))
    arrow, ball = picture.arrow, picture.ball
    vertical = abs(_dx(arrow)) <= _TOUCH
    assert vertical is (abs(_dy(arrow)) > _TOUCH)
    assert math.hypot(ball.cx - arrow.x2, ball.cy - arrow.y2) <= 2 * ball.r


@pytest.mark.parametrize(
    "concept", ["in", "out", "on", "under", "above", "beside", "in_front", "behind", "top", "bottom"]
)
def test_every_block_rests_on_the_ground_except_the_table(concept: str) -> None:
    """A block floating in mid-air would leave the relation unreadable."""
    picture = _parse(render_spatial_svg(concept))
    for block in picture.blocks:
        if concept == "under":
            assert block.y + block.h < picture.ground, "the table's slab is raised, not sitting on the ground"
        else:
            assert _touches(block.y + block.h, picture.ground)


@pytest.mark.parametrize("concept", ["out", "under", "between", "beside"])
def test_a_ball_on_the_ground_touches_it(concept: str) -> None:
    """Rests on the ground rather than hovering near it — and that is what
    separates these four from the concepts whose ball is up in the air."""
    picture = _parse(render_spatial_svg(concept))
    assert _touches(picture.ball.cy + picture.ball.r, picture.ground)


@pytest.mark.parametrize("concept", ["in", "in_front", "behind"])
def test_the_three_overlapping_concepts_say_which_is_in_front(concept: str) -> None:
    """Containment and paint order are all that separate these three."""
    picture = _parse(render_spatial_svg(concept))
    block = picture.blocks[0]
    assert _intersects(_body(block), _disc(picture.ball))
    assert picture.painted == (["ball", "box"] if concept == "behind" else ["box", "ball"])
    assert _contains(_body(block), _disc(picture.ball)) is (concept == "in")


@pytest.mark.parametrize("concept", ["top", "bottom"])
def test_the_band_is_thin_and_as_wide_as_the_block(concept: str) -> None:
    """A band taller than a third of the block would read as a second block."""
    picture = _parse(render_spatial_svg(concept))
    band, block = picture.bands[0], picture.blocks[0]
    assert band.w == block.w
    assert band.h <= block.h / 3


# ── Everything every picture must satisfy ──────────────────────────────────


def _extent(el: ET.Element) -> Rect:
    """The element's own extent, in viewBox units, whatever shape it is."""
    if el.tag == f"{_SVG}rect":
        x, y = float(el.get("x")), float(el.get("y"))
        return (x, y, x + float(el.get("width")), y + float(el.get("height")))
    if el.tag == f"{_SVG}circle":
        cx, cy, r = (float(el.get(k)) for k in ("cx", "cy", "r"))
        return (cx - r, cy - r, cx + r, cy + r)
    if el.tag == f"{_SVG}line":
        xs = (float(el.get("x1")), float(el.get("x2")))
        ys = (float(el.get("y1")), float(el.get("y2")))
        return (min(xs), min(ys), max(xs), max(ys))
    if el.tag == f"{_SVG}polygon":
        points = [tuple(float(v) for v in pair.split(",")) for pair in (el.get("points") or "").split()]
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        return (min(xs), min(ys), max(xs), max(ys))
    raise AssertionError(f"unexpected element {el.tag}")


@pytest.mark.parametrize("concept", SPATIAL_CONCEPTS)
def test_is_well_formed_xml_on_a_svg_root_with_a_viewbox(concept: str) -> None:
    root = _root(render_spatial_svg(concept))
    assert root.tag == f"{_SVG}svg"
    x, y, width, height = _viewbox(root)
    assert (x, y) == (0.0, 0.0)
    assert width > 0 and height > 0


@pytest.mark.parametrize("concept", SPATIAL_CONCEPTS)
def test_paints_its_own_opaque_background_first(concept: str) -> None:
    """An <img> cannot inherit currentColor, and night mode would erase the card."""
    root = _root(render_spatial_svg(concept))
    _, _, width, height = _viewbox(root)
    first = root[0]
    assert first.tag == f"{_SVG}rect"
    assert "bg" in _classes(first)
    assert _extent(first) == (0.0, 0.0, width, height)
    assert first.get("fill", "").startswith("#")


@pytest.mark.parametrize("concept", SPATIAL_CONCEPTS)
def test_nothing_is_made_translucent(concept: str) -> None:
    """Document order is the only depth cue in these pictures, so an opaque
    fill is the spec rather than a preference: a translucent body would let the
    ball show through and `in_front` would stop meaning anything."""
    for el in _root(render_spatial_svg(concept)).iter():
        assert "opacity" not in el.attrib
        assert "fill-opacity" not in el.attrib


@pytest.mark.parametrize("concept", SPATIAL_CONCEPTS)
def test_has_exactly_one_horizontal_ground_line(concept: str) -> None:
    svg = render_spatial_svg(concept)
    grounds = [el for el in _by_class(svg, "ground") if el.tag == f"{_SVG}line"]
    assert len(grounds) == 1
    assert grounds[0].get("y1") == grounds[0].get("y2")
    picture = _parse(svg)
    assert grounds[0].get("y1") == f"{picture.ground:.1f}".rstrip("0").rstrip(".")
    assert 0 < picture.ground < picture.height


@pytest.mark.parametrize("concept", SPATIAL_CONCEPTS)
def test_carries_no_word_that_would_answer_the_card(concept: str) -> None:
    """A printed "under" would make the card answerable by reading it."""
    assert b"<text" not in render_spatial_svg(concept)


@pytest.mark.parametrize("concept", SPATIAL_CONCEPTS)
def test_draws_exactly_the_elements_its_row_asks_for(concept: str) -> None:
    blocks, ball, arrow = _SHAPES[concept]
    picture = _parse(render_spatial_svg(concept))
    assert (len(picture.blocks), picture.ball is not None, picture.arrow is not None) == (blocks, ball, arrow)
    assert len(picture.legs) == (2 if concept == "under" else 0)
    assert len(picture.bands) == (1 if concept in ("top", "bottom") else 0)


@pytest.mark.parametrize("concept", SPATIAL_CONCEPTS)
def test_every_shape_is_inside_the_viewbox(concept: str) -> None:
    """A card crops nothing, so a shape past the edge is a shape somebody cut."""
    svg = render_spatial_svg(concept)
    _, _, width, height = _viewbox(_root(svg))
    shapes = [
        el for el in _root(svg).iter() if el.tag in (f"{_SVG}rect", f"{_SVG}circle", f"{_SVG}line", f"{_SVG}polygon")
    ]
    assert shapes
    for el in shapes:
        x0, y0, x1, y1 = _extent(el)
        assert x0 >= 0 and x1 <= width, f"{el.get('class')} runs off the sides"
        assert y0 >= 0 and y1 <= height, f"{el.get('class')} runs off the top or bottom"


@pytest.mark.parametrize("concept", SPATIAL_CONCEPTS)
def test_the_picture_keeps_an_aspect_a_card_can_scale(concept: str) -> None:
    """The card scales an image to its width, so a wide strip shrinks the very
    relation the card is about."""
    _, _, width, height = _viewbox(_root(render_spatial_svg(concept)))
    assert 0.75 <= width / height <= 2.0, f"{concept} renders {width}x{height}"


# ── Contrast: the measured palette, recomputed from the markup ─────────────


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


def _roles(concept: str) -> dict[str, str]:
    """The palette roles actually present in this picture, off its own attributes."""
    svg = render_spatial_svg(concept)
    roles = {"bg": _root(svg)[0].get("fill")}
    bodies = _by_class(svg, "box-body")
    if bodies:
        roles["box-fill"] = bodies[0].get("fill")
        # An open container outlines itself with its rim, not its body.
        stroke = bodies[0].get("stroke")
        roles["box-stroke"] = _by_class(svg, "rim")[0].get("stroke") if stroke == "none" else stroke
    balls = _by_class(svg, "ball")
    if balls:
        roles["ball"] = balls[0].get("fill")
    accents = {el.get("stroke") for el in _by_class(svg, "shaft")} | {el.get("fill") for el in _by_class(svg, "head")}
    accents |= {el.get("fill") for el in _by_class(svg, "highlight")}
    if accents:
        assert len(accents) == 1, f"{concept} draws more than one accent colour: {accents}"
        roles["accent"] = accents.pop()
    return roles


@pytest.mark.parametrize("concept", SPATIAL_CONCEPTS)
def test_every_colour_pair_in_this_picture_is_legible(concept: str) -> None:
    """Recomputed from the SVG's own fills, and held to the measured ratios.

    A property rather than a style note, because of the number work: the coin
    labels were drawn in a colour that was present, structured and invisible —
    2.26:1 against the background. No structural test could have caught that, so
    contrast is asserted here instead of reviewed.
    """
    roles = _roles(concept)
    for (one, two), measured in _MEASURED.items():
        if one not in roles or two not in roles:
            continue
        ratio = _contrast(roles[one], roles[two])
        assert ratio >= 3.0, f"{concept}: {one} on {two} is only {ratio:.2f}:1"
        assert ratio == pytest.approx(measured, abs=0.05), f"{concept}: {one} on {two} drifted off its measured ratio"


def test_every_measured_pair_is_still_reachable_in_some_picture() -> None:
    """Otherwise a colour could be dropped and the floor test would never know."""
    present = {
        (one, two) for concept in SPATIAL_CONCEPTS for (one, two) in _MEASURED if {one, two} <= set(_roles(concept))
    }
    assert present == set(_MEASURED)


# ── Discrimination: the reason the oracle table exists ─────────────────────


@pytest.mark.parametrize(("first", "second"), _DISCRIMINATED)
def test_neighbour_concepts_are_told_apart_by_their_own_predicate(first: str, second: str) -> None:
    """A predicate both neighbours satisfy would put two words on one picture."""
    picture = _parse(render_spatial_svg(first))
    assert _PREDICATES[first](picture)
    assert not _PREDICATES[second](picture), f"the {first} render also satisfies {second}"


# ── As a byte string, like every other media file ──────────────────────────


@pytest.mark.parametrize("concept", SPATIAL_CONCEPTS)
def test_is_deterministic(concept: str) -> None:
    """The filename is the content hash; a jittered render re-stages forever."""
    assert render_spatial_svg(concept) == render_spatial_svg(concept)


def test_every_concept_renders_different_bytes() -> None:
    """Identical bytes would point two cards at one picture."""
    assert len({render_spatial_svg(concept) for concept in SPATIAL_CONCEPTS}) == len(SPATIAL_CONCEPTS)


@pytest.mark.parametrize("concept", ["sideways", "", "UP", "in front", "up ", "on_top", "beside ", "čez", "on\t"])
def test_refuses_a_concept_it_cannot_draw(concept: str) -> None:
    """An empty picture is indistinguishable from a render that failed, so a
    concept this module cannot draw is refused rather than approximated."""
    with pytest.raises(ValueError, match="renderable"):
        render_spatial_svg(concept)


@pytest.mark.parametrize("concept", SPATIAL_CONCEPTS)
def test_only_in_draws_an_open_container(concept: str) -> None:
    """Every other box is closed, so an open top can only ever mean "in"."""
    picture = _parse(render_spatial_svg(concept))
    if concept == "in":
        assert _is_open(picture)
    else:
        assert picture.rims == []
        assert all(el.get("stroke") != "none" for el in _by_class(render_spatial_svg(concept), "box-body"))
