"""The drill card: one card per lesson drill, each review asking the drill's next step (tunatale-5i1p.4).

Design: ``.beads-tasks/briefs/design-rotating-drill-card-2026-11.md``. The
scheduler spaces the PATTERN, not one sentence:

* **Which step:** ``step = reps mod steps``, where ``reps`` is the card's review
  count. TT and Anki hold the same count after a sync, so they agree on the
  step without storing it, and nothing here is ever written back to the row.
* **Front:** the sentence before, then the cue. For a substitution drill the
  sentence before is the model (step 0) or the previous step's answer; for a
  transformation drill it is one worked pair, the previous step's cue and
  answer (step 0 takes the last pair); a true/false statement stands alone.
  **Back:** the answer, and English only where the lesson gives a step one.
* **A question-and-answer drill does not rotate:** it makes one card per step,
  the question as the cue, keyed ``sent:<set>:drill-<n>:<k>`` (k counts from
  1, as n does). Its view is always its own step.

The row (``card_type = "cloze"``, PRODUCTION only) is keyed
``sent:<set>:drill-<n>``: the ``sent:`` prefix keeps it in the sentence-card
plumbing, and its sibling group is its own key (one note, one card). Its
``text`` never changes, since the guid is ``compute_guid(text, language, key)``:
the model for a substitution drill, else the first step's cue.

Its JSON is a shape of its own, ``{"drill": {...}}``, beside
``SentenceCard``'s: ``SentenceCard.from_json`` reads it as ``None`` and this
module's ``from_json`` reads a sentence card as ``None``, so neither widens
what the other's rows serialise to. Every reader asks for :meth:`view` and
goes no further into the drill.

The step view names the media row each clip lives in (``drill_model``,
``drill_cue_<k>``, ``drill_answer_<k>``, k = the 0-based step), so the review
payload and, later, the note's fields find a step's sound the same way.

Nothing here writes anything.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from app.models.syntactic_unit import SyntacticUnit
from app.srs.sentence_card import SENTENCE_KEY_PREFIX

#: Kinds that rotate: one card per drill.
ROTATING_KINDS = ("substitution", "transformation", "true_false")
#: Kinds that make one card per step.
PER_STEP_KINDS = ("question_answer",)

_DRILL_KEY = re.compile(r"sent:[a-z0-9]+(?:-[a-z0-9]+)*:drill-[1-9][0-9]*(?::[1-9][0-9]*)?")

MODEL_MEDIA = "drill_model"


def cue_media(k: int) -> str:
    return f"drill_cue_{k}"


def answer_media(k: int) -> str:
    return f"drill_answer_{k}"


def is_drill_key(key: str) -> bool:
    """Whether *key* is a drill card's: ``sent:<set>:drill-<n>`` or ``sent:<set>:drill-<n>:<k>``, whole."""
    return _DRILL_KEY.fullmatch(key) is not None


@dataclass(frozen=True)
class DrillStep:
    cue: str
    answer: str
    english: str = ""


@dataclass(frozen=True)
class DrillView:
    """What one review of a drill card shows, and which media row plays each part."""

    step: int  # 0-based
    of: int
    kind: str
    cue: str
    answer: str
    english: str
    before: str | None  # the sentence before (substitution), or the worked pair's answer (transformation)
    before_cue: str | None  # the worked pair's cue (transformation only)
    cue_media: str
    answer_media: str
    before_media: str | None
    before_cue_media: str | None


@dataclass(frozen=True)
class DrillCard:
    card_set: str
    drill: int  # 1-based place in the story's ``drills``
    kind: str
    model: str  # a substitution drill's model sentence; "" for the other kinds
    steps: tuple[DrillStep, ...]
    step: int | None = None  # a question-and-answer card's own step (0-based); None = rotating

    @property
    def key(self) -> str:
        base = f"{SENTENCE_KEY_PREFIX}{self.card_set}:drill-{self.drill}"
        return base if self.step is None else f"{base}:{self.step + 1}"

    @property
    def text(self) -> str:
        """The row's text, fixed for the life of the card (the guid is built from it)."""
        if self.step is not None:
            return self.steps[self.step].cue
        return self.model if self.kind == "substitution" else self.steps[0].cue

    def step_at(self, reps: int) -> int:
        return self.step if self.step is not None else reps % len(self.steps)

    def view(self, reps: int) -> DrillView:
        """The step a card with *reps* reviews behind it shows."""
        s = self.step_at(reps)
        this = self.steps[s]
        before = before_cue = before_media = before_cue_media = None
        if self.step is None and self.kind == "substitution":
            if s == 0:
                before, before_media = self.model, MODEL_MEDIA
            else:
                before, before_media = self.steps[s - 1].answer, answer_media(s - 1)
        elif self.step is None and self.kind == "transformation":
            p = (s - 1) % len(self.steps)
            before_cue, before_cue_media = self.steps[p].cue, cue_media(p)
            before, before_media = self.steps[p].answer, answer_media(p)
        return DrillView(
            step=s,
            of=len(self.steps),
            kind=self.kind,
            cue=this.cue,
            answer=this.answer,
            english=this.english,
            before=before,
            before_cue=before_cue,
            cue_media=cue_media(s),
            answer_media=answer_media(s),
            before_media=before_media,
            before_cue_media=before_cue_media,
        )

    def media_kinds(self) -> list[tuple[str, str]]:
        """``(media kind, the text its clip says)`` for every clip any view of this card plays."""
        wanted: list[tuple[str, str]] = []
        if self.step is None and self.kind == "substitution":
            wanted.append((MODEL_MEDIA, self.model))
        indices = range(len(self.steps)) if self.step is None else (self.step,)
        for k in indices:
            wanted.append((cue_media(k), self.steps[k].cue))
            wanted.append((answer_media(k), self.steps[k].answer))
        return wanted

    def to_json(self) -> str:
        return json.dumps({"drill": asdict(self)}, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str | None) -> DrillCard | None:
        """The card *raw* holds, or ``None`` for anything that is not one (a bad row never breaks a render)."""
        try:
            d = json.loads(raw or "")["drill"]
            return cls(
                card_set=d["card_set"],
                drill=d["drill"],
                kind=d["kind"],
                model=d["model"],
                steps=tuple(DrillStep(**s) for s in d["steps"]),
                step=d["step"],
            )
        except ValueError, TypeError, KeyError:
            return None

    def unit(self) -> SyntacticUnit:
        """The card as TT stores it: a production-only cloze carrying the whole drill in ``card_data``."""
        return SyntacticUnit(
            text=self.text,
            translation="",
            word_count=max(1, len(self.text.split())),
            difficulty=1,
            source="user",
            disambig_key=self.key,
            card_type="cloze",
            source_sentence=f"{{{{c1::{self.text}}}}}",
            source_lesson_id=self.card_set,
            card_data=self.to_json(),
        )


def drill_cards(card_set: str, n: int, drill: dict) -> list[DrillCard]:
    """The cards a story's drill *n* (1-based) makes: one rotating card, or one per step for question-and-answer."""
    steps = tuple(DrillStep(s["cue"], s["answer"], s.get("english", "")) for s in drill["steps"])
    kind = drill["kind"]
    base = DrillCard(card_set=card_set, drill=n, kind=kind, model=drill.get("model", ""), steps=steps)
    if kind in PER_STEP_KINDS:
        return [DrillCard(**{**base.__dict__, "step": k}) for k in range(len(steps))]
    return [base]


def drill_card(unit: SyntacticUnit) -> DrillCard | None:
    """The drill card *unit* is, or ``None`` for any other card."""
    if not is_drill_key(unit.disambig_key):
        return None
    return DrillCard.from_json(unit.card_data)
