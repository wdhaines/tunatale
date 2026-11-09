"""One "TT Sentence" note's fields, built from a lesson line's cards (tunatale-h0q3.55).

A line's cards — its blanks and its whole-line card — become clozes of one note,
so Anki buries them as siblings. The whole line is always cloze 1 and wraps the
blanks (a nested cloze; Anki renders the inner ones as plain text on the whole
card). The blanks take 2, 3, … in the order the lesson lists them. A line with no
whole card leaves cloze 1 unused, which Anki allows.

Nothing here writes anything. The field order is ``sentence_notetype.FIELD_NAMES``.
"""

from __future__ import annotations

import html

from app.srs.sentence_card import Gloss, SentenceCard, rich

#: The whole line's cloze number.
WHOLE_ORD = 1


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def cloze_ords(cards: list[SentenceCard]) -> dict[str, int]:
    """Each card's cloze number, by card key: the whole line 1, the blanks 2.. in list order."""
    lines = {(c.lesson_id, c.line_index) for c in cards}
    if len(lines) != 1:
        raise ValueError(f"a TT Sentence note holds one line's cards, got {sorted(lines)}")
    ords: dict[str, int] = {}
    next_blank = WHOLE_ORD + 1
    for card in cards:
        if card.key in ords:
            raise ValueError(f"card {card.key} listed twice")
        if card.blank is None:
            ords[card.key] = WHOLE_ORD
        else:
            ords[card.key] = next_blank
            next_blank += 1
    return ords


def _text(cards: list[SentenceCard], ords: dict[str, int]) -> str:
    text = cards[0].text
    if any(mark in text for mark in ("{{", "}}", "::")):
        raise ValueError(f"cloze syntax in a line's text: {text!r}")
    blanks = sorted((c.blank, ords[c.key]) for c in cards if c.blank is not None)
    out, pos = [], 0
    for (start, end), n in blanks:
        if start < pos:
            raise ValueError(f"blanks overlap in {text!r}")
        out.append(_esc(text[pos:start]))
        out.append(f"{{{{c{n}::{_esc(text[start:end])}}}}}")
        pos = end
    out.append(_esc(text[pos:]))
    inner = "".join(out)
    if any(c.blank is None for c in cards):
        return f"{{{{c{WHOLE_ORD}::{inner}}}}}"
    return inner


def _gloss_rows(glosses: tuple[Gloss, ...]) -> str:
    return "".join(f'<div class="tt-w">{_esc(g.word)}</div><div class="tt-g">{rich(g.gloss)}</div>' for g in glosses)


def _sound(filename: str | None) -> str:
    return f"[sound:{filename}]" if filename else ""


def sentence_note_fields(
    cards: list[SentenceCard], *, cue_audio: str | None, answer_audio: str | None, source: str
) -> list[str]:
    """The note's fields in ``FIELD_NAMES`` order, for one line's *cards*."""
    ords = cloze_ords(cards)
    line = cards[0]
    cue = line.cue
    blank_glosses = "".join(
        f'<div class="tt-bg" data-c="{ords[c.key]}"><b>{_esc(line.text[c.blank[0] : c.blank[1]])}</b> '
        f"{rich(c.blank_gloss)}</div>"
        for c in cards
        if c.blank is not None
    )
    cue_means = (
        ""
        if cue is None
        else (
            (f'<div class="tt-glosses">{_gloss_rows(cue.glosses)}</div>' if cue.glosses else "")
            + f'<div class="tt-en">{_esc(cue.english)}</div>'
        )
    )
    return [
        _text(cards, ords),
        _esc(line.speaker),
        "" if cue is None else _esc(cue.speaker),
        "" if cue is None else _esc(cue.text),
        _sound(cue_audio) if cue is not None else "",
        _sound(answer_audio),
        f'<img src="{html.escape(line.picture)}">' if line.picture else "",
        _esc(line.english),
        blank_glosses,
        _gloss_rows(line.glosses),
        cue_means,
        _esc(source),
    ]
