"""The contrast card: one root across an affix pattern, one form blank (tunatale-ve4p.6).

A cloze over a small paradigm instead of over a sentence. For Cebuano
``lakaw`` in ``mo-`` / ``mi-``::

    lakaw · walk
    will walk: molakaw
    walked: [...]

The forms the learner is not asked for are the model; the blank is the same
root one step over. A lesson line that says the blanked form follows the grid
when there is one, with the same blank.

**The grid is never stored.** A card's row holds what any cloze holds: the
form (``text``), the root (``lemma``), and a sentence with the form blanked
(``source_sentence``), which is the lesson line or, with no line, the form
alone. Everything that voices or matches ``source_sentence`` therefore still
gets speech. The grid is built from the root and the key when the card is
drawn (:func:`paradigm`) and when its Anki note is written
(:func:`cloze_note_text`), from the language's own table as it stands then.

The key is ``morph:pair-<feature>``. ``morph:`` keeps the card out of the
mastery grouping and inside ``get_inflection_clozes_for_lemma``, like the
sentence cloze the Inflect button makes; ``pair-`` keeps the two from
colliding on one surface under ``UNIQUE(text, disambig_key)``.

Nothing here writes anything.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.generation.affix_drill import DrillRoot, drill_root
from app.languages import get_a1_morphology
from app.models.syntactic_unit import SyntacticUnit
from app.srs.a1_morphology import AffixPattern
from app.srs.function_words import make_cloze_text, uncloze_text

_PAIR_PREFIX = "morph:pair-"


def pair_key(feature: str) -> str:
    """The ``disambig_key`` of the contrast card that asks for *feature*'s form."""
    return _PAIR_PREFIX + feature.replace(":", "-")


def _bare(form: str) -> str:
    return f"{{{{c1::{form}}}}}"


@dataclass(frozen=True)
class ContrastCard:
    root: str
    english: str
    cells: tuple[tuple[str, str], ...]  # (form, its English), in the pattern's order
    blank: int
    feature: str
    line: str = ""  # a lesson line that says the blanked form, as the lesson wrote it
    line_english: str = ""

    @property
    def form(self) -> str:
        return self.cells[self.blank][0]

    @property
    def prompt(self) -> str:
        """The English the learner is asked to say."""
        return self.cells[self.blank][1]

    def unit(self) -> SyntacticUnit:
        """The card as TT stores it: a production-only cloze on the form."""
        return SyntacticUnit(
            text=self.form,
            translation=self.prompt,
            word_count=1,
            difficulty=1,
            source="llm",
            lemma=self.root,
            disambig_key=pair_key(self.feature),
            card_type="cloze",
            source_sentence=make_cloze_text(self.form, self.line) if self.line else _bare(self.form),
            source_sentence_translation=self.line_english,
            grammar=f"{self.root} · {self.english}",
        )


def contrast_card(
    language_code: str, pattern: AffixPattern, root: str, blank: int, *, line: tuple[str, str] | None = None
) -> ContrastCard | None:
    """*root*'s card in *pattern* with cell *blank* left to say, or ``None``.

    ``None`` when the language cannot drill the root in this pattern: it
    cannot vouch for every form, or nobody has written down what they mean.
    *line* is ``(a lesson line, its English)`` and has to say the blanked form.
    """
    ready = drill_root(language_code, root, pattern)
    if not isinstance(ready, DrillRoot):
        return None
    form = ready.cells[blank][0]
    text, english = line or ("", "")
    if text and make_cloze_text(form, text) == text:
        raise ValueError(f"the line {text!r} does not say {form!r}")
    return ContrastCard(
        root=ready.root,
        english=ready.english,
        cells=ready.cells,
        blank=blank,
        feature=pattern.features[blank],
        line=text,
        line_english=english,
    )


def paradigm(language_code: str, unit: SyntacticUnit) -> ContrastCard | None:
    """The contrast card *unit* is, or ``None`` for any other card.

    Read back from the root and the key against the language's table as it is
    today. A stored form the table no longer spells that way comes back
    ``None``, so the card is drawn as the ordinary cloze it also is rather
    than beside a model nobody vouches for.
    """
    bundle = get_a1_morphology(language_code)
    if bundle is None or not unit.disambig_key.startswith(_PAIR_PREFIX) or not unit.lemma:
        return None
    for pattern in bundle.patterns:
        for blank, feature in enumerate(pattern.features):
            if pair_key(feature) != unit.disambig_key:
                continue
            ready = drill_root(language_code, unit.lemma, pattern)
            if isinstance(ready, DrillRoot) and ready.cells[blank][0].casefold() == unit.text.casefold():
                has_line = unit.source_sentence != _bare(ready.cells[blank][0])
                return ContrastCard(
                    root=ready.root,
                    english=ready.english,
                    cells=ready.cells,
                    blank=blank,
                    feature=feature,
                    line=uncloze_text(unit.source_sentence) if has_line else "",
                    line_english=unit.source_sentence_translation if has_line else "",
                )
    return None


def cloze_note_text(language_code: str, unit: SyntacticUnit, sentence: str) -> str:
    """Field 0 of *unit*'s Anki note, given its *sentence* with the blank marked.

    Under the grid for a contrast card. For any other cloze it is *sentence*,
    returned untouched: marking the blank stays the job of whoever stored the
    sentence. Every blank is ``c1``, so the note makes one card.
    """
    card = paradigm(language_code, unit)
    if card is None:
        return sentence
    rows = [f"{card.root} · {card.english}"]
    rows += [f"{english}: {_bare(form) if i == card.blank else form}" for i, (form, english) in enumerate(card.cells)]
    grid = "<br>".join(rows)
    return f"{grid}<br><br>{sentence}" if card.line else grid
