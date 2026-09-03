"""Counting pictures for number words — the production card a numeral deserves.

A number word routed to a cloze production card is a broken card: ``jeg har ___
barn`` admits every number, so it constrains nothing, teaches nothing, and marks
a right answer wrong. Numbers reach that route honestly rather than by a bug —
``promote_production_cards`` asks the language registry whether the word is
closed-class, and a deck that labels its numerals *determinative* (UPOS ``DET``)
gets a truthful yes.

The fix is not to loosen that test but to answer a prior question. Numbers are
the one word class with a **perfect, unambiguous picture**: the quantity itself.
Five apples IS five. There is no imageability judgement to make, no gloss to
trust, and — decisively — no verification problem, because the picture is
*rendered* rather than searched for.

**Why rendered, and why that is the argument rather than a preference.** Pixabay
cannot be asked for a count and cannot be trusted to deliver one; verifying a
count in a fetched photo is a vision problem. So for a fetched photo the claim
"this card shows exactly five things" has no oracle at all, while for a render it
is true *by construction* and a unit test can simply count the emitted elements.
The asymmetry is the whole case. The cost is honest and was accepted by the user
on 2026-09-03: these cards look like a diagram, and every other card in the deck
is a photograph.

**The encoding is base-10 place value**, at the user's request: a loose dot is
one, a framed rod of ten dots is a completed ten, and thirteen is one rod and
three dots rather than thirteen scattered marks. That keeps the picture countable
at a glance well past the point where a heap stops being countable — which is
what the range question was really asking. Ones stack in columns of five so six
reads as *a five and a one*.

Nothing here is per-language: only the WORD is, never the picture. The
vocabulary that maps a surface to a quantity is a plain JSON file per language
plugin, resolved through ``app.languages``, so this module holds no language
literal and needs none.
"""

from __future__ import annotations

import json
from functools import cache

from app.languages import get_numbers_path

#: Dots in one completed ten-rod. The base the whole picture is written in.
ONES_PER_ROD = 10

#: The largest quantity worth drawing. A hundred is ten rods and still reads as
#: *ten tens*; a thousand is a hundred rods, which nobody counts — it stops being
#: a countable set and becomes a texture. Words above this keep the cloze they
#: have today, which is the honest outcome: a cloze that cannot be answered from
#: context is a poor card, but a picture that cannot be counted is a wrong one.
MAX_RENDERABLE = 100

# ── Geometry ───────────────────────────────────────────────────────────────
# Chosen so a rod's ten dots read as one object at card size and the whole
# picture stays within a sensible width at 100 (ten rods → 940px).

_DOT_R = 9  # dot radius
_PITCH = 26  # centre-to-centre spacing, both axes
_ROD_PAD = 14  # frame inset around a rod's dots
_ROD_COLS = 2  # a rod is 2 dots wide …
_ROD_ROWS = 5  # … and 5 tall
_ROD_GAP = 10  # between adjacent rods
_ROW_GAP = 16  # between wrapped rows of rods
_GROUP_GAP = 30  # between the rods and the loose ones — the place-value seam
_MARGIN = 20  # around the whole drawing

#: Rods before the row wraps. Ten rods in one line is 940x198, and the drill card
#: caps an image at 240px wide on a small screen — which would draw those dots at
#: about two pixels and make the one thing the card asks for impossible. Wrapping
#: at five keeps the widest picture near 3:1 and costs the encoding nothing: five
#: is already the grouping the loose ones use, so a row of rods subitises the same
#: way a column of dots does.
_RODS_PER_ROW = 5

# The file paints its own opaque ground on purpose: an ``<img>`` cannot inherit
# ``currentColor``, so a transparent render with dark dots would vanish in Anki's
# night mode. One palette, legible on either host, in both TT and Anki.
_BG = "#faf8f4"
_DOT_FILL = "#2f5d9e"
_ROD_FILL = "#e8eef8"
_ROD_STROKE = "#93a9c9"


@cache
def _load_number_config(language_code: str) -> tuple[dict[str, int], frozenset[str]]:
    """Load ``(values, exclude)`` for *language_code*.

    Returns an empty vocabulary when the language registers no file — a language
    without one simply has no number words, and every one of its words keeps the
    routing it has today. Both maps are casefolded for case-insensitive lookup.
    """
    path = get_numbers_path(language_code)
    if path is None or not path.exists():
        return {}, frozenset()
    data = json.loads(path.read_text(encoding="utf-8"))
    values = {str(word).casefold(): int(value) for word, value in data.get("values", {}).items()}
    exclude = frozenset(str(word).casefold() for word in data.get("exclude", []))
    return values, exclude


def number_value(text: str, language_code: str) -> int | None:
    """The quantity *text* names in *language_code*, or ``None``.

    ``None`` means "not a word this module will picture", and every reason
    collapses into that one answer on purpose — the caller's only decision is
    whether to leave the word on the route it is already on.

    Four ways to get it: the word is not a number at all; it is listed in the
    language's ``exclude`` (Norwegian ``en`` is 'one' *and* 'a/an', and a picture
    of one apple is equally a picture of "an apple"); its quantity is zero, where
    an empty frame is indistinguishable from a render that failed; or its
    quantity is above :data:`MAX_RENDERABLE`.

    The range test lives here rather than in the JSON so the cut-off has exactly
    one home. The files list every cardinal their deck carries, ``tusen`` and
    ``null`` included, which records that those words were considered rather than
    overlooked.
    """
    values, exclude = _load_number_config(language_code)
    word = text.strip().casefold()
    if word in exclude:
        return None
    value = values.get(word)
    if value is None or not 1 <= value <= MAX_RENDERABLE:
        return None
    return value


def _dot(cx: int, cy: int) -> str:
    return f'<circle cx="{cx}" cy="{cy}" r="{_DOT_R}" fill="{_DOT_FILL}"/>'


def _rod(x: int, y: int) -> str:
    """One completed ten: ``ONES_PER_ROD`` dots inside a frame, read as a group."""
    width = _ROD_COLS * _PITCH + 2 * _ROD_PAD
    height = _ROD_ROWS * _PITCH + 2 * _ROD_PAD
    parts = [
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="12" '
        f'fill="{_ROD_FILL}" stroke="{_ROD_STROKE}" stroke-width="2"/>'
    ]
    for row in range(_ROD_ROWS):
        for col in range(_ROD_COLS):
            parts.append(
                _dot(
                    x + _ROD_PAD + _PITCH // 2 + col * _PITCH,
                    y + _ROD_PAD + _PITCH // 2 + row * _PITCH,
                )
            )
    return "".join(parts)


def _loose_ones(x: int, y: int, count: int) -> str:
    """*count* unframed dots, stacked bottom-up in columns of ``_ROD_ROWS``.

    Columns of five rather than a single line so six reads as *a five and a one*
    — the same subitising the rod relies on, one order of magnitude down.
    """
    height = _ROD_ROWS * _PITCH
    parts = []
    for i in range(count):
        col, row = divmod(i, _ROD_ROWS)
        parts.append(_dot(x + _PITCH // 2 + col * _PITCH, y + height - _PITCH // 2 - row * _PITCH))
    return "".join(parts)


def render_count_svg(value: int) -> bytes:
    """Draw exactly *value* objects as base-10 place value, as SVG bytes.

    Tens first as framed rods, then the remainder as loose dots, separated by a
    visible seam so the two places do not read as one heap. The element count is
    the quantity by construction — which is the property the tests assert and the
    reason this is a render and not a photo search.

    SVG rather than a raster: it is a string, so it needs no imaging dependency
    (there is no Pillow in this project), it stays sharp at any card size, and
    both Anki and the TT reader draw it from a plain ``<img src>``. It is written
    through ``store_tt_media`` like any other picture, and ``serve_media`` types
    it from the extension.
    """
    if not 1 <= value <= MAX_RENDERABLE:
        raise ValueError(f"{value} is outside the renderable range 1..{MAX_RENDERABLE}")

    rods, ones = divmod(value, ONES_PER_ROD)
    rod_width = _ROD_COLS * _PITCH + 2 * _ROD_PAD
    rod_height = _ROD_ROWS * _PITCH + 2 * _ROD_PAD

    body: list[str] = []
    x = 0
    row_width = 0
    row_top = 0
    for i in range(rods):
        if i and i % _RODS_PER_ROW == 0:
            row_width = max(row_width, x - _ROD_GAP)
            x = 0
            row_top += rod_height + _ROW_GAP
        body.append(_rod(x, row_top))
        x += rod_width + _ROD_GAP
    if rods:
        x -= _ROD_GAP

    # The remainder sits at the end of the last rod row, in reading order, so the
    # two places stay one sentence: "four tens and seven".
    if rods and ones:
        x += _GROUP_GAP
    if ones:
        body.append(_loose_ones(x, row_top + _ROD_PAD, ones))
        x += ((ones + _ROD_ROWS - 1) // _ROD_ROWS) * _PITCH

    width = max(row_width, x) + 2 * _MARGIN
    height = row_top + rod_height + 2 * _MARGIN
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}">'
        f'<rect width="{width}" height="{height}" fill="{_BG}"/>'
        f'<g transform="translate({_MARGIN},{_MARGIN})">{"".join(body)}</g></svg>'
    )
    return svg.encode("utf-8")
