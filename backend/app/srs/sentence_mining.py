"""Sentence mining: turn a lesson line's cards on and off, word by word (tunatale-h0q3.52, .66).

A tapped word becomes one more blank of its line: a new row in the line's
sibling group, written through ``add_collocation`` per the card-adding contract
(NEW, no Anki ids). The next sync adds its card to the line's "TT Sentence"
note (``AnkiSync._mint_sentence_line``), at the FRONT of the new queue, where
TT's other additions go (owner, 2026-11-10): its row's source is ``user``, not
the course's. Removing a card deletes its row; the next push drops its cloze
and graves its card (``_flag_line_of_deleted``). A card the lesson's list makes,
turned off, also gets a not-wanted mark so no mint makes it again.

A Save's edits go through three calls, so the caller can fetch glosses between
validating and writing, and run the study pass between the marks and the rows
(design § 5): :func:`plan_line_edits` (validates everything, writes nothing),
:func:`write_marks`, then :func:`apply_rows`. Nothing here calls a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from app.common.guid import compute_guid
from app.models.lesson import Lesson
from app.models.srs_item import SRSItem
from app.srs.lesson_cards import LessonCard
from app.srs.sentence_card import SentenceCard, sentence_card
from app.srs.sentence_lines import ListLine, _reviewed, list_lines, read_line

#: The line's takes, which every card of the line plays (``app.audio.sentence_card_audio``).
_TAKE_KINDS = ("audio_tts_sentence", "audio_cue")
_WORD = re.compile(r"\w")


class MiningError(ValueError):
    """A request the line cannot take; ``status`` is the HTTP answer."""

    status = 400


class LineNotFound(MiningError):
    status = 404


class NotWholeWords(MiningError):
    status = 422


class BlankOverlaps(MiningError):
    status = 409


class LastCardOfLine(MiningError):
    status = 409


class NeedsConfirm(MiningError):
    """Cards with review history the edit would delete; nothing is applied until the learner confirms."""

    status = 409

    def __init__(self, keys: list[str]) -> None:
        super().__init__(f"these cards have been reviewed; turning them off deletes their history: {keys}")
        self.keys = keys


@dataclass(frozen=True)
class BlankPlan:
    card: SentenceCard  # the whole line (no blank)
    existing: int | None  # the row that already has this blank


Row = tuple[str, SRSItem, int]


def _line(db, line_key: str, base: SentenceCard | None = None) -> tuple[list[tuple[Row, SentenceCard]], SentenceCard]:
    """The line's rows and its whole-line card: from its rows, else *base* (a line with no row yet)."""
    rows = [(r, c) for r in db.list_sibling_rows(line_key) if (c := sentence_card(r[1].syntactic_unit)) is not None]
    if rows:
        return rows, replace(rows[0][1], blank=None, blank_gloss="")
    if base is None:
        raise LineNotFound(f"no card of line {line_key!r}")
    return rows, base


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


def plan_blank(db, line_key: str, span: tuple[int, int], base: SentenceCard | None = None) -> BlankPlan:
    """The line's card, and the row that already blanks *span*; raise when *span* cannot be a blank.

    *base*: the line's whole-line card when it has no row yet (``sentence_lines.line_card``).
    """
    rows, card = _line(db, line_key, base)
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


def add_blank(
    db, language_code: str, line_key: str, span: tuple[int, int], gloss: str, *, base: SentenceCard | None = None
) -> int:
    """Add the blank *span* to the line; return its row id (the existing row's, when it has one)."""
    plan = plan_blank(db, line_key, span, base)
    if plan.existing is not None:
        return plan.existing
    return _add_row(db, language_code, line_key, plan.card.with_blank(span, gloss))


def _add_row(db, language_code: str, line_key: str, card: SentenceCard) -> int:
    """Write *card* as a mined row of its line, with the takes a sibling already has."""
    unit = card.unit()
    db.add_collocation(unit, language_code)
    row_id = db.get_collocation_id_by_guid(compute_guid(unit.text, language_code, unit.disambig_key))
    for kind in _TAKE_KINDS:
        source = next((r for (_, _, r), _ in _line(db, line_key)[0] if db.has_media_row(r, kind)), None)
        if source is not None:
            db.copy_media_row(source, row_id, kind)
    return row_id


# --------------------------------------------------------------------------
# A Save's edits: plan, marks, rows
# --------------------------------------------------------------------------

WHOLE = "whole"
Span = tuple[int, int] | None  # None = the whole-line card


@dataclass(frozen=True)
class GlossNeed:
    """A new blank and what a gloss for it is found from: the line's own, else the model."""

    line_key: str
    span: tuple[int, int]
    word: str
    sentence: str
    own_gloss: str


@dataclass
class LineEditPlan:
    marks_on: list[str] = field(default_factory=list)  # list cards turned off: mark them
    marks_off: list[str] = field(default_factory=list)  # list cards turned on: clear their marks
    removals: list[int] = field(default_factory=list)
    adds: list[tuple[ListLine, Span]] = field(default_factory=list)  # mined cards to write
    list_on: list[str] = field(default_factory=list)  # list cards turned on that the deck lacks
    needs_gloss: list[GlossNeed] = field(default_factory=list)
    linked_after: dict[str, int] = field(default_factory=dict)  # a linked line's card count after the edit


@dataclass
class LineEditResult:
    made: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)
    not_made: list[dict] = field(default_factory=list)
    #: Each mined row, with the voice roles of its line and cue: what the media step gives the
    #: lesson's takes to (``attach_lesson_card_audio``). A line no list names has no other source.
    voice: list[tuple[int, LessonCard]] = field(default_factory=list)


def _spans(raw: list) -> list[Span]:
    return [None if s == WHOLE else (int(s[0]), int(s[1])) for s in raw]


def plan_line_edits(db, lesson: Lesson, content_id: str, edits: list[dict]) -> LineEditPlan:
    """Validate every edit and say what it writes; raise a :class:`MiningError` and write nothing when one cannot apply.

    An edit is ``{line_key, on: [[start, end] | "whole"], off: [...], confirm}``. A
    span must be a token of the line (``sentence_lines``): a whole written word,
    or the part of one the list blanks. A reviewed card goes only with
    ``confirm`` (:class:`NeedsConfirm` names every one). A line whose note is in
    Anki keeps at least one card, counted after the edit: the only thing that
    takes a note out of Anki is a sibling flagged by a deletion.
    """
    lines = {listed.card.line_key: listed for listed in list_lines(lesson, content_id).values()}
    marks = db.sentence_card_marks()
    plan = LineEditPlan()
    unconfirmed: list[str] = []
    seen: set[str] = set()
    for edit in edits:
        line_key = edit["line_key"]
        listed = lines.get(line_key)
        if listed is None:
            raise LineNotFound(f"{line_key!r} is not a line of {content_id!r}")
        if line_key in seen:
            raise MiningError(f"{line_key!r} is edited twice")
        seen.add(line_key)
        read = read_line(db, listed, marks)
        tokens = {t.span: t for t in read.tokens}
        on, off = _spans(edit.get("on", [])), _spans(edit.get("off", []))
        for span in on + off:
            if span is not None and span not in tokens:
                raise NotWholeWords(f"{span} is not a word of {read.text!r}")
        if set(on) & set(off):
            raise MiningError(f"{line_key!r}: a card both on and off")
        rows = {
            card.key: (row_id, item)
            for _, item, row_id in db.list_sibling_rows(line_key)
            if (card := sentence_card(item.syntactic_unit)) is not None
        }
        after = len(rows)
        for span in off:
            key = listed.key(span)
            if key in rows:
                row_id, item = rows[key]
                if _reviewed(item) and not edit.get("confirm", False):
                    unconfirmed.append(key)
                plan.removals.append(row_id)
                after -= 1
            if listed.listed(span):
                plan.marks_on.append(key)
        for span in on:
            key = listed.key(span)
            if key in rows:
                continue
            after += 1
            if listed.listed(span):
                if key in marks:
                    plan.marks_off.append(key)
                plan.list_on.append(key)
                continue
            if span is not None:
                # The same check ``add_blank`` makes, here, so ``apply_rows`` cannot raise after
                # the grades, marks and study pass are written (review F3).
                plan_blank(db, line_key, span, listed.card)
            plan.adds.append((listed, span))
            if span is not None:
                word = read.text[span[0] : span[1]]
                plan.needs_gloss.append(GlossNeed(line_key, span, word, read.text, own_gloss(listed.card, span)))
        if read.linked:
            if after < 1:
                raise LastCardOfLine(f"{line_key!r} is in Anki and keeps at least one card")
            plan.linked_after[line_key] = after
    if unconfirmed:
        raise NeedsConfirm(unconfirmed)
    return plan


def check_glossed(plan: LineEditPlan, glosses: dict) -> None:
    """Refuse the edit when a linked line is kept only by new blanks that got no gloss.

    ``plan_line_edits`` counts a new blank before its gloss is fetched; a blank with
    none is not made (``not_made``), so a linked line could lose every row while its
    cards stay due in Anki (review G1). Run after the glosses, before any write.
    """
    for line_key, after in plan.linked_after.items():
        unglossed = sum(1 for n in plan.needs_gloss if n.line_key == line_key and (line_key, n.span) not in glosses)
        if after - unglossed < 1:
            raise LastCardOfLine(
                f"{line_key!r} is in Anki and keeps at least one card; no meaning was found for the new blank"
            )


def write_marks(db, plan: LineEditPlan) -> None:
    """The not-wanted marks: before the study pass, so its mint already honours them."""
    for key in plan.marks_on:
        db.add_sentence_card_mark(key)
    for key in plan.marks_off:
        db.remove_sentence_card_mark(key)


def apply_rows(
    db, language_code: str, lesson: Lesson, plan: LineEditPlan, glosses: dict, *, studied: bool
) -> LineEditResult:
    """Delete the cards turned off, write the mined ones, and in a *studied* lesson mint the list cards turned on
    (those only: a list card deleted on the Cards page waits for the next study pass, as before).

    *glosses* maps ``(line_key, span)`` to a new blank's gloss; a blank with none
    is named in ``not_made`` and the rest still apply (the owner wants loud failures).
    """
    from app.srs.lesson_card_mint import mint_lesson_cards  # lesson_card_mint is built on this package

    result = LineEditResult()
    for row_id in plan.removals:
        db.delete_collocation(row_id)
        result.removed.append(row_id)
    for listed, span in plan.adds:
        line_key = listed.card.line_key
        if span is None:
            row_id = _add_row(db, language_code, line_key, _line(db, line_key, listed.card)[1])
        else:
            gloss = glosses.get((line_key, span), "")
            if not gloss:
                word = listed.card.text[span[0] : span[1]]
                result.not_made.append({"line_key": line_key, "start": span[0], "end": span[1], "word": word})
                continue
            row_id = add_blank(db, language_code, line_key, span, gloss, base=listed.card)
        result.made.append(row_id)
        unit = db.get_collocation_by_id(row_id)[1].syntactic_unit
        result.voice.append((row_id, LessonCard(unit=unit, role=listed.role, cue_role=listed.cue_role)))
    if plan.list_on and studied:
        story = lesson.generation_metadata["story"]
        minted = mint_lesson_cards(db, language_code, story["cards"], story.get("drills", []), only=set(plan.list_on))
        result.made += [cid for cid, _ in minted]
    return result
