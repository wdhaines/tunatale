"""A lesson's sentence lines, as the screens that mine them read them (tunatale-h0q3.66).

Design: ``.beads-tasks/briefs/design-sentence-mining-2026-11.md`` § Design 1-2.
Grade's plan and Read both call :func:`sentence_lines`, so the two screens cannot
disagree about a line.

A line is one the lesson's card list names (slice A; any scene line is slice C).
Its tokens are its written words, split here and never in the page: a word is
``\\w`` characters with an internal hyphen or apostrophe kept inside
(``Per-Ola's``). One word per blank. A word the list blanks only part of (a
linker suffix) carries that part, and toggling the word toggles the part.

Each token, and the whole-line card, is:

* ``on``: a card exists, or the list will mint it when the lesson is studied;
* ``declined``: a list card the learner turned off (a not-wanted mark,
  ``sentence_card_marks``), which nothing mints again;
* ``off``: no card.

``origin`` is ``lesson`` for the list's own cards and ``mine`` for the rest.

Nothing here writes anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.lesson import Lesson
from app.models.srs_item import Direction, SRSItem
from app.srs.lesson_cards import COURSE_SOURCE, lesson_cards
from app.srs.sentence_card import SentenceCard, sentence_card

WORD = re.compile(r"\w+(?:['’-]\w+)*")

ON, OFF, DECLINED = "on", "off", "declined"


@dataclass(frozen=True)
class LineToken:
    start: int
    end: int
    part: tuple[int, int] | None  # the span the list blanks inside this word, when it is not the whole word
    state: str
    origin: str
    reviewed: bool
    gloss: str

    @property
    def span(self) -> tuple[int, int]:
        """The blank this token toggles."""
        return self.part or (self.start, self.end)


@dataclass(frozen=True)
class SentenceLine:
    line_key: str
    position: int
    speaker: str
    text: str
    english: str
    linked: bool  # its note is in Anki: the line keeps at least one card
    tokens: tuple[LineToken, ...]
    whole: LineToken
    spoken_map: list[int] | None  # the card word for each word the lesson speaks; None when the counts differ


@dataclass(frozen=True)
class ListLine:
    """What the card list says of one line: its base card, and which of its cards the list makes."""

    card: SentenceCard  # the whole line, no blank
    blanks: frozenset[tuple[int, int]]
    whole: bool

    def key(self, span: tuple[int, int] | None) -> str:
        return self.card.with_blank(span, "").key if span else self.card.key

    def listed(self, span: tuple[int, int] | None) -> bool:
        return self.whole if span is None else span in self.blanks


def list_lines(lesson: Lesson) -> dict[int, ListLine]:
    """The lines the lesson's card list names, by position, in list order."""
    story = lesson.generation_metadata.get("story") or {}
    cards = story.get("cards")
    if not cards:
        return {}
    lines: dict[int, tuple[SentenceCard, set, list]] = {}
    for card in lesson_cards(cards, story.get("drills", [])):
        sentence = sentence_card(card.unit)
        if sentence is None:
            continue
        base, blanks, whole = lines.setdefault(sentence.line_index, (_base(sentence), set(), [False]))
        if sentence.blank is None:
            whole[0] = True
        else:
            blanks.add(sentence.blank)
    return {pos: ListLine(base, frozenset(blanks), whole[0]) for pos, (base, blanks, whole) in lines.items()}


def _base(card: SentenceCard) -> SentenceCard:
    return card.with_blank(None, "") if card.blank is not None else card


def line_card(lesson: Lesson, position: int) -> SentenceCard | None:
    """The whole-line card of the line at *position*, exactly as the list builds it, or None."""
    found = list_lines(lesson).get(position)
    return found.card if found is not None else None


def words(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in WORD.finditer(text)]


def _reviewed(item: SRSItem) -> bool:
    prod = item.directions.get(Direction.PRODUCTION)
    return prod is not None and (prod.reps > 0 or prod.last_review is not None)


def _own_gloss(card: SentenceCard, span: tuple[int, int]) -> str:
    word = card.text[span[0] : span[1]].casefold()
    return next((g.gloss for g in card.glosses if g.word.casefold() == word), "")


def read_line(db, listed: ListLine, marks: set[str]) -> SentenceLine:
    """One line of the read model: *listed* against the rows the deck holds and the marks."""
    card = listed.card
    rows = {
        made.key: item
        for _, item, _ in db.list_sibling_rows(card.line_key)
        if (made := sentence_card(item.syntactic_unit)) is not None
    }

    def token(start: int, end: int, part: tuple[int, int] | None, span: tuple[int, int] | None) -> LineToken:
        key = listed.key(span)
        item = rows.get(key)
        if item is not None:
            origin = "lesson" if item.syntactic_unit.source == COURSE_SOURCE else "mine"
            return LineToken(start, end, part, ON, origin, _reviewed(item), _gloss(span))
        if listed.listed(span):
            return LineToken(start, end, part, DECLINED if key in marks else ON, "lesson", False, _gloss(span))
        return LineToken(start, end, part, OFF, "mine", False, _gloss(span))

    def _gloss(span):
        return _own_gloss(card, span) if span else ""

    tokens = []
    for start, end in words(card.text):
        part = next((b for b in listed.blanks if start <= b[0] and b[1] <= end and b != (start, end)), None)
        tokens.append(token(start, end, part, part or (start, end)))
    shown, spoken = words(card.text), words(card.spoken)
    return SentenceLine(
        line_key=card.line_key,
        position=card.line_index,
        speaker=card.speaker,
        text=card.text,
        english=card.english,
        linked=any(item.anki_note_id is not None for item in rows.values()),
        tokens=tuple(tokens),
        whole=token(0, len(card.text), None, None),
        spoken_map=list(range(len(shown))) if len(shown) == len(spoken) else None,
    )


def sentence_lines(db, lesson: Lesson, content_id: str) -> list[SentenceLine]:
    """Every line of *lesson* the screens may mine, in the list's order. *content_id* is unused until
    slice C, where a lesson with no list keys its lines by it."""
    marks = db.sentence_card_marks()
    return [read_line(db, listed, marks) for listed in list_lines(lesson).values()]
