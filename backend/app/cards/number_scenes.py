"""The two pictures a number word wants when it is used as an hour or a price.

A native cardinal gets a heap of dots — ``app.cards.number_image`` draws the
quantity, and thirteen is one rod and three dots. The Spanish-derived numbers of
Tagalog and Cebuano are not used as bare quantities: ``alas singko`` is *five
o'clock*, and the same words on a price list are money. A heap of five dots is a
true answer to a question nobody asked, so these two words get drawn in the
context they are actually used in, per the user's decision (2026-09-27):

- **1–12 → an analog clock** stopped at that hour;
- **13 and up → the coins and bills** that make the amount.

**Why rendered rather than fetched, which is the whole case.** Pixabay cannot be
asked for a clock reading five o'clock, and a fetched photograph of a clock face
cannot be *checked* for reading five o'clock — that is a vision problem with no
oracle, so a test could only assert that some bytes came back. A render is true
by construction: the hour hand is a line whose endpoints are its own answer, so
the test reads the angle off the coordinates and compares it to a table. The
money picture is the same argument one level down — a piece carries its value in
``data-value``, so "this picture is worth exactly *n*" is a sum.

The cost is honest and was accepted for the counting picture too: these look like
diagrams, and every other card in the deck is a photograph.

**Nothing here is per-language.** The symbol and the denominations are
*parameters*, loaded from each language's ``numbers.json`` by the caller — which
is what keeps this module clear of a currency literal. It shares
``number_image``'s palette rather than defining its own, so all three pictures
read as one deck; it is a separate module precisely because it imports from that
one and they must not import each other.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from xml.sax.saxutils import escape

from app.cards.number_image import _BG, _DOT_FILL, _ROD_FILL, _ROD_STROKE

#: The last hour on the dial. Twelve, because a clock has twelve hours and
#: "thirteen o'clock" is not a thing a clock can be stopped at.
MAX_CLOCK_HOUR = 12

# ── Clock geometry ─────────────────────────────────────────────────────────
# A near-square face: the drill card scales an image to its width, so anything
# much wider than tall shrinks the thing the card is about. This one is square
# by construction and needs no wrapping argument.

_FACE_R = 100  # dial radius
_MARGIN = 20  # around the whole drawing
_TICK_OUT = 92  # ticks stop just inside the rim …
_TICK_IN_MINOR = 72  # … and start here, or nearer for the cardinals
_TICK_IN_MAJOR = 62
_MINOR_W = 3
_MAJOR_W = 5
_HOUR_LEN = 58  # the hour hand is the short one; that is how a clock reads
_MINUTE_LEN = 82

# ── Money geometry ─────────────────────────────────────────────────────────
# The value is written ON the piece, the way real money carries it — a label
# underneath read as a caption, and the first cut's white coin labels vanished
# into the background below the coin. Three per row keeps the widest picture
# (three pieces, one row) at 312x112, under the 3:1 bound the counting picture
# also holds. Integer coordinates throughout: the tests read them with int().

_CELL_W = 84
_CELL_H = 72
_PIECE_GAP = 10
_ROW_GAP = 16
_MARGIN_MONEY = 20
_PIECES_PER_ROW = 3
_COIN_R = 30
_BILL_W = 80
_BILL_H = 52
_COIN_LABEL_SIZE = 16
_BILL_LABEL_SIZE = 18
_COIN_LABEL_COLOR = "#ffffff"  # on the coin's dark fill


def _polar(cx: float, cy: float, radius: float, degrees: float) -> tuple[float, float]:
    """A point *radius* from ``(cx, cy)`` at *degrees* clockwise from straight up.

    SVG's y axis grows downward, hence the minus on the cosine. This is the one
    place the dial's handedness is decided, and getting it wrong mirrors 3 and 9
    while leaving 12 and 6 correct — which is why the tests read all four.
    """
    radians = math.radians(degrees)
    return cx + radius * math.sin(radians), cy - radius * math.cos(radians)


def _n(value: float) -> str:
    """Format a coordinate, trimmed so the markup carries no ``48.000000``."""
    return f"{value:.1f}".rstrip("0").rstrip(".")


def render_clock_svg(hour: int) -> bytes:
    """Draw an analog face stopped at *hour* o'clock, as SVG bytes.

    The hour hand is a line from the centre at ``hour * 30`` degrees; the minute
    hand points at twelve and is the longer of the two, so the two are told
    apart by length the way they are on a real clock. **No numeral is drawn on
    the dial** — a "5" would answer the card by being read, which is the one
    thing a picture of an hour must not do.
    """
    if not 1 <= hour <= MAX_CLOCK_HOUR:
        raise ValueError(f"{hour} is outside the renderable range 1..{MAX_CLOCK_HOUR}")

    cx = cy = _FACE_R + _MARGIN
    parts: list[str] = [
        f'<circle class="face" cx="{_n(cx)}" cy="{_n(cy)}" r="{_FACE_R}" '
        f'fill="{_BG}" stroke="{_ROD_STROKE}" stroke-width="3"/>'
    ]

    for mark in range(1, MAX_CLOCK_HOUR + 1):
        # 12, 3, 6 and 9 are the ones a reader uses to orient themselves, so
        # they are the ones drawn long enough to find at a glance.
        major = mark % 3 == 0
        inner = _TICK_IN_MAJOR if major else _TICK_IN_MINOR
        width = _MAJOR_W if major else _MINOR_W
        x1, y1 = _polar(cx, cy, inner, mark * 30)
        x2, y2 = _polar(cx, cy, _TICK_OUT, mark * 30)
        cls = "tick major" if major else "tick"
        parts.append(
            f'<line class="{cls}" x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}" '
            f'stroke="{_DOT_FILL}" stroke-width="{width}" stroke-linecap="round"/>'
        )

    hx, hy = _polar(cx, cy, _HOUR_LEN, hour * 30)
    mx, my = _polar(cx, cy, _MINUTE_LEN, 0)
    parts.append(
        f'<line class="hour-hand" x1="{_n(cx)}" y1="{_n(cy)}" x2="{_n(hx)}" y2="{_n(hy)}" '
        f'stroke="{_DOT_FILL}" stroke-width="7" stroke-linecap="round"/>'
    )
    parts.append(
        f'<line class="minute-hand" x1="{_n(cx)}" y1="{_n(cy)}" x2="{_n(mx)}" y2="{_n(my)}" '
        f'stroke="{_DOT_FILL}" stroke-width="5" stroke-linecap="round"/>'
    )

    size = 2 * (_FACE_R + _MARGIN)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'width="{size}" height="{size}">'
        f'<rect width="{size}" height="{size}" fill="{_BG}"/>'
        f"{''.join(parts)}</svg>"
    )
    return svg.encode("utf-8")


def break_into_denominations(amount: int, denominations: Sequence[int]) -> list[int]:
    """Split *amount* into *denominations*, largest first, as a descending list.

    Greedy: take as many of the largest value as fit, then the next, and so on.
    Each piece is one coin or one bill on the picture, so the returned multiset
    *is* the drawing.

    A greedy split is not guaranteed to find a representation where one exists —
    (4, 5) cannot make 8, though 4 + 4 can. That is a property of the coinage,
    not a bug here, and the Philippine set is canonical enough that every amount
    it should make, it does; a value it cannot make raises rather than quietly
    drawing a wrong amount.
    """
    if amount < 1:
        raise ValueError(f"{amount} is not a renderable amount: it must be at least 1")
    usable = sorted({d for d in denominations if d > 0}, reverse=True)
    if not usable:
        raise ValueError(f"{amount} is not a renderable amount: no usable denominations")

    pieces: list[int] = []
    remaining = amount
    for value in usable:
        count, remaining = divmod(remaining, value)
        pieces.extend([value] * count)
    if remaining:
        raise ValueError(f"{amount} is not a renderable amount for denominations {usable}")
    return pieces


def _label(x: float, y: float, text: str, size: int, fill: str) -> str:
    """*text* centred on ``(x, y)``. ``dy`` rather than ``dominant-baseline``,
    which not every SVG renderer honours."""
    return (
        f'<text x="{_n(x)}" y="{_n(y)}" dy="0.35em" text-anchor="middle" font-family="sans-serif" '
        f'font-weight="bold" font-size="{size}" fill="{fill}">{escape(text)}</text>'
    )


def _coin(cx: float, cy: float, value: int, symbol: str) -> str:
    return (
        f'<g class="coin" data-value="{value}">'
        f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="{_COIN_R}" fill="{_DOT_FILL}"/>'
        f"{_label(cx, cy, f'{symbol}{value}', _COIN_LABEL_SIZE, _COIN_LABEL_COLOR)}"
        f"</g>"
    )


def _bill(x: float, y: float, value: int, symbol: str) -> str:
    return (
        f'<g class="bill" data-value="{value}">'
        f'<rect x="{_n(x)}" y="{_n(y)}" width="{_BILL_W}" height="{_BILL_H}" rx="6" '
        f'fill="{_ROD_FILL}" stroke="{_ROD_STROKE}" stroke-width="2"/>'
        f"{_label(x + _BILL_W / 2, y + _BILL_H / 2, f'{symbol}{value}', _BILL_LABEL_SIZE, _DOT_FILL)}"
        f"</g>"
    )


def render_money_svg(amount: int, *, symbol: str, coins: Sequence[int], bills: Sequence[int]) -> bytes:
    """Draw the coins and bills that make *amount*, as SVG bytes.

    The breakdown is :func:`break_into_denominations` over both sets, so the
    picture is worth exactly *amount* by construction — the sum of the emitted
    ``data-value`` attributes, which is the oracle the tests use.

    A value carried by *bills* is drawn as a bill and anything else as a coin,
    with one deliberate exception: a value in **both** is a coin, because ₱20 is
    a coin and a bill and the coin is what is current. The symbol and the two
    denomination sets are parameters rather than literals so that this module
    holds no currency of its own.
    """
    pieces = break_into_denominations(amount, [*coins, *bills])
    coin_values = frozenset(coins)
    bill_values = frozenset(bills)

    columns = min(len(pieces), _PIECES_PER_ROW)
    rows = math.ceil(len(pieces) / _PIECES_PER_ROW)
    width = columns * _CELL_W + (columns - 1) * _PIECE_GAP + 2 * _MARGIN_MONEY
    height = rows * _CELL_H + (rows - 1) * _ROW_GAP + 2 * _MARGIN_MONEY

    parts: list[str] = []
    for index, value in enumerate(pieces):
        row, col = divmod(index, _PIECES_PER_ROW)
        x = _MARGIN_MONEY + col * (_CELL_W + _PIECE_GAP)
        y = _MARGIN_MONEY + row * (_CELL_H + _ROW_GAP)
        if value in bill_values and value not in coin_values:
            parts.append(_bill(x + (_CELL_W - _BILL_W) // 2, y + (_CELL_H - _BILL_H) // 2, value, symbol))
        else:
            parts.append(_coin(x + _CELL_W // 2, y + _CELL_H // 2, value, symbol))

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}">'
        f'<rect width="{width}" height="{height}" fill="{_BG}"/>'
        f"{''.join(parts)}</svg>"
    )
    return svg.encode("utf-8")
