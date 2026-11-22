"""A lesson's sentence lines, as the screens that mine them read them (tunatale-h0q3.66).

Design: ``.beads-tasks/briefs/design-sentence-mining-2026-11.md`` § Design 1-2.
Grade's plan and Read both call :func:`sentence_lines`, so the two screens cannot
disagree about a line.

A line is any line of the story's scenes (tunatale-h0q3.68). One the lesson's
card list names is the list's card; any other is built from the scene line
(:func:`line_card`), keyed by the list's ``set`` when the lesson has a list, else
by the content id. Its tokens are its written words, split here and never in the page: a word is
``\\w`` characters with an internal hyphen or apostrophe kept inside
(``Per-Ola's``). One word per blank mined; a list blank the lesson made over
several words (``na lang``) is one token over all of them (review F3). A word
the list blanks only part of (a linker suffix) carries that part, and toggling
the word toggles the part.

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
import unicodedata
from dataclasses import dataclass, replace
from difflib import SequenceMatcher

from app.models.lesson import Lesson, SectionType
from app.models.srs_item import Direction, SRSItem
from app.srs.lesson_cards import COURSE_SOURCE, lesson_cards
from app.srs.sentence_card import Cue, Gloss, SentenceCard, sentence_card

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
    in_deck: bool = False  # a row holds this card; a listed card the deck lacks is ``on`` and not in it

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
    # For each word the lesson speaks, the index of its token (words pair by index, "gar" with "går");
    # None when the counts differ. Two words of one list blank share a token.
    spoken_map: list[int] | None
    # The line's index in the transcript's ``dialogue_lines`` (Read's Cloze…); None when the voice does not say it.
    dialogue_index: int | None = None


@dataclass(frozen=True)
class ListLine:
    """What the lesson says of one line: its base card, which of its cards the list makes (none, for a
    line the list does not name), and the voice roles that say the line and its cue."""

    card: SentenceCard  # the whole line, no blank
    blanks: frozenset[tuple[int, int]]
    whole: bool
    role: str = ""  # the line's voice role
    cue_role: str = ""  # its cue's; "" when the line has no cue

    def key(self, span: tuple[int, int] | None) -> str:
        return self.card.with_blank(span, "").key if span else self.card.key

    def listed(self, span: tuple[int, int] | None) -> bool:
        return self.whole if span is None else span in self.blanks


def line_set(lesson: Lesson, content_id: str) -> str:
    """The set a lesson's line keys use (``sent:<set>:<pos>``): its card list's ``set``, else the
    ``line_set`` an import carried into it (PR #449 review F1), else its own content id."""
    story = lesson.generation_metadata.get("story") or {}
    cards = story.get("cards")
    if cards:
        return cards["set"]
    return lesson.generation_metadata.get("line_set") or content_id


def list_lines(lesson: Lesson, content_id: str) -> dict[int, ListLine]:
    """Every line of the lesson, by position, in story order: the list's card where the list names
    the line, else the scene line's own (tunatale-h0q3.68)."""
    story = lesson.generation_metadata.get("story") or {}
    cards = story.get("cards")
    lines: dict[int, tuple[SentenceCard, set, list, str, str]] = {}
    for card in lesson_cards(cards, story.get("drills", [])) if cards else []:
        sentence = sentence_card(card.unit)
        if sentence is None:
            continue
        base, blanks, whole, _, _ = lines.setdefault(
            sentence.line_index, (_base(sentence), set(), [False], card.role, card.cue_role)
        )
        if sentence.blank is None:
            whole[0] = True
        else:
            blanks.add(sentence.blank)
    found = {
        pos: ListLine(base, frozenset(blanks), whole[0], role, cue_role)
        for pos, (base, blanks, whole, role, cue_role) in lines.items()
    }
    set_id = line_set(lesson, content_id)
    glosses = _lesson_glosses(lesson)
    for pos, (line, before) in enumerate(_scene_lines(story), start=1):
        if pos not in found:
            card = _scene_card(set_id, pos, line, before, glosses)
            # Lowercased, as the section builder resolves a voice (review N1).
            role, cue_role = line["speaker"].lower(), before["speaker"].lower() if before else ""
            found[pos] = ListLine(card, frozenset(), False, role, cue_role)
    return dict(sorted(found.items()))


def has_mined_lines(db, set_id: str) -> bool:
    """Whether a line keyed under *set_id* holds a row the learner mined (not the course's)."""
    return any(
        item.syntactic_unit.source != COURSE_SOURCE
        for group in db.list_sibling_groups(f"sent:{set_id}:")
        for _, item, _ in db.list_sibling_rows(group)
        if sentence_card(item.syntactic_unit) is not None
    )


def _base(card: SentenceCard) -> SentenceCard:
    return card.with_blank(None, "") if card.blank is not None else card


def _scene_lines(story: dict) -> list[tuple[dict, dict | None]]:
    """Each scene line, with the line before it in the same scene (``None`` for a scene's first)."""
    out: list[tuple[dict, dict | None]] = []
    for scene in story.get("scenes", []):
        before = None
        for line in scene.get("lines", []):
            out.append((line, before))
            before = line
    return out


def _lesson_glosses(lesson: Lesson) -> dict[str, str]:
    return {word.casefold(): gloss for word, gloss in (lesson.generation_metadata.get("token_glosses") or {}).items()}


def _word_glosses(text: str, glosses: dict[str, str]) -> tuple[Gloss, ...]:
    """The lesson's gloss of each word of *text* it glosses, as written, in order, once each."""
    out: list[Gloss] = []
    seen: set[str] = set()
    for m in WORD.finditer(text):
        key = m.group().casefold()
        if key in glosses and key not in seen:
            seen.add(key)
            out.append(Gloss(m.group(), glosses[key]))
    return tuple(out)


def _scene_card(set_id: str, position: int, line: dict, before: dict | None, glosses: dict) -> SentenceCard:
    """The whole-line card of a scene line no list names: the line as the lesson says it."""
    cue = None
    if before is not None:
        cue = Cue(
            speaker=before["speaker"],
            text=before["text"],
            spoken=before["text"],
            english=before["translation"],
            glosses=_word_glosses(before["text"], glosses),
        )
    return SentenceCard(
        lesson_id=set_id,
        line_index=position,
        speaker=line["speaker"],
        text=line["text"],
        spoken=line["text"],
        english=line["translation"],
        glosses=_word_glosses(line["text"], glosses),
        cue=cue,
    )


def line_card(lesson: Lesson, position: int, content_id: str) -> SentenceCard | None:
    """The whole-line card of the line at *position*: the list's, else the scene line's; None past the story."""
    found = list_lines(lesson, content_id).get(position)
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
            return LineToken(start, end, part, ON, origin, _reviewed(item), _gloss(span), in_deck=True)
        if listed.listed(span):
            return LineToken(start, end, part, DECLINED if key in marks else ON, "lesson", False, _gloss(span))
        return LineToken(start, end, part, OFF, "mine", False, _gloss(span))

    def _gloss(span):
        return _own_gloss(card, span) if span else ""

    tokens = []
    # A list blank spanning several words (live: "na lang") is one token over all of them.
    spans = [(s, e) for s, e in words(card.text)]
    for blank in sorted(listed.blanks):
        inside = [w for w in spans if blank[0] <= w[0] and w[1] <= blank[1]]
        if len(inside) > 1:
            spans = [w for w in spans if w not in inside] + [blank]
    for start, end in sorted(spans):
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
        spoken_map=[_token_of(tokens, w) for w in shown] if len(shown) == len(spoken) else None,
    )


def _said(lesson: Lesson) -> list[str]:
    """What the transcript shows, line by line: the Natural Speed phrases in the lesson's language.

    The same walk as ``drill_lines.py`` and ``transcript.extract_transcript``: one transcript line each.
    """
    story = next((s for s in lesson.sections if s.section_type is SectionType.NATURAL_SPEED), None)
    if story is None:
        return []
    return [phrase.text for phrase in story.phrases if phrase.language_code == lesson.language_code]


def _plain(text: str) -> str:
    """*text* as its words, case and accents aside: a list's ``går`` matches a take's ``gar``."""
    folded = "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))
    return " ".join(m.group() for m in WORD.finditer(folded))


def _dialogue_indexes(lesson: Lesson, cards: list[SentenceCard]) -> list[int | None]:
    """Each card's transcript line: the cards and what the voice says, aligned by text in order.

    Measured 2026-11-22 on the live Cebuano lessons and review sessions: 17 of 17 with scenes align
    one to one. The alignment is a longest-matching-blocks diff, not a greedy walk (review N1): a line
    the voice says otherwise matches nothing and takes no later line's saying, and a spoken line the
    story lacks shifts nothing after it.
    """
    said = [_plain(text) for text in _said(lesson)]
    wants = [_plain(card.spoken) for card in cards]
    out: list[int | None] = [None] * len(cards)
    for block in SequenceMatcher(None, wants, said, autojunk=False).get_matching_blocks():
        for k in range(block.size):
            out[block.a + k] = block.b + k
    return out


def _token_of(tokens: list[LineToken], word: tuple[int, int]) -> int:
    """The index of the token holding *word* (every word of the card's text is inside one)."""
    return next(i for i, t in enumerate(tokens) if t.start <= word[0] and word[1] <= t.end)


def sentence_lines(db, lesson: Lesson, content_id: str) -> list[SentenceLine]:
    """Every line of *lesson*, in story order; a lesson with no card list keys its lines by *content_id*."""
    marks = db.sentence_card_marks()
    listed = list(list_lines(lesson, content_id).values())
    indexes = _dialogue_indexes(lesson, [line.card for line in listed])
    return [
        replace(read_line(db, line, marks), dialogue_index=index) for line, index in zip(listed, indexes, strict=True)
    ]
