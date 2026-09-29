"""Calendar pictures: which month of the year, and which day of the week.

Months and weekdays are the one vocabulary whose picture can be drawn rather
than found — and the reason is sharper here than it was for the numbers. A photo
search asked for "March" returns a march of people, and asked for "Monday"
returns whatever weekday the photographer liked; neither is the thing the word
names, which is a **position in a structure**: the third cell of the third row of
a year, the first cell of a week. So the picture is the structure, with the one
cell the word points at filled, rendered by ``app.cards.calendar_scenes``.

**Nothing is labelled, except the one thing that must be.** A printed "Monday"
would let the card be answered by reading it, which is the single thing a
vocabulary picture must not do (the rule the spatial and pronoun suites hold).
A month is the deliberate exception, and the reason is that a NUMBER cannot be
read off a position: "the seventh cell" is not a thing a learner sees, and the
position is the same in every language while the word is not. So the lit month
cell carries its number and the week carries none, and both halves of that are
asserted — a month whose label says something other than its own number is
refused as firmly as a week that has grown one.

**The week start is DATA, and this file holds its own copy of the order.** Which
day opens a week is a local custom, not a fact about calendars: the layout here
follows Monday for the European decks and Sunday for the Philippine ones, and a
module constant would draw half of them wrong. It arrives as an argument, so the
renderer's copy of the order proves nothing — the checker carries the ISO order
itself and reads the lit cell's position off the geometry. A Sunday-first week
drawn Monday-first is then a test failure rather than a plausible-looking page.

**Every number here is read off the parsed markup**, never off a builder
constant, and the sheet is found by being the largest thing drawn on the canvas
rather than by its white fill — so a recoloured page is still the page and the
fit check goes on measuring it. The probes at the end feed each check *mutated*
renders and require a named refusal, so the checker is proven to discriminate
rather than to agree with the renderer that produced its input. The first mockup
ran a weekday cell off the page's right edge, which no count, colour or grid
test would ever have noticed and which is why the fit check is here.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import NamedTuple

import pytest

from app.cards.calendar_scenes import CALENDAR_CONCEPTS, WEEKEND_FILL, render_calendar_svg

_SVG = "{http://www.w3.org/2000/svg}"

# ── The palette, as literals ────────────────────────────────────────────────
# Held here rather than imported, because "the renderer agrees with itself" is
# not what pins a colour. The three cell fills are the legibility decision: the
# lit one has to carry white ink at 6.60:1, and the two unlit ones have to be
# told apart from EACH OTHER by lightness rather than by hue.
_LIT = "#2f5d9e"  # number_image._DOT_FILL
_PALE = "#e8eef8"  # number_image._ROD_FILL
_WEEKEND = "#e2b79c"  # calendar_scenes.WEEKEND_FILL, the weekend tint
_LIT_STROKE = "#1f406f"  # the lit cell's outline, the deck's reference blue
_LINE = "#6b7f9c"  # spatial_scenes._BOX_STROKE
_PAGE = "#ffffff"
_DIGIT = "#ffffff"
_BG = "#faf8f4"

_FRAME = (260.0, 220.0)
_UNLIT_STROKE_W = 1.2
_LIT_STROKE_W = 2.0

#: A cell is the one rect with this corner radius: the page rounds at 10, its
#: header band at 9, the binding rings at 3, and the background is square. A
#: cell in the WRONG COLOUR is still a cell, which is what lets the tint and
#: contrast checks see a repainted one instead of counting fewer cells.
_CELL_RX = "5"
_CELL_EL = re.compile(
    r'<rect x="(?P<x>[-\d.]+)" y="(?P<y>[-\d.]+)" width="(?P<w>[-\d.]+)" height="(?P<h>[-\d.]+)" '
    r'rx="5" fill="[^"]*" stroke="[^"]*" stroke-width="[^"]*"/>'
)
_TEXT_EL = re.compile(r'<text x="([-\d.]+)" y="([-\d.]+)"[^>]*>([^<]*)</text>')

MONTHS = tuple(f"month_{n}" for n in range(1, 13))
WEEKDAYS = tuple(f"weekday_{d}" for d in range(1, 8))
WEEK_STARTS = ("monday", "sunday")


# ── The parsed render ───────────────────────────────────────────────────────


class Box(NamedTuple):
    """One drawn rectangle — a cell, or the page — in viewBox units."""

    left: float
    top: float
    right: float
    bottom: float
    fill: str
    stroke: str
    stroke_width: float

    @property
    def cx(self) -> float:
        return (self.left + self.right) / 2

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def lit(self) -> bool:
        """The filled cell. WHICH fill that is, is settled by legibility below."""
        return self.fill == _LIT

    @property
    def weekend(self) -> bool:
        return self.fill == _WEEKEND

    def holds(self, x: float, y: float) -> bool:
        return self.left <= x <= self.right and self.top <= y <= self.bottom


class Label(NamedTuple):
    """One ``<text>``: the only ink a calendar picture is allowed to carry."""

    x: float
    y: float
    text: str
    fill: str

    def inside(self, cell: Box) -> bool:
        return cell.holds(self.x, self.y)


class Picture(NamedTuple):
    width: float
    height: float
    page: Box
    cells: tuple[Box, ...]  # document order
    labels: tuple[Label, ...]

    def ordered(self) -> tuple[Box, ...]:
        """The cells in READING order: top to bottom, left to right in a row.

        This is what makes "the third one" a number read off the picture. Cells
        are placed by (row, column), so sorting by centre reproduces exactly the
        order a reader's eye takes — and a cell in the wrong place sorts into
        the wrong position, which is the point.
        """
        return tuple(sorted(self.cells, key=lambda c: (c.cy, c.cx)))


def _root(svg: bytes) -> ET.Element:
    return ET.fromstring(svg.decode())


def _num(el: ET.Element, name: str) -> float:
    return float(el.get(name))


def _box(el: ET.Element) -> Box:
    x, y, w, h = (_num(el, name) for name in ("x", "y", "width", "height"))
    # A shape with no stroke-width has no stroke: the binding rings and the
    # header band are filled only, and their width is 0 rather than absent.
    return Box(
        x,
        y,
        x + w,
        y + h,
        el.get("fill"),
        el.get("stroke"),
        _num(el, "stroke-width") if el.get("stroke-width") else 0.0,
    )


def _boxes(svg: bytes) -> tuple[Box, ...]:
    return tuple(_box(el) for el in _root(svg).iter() if el.tag == f"{_SVG}rect")


def _parse(svg: bytes) -> Picture:
    root = _root(svg)
    rects = _boxes(svg)
    # The background is the only rect drawn at the origin, and the page is the
    # largest thing left on the canvas. That is how the sheet is found WITHOUT
    # asking for its fill, so a recoloured page is still the page and the fit
    # check goes on measuring it.
    drawn = [b for b in rects if not (b.left == 0 and b.top == 0)]
    assert len(rects) - len(drawn) == 1, "expected exactly one background rect"
    page = max(drawn, key=lambda b: b.width * b.height)
    cells = tuple(_box(el) for el in root.iter() if el.tag == f"{_SVG}rect" and el.get("rx") == _CELL_RX)
    labels = tuple(
        Label(_num(el, "x"), _num(el, "y"), el.text or "", el.get("fill"))
        for el in root.iter()
        if el.tag == f"{_SVG}text"
    )
    _, _, width, height = (float(v) for v in root.get("viewBox").split())
    return Picture(width, height, page, cells, labels)


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


# ── The brief's oracles, as predicates over the parsed picture ──────────────
#
# Functions rather than one dict comparison, so that each probe at the end can
# hand its check a MUTATED render and get a NAMED refusal instead of asserting
# that a whole row changed.


def _count_is(p: Picture, n: int) -> bool:
    return len(p.cells) == n


def _grid_is(p: Picture, columns: int, rows: int) -> bool:
    """Distinct column centres and row centres: a 4x3 grid, read off geometry."""
    return len({c.cx for c in p.cells}) == columns and len({c.cy for c in p.cells}) == rows


def _lit_at(p: Picture, index: int) -> bool:
    """Exactly one lit cell, and it is the one at reading-order *index*.

    Bounds-safe on purpose: a render missing a cell has no position ``index`` at
    all, and that is a refusal like any other rather than an IndexError.
    """
    cells = p.ordered()
    return len(cells) > index and sum(1 for c in cells if c.lit) == 1 and cells[index].lit


def _digit_is(p: Picture, text: str, index: int) -> bool:
    """One label, saying *text*, sitting inside the lit cell."""
    cells = p.ordered()
    return len(p.labels) == 1 and p.labels[0].text == text and index < len(cells) and p.labels[0].inside(cells[index])


def _unlabelled(p: Picture) -> bool:
    return not p.labels


def _row_is(p: Picture) -> bool:
    """Seven cells in ONE row: same top, same height, tiling left to right.

    Tiling, not merely "rising x": seven cells sorted by centre are always
    rising, so that half of the claim would be vacuous. What can go wrong is two
    cells drawn on top of each other, and a week row that overlaps itself shows
    six days while counting seven.
    """
    cells = p.ordered()
    return (
        len(cells) == 7
        and len({c.top for c in cells}) == 1
        and len({c.height for c in cells}) == 1
        and all(b.left >= a.right for a, b in zip(cells, cells[1:], strict=False))
    )


def _emitted_left_to_right(p: Picture) -> bool:
    """The cells are emitted left to right, so the markup reads in the order a
    reader's eye takes and a diff of two renders is a diff of a row."""
    return all(b.left > a.left for a, b in zip(p.cells, p.cells[1:], strict=False))


#: The ISO week order the drawings must follow, transcribed here rather than
#: imported: 1 = Monday … 7 = Sunday, so a Sunday-first week opens with 7. This
#: file's own copy is the point — ``week_start`` arrives as an argument, so the
#: renderer's copy of the order would prove nothing.
_WEEK_ORDER: dict[str, tuple[int, ...]] = {
    "monday": (1, 2, 3, 4, 5, 6, 7),
    "sunday": (7, 1, 2, 3, 4, 5, 6),
}
_WEEKEND_DAYS = frozenset({6, 7})  # Saturday, Sunday


def _weekend_tint_is(p: Picture, week_start: str) -> bool:
    """Weekend cells tinted, and ONLY weekend cells — the lit one exempt.

    The tint is what lets the row read without labels, so it has to be on both
    weekend days, and a tint on a weekday is a picture claiming Friday ends the
    week. The exemption is the point where this check earns its keep: when the
    lit day IS a weekend day, that cell is filled instead of tinted, and a rule
    that forgot the exemption would refuse the two most common cards in a
    Sunday-first deck.
    """
    cells = p.ordered()
    if len(cells) != 7:
        return False
    return all(
        cell.weekend == (iso in _WEEKEND_DAYS)
        for cell, iso in zip(cells, _WEEK_ORDER[week_start], strict=True)
        if not cell.lit
    )


def _fits(p: Picture, margin: float = 4.0) -> bool:
    """Every cell inside the page by *margin* on every side, and the page in frame.

    The first mockup's overflow. Nothing structural notices: the counts, the
    grid and the colours are all still correct, and the card shows six and a half
    days.
    """
    if not (p.page.left >= 0 and p.page.top >= 0 and p.page.right <= _FRAME[0] and p.page.bottom <= _FRAME[1]):
        return False
    return all(
        c.left >= p.page.left + margin
        and c.right <= p.page.right - margin
        and c.top >= p.page.top + margin
        and c.bottom <= p.page.bottom - margin
        for c in p.cells
    )


# ── The contrast floor, read off the fills the picture carries ──────────────
#
# (measured ratio, the floor it must clear). The brief's rows and the reason each
# exists: the lit cell carries white ink (6.60), a lit cell must not be
# confusable with a weekend cell (3.61), and the two unlit fills must differ by
# LIGHTNESS rather than hue (1.57 — a hue-only difference is invisible to a
# reader who cannot see the picture's colour names).
_MEASURED: dict[tuple[str, str], tuple[float, float]] = {
    ("digit", "lit"): (6.60, 3.0),
    ("lit", "page"): (6.60, 3.0),
    ("lit", "weekend"): (3.61, 3.0),
    ("line", "cell"): (3.50, 3.0),
    ("weekend", "cell"): (1.57, 1.5),
}

#: What a picture must be carrying before any of those pairs means anything.
#: Without this, a picture whose lit cell was repainted has no `lit` role at
#: all, every pair is silently skipped, and the contrast check passes a picture
#: with an invisible lit cell.
_REQUIRED = frozenset({"bg", "page", "lit", "lit-stroke", "cell", "line"})


def _palette(svg: bytes) -> dict[str, str]:
    """The palette roles this picture actually carries, off its own attributes.

    The two unlit fills are found by SLOT, not by matching the exact hex: the
    lighter unlit fill is the ordinary cell and the darker one is the weekend
    tint. Pinning the literals here instead would make a hue-nudged tint VANISH
    from the palette — the picture would then carry no `weekend` role at all, the
    weekend/cell pair would be silently skipped, and the exact-tolerance assert
    on that pair would never run. Read by slot, the nudge is measured and fails.
    """
    picture = _parse(svg)
    roles = {"bg": _root(svg)[0].get("fill"), "page": picture.page.fill}
    lit = [c for c in picture.cells if c.lit]
    if lit:
        roles["lit"] = lit[0].fill
        roles["lit-stroke"] = lit[0].stroke
    unlit = [c for c in picture.cells if not c.lit]
    if unlit:
        roles["line"] = unlit[0].stroke
        pale = [c for c in unlit if c.fill == _PALE]
        roles["cell"] = (pale or unlit)[0].fill
        tinted = [c for c in unlit if c.fill != _PALE]
        if tinted:
            roles["weekend"] = tinted[0].fill
    if picture.labels:
        roles["digit"] = picture.labels[0].fill
    return roles


def _pair_legible(roles: dict[str, str], one: str, two: str) -> float:
    """One measured pair: the floor, then the exact measurement. Returns the ratio.

    Split out from ``_legible`` so a probe can aim at a SINGLE pair. Nudging the
    weekend tint moves every pair it takes part in, and the table's first
    matching entry (``lit/weekend``) would then fire its exact-measurement
    assert before the weekend/cell floor ever got a look — which would leave the
    floor's own probe unproven, for no reason to do with the floor.
    """
    (measured, floor) = _MEASURED[(one, two)]
    ratio = _contrast(roles[one], roles[two])
    assert ratio >= floor, f"{one} on {two} is only {ratio:.2f}:1, floor {floor}"
    assert ratio == pytest.approx(measured, abs=0.01), f"{one} on {two} drifted off its measurement"
    return ratio


def _legible(svg: bytes) -> dict[str, str]:
    """Assert every measured pair this picture carries, and return its roles."""
    roles = _palette(svg)
    missing = _REQUIRED - set(roles)
    assert not missing, f"this picture carries no {sorted(missing)} to measure"
    for one, two in _MEASURED:
        if one not in roles or two not in roles:
            continue
        _pair_legible(roles, one, two)
    return roles


def test_the_weekend_tint_is_the_colour_the_approver_approved() -> None:
    """The constant is public, so it is pinned here: it must be the DARKER of the
    two unlit fills, which is what "differ in lightness, not hue alone" means."""
    assert WEEKEND_FILL == _WEEKEND
    assert _luminance(_WEEKEND) < _luminance(_PALE)


# ── The concept list ────────────────────────────────────────────────────────


def test_the_concept_list_is_the_one_the_cards_route_on() -> None:
    assert (*MONTHS, *WEEKDAYS) == CALENDAR_CONCEPTS
    assert len(CALENDAR_CONCEPTS) == len(set(CALENDAR_CONCEPTS)) == 19


@pytest.mark.parametrize(
    "concept",
    ["sideways", "", "I", "month_0", "month_13", "weekday_0", "weekday_8", "weekday ", "month_1 ", "MONTH_1"],
)
def test_refuses_a_concept_it_cannot_draw(concept: str) -> None:
    """An empty page is indistinguishable from a render that failed, so a concept
    this module cannot draw is refused rather than approximated — a page with
    eleven months is worse than no page, because it is confidently wrong."""
    with pytest.raises(ValueError, match="renderable"):
        render_calendar_svg(concept, week_start="monday")


@pytest.mark.parametrize("week_start", ["tuesday", "", "Monday", "MONDAY", "sat", "mon", "monday "])
def test_refuses_a_week_start_it_cannot_lay_out(week_start: str) -> None:
    """Which day opens a week is DATA, and a value this module does not know is
    refused rather than drawn Monday-first — which would be a confidently wrong
    page for every card in a Sunday-first language."""
    with pytest.raises(ValueError, match="week start"):
        render_calendar_svg("month_1", week_start=week_start)


def test_the_week_start_is_keyword_only() -> None:
    """Positional would let a call site pass the day count by accident, and the
    picture would be a week nobody asked for."""
    with pytest.raises(TypeError):
        render_calendar_svg("month_1", "monday")  # type: ignore[misc]


# ── A month: twelve cells, one lit, one number ──────────────────────────────


@pytest.mark.parametrize("n", range(1, 13))
@pytest.mark.parametrize("week_start", WEEK_STARTS)
def test_a_month_draws_a_year_with_its_own_cell_lit(n: int, week_start: str) -> None:
    """ORACLE B1.1, read off the geometry: twelve cells in four columns and three
    rows, exactly one lit, at reading-order position n-1, carrying the number n
    and nothing else."""
    picture = _parse(render_calendar_svg(f"month_{n}", week_start=week_start))
    assert _count_is(picture, 12)
    assert _grid_is(picture, 4, 3)
    assert _lit_at(picture, n - 1)
    assert _digit_is(picture, str(n), n - 1)


@pytest.mark.parametrize("n", range(1, 13))
def test_a_month_is_the_same_page_whatever_day_the_week_starts(n: int) -> None:
    """A year holds no weeks, so the layout argument must not move a cell. The
    three languages on a Sunday-first calendar would otherwise get a different
    January from the two on a Monday-first one, for no reason at all."""
    assert render_calendar_svg(f"month_{n}", week_start="monday") == render_calendar_svg(
        f"month_{n}", week_start="sunday"
    )


@pytest.mark.parametrize("n", range(1, 13))
def test_exactly_one_cell_of_twelve_is_filled(n: int) -> None:
    """Eleven pale cells and one filled: the difference IS the month, so the
    count of filled cells is the claim and eleven pale ones are all alike."""
    cells = _parse(render_calendar_svg(f"month_{n}", week_start="monday")).ordered()
    assert [c.lit for c in cells] == [i == n - 1 for i in range(12)]
    assert {c.fill for c in cells if not c.lit} == {_PALE}
    assert {c.stroke for c in cells if not c.lit} == {_LINE}
    assert {c.stroke for c in cells if c.lit} == {_LIT_STROKE}


@pytest.mark.parametrize("concept", CALENDAR_CONCEPTS)
def test_a_pale_cell_carries_an_outline_and_the_lit_one_a_stronger_one(concept: str) -> None:
    """A pale cell is 1.10:1 on the paper, so its outline is the only reason it is
    visible — and the lit cell is outlined harder so a row of twelve does not
    read as a ragged edge with a smudge in it."""
    picture = _parse(render_calendar_svg(concept, week_start="monday"))
    assert {c.stroke_width for c in picture.cells if not c.lit} == {_UNLIT_STROKE_W}, concept
    assert {c.stroke_width for c in picture.cells if c.lit} == {_LIT_STROKE_W}, concept


# ── A weekday: seven cells in one row, and a weekend that reads unlabelled ──


@pytest.mark.parametrize("day", range(1, 8))
@pytest.mark.parametrize("week_start", WEEK_STARTS)
def test_a_weekday_draws_a_week_with_its_own_day_lit(day: int, week_start: str) -> None:
    """ORACLE B1.2: seven cells in one row, exactly one lit, at the position that
    day occupies in THIS language's week — which is the whole reason
    ``week_start`` is an argument and not a constant."""
    picture = _parse(render_calendar_svg(f"weekday_{day}", week_start=week_start))
    assert _row_is(picture)
    assert _emitted_left_to_right(picture)
    assert _lit_at(picture, _WEEK_ORDER[week_start].index(day))
    assert _weekend_tint_is(picture, week_start)
    assert _unlabelled(picture), "a day must be readable by position, never by reading it"


@pytest.mark.parametrize("week_start", WEEK_STARTS)
def test_the_two_week_orders_never_agree_on_a_days_position(week_start: str) -> None:
    """The property that catches a Sunday-first week drawn Monday-first, stated
    as a shape rather than a spot check: for every day the lit position is where
    this file's order says it is, and the two starts disagree on all seven."""
    other = "sunday" if week_start == "monday" else "monday"
    for day in range(1, 8):
        picture = _parse(render_calendar_svg(f"weekday_{day}", week_start=week_start))
        assert _lit_at(picture, _WEEK_ORDER[week_start].index(day)), (week_start, day)
        assert not _lit_at(picture, _WEEK_ORDER[other].index(day)), (week_start, day)


@pytest.mark.parametrize("week_start", WEEK_STARTS)
def test_exactly_two_cells_of_seven_carry_the_weekend_tint(week_start: str) -> None:
    """Two, always: both weekend days, except on the two cards where the lit day
    IS one of them and the tint has nowhere to go."""
    for day in range(1, 8):
        picture = _parse(render_calendar_svg(f"weekday_{day}", week_start=week_start))
        tinted = [i for i, c in enumerate(picture.ordered()) if c.weekend]
        expected = [i for i, iso in enumerate(_WEEK_ORDER[week_start]) if iso in _WEEKEND_DAYS and iso != day]
        assert tinted == expected, (week_start, day)


# ── The page around the grid: a sheet, not a bare grid ─────────────────────


@pytest.mark.parametrize("week_start", WEEK_STARTS)
def test_the_sheet_has_two_binding_rings_above_it(week_start: str) -> None:
    """A spiral binding, which is what says "calendar" before any cell is read —
    and the rings are the only thing on the canvas ABOVE the page's top edge."""
    picture = _parse(render_calendar_svg("weekday_1", week_start=week_start))
    rings = [b for b in _boxes(render_calendar_svg("weekday_1", week_start=week_start)) if b.fill == _LINE]
    assert len(rings) == 2
    assert all(r.top < picture.page.top for r in rings)
    assert {r.cx for r in rings} == {picture.page.left + 45, picture.page.right - 45}


@pytest.mark.parametrize("concept", CALENDAR_CONCEPTS)
def test_the_sheet_has_a_header_band_above_the_grid(concept: str) -> None:
    """The band is what makes the rectangle read as a page with a heading on it,
    and it has to sit ABOVE the grid: a band behind the cells would be a wash
    across them rather than a heading."""
    svg = render_calendar_svg(concept, week_start="monday")
    picture = _parse(svg)
    bands = [b for b in _boxes(svg) if b.height == 24]
    assert len(bands) == 1, "one header band, and nothing else that tall and thin"
    band = bands[0]
    assert band.top > picture.page.top and band.bottom < min(c.top for c in picture.cells)
    assert picture.page.left <= band.left and band.right <= picture.page.right
    assert band.width < picture.page.width, "an inset band, so the page's own edge still reads"


# ── Fit: the property the first mockup failed ───────────────────────────────


@pytest.mark.parametrize("concept", CALENDAR_CONCEPTS)
@pytest.mark.parametrize("week_start", WEEK_STARTS)
def test_every_cell_is_inside_the_page_and_the_page_inside_the_frame(concept: str, week_start: str) -> None:
    assert _fits(_parse(render_calendar_svg(concept, week_start=week_start)))


@pytest.mark.parametrize("concept", CALENDAR_CONCEPTS)
def test_the_page_leaves_more_room_than_the_check_demands(concept: str) -> None:
    """The check asks for 4; the drawing gives 8 at the sides and 10 below it. The
    slack is asserted so that tightening the layout later is a visible decision
    rather than a silent one — the first overflow came from exactly that."""
    picture = _parse(render_calendar_svg(concept, week_start="monday"))
    assert min(c.left - picture.page.left for c in picture.cells) >= 4
    assert min(picture.page.right - c.right for c in picture.cells) >= 4
    assert min(picture.page.bottom - c.bottom for c in picture.cells) >= 4


@pytest.mark.parametrize("concept", CALENDAR_CONCEPTS)
@pytest.mark.parametrize("week_start", WEEK_STARTS)
def test_every_shape_is_inside_the_viewbox(concept: str, week_start: str) -> None:
    svg = render_calendar_svg(concept, week_start=week_start)
    picture = _parse(svg)
    for box in _boxes(svg):
        assert box.left >= 0 and box.top >= 0, (concept, week_start)
        assert box.right <= picture.width and box.bottom <= picture.height, (concept, week_start)


# ── The frame, and what a card may not do with it ───────────────────────────


@pytest.mark.parametrize("concept", CALENDAR_CONCEPTS)
def test_is_well_formed_xml_on_an_svg_root_with_a_viewbox(concept: str) -> None:
    root = _root(render_calendar_svg(concept, week_start="monday"))
    assert root.tag == f"{_SVG}svg"
    assert root.get("viewBox") == "0 0 260 220"
    assert (root.get("width"), root.get("height")) == ("260", "220")


@pytest.mark.parametrize("concept", CALENDAR_CONCEPTS)
def test_paints_its_own_opaque_background_first(concept: str) -> None:
    """An ``<img>`` cannot inherit ``currentColor``, so a transparent render with
    dark cells vanishes in Anki's night mode — the one-palette rule the other
    three families hold, and why this family has a colour module at all."""
    first = _root(render_calendar_svg(concept, week_start="monday"))[0]
    assert first.tag == f"{_SVG}rect"
    assert first.get("fill") == _BG
    assert (_num(first, "x"), _num(first, "y")) == (0, 0)
    assert (_num(first, "width"), _num(first, "height")) == _FRAME


@pytest.mark.parametrize("concept", CALENDAR_CONCEPTS)
def test_the_picture_keeps_an_aspect_a_card_can_scale(concept: str) -> None:
    picture = _parse(render_calendar_svg(concept, week_start="monday"))
    assert 1.0 < picture.width / picture.height < 1.5


@pytest.mark.parametrize("concept", CALENDAR_CONCEPTS)
@pytest.mark.parametrize("week_start", WEEK_STARTS)
def test_is_deterministic(concept: str, week_start: str) -> None:
    assert render_calendar_svg(concept, week_start=week_start) == render_calendar_svg(concept, week_start=week_start)


# ── Discrimination: two words must not share one page ───────────────────────


def test_no_two_weekday_renders_are_the_same_page() -> None:
    """A calendar is the one picture where a copy-paste is invisible: move the lit
    cell one place and the page still looks like a page. So every weekday is
    compared with every other, in both week orders."""
    renders = {
        (concept, week_start): render_calendar_svg(concept, week_start=week_start)
        for concept in CALENDAR_CONCEPTS
        if concept.startswith("weekday")
        for week_start in WEEK_STARTS
    }
    for (a, sa), svg_a in renders.items():
        for (b, sb), svg_b in renders.items():
            if (a, sa) != (b, sb):
                assert svg_a != svg_b, f"{a}/{sa} and {b}/{sb} are the same page"


def test_the_two_week_starts_never_produce_the_same_week() -> None:
    """Seven renders, each of which must differ from its own counterpart in the
    other order — otherwise the argument is decoration."""
    for day in range(1, 8):
        assert render_calendar_svg(f"weekday_{day}", week_start="monday") != render_calendar_svg(
            f"weekday_{day}", week_start="sunday"
        ), day


# ── Contrast, measured off the fills the pictures carry ─────────────────────


@pytest.mark.parametrize("concept", CALENDAR_CONCEPTS)
@pytest.mark.parametrize("week_start", WEEK_STARTS)
def test_every_colour_pair_in_this_picture_is_legible(concept: str, week_start: str) -> None:
    _legible(render_calendar_svg(concept, week_start=week_start))


def test_every_measured_pair_is_still_reachable_in_some_picture() -> None:
    """Otherwise a colour could be dropped and the floor test would never know."""
    present = {
        (one, two)
        for concept in CALENDAR_CONCEPTS
        for week_start in WEEK_STARTS
        for (one, two) in _MEASURED
        if {one, two} <= set(_palette(render_calendar_svg(concept, week_start=week_start)))
    }
    assert present == set(_MEASURED)


def test_the_darker_cell_fill_is_the_one_the_checker_calls_lit() -> None:
    """Which fill the checker calls "lit" is settled by legibility, not by name:
    it is the one that carries white ink and is still seen, where a pale cell
    cannot."""
    roles = _palette(render_calendar_svg("month_1", week_start="monday"))
    assert _luminance(roles["lit"]) < _luminance(roles["cell"])
    assert _contrast(_DIGIT, roles["lit"]) >= 3.0
    assert _contrast(_DIGIT, roles["cell"]) < 2.0, "white ink on a pale cell is unreadable"


def test_the_digit_is_the_sheets_own_white() -> None:
    """One ink for the paper and the number on it, so a month reads as part of
    the page rather than as a sticker on it. Asserted because it is two names
    for one colour, which is exactly the shape that drifts apart silently."""
    assert _DIGIT == _PAGE
    roles = _palette(render_calendar_svg("month_1", week_start="monday"))
    assert roles["digit"] == roles["page"]


@pytest.mark.parametrize("week_start", WEEK_STARTS)
def test_the_two_unlit_fills_differ_in_lightness_not_only_in_hue(week_start: str) -> None:
    """The brief's own property (>= 1.5), stated as what it is FOR: a reader who
    cannot see hue has to be able to tell the weekend off the week."""
    for day in range(1, 8):
        roles = _palette(render_calendar_svg(f"weekday_{day}", week_start=week_start))
        assert _contrast(roles["weekend"], roles["cell"]) >= 1.5
        assert _luminance(roles["weekend"]) < _luminance(roles["cell"]), (week_start, day)


# ── The probes: each check, shown to reject a real near-miss ────────────────


def _fmt(value: float) -> str:
    return f"{value:g}"


def _cells_in_reading_order(text: str) -> list[re.Match[str]]:
    return sorted(_CELL_EL.finditer(text), key=lambda m: (float(m["y"]), float(m["x"])))


def _with_cell(svg: bytes, index: int, **changes: str) -> bytes:
    """The cell at reading-order *index*, with its attributes replaced."""
    text = svg.decode()
    target = _cells_in_reading_order(text)[index]
    element = target.group(0)
    for name, value in changes.items():
        element = re.sub(rf'\b{name}="[^"]*"', f'{name}="{value}"', element, count=1)
    return (text[: target.start()] + element + text[target.end() :]).encode()


def _drop_cell(svg: bytes, index: int) -> bytes:
    """The cell at reading-order *index*, gone."""
    text = svg.decode()
    target = _cells_in_reading_order(text)[index]
    return (text[: target.start()] + text[target.end() :]).encode()


def _move_digit_to(svg: bytes, x: str, y: str) -> bytes:
    """The number, somewhere else on the page — the one piece of ink there is."""
    text = svg.decode()
    match = _TEXT_EL.search(text)
    assert match is not None, "a month render carries exactly one label to move"
    attributes = match.group(0)[match.group(0).index(" text-anchor") :]
    return (text[: match.start()] + f'<text x="{x}" y="{y}"{attributes}' + text[match.end() :]).encode()


def test_a_lit_cell_moved_one_place_is_not_the_month() -> None:
    """July drawn as August: still a calendar, so nothing structural notices.
    The lit POSITION, read off the geometry, does — and the number is carried
    along with it, so this probe isolates the position rather than the label."""
    svg = render_calendar_svg("month_7", week_start="monday")
    cells = _parse(svg).ordered()
    assert _lit_at(_parse(svg), 6)
    moved = _with_cell(_with_cell(svg, 6, fill=_PALE), 7, fill=_LIT)
    moved = _move_digit_to(moved, _fmt(cells[7].cx), _fmt(cells[7].cy + 8))
    assert not _lit_at(_parse(moved), 6)
    assert _lit_at(_parse(moved), 7)
    assert _digit_is(_parse(moved), "7", 7), "the number went with the cell, so only the position is wrong"


def test_a_digit_outside_its_own_cell_is_refused() -> None:
    """The number is the one piece of ink allowed, so where it sits is the whole
    check: a digit that slid off its cell is a card nobody can read."""
    svg = render_calendar_svg("month_7", week_start="monday")
    assert _digit_is(_parse(svg), "7", 6)
    moved = _move_digit_to(svg, "35", "35")  # the page's corner, over the header
    assert not _digit_is(_parse(moved), "7", 6)
    assert _lit_at(_parse(moved), 6), "only the number moved, not the cell"


def test_a_label_saying_something_other_than_the_month_is_refused() -> None:
    """The digit has to BE the number the concept names: a label carrying a word
    would let the card be answered by reading it, which is the rule the week
    holds and the one exception here is careful about."""
    svg = render_calendar_svg("month_7", week_start="monday")
    spelled = svg.decode().replace(">7</text>", ">July</text>")
    assert not _digit_is(_parse(spelled.encode()), "7", 6)
    assert _lit_at(_parse(spelled.encode()), 6), "the cell is still right; only the ink is wrong"


def test_a_label_on_a_week_row_is_refused() -> None:
    """A weekday must be readable by POSITION. The instant a word is printed on
    the row, the card can be answered by reading it."""
    svg = render_calendar_svg("weekday_1", week_start="monday")
    assert _unlabelled(_parse(svg))
    labelled = svg.decode().replace("</svg>", '<text x="130" y="127">Monday</text></svg>')
    assert not _unlabelled(_parse(labelled.encode()))


def test_a_sunday_first_week_drawn_monday_first_is_refused() -> None:
    """The probe the brief asks for, in its real form: the whole row is laid out
    the other way, so the weekend lands on Friday and Saturday and Sunday moves
    to the far end. Both the lit position and the tint have to catch it, and the
    tint is the one that would not, if it were checked by counting."""
    svg = render_calendar_svg("weekday_7", week_start="sunday")
    assert _lit_at(_parse(svg), 0)
    assert _weekend_tint_is(_parse(svg), "sunday")
    wrong = _with_cell(svg, 0, fill=_PALE)  # Sunday's cell, now Monday's
    wrong = _with_cell(wrong, 5, fill=_WEEKEND)  # Friday wears the weekend tint
    wrong = _with_cell(wrong, 6, fill=_LIT)  # and Saturday is the lit day
    picture = _parse(wrong)
    assert not _weekend_tint_is(picture, "sunday"), "Friday is not a weekend day"
    assert not _lit_at(picture, 0)
    assert _lit_at(picture, 6)


def test_a_weekend_tint_on_a_weekday_is_refused() -> None:
    """Friday wearing the weekend tint claims the week ends on Thursday: a
    different calendar, not a different shade."""
    svg = render_calendar_svg("weekday_1", week_start="monday")
    assert _weekend_tint_is(_parse(svg), "monday")
    assert not _weekend_tint_is(_parse(_with_cell(svg, 4, fill=_WEEKEND)), "monday")
    assert _weekend_tint_is(_parse(svg), "monday"), "the untouched render is still right"


def test_a_weekend_tint_moved_off_a_weekend_day_is_refused() -> None:
    """The other direction, which a count of tinted cells would not see: still
    exactly two tinted cells, but one of them is a Tuesday."""
    svg = render_calendar_svg("weekday_1", week_start="monday")
    wrong = _with_cell(svg, 5, fill=_PALE)  # Saturday loses the tint
    wrong = _with_cell(wrong, 1, fill=_WEEKEND)  # Tuesday gains it
    assert len([c for c in _parse(wrong).ordered() if c.weekend]) == 2
    assert not _weekend_tint_is(_parse(wrong), "monday")


def test_a_cell_pushed_past_the_page_edge_is_refused() -> None:
    """The first mockup's overflow. The counts, the grid and the colours are all
    still correct, and the card shows six and a half days."""
    svg = render_calendar_svg("weekday_1", week_start="monday")
    assert _fits(_parse(svg))
    assert not _fits(_parse(_with_cell(svg, 6, x="232")))
    assert _fits(_parse(svg)), "the untouched render is still inside the page"


def test_a_lit_cell_painted_pale_is_refused_before_any_ratio_is_measured() -> None:
    """The lit cell repainted: count, grid, position and colours-as-counted all
    still pass, and the white number on it drops to 1.17:1. This is the probe
    that says the contrast check is load-bearing rather than decorative.

    Which refusal fires is worth being exact about, because the two are
    different guards. Repainting the cell destroys the `lit` role, so the
    picture arrives carrying no lit colour to measure at all, and the
    ``_REQUIRED`` precondition catches it FIRST. A contrast floor cannot be
    evaluated on a picture that has stopped carrying one of the two colours it
    compares — the ratio assert never gets to run, and the 1.17:1 would go
    unmeasured if this precondition were dropped. The precondition is therefore
    the load-bearing part of the legibility check, not a nicety in front of it.
    """
    svg = render_calendar_svg("month_7", week_start="monday")
    _legible(svg)
    repainted = _with_cell(svg, 6, fill=_PALE)
    assert "lit" not in _palette(repainted), "the lit role is gone, not merely weakened"
    with pytest.raises(AssertionError, match=r"no \['lit'"):
        _legible(repainted)


def test_a_lit_cell_painted_in_the_weekend_tint_is_caught_too() -> None:
    """Same failure in the other direction: a lit cell that reads like a weekend
    cell is a month that looks like a Sunday. The adjacency pair
    (lit/weekend, 3.61) is the guard that WOULD catch it if a lit role
    survived — and it does not, because repainting removes the role, so this is
    caught by the same precondition. Both refusals are named so the probes
    cannot pass for the wrong reason."""
    svg = render_calendar_svg("month_1", week_start="monday")
    repainted = _with_cell(svg, 0, fill=_WEEKEND)
    assert "weekend" in _palette(repainted), "the tint is still there to be confused with"
    assert "lit" not in _palette(repainted)
    with pytest.raises(AssertionError, match=r"no \['lit'"):
        _legible(repainted)


#: A tint that differs from the pale cell in HUE and roughly matches it in
#: lightness (0.588 against the pale cell's 0.851) — a colour-blind-safe reader
#: sees nothing. It stands in for a plausible "let me warm the weekend tint up"
#: edit, which is exactly the change the 1.5 floor exists to refuse.
_HUE_ONLY = "#c0c8f0"


def test_a_weekend_tint_differing_only_in_hue_is_refused() -> None:
    """The 1.5 floor, with the probe that gives it a job. The near-miss is a
    weekend tint at the SAME lightness as the ordinary cell and a different hue:
    it is a perfectly reasonable-looking colour choice, and it is invisible to
    any reader who cannot see hue, which is why the brief asks for a LIGHTNESS
    difference rather than merely a different colour.

    Two guards have to be shown firing, and the order matters. The palette is
    read by SLOT, so this tint is still recognised as the weekend fill (an
    exact-hex palette would drop the role and skip the pair entirely) — and
    then the ratio it produces is below the floor, so the floor refuses it.
    """
    assert _contrast(_HUE_ONLY, _PALE) < 1.5, "the near-miss must be below the floor to be a probe"
    svg = render_calendar_svg("weekday_1", week_start="monday")
    nudged = _with_cell(_with_cell(svg, 5, fill=_PALE), 6, fill=_HUE_ONLY)
    roles = _palette(nudged)
    assert roles["weekend"] == _HUE_ONLY, "read by slot, so the nudged tint is still measured"
    with pytest.raises(AssertionError, match="weekend on cell is only"):
        _pair_legible(roles, "weekend", "cell")
    with pytest.raises(AssertionError):
        _legible(nudged)
    _pair_legible(_palette(svg), "weekend", "cell"), "the real render clears it"


def test_a_missing_cell_is_refused() -> None:
    """Eleven months in the year. The COUNT is the only check that catches this,
    and the probe is here to say so: dropping one cell of a column of three
    leaves four distinct columns and three distinct rows, so the grid is still a
    perfect 4x3 and December's own position no longer exists at all."""
    svg = render_calendar_svg("month_12", week_start="monday")
    assert _count_is(_parse(svg), 12)
    short = _drop_cell(svg, 0)
    assert not _count_is(_parse(short), 12)
    assert _grid_is(_parse(short), 4, 3), "which is why the count is the check that matters"
    assert not _lit_at(_parse(short), 11), "and December's position is simply gone"
    assert _lit_at(_parse(svg), 11), "the untouched render has it"


def test_a_cell_moved_off_its_column_or_row_is_refused() -> None:
    """The grid is what makes "the third one" mean anything: a cell that has
    drifted out of its column is a different position in the year."""
    svg = render_calendar_svg("month_1", week_start="monday")
    assert _grid_is(_parse(svg), 4, 3)
    assert not _grid_is(_parse(_with_cell(svg, 0, x="60")), 4, 3), "a fifth column appeared"
    assert not _grid_is(_parse(_with_cell(svg, 0, y="90")), 4, 3), "a fourth row appeared"
    assert _fits(_parse(_with_cell(svg, 0, x="60"))), "so the fit check is not what catches it"


def test_two_cells_drawn_on_top_of_each_other_are_refused() -> None:
    """A week row that overlaps itself counts seven days and shows six — and
    sorting by centre cannot see it, which is why the row is checked for tiling."""
    svg = render_calendar_svg("weekday_1", week_start="monday")
    assert _row_is(_parse(svg))
    assert not _row_is(_parse(_with_cell(svg, 0, x="60")))
    assert _emitted_left_to_right(_parse(_with_cell(svg, 0, x="60"))), "and the order is still fine"


def test_cells_emitted_out_of_order_are_refused() -> None:
    """The document order is what makes the markup read as a row, and a swap is
    invisible to every check that reads the picture in reading order."""
    svg = render_calendar_svg("weekday_1", week_start="monday")
    assert _emitted_left_to_right(_parse(svg))
    swapped = _with_cell(svg, 0, x="65")
    swapped = _with_cell(swapped, 1, x="38")
    assert not _emitted_left_to_right(_parse(swapped))
    assert _row_is(_parse(swapped)), "so the tiling check is not what catches it"
