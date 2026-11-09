"""The sentence card: a lesson line said back, cued by the line before it (tunatale-h0q3.42).

Built for a course's Basic Sentences (Wolff's *Beginning Cebuano*), but nothing
here knows a language. The owner's rules (2026-11-05, on the bead):

* **No English on the front.** The front is the line before, in the target
  language with its own audio, and who answers it::

      Kari  Hvor bor du?   [sound]
      Per: [...]

  A line nobody cued (an opener) gets a picture, or only the speaker.
* A **blank card** asks for one word of the sentence instead of all of it (a
  particle, a linker, anything an English speaker would not predict). A linker
  suffix stays attached to its stem: ``Godt{{c1::ng}} dag``.
* **The back** shows the blanked word's own gloss and the sentence's English
  openly; its word-by-word glosses and "What the cue means" start closed.

The card is an Anki **Cloze** note like the focus card (no new notetype, so no
forced full upload). Unlike the focus card its content is not computed from a
language table: it is the course's text, which lives only in the learner's
database and deck, never in this repo. So the card is stored whole as JSON in
the row's ``card_data``, and field 0 and Back Extra are built from it, here,
for both TT and the Anki write.

Each blank is its own note for now (owner, 2026-11-06: separate notes now,
real siblings later, tunatale-h0q3.55): TT maps one collocation to one note.

The key is ``sent:<lesson>:<line>`` for the whole sentence and
``sent:<lesson>:<line>:<start>-<end>`` for a blank. ``sent:`` keeps the card
out of the mastery grouping, as ``morph:`` does for the grammar cards.

Nothing here writes anything.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, replace

from app.models.syntactic_unit import SyntacticUnit

SENTENCE_KEY_PREFIX = "sent:"


def is_sentence_key(key: str) -> bool:
    return key.startswith(SENTENCE_KEY_PREFIX)


@dataclass(frozen=True)
class Gloss:
    word: str
    gloss: str


@dataclass(frozen=True)
class Cue:
    """The line before: who said it, what (as shown and as the lesson spoke it), and its meaning."""

    speaker: str
    text: str
    spoken: str  # the lesson's text for this line, whose take is the cue's audio
    english: str
    glosses: tuple[Gloss, ...] = ()


def blank_span(text: str, blank: str) -> tuple[int, int]:
    """Where *blank* sits in *text*: the first whole word equal to it (case ignored), or for
    ``-suffix`` the suffix at the end of the first word that ends in it."""
    if blank.startswith("-"):
        m = re.search(rf"\w(?P<b>{re.escape(blank[1:])})(?!\w)", text, re.IGNORECASE)
        if m:
            return m.span("b")
    else:
        m = re.search(rf"(?<!\w){re.escape(blank)}(?!\w)", text, re.IGNORECASE)
        if m:
            return m.span()
    raise ValueError(f"blank {blank!r} not in {text!r}")


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def rich(s: str) -> str:
    """Escaped, with Wolff's ``_word_`` markup as italics: a gloss as HTML, on the card and on the review screen."""
    return re.sub(r"_([^_]+)_", r"<i>\1</i>", _esc(s))


def _gloss_rows(glosses: tuple[Gloss, ...]) -> str:
    return "".join(f'<div class="tt-gloss"><b>{_esc(g.word)}</b> {rich(g.gloss)}</div>' for g in glosses)


@dataclass(frozen=True)
class SentenceCard:
    lesson_id: str
    line_index: int
    speaker: str
    text: str  # the sentence as the card shows it
    spoken: str  # the lesson's text for this line, whose take is the answer's audio
    english: str
    glosses: tuple[Gloss, ...]
    cue: Cue | None = None
    blank: tuple[int, int] | None = None  # a span of ``text``; None = the whole sentence
    blank_gloss: str = ""
    picture: str = ""  # an opener's picture (a media filename), shown where a cue would be

    @property
    def line_key(self) -> str:
        """The line's key: the whole-line card's key, and the sibling group of all the line's cards."""
        return f"{SENTENCE_KEY_PREFIX}{self.lesson_id}:{self.line_index}"

    @property
    def key(self) -> str:
        return f"{self.line_key}:{self.blank[0]}-{self.blank[1]}" if self.blank else self.line_key

    def with_blank(self, span: tuple[int, int], gloss: str) -> SentenceCard:
        return replace(self, blank=span, blank_gloss=gloss)

    def cloze(self) -> str:
        """The sentence with its blank marked: the row's ``source_sentence``."""
        if self.blank is None:
            return f"{{{{c1::{self.text}}}}}"
        s, e = self.blank
        return f"{self.text[:s]}{{{{c1::{self.text[s:e]}}}}}{self.text[e:]}"

    def note_text(self, cue_audio: str | None = None) -> str:
        """Field 0 of the Anki Cloze note: the cue (or picture), then who answers, then the blank."""
        if self.picture:
            top = f'<div class="tt-pic"><img src="{html.escape(self.picture)}"></div>'
        elif self.cue is not None:
            top = (
                f'<div class="tt-cue"><span class="tt-who">{_esc(self.cue.speaker)}</span> {_esc(self.cue.text)}</div>'
            )
            top += f"[sound:{cue_audio}]" if cue_audio else ""
        else:
            top = ""
        if self.blank is None:
            say = f"{{{{c1::{_esc(self.text)}}}}}"
        else:
            s, e = self.blank
            say = f"{_esc(self.text[:s])}{{{{c1::{_esc(self.text[s:e])}}}}}{_esc(self.text[e:])}"
        return f'{top}<div class="tt-say"><span class="tt-who">{_esc(self.speaker)}:</span> {say}</div>'

    def back_extra(self, answer_audio: str | None = None) -> str:
        """Back Extra: the blank's gloss and the English openly; glosses and the cue's meaning closed."""
        parts = []
        if self.blank is not None:
            s, e = self.blank
            parts.append(f'<div class="tt-blank"><b>{_esc(self.text[s:e])}</b> = {rich(self.blank_gloss)}</div>')
        if answer_audio:
            parts.append(f"[sound:{answer_audio}]")
        parts.append(f'<div class="tt-en">{_esc(self.english)}</div>')
        if self.glosses:  # a line with no build-up rows would open onto nothing
            parts.append(
                f'<details class="tt-words"><summary>Word by word</summary>{_gloss_rows(self.glosses)}</details>'
            )
        if self.cue is not None:
            parts.append(
                '<details class="tt-cue-means"><summary>What the cue means</summary>'
                f'{_gloss_rows(self.cue.glosses)}<div class="tt-en">{_esc(self.cue.english)}</div></details>'
            )
        return "".join(parts)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str | None) -> SentenceCard | None:
        """The card *raw* holds, or ``None`` for anything that is not one (a bad row never breaks a render)."""
        try:
            d = json.loads(raw or "")
            cue = d["cue"]
            return cls(
                lesson_id=d["lesson_id"],
                line_index=d["line_index"],
                speaker=d["speaker"],
                text=d["text"],
                spoken=d["spoken"],
                english=d["english"],
                glosses=tuple(Gloss(**g) for g in d["glosses"]),
                cue=None if cue is None else Cue(**{**cue, "glosses": tuple(Gloss(**g) for g in cue["glosses"])}),
                blank=tuple(d["blank"]) if d["blank"] is not None else None,
                blank_gloss=d["blank_gloss"],
                picture=d["picture"],
            )
        except ValueError, TypeError, KeyError:
            return None

    def unit(self) -> SyntacticUnit:
        """The card as TT stores it: a production-only cloze carrying itself in ``card_data``."""
        return SyntacticUnit(
            text=self.text,
            translation=self.english,
            word_count=max(1, len(self.text.split())),
            difficulty=1,
            source="user",
            disambig_key=self.key,
            card_type="cloze",
            source_sentence=self.cloze(),
            source_lesson_id=self.lesson_id,
            source_line_index=self.line_index,
            card_data=self.to_json(),
        )


def sibling_group(unit: SyntacticUnit) -> str | None:
    """The sibling group a new row for *unit* joins: its line, for a sentence card; else none.

    The rows of one group become the cards of one "TT Sentence" note
    (``collocations.sibling_group``, tunatale-h0q3.55).
    """
    card = sentence_card(unit)
    return card.line_key if card is not None else None


def sentence_card(unit: SyntacticUnit) -> SentenceCard | None:
    """The sentence card *unit* is, or ``None`` for any other card."""
    if not is_sentence_key(unit.disambig_key):
        return None
    return SentenceCard.from_json(unit.card_data)
