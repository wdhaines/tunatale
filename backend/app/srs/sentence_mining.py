"""Sentence mining: add or remove a blank on a lesson line that has cards (tunatale-h0q3.52).

A tapped word becomes one more blank of its line: a new row in the line's
sibling group, written through ``add_collocation`` per the card-adding contract
(NEW, no Anki ids). The next sync adds its card to the line's "TT Sentence"
note (``AnkiSync._mint_sentence_line``), at the FRONT of the new queue, where
TT's other additions go (owner, 2026-11-10): its row's source is ``user``, not
the course's. Removing a blank deletes its row; the next push drops its cloze
and graves its card (``_flag_line_of_deleted``).

Nothing here calls a model: the route finds a gloss (the line's own word gloss,
else ``generate_word_gloss``) between :func:`plan_blank` and :func:`add_blank`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from app.common.guid import compute_guid
from app.models.srs_item import Direction, SRSItem
from app.srs.sentence_card import SentenceCard, sentence_card

#: The line's takes, which every card of the line plays (``app.audio.sentence_card_audio``).
_TAKE_KINDS = ("audio_tts_sentence", "audio_cue")
_WORD = re.compile(r"\w")


class MiningError(ValueError):
    """A request the line cannot take; ``status`` is the HTTP answer."""

    status = 400


class LineNotFound(MiningError):
    status = 404


class BlankNotFound(MiningError):
    status = 404


class NotWholeWords(MiningError):
    status = 422


class BlankOverlaps(MiningError):
    status = 409


class BlankHasHistory(MiningError):
    status = 409


class LastCardOfLine(MiningError):
    status = 409


@dataclass(frozen=True)
class BlankPlan:
    card: SentenceCard  # the whole line (no blank)
    existing: int | None  # the row that already has this blank


Row = tuple[str, SRSItem, int]


def _line(db, line_key: str) -> tuple[list[tuple[Row, SentenceCard]], SentenceCard]:
    rows = [(r, c) for r in db.list_sibling_rows(line_key) if (c := sentence_card(r[1].syntactic_unit)) is not None]
    if not rows:
        raise LineNotFound(f"no card of line {line_key!r}")
    return rows, replace(rows[0][1], blank=None, blank_gloss="")


def _check_whole_words(text: str, span: tuple[int, int]) -> None:
    s, e = span
    ok = (
        0 <= s < e <= len(text)
        and not text[s].isspace()
        and not text[e - 1].isspace()
        and (s == 0 or not _WORD.match(text[s - 1]))
        and (e == len(text) or not _WORD.match(text[e]))
        and _WORD.search(text[s:e]) is not None
    )
    if not ok:
        raise NotWholeWords(f"{span} is not whole words of {text!r}")


def plan_blank(db, line_key: str, span: tuple[int, int]) -> BlankPlan:
    """The line's card, and the row that already blanks *span*; raise when *span* cannot be a blank."""
    rows, card = _line(db, line_key)
    _check_whole_words(card.text, span)
    for (_, _, row_id), c in rows:
        if c.blank is None:
            continue
        if c.blank == span:
            return BlankPlan(card, row_id)
        if c.blank[0] < span[1] and span[0] < c.blank[1]:
            raise BlankOverlaps(f"{span} overlaps the blank {c.blank} of {line_key!r}")
    return BlankPlan(card, None)


def own_gloss(card: SentenceCard, span: tuple[int, int]) -> str:
    """The line's own gloss of the word at *span* (case ignored), or ""."""
    word = card.text[span[0] : span[1]].casefold()
    return next((g.gloss for g in card.glosses if g.word.casefold() == word), "")


def add_blank(db, language_code: str, line_key: str, span: tuple[int, int], gloss: str) -> int:
    """Add the blank *span* to the line; return its row id (the existing row's, when it has one)."""
    plan = plan_blank(db, line_key, span)
    if plan.existing is not None:
        return plan.existing
    unit = plan.card.with_blank(span, gloss).unit()
    db.add_collocation(unit, language_code)
    row_id = db.get_collocation_id_by_guid(compute_guid(unit.text, language_code, unit.disambig_key))
    for kind in _TAKE_KINDS:
        source = next((r for (_, _, r), _ in _line(db, line_key)[0] if db.has_media_row(r, kind)), None)
        if source is not None:
            db.copy_media_row(source, row_id, kind)
    return row_id


def remove_blank(db, line_key: str, span: tuple[int, int], *, confirm: bool) -> int:
    """Delete the line's blank *span*; return its row id.

    A blank whose card has been reviewed takes its history with it, so it goes
    only with *confirm* (owner, 2026-11-10). The line's last card stays: with no
    sibling left to flag, nothing would take its card out of Anki.
    """
    rows, _ = _line(db, line_key)
    found = next(((item, row_id) for (_, item, row_id), c in rows if c.blank == span), None)
    if found is None:
        raise BlankNotFound(f"line {line_key!r} has no blank {span}")
    item, row_id = found
    if len(rows) == 1:
        raise LastCardOfLine(f"{span} is the only card of {line_key!r}")
    prod = item.directions.get(Direction.PRODUCTION)
    if not confirm and prod is not None and (prod.reps > 0 or prod.last_review is not None):
        raise BlankHasHistory(f"the blank {span} has been reviewed; removing it deletes its review history")
    db.delete_collocation(row_id)
    return row_id
