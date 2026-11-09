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


def cloze_ords(cards: list[SentenceCard], taken: dict[str, int] | None = None) -> dict[str, int]:
    """Each card's cloze number, by card key: the whole line 1, the blanks 2.. in list order.

    *taken* holds the numbers of cards already minted (an Anki card's ord is its
    cloze number - 1), which are kept: renumbering a cloze would hand its card,
    and its review history, to another blank. A blank without one takes the
    next number above every number in use, so a number freed by a deleted card
    is never handed to a different blank.
    """
    lines = {(c.lesson_id, c.line_index) for c in cards}
    if len(lines) != 1:
        raise ValueError(f"a TT Sentence note holds one line's cards, got {sorted(lines)}")
    taken = {k: n for k, n in (taken or {}).items() if k in {c.key for c in cards}}
    ords: dict[str, int] = {}
    next_blank = max([WHOLE_ORD, *taken.values()]) + 1
    for card in cards:
        if card.key in ords:
            raise ValueError(f"card {card.key} listed twice")
        if card.key in taken:
            ords[card.key] = taken[card.key]
        elif card.blank is None:
            ords[card.key] = WHOLE_ORD
        else:
            ords[card.key] = next_blank
            next_blank += 1
    numbers = sorted(ords.values())
    for a, b in zip(numbers, numbers[1:], strict=False):
        if a == b:
            raise ValueError(f"cloze {a} given to two cards of {cards[0].line_key}")
    return ords


def source_label(card: SentenceCard) -> str:
    """Where the line comes from, as the back's fold-out names it: ``Wolff 1 · line 4``."""
    title = card.lesson_id.replace("-", " ")
    return f"{title[:1].upper()}{title[1:]} · line {card.line_index}"


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
    cards: list[SentenceCard],
    *,
    cue_audio: str | None,
    answer_audio: str | None,
    source: str,
    ords: dict[str, int] | None = None,
) -> list[str]:
    """The note's fields in ``FIELD_NAMES`` order, for one line's *cards*, numbered *ords*
    (``cloze_ords``; computed fresh when omitted)."""
    ords = ords if ords is not None else cloze_ords(cards)
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
