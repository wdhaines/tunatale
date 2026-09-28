"""The one answer to "does this word get a drawn picture, and which one?".

Three pictures exist, each for the context its words are used in:

- a native cardinal is a **counting picture** — the quantity as a heap of dots
  (``app.cards.number_image``);
- a word a language uses for the hour is a **clock face** at that hour;
- a word it uses for prices is the **coins and bills** that make the amount
  (both in ``app.cards.number_scenes``).

Which word belongs to which is data, in each language's ``numbers.json``; this
module holds no literal. Every path that gives a word its image asks here FIRST
— the add-time fetch, the sync that mints a new card, the production pre-stage,
and the mint's closed-class fork — because a drawn picture is right by
construction and a photo search for "five" is not. Before this existed only the
pre-stage asked, so a number that entered as a NEW card (a base-list seed, an
add from the reader) got a Pixabay photo in every language (tunatale-w4m7.10).

Kept apart from ``number_image`` because ``number_scenes`` imports that module's
palette; drawing all three from one place would make the two import each other.
"""

from __future__ import annotations

import hashlib
from typing import NamedTuple

from app.cards.number_image import load_number_config, number_value, render_count_svg
from app.cards.number_scenes import render_clock_svg, render_money_svg


class NumberPicture(NamedTuple):
    """A rendered picture and the content-addressed name it is stored under."""

    filename: str
    svg: bytes


def number_picture(text: str, language_code: str) -> NumberPicture | None:
    """The drawn picture for *text* in *language_code*, or ``None``.

    ``None`` means "take the route you were already on" — not a number, or a
    number this module will not draw (an excluded doublet, zero, out of range).

    Clock and money words are checked before the counting picture and ignore
    ``exclude``, which governs only the heap: the Spanish-derived numbers are
    excluded from counting precisely because they have a context of their own.
    """
    config = load_number_config(language_code)
    word = text.strip().casefold()
    if word in config.clock:
        value = config.values[word]
        stem, svg = f"clock_{value:02d}", render_clock_svg(value)
    elif word in config.money:
        value = config.values[word]
        svg = render_money_svg(value, symbol=config.money_symbol, coins=config.coins, bills=config.bills)
        stem = f"money_{value:04d}"
    else:
        count = number_value(text, language_code)
        if count is None:
            return None
        stem, svg = f"count_{count:03d}", render_count_svg(count)
    # Hash-suffixed like every other picture here: the name changes when the
    # drawing does, so a redraw can never overwrite a file a card already shows.
    return NumberPicture(f"{stem}_{hashlib.sha256(svg).hexdigest()[:8]}.svg", svg)
