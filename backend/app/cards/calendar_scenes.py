"""Calendar pictures: a year of months, and a week of days.

**The month and the weekday are the one pair of concepts where a photo search
cannot be argued with.** Asked for "March" it returns a march of people; asked
for "Monday" it returns whatever weekday the photographer liked. Neither is the
thing the word names, which is a **position in a structure** — the third cell of
the third row of a year, the first cell of a week. So the structure is drawn, with
the one cell the word points at filled, and the claim becomes a number a test
reads off the markup. Same argument, one vocabulary across, as
``app.cards.number_scenes`` and ``app.cards.spatial_scenes``; the pictures look
like diagrams and that cost was accepted for those too.

**A month is the one drawn picture that carries a word, and only a number.** Every
other family here refuses to print anything, because a printed answer lets a
vocabulary card be answered by reading it. A month has to break the rule, and the
reason is that its content is an ORDINAL: "the seventh cell" is not a thing a
learner can see, and the position means the same thing in every language while
the word does not. So the lit month cell carries its own number — 1 to 12, never
a name — and the week carries nothing at all, because a day's position in a row
of seven *is* visible.

**The week start is data, and this module holds no opinion about it.** Whether a
week opens on Monday or on Sunday is a local custom, not a fact about calendars:
drawing the Philippine languages Monday-first would quietly give every Sunday card
the picture of a Monday. So ``week_start`` is an argument, the order is looked up
from it, and a value this module does not know is refused rather than approximated
— see :func:`render_calendar_svg`.

**The weekend is drawn, not labelled.** The two weekend cells carry a tint that
differs from an ordinary cell in LIGHTNESS rather than in hue, which is what lets
the row read at card size to a reader who cannot see the colour names. The tint is
on both weekend days except where one of them is the lit cell, which is filled
instead — the case that would be missed by any rule written for the common one.

**The palette is shared with the counting and spatial pictures** — background,
cell fill, pale fill, page outline and header band all imported rather than
redeclared — so all four families read as one deck. Only the weekend tint is new,
and it is a colour property here rather than a style note: the tests recompute
the WCAG ratio of every pair this picture carries off its own attributes, and hold
each to the ratio measured before this was written.
"""

from __future__ import annotations

from app.cards.number_image import _BG, _DOT_FILL, _ROD_FILL
from app.cards.spatial_scenes import _ACCENT, _BOX_STROKE

#: The concepts this module knows how to draw, and the only ones. ``month_n`` is
#: the nth month of a year and ``weekday_n`` the nth day of a week in ISO order,
#: where 1 is Monday and 7 is Sunday — which is why a language's week start is a
#: reorder of this numbering rather than a renumbering of it.
CALENDAR_CONCEPTS: tuple[str, ...] = (
    *(f"month_{n}" for n in range(1, 13)),
    *(f"weekday_{d}" for d in range(1, 8)),
)

#: Cells per row and per column in the year. A year reads left to right, top to
#: bottom, so the month's cell is at reading-order position n - 1.
_MONTH_COLUMNS = 4
_MONTH_COUNT = 12

#: Cells in the week, and which ISO days are the weekend. Exposed through the
#: layout rather than as a constant of their own, because the draw order already
#: carries the answer for whichever ``week_start`` was asked for.
_WEEK_DAYS = 7
_WEEKEND_DAYS = frozenset({6, 7})  # Saturday, Sunday

#: ISO day numbers in drawing order, by week start. A Sunday-first week opens
#: with 7 and runs to Saturday; a Monday-first week is the identity. This is the
#: ONLY place the local custom lives, and it is keyed on the caller's value.
_WEEK_ORDER: dict[str, tuple[int, ...]] = {
    "monday": (1, 2, 3, 4, 5, 6, 7),
    "sunday": (7, 1, 2, 3, 4, 5, 6),
}

#: The weekend tint. Chosen as a DESATURATED step darker than the pale cell fill
#: rather than as a different hue: the row has to read without labels, and a hue
#: difference alone is invisible to a reader who cannot see the colour names.
#: Measured at 1.57:1 against an ordinary cell, which is the ">= 1.5, and in
#: lightness" property the tests hold.
WEEKEND_FILL = "#e2b79c"

#: The paper, and the ink on the lit month cell. One colour in two roles, so a
#: month reads as part of the page rather than as a label stuck onto it. It is
#: also the pair the contrast floor measures hardest: 6.60:1 against the lit cell
#: is the only reason a number can be printed on one at all.
_PAGE_FILL = "#ffffff"
_DIGIT_FILL = "#ffffff"

#: The lit cell's outline. The deck's reference blue — the same one the pronoun
#: referents are outlined in — held here rather than imported so that this family
#: stays independent of the other three; two modules naming one colour is a
#: decision a reader can see, and an import of a private name across families is
#: a coupling none of them asked for.
_LIT_STROKE = "#1f406f"
#: A lit cell is outlined twice as hard as an unlit one, so a row of twelve does
#: not read as a ragged edge with a smudge in it. An unlit cell is 1.10:1 on the
#: paper, so its outline is the only reason it is visible at all.
_LIT_STROKE_W = 2
_UNLIT_STROKE_W = 1.2

# ── One frame for every concept ─────────────────────────────────────────────
# The same 260x220 the other three families use, so a card in a mixed deck is one
# size. Nothing is ever drawn to the boundary: the binding rings sit above the
# page's top edge and the page itself is inset all round.

_W = 260
_H = 220
_PAGE_TOP = 30
_PAGE_BOTTOM = 204
_PAGE_LEFT = 30
_PAGE_RIGHT = 230

#: The header band across the top of the sheet, and the rule under it. The band is
#: what makes the rectangle read as a page with a heading on it; the rule is where
#: the heading stops and the grid starts, which is why it is at ``top + 26`` and
#: the band ends at ``top + 25``.
_HEADER_H = 24
_HEADER_RULE_DY = 26
_HEADER_RX = 9
_HEADER_OPACITY = ".85"

#: The two binding rings, and how far above the page they start. They straddle the
#: page's top edge, which is what makes them read as rings THROUGH a page rather
#: than as tabs on top of it.
_RING_W = 6
_RING_H = 16
_RING_DY = 9
_RING_DX = 45
_RING_RX = 3

#: Every cell rounds at this radius, which is what the tests find a cell by: the
#: page rounds at 10, the header band at 9, the rings at 3, the background is
#: square. A cell in the wrong colour is still a cell, so a repainted one is still
#: found by the contrast check instead of vanishing from the count.
_CELL_RX = 5

#: The year grid. Four columns of three, with the left and right margins at 11
#: and the bottom at 10 — every one of them comfortably past the 4px the fit
#: check demands, because the first mockup ran a cell off the page's right edge
#: and nothing structural would have complained.
_MONTH_X0 = 41
_MONTH_Y0 = 68
_MONTH_CW = 40
_MONTH_CH = 38
_MONTH_GAP = 6

#: The week row: one row of seven tall cells, 8px in from each side of the sheet.
_WEEK_X0 = 38
_WEEK_Y0 = 72
_WEEK_CW = 22
_WEEK_CH = 110
_WEEK_GAP = 5

#: The month's number, set in the one typeface the other families' cards already
#: assume, dropped 8 below the cell's centre so it sits optically centred rather
#: than mathematically centred — digits have no descender, so the geometric centre
#: of the cell reads a line low.
_DIGIT_DY = 8
_DIGIT_SIZE = 22
_DIGIT_FONT = "Helvetica,Arial,sans-serif"


def _n(value: float) -> str:
    """Format a coordinate, trimmed so the markup carries no ``48.000000``."""
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _page(inner: str) -> str:
    """The sheet: a page with a header, two binding rings, and *inner* in it.

    ``inner`` is the grid, and it is the last thing emitted so that every cell is
    painted over the page rather than under it — the reverse would hide the grid
    behind the fill.
    """
    rings = "".join(
        f'<rect x="{_n(x - _RING_W / 2)}" y="{_n(_PAGE_TOP - _RING_DY)}" width="{_n(_RING_W)}" '
        f'height="{_n(_RING_H)}" rx="{_RING_RX}" fill="{_BOX_STROKE}"/>'
        for x in (_PAGE_LEFT + _RING_DX, _PAGE_RIGHT - _RING_DX)
    )
    return (
        f'<rect x="{_PAGE_LEFT}" y="{_PAGE_TOP}" width="{_n(_PAGE_RIGHT - _PAGE_LEFT)}" '
        f'height="{_n(_PAGE_BOTTOM - _PAGE_TOP)}" rx="10" fill="{_PAGE_FILL}" '
        f'stroke="{_BOX_STROKE}" stroke-width="2"/>'
        f'<path d="M{_PAGE_LEFT} {_n(_PAGE_TOP + _HEADER_RULE_DY)} H{_PAGE_RIGHT}" '
        f'stroke="{_BOX_STROKE}" stroke-width="2"/>'
        f'<rect x="{_PAGE_LEFT + 1}" y="{_PAGE_TOP + 1}" '
        f'width="{_n(_PAGE_RIGHT - _PAGE_LEFT - 2)}" height="{_HEADER_H}" rx="{_HEADER_RX}" '
        f'fill="{_ACCENT}" opacity="{_HEADER_OPACITY}"/>'
        f"{rings}{inner}"
    )


def _cell(x: float, y: float, width: float, height: float, *, lit: bool, unlit_fill: str | None = None) -> str:
    """One day of the grid: filled when the word points at it, pale when not.

    ``unlit_fill`` is how the weekend tint gets in without this function knowing
    what a weekend is — the caller owns the week order, so it owns the tint too.
    """
    if lit:
        fill, stroke, stroke_width = _DOT_FILL, _LIT_STROKE, _LIT_STROKE_W
    else:
        fill, stroke, stroke_width = unlit_fill or _ROD_FILL, _BOX_STROKE, _UNLIT_STROKE_W
    return (
        f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(width)}" height="{_n(height)}" '
        f'rx="{_CELL_RX}" fill="{fill}" stroke="{stroke}" stroke-width="{_n(stroke_width)}"/>'
    )


def _digit(cx: float, cy: float, n: int) -> str:
    """The month's own number, and the only ink in the family that is a word."""
    return (
        f'<text x="{_n(cx)}" y="{_n(cy)}" text-anchor="middle" '
        f'font-family="{_DIGIT_FONT}" font-size="{_DIGIT_SIZE}" font-weight="700" '
        f'fill="{_DIGIT_FILL}">{n}</text>'
    )


def _year(n: int) -> str:
    """A year of twelve cells, the *n*th one filled and carrying its number.

    Placement is by (row, column) in reading order, which is what makes the lit
    cell's position — the thing the card actually asks — equal to ``n - 1`` and
    not merely near it.
    """
    parts: list[str] = []
    for i in range(_MONTH_COUNT):
        row, column = divmod(i, _MONTH_COLUMNS)
        x = _MONTH_X0 + column * (_MONTH_CW + _MONTH_GAP)
        y = _MONTH_Y0 + row * (_MONTH_CH + _MONTH_GAP)
        lit = i == n - 1
        parts.append(_cell(x, y, _MONTH_CW, _MONTH_CH, lit=lit))
        if lit:
            parts.append(_digit(x + _MONTH_CW / 2, y + _MONTH_CH / 2 + _DIGIT_DY, n))
    return "".join(parts)


def _week(day: int, week_start: str) -> str:
    """A week of seven cells, the *day* one filled, in the language's own order.

    The weekend tint is applied to every cell whose ISO day is a weekend day and
    which is not the lit one — a filled cell needs no tint, and the two cards where
    the lit day IS the weekend are exactly the ones a rule written for the common
    case would get wrong.
    """
    parts: list[str] = []
    for i, iso in enumerate(_WEEK_ORDER[week_start]):
        lit = iso == day
        parts.append(
            _cell(
                _WEEK_X0 + i * (_WEEK_CW + _WEEK_GAP),
                _WEEK_Y0,
                _WEEK_CW,
                _WEEK_CH,
                lit=lit,
                unlit_fill=WEEKEND_FILL if iso in _WEEKEND_DAYS else None,
            )
        )
    return "".join(parts)


def render_calendar_svg(concept: str, *, week_start: str) -> bytes:
    """Draw the calendar *concept* names, as SVG bytes.

    ``week_start`` is ``"monday"`` or ``"sunday"`` and is the caller's language
    data, not a default: which day opens a week is a local custom, and getting it
    wrong is not a near-miss but a different week. It changes nothing for a month,
    which holds no week.

    Raises ``ValueError`` for a concept this module cannot draw, or a week start
    it cannot lay out, rather than emitting a plausible-looking page: a calendar
    with eleven months, or a Sunday-first deck drawing Mondays, is worse than no
    picture, because it is confidently wrong.
    """
    if concept not in CALENDAR_CONCEPTS:
        raise ValueError(
            f"{concept!r} is not a renderable calendar concept; expected one of {', '.join(CALENDAR_CONCEPTS)}"
        )
    if week_start not in _WEEK_ORDER:
        raise ValueError(
            f"{week_start!r} is not a week start this module can lay out; expected one of {', '.join(_WEEK_ORDER)}"
        )
    if concept.startswith("month_"):
        inner = _year(int(concept.removeprefix("month_")))
    else:
        inner = _week(int(concept.removeprefix("weekday_")), week_start)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_W} {_H}" width="{_W}" height="{_H}">'
        f'<rect x="0" y="0" width="{_W}" height="{_H}" fill="{_BG}"/>'
        f"{_page(inner)}</svg>"
    )
    return svg.encode("utf-8")
