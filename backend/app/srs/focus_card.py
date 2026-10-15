"""The focus card: one cell of a sentence-taught root, said as a whole sentence (tunatale-ve4p.9).

Cebuano object focus adds a second choice to the affix, which noun is the
``ang`` noun, and English shows it only in the object's article. So the card
asks for a whole sentence::

    front:  I drank **the water**.          [...]
    back:   **Giinom** ko **ang tubig**.

The front is the English with the focus in bold, the words the narrator
stresses; the back bolds the affixed verb and the focus. Each root gets one
card per cell, actor focus and object focus alike, so no card can be passed
on tense alone. The language says which sentence each card is
(``A1Morphology.pattern_cards``).

**The bold is never stored.** A card's row holds what any cloze holds: the
sentence (``text``), its English (``translation``), the root (``lemma``) and
the sentence as the blank (``source_sentence``). The bold is derived when the
card is served (:func:`focus`) and when its Anki note is written
(``contrast_card.cloze_note_text``), from the language's table as it stands
then. A sentence the table no longer makes comes back as a plain cloze.

The key is ``morph:focus-<feature>``. ``morph:`` keeps the card out of the
mastery grouping, as for the contrast card; ``focus-`` keeps an actor focus
card ("Mikaon siya og pan.") out of the mo-mi gate, which counts
``morph:pair-verb-mi``.

Nothing here writes anything.

Design: .beads-tasks/briefs/design-ve4p-stage6-object-focus-2026-10.md § 6.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.languages import get_a1_morphology
from app.models.syntactic_unit import SyntacticUnit
from app.srs.a1_morphology import AffixPattern

_FOCUS_PREFIX = "morph:focus-"


def focus_key(feature: str) -> str:
    """The ``disambig_key`` of the focus card that asks for *feature*'s cell."""
    return _FOCUS_PREFIX + feature.replace(":", "-")


def _runs(text: str, spans: tuple[tuple[int, int], ...]) -> list[tuple[str, bool]]:
    """*text* cut into ``(run, bold)`` pieces, the *spans* bold and the rest plain."""
    runs: list[tuple[str, bool]] = []
    at = 0
    for start, end in sorted(spans):
        runs += [(text[at:start], False), (text[start:end], True)]
        at = end
    runs.append((text[at:], False))
    return [(run, bold) for run, bold in runs if run]


def _html(runs: list[tuple[str, bool]]) -> str:
    return "".join(f"<b>{run}</b>" if bold else run for run, bold in runs)


@dataclass(frozen=True)
class FocusCard:
    root: str
    root_english: str
    feature: str
    text: str  # the sentence to say
    english: str
    stressed: tuple[int, int]  # the focus, as a span of ``english``
    bold: tuple[tuple[int, int], ...]  # the verb and the focus, as spans of ``text``

    @property
    def key(self) -> str:
        return focus_key(self.feature)

    # The contrast card's names, which the listen preview and commit read off
    # either card: the row is named by what it asks for, here the sentence.
    @property
    def form(self) -> str:
        return self.text

    @property
    def prompt(self) -> str:
        return self.english

    @property
    def model(self) -> tuple[str, ...]:
        """Nothing is shown beside the card: the whole sentence is the answer."""
        return ()

    def prompt_parts(self) -> list[tuple[str, bool]]:
        """The front: the English, focus in bold."""
        return _runs(self.english, (self.stressed,))

    def answer_parts(self) -> list[tuple[str, bool]]:
        """The back: the sentence, verb and focus in bold."""
        return _runs(self.text, self.bold)

    def note_text(self) -> str:
        """Field 0 of the card's Anki Cloze note: the bold English over the bold sentence as the blank."""
        return f"{_html(self.prompt_parts())}<br><br>{{{{c1::{_html(self.answer_parts())}}}}}"

    def unit(self) -> SyntacticUnit:
        """The card as TT stores it: a production-only cloze on the whole sentence."""
        return SyntacticUnit(
            text=self.text,
            translation=self.english,
            word_count=len(self.text.split()),
            difficulty=1,
            source="llm",
            lemma=self.root,
            disambig_key=self.key,
            card_type="cloze",
            source_sentence=f"{{{{c1::{self.text}}}}}",
            grammar=f"{self.root} · {self.root_english}",
        )


def focus_cards(language_code: str, pattern: AffixPattern, root: str) -> tuple[FocusCard, ...]:
    """*root*'s focus cards in *pattern*, in the order a learner gets them; none where nobody checked them."""
    bundle = get_a1_morphology(language_code)
    if bundle is None or bundle.pattern_cards is None or bundle.pattern_sentences is None or not pattern.sentences:
        return ()
    sentences = bundle.pattern_sentences(root, pattern)
    if sentences is None:
        return ()
    return tuple(
        FocusCard(root, sentences.english, card.feature, card.text, card.english, card.stressed, card.bold)
        for card in bundle.pattern_cards(root, pattern)
    )


def focus(language_code: str, unit: SyntacticUnit) -> FocusCard | None:
    """The focus card *unit* is, or ``None`` for any other card.

    Read back from the root, the key and the sentence against the language's
    table as it is today. A stored sentence the table no longer makes comes
    back ``None``, so the card is drawn as the plain cloze it also is.
    """
    bundle = get_a1_morphology(language_code)
    if bundle is None or not unit.disambig_key.startswith(_FOCUS_PREFIX) or not unit.lemma:
        return None
    for pattern in bundle.patterns:
        for card in focus_cards(language_code, pattern, unit.lemma):
            if card.key == unit.disambig_key and card.text.casefold() == unit.text.casefold():
                return card
    return None
