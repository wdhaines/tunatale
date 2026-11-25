"""The sentence band under a 260x220 card picture (tunatale-37c1, tunatale-avcl).

A pronoun's picture names the person and its band names the slot (``Moadto ___ sa
merkado.``); a demonstrative pair's spatial picture carries one too. Both renderers
draw it from here, so the band is the same on every card.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

_W = 260
_H = 220

#: The sentence band under a framed scene (tunatale-37c1). The blank is written
#: ``___`` in the data and drawn as a longer run of underscores, which every font
#: draws as one rule: a drawn ``<line>`` would have to know where the text before
#: it ends, and an SVG cannot measure its own text. Arial is the card's own face
#: (the vocab notetype CSS), and the ink is the card's own black.
FRAME_BLANK = "___"
_BLANK_DRAWN = "____"
#: Which form a card asks for (tunatale-avcl, the owner's D3, 2026-11-25): every
#: syllable is one short slot, so the short form is one slot and the long form two,
#: split by a syllable dot the way a dictionary splits syllables. A syllable slot is
#: shorter than the word blank (the owner, same day). Every short form in Wolff's charts is one
#: syllable and every long form two, so the cue is exact, and it is language-free.
#: A card that asks for neither keeps the plain blank.
_BLANK_FOR_CUE = {None: _BLANK_DRAWN, "short": "__", "long": "__\u00b7__"}
FRAME_BAND = 36
_FRAME_BASELINE = 24
_FRAME_SIZE = 18
_FRAME_FONT = "Arial, Helvetica, sans-serif"
_FRAME_INK = "#000000"
#: About 0.5em a character in Arial at this size leaves 26 characters inside the
#: frame's 8-unit margins with room to spare.
_FRAME_MAX_CHARS = 26


def _n(value: float) -> str:
    """Format a coordinate, trimmed so the markup carries no ``48.000000``."""
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _frame_text(frame: str, cue: str | None = None) -> str:
    """*frame*'s one blank widened to a drawn line of underscores, or ``ValueError``.

    *cue* ``"short"`` or ``"long"`` draws the blank of that form instead
    (``_BLANK_FOR_CUE``).

    Refused rather than drawn badly: a sentence with no blank shows the slot
    already filled, two blanks leave the card asking which one is the word, and a
    sentence wider than the card is cut off by the frame's own edge. The width
    check is an estimate — an SVG cannot measure its text before a font renders
    it — so the budget sits well inside the frame.
    """
    pieces = frame.split(FRAME_BLANK)
    if len(pieces) != 2:
        raise ValueError(f"frame {frame!r} must hold exactly one {FRAME_BLANK!r} blank")
    text = _BLANK_FOR_CUE[cue].join(pieces)
    if len(text) > _FRAME_MAX_CHARS:
        raise ValueError(f"frame {frame!r} is {len(text)} characters drawn; at most {_FRAME_MAX_CHARS} fit the card")
    return text


def frame_band(frame: str, cue: str | None = None) -> str:
    """The sentence band under a 260-wide scene, as SVG markup; it adds ``FRAME_BAND`` to the height.

    Shared with the spatial pictures (``app.cards.spatial_scenes``), so a
    demonstrative's band is drawn exactly as a pronoun's.
    """
    return (
        f'<text x="{_n(_W / 2)}" y="{_H + _FRAME_BASELINE}" text-anchor="middle" '
        f'font-family="{_FRAME_FONT}" font-size="{_FRAME_SIZE}" fill="{_FRAME_INK}">'
        f"{escape(_frame_text(frame, cue))}</text>"
    )
