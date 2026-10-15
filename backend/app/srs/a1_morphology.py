"""Per-language A1 morphology bundles — the registry payload for morphology features.

The A1 morphology vocabulary (which inflections are exercised in A1, how a UD
analysis maps to a TT feature string, which feature strings validate, and how
they render as hint text) is language-specific: Slovene is person/number-driven
(``verb:1sg``), Norwegian is definite/tense-driven (``noun:def:sg``,
``verb:past``). Each language plugin registers an ``A1Morphology`` bundle;
languages that register none fall back to the default (Slovene-shaped)
behaviour in ``app.srs.function_words``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.srs.lemmatizer import TokenAnalysis


@dataclass(frozen=True)
class AffixPattern:
    """A set of affixes taught as one contrast on a single root.

    ``features`` are A1 feature strings in teaching order (Cebuano's
    ``("verb:mo", "verb:mi")``: not yet done, then done). ``key`` names the
    pattern in stored lessons and card keys, so it must stay stable.

    ``labels`` name each feature's cell where the forms are laid out in
    columns, and ``notes`` are the sentences a narrator says to tell the learner
    what the forms are for; they speak of "the first" and "the second" form, in
    ``features`` order. Both are the language's to word, and both are empty for
    a pattern that has neither.

    ``review_only`` marks a pattern the curriculum reviews rather than teaches,
    because the learner brings it with them (Cebuano ma- / na-, which a
    Tagalog speaker already has). It passes the pace gate on a drill alone
    (``app.generation.pattern_gate``).

    ``sentences`` is True for patterns taught as whole sentences rather than
    as isolated word forms. ``spelled`` names how each feature's affix appears
    in a title (empty means ``"<affix>-"`` as today).

    ``card_features`` are what a sentence pattern's cards ask for, in the order
    a root takes them: every cell of the sentence grid, the forms the learner
    already has among them (Cebuano gi-, mi-, -on, mo-). Empty for a word
    pattern, whose cards ask for ``features``.

    ``halves`` split a sentence pattern's cells over two lessons, each a tuple
    of cell indices, taken in turn by the days that drill it; ``half_notes``
    are each half's own ``notes``. Both empty for a pattern drilled whole.
    """

    key: str
    features: tuple[str, ...]
    labels: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    review_only: bool = False
    sentences: bool = False
    spelled: tuple[str, ...] = ()
    card_features: tuple[str, ...] = ()
    halves: tuple[tuple[int, ...], ...] = ()
    half_notes: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class PatternSentences:
    """The checked sentences for a root in a sentence-taught pattern."""

    english: str
    forms: tuple[str, ...]
    cells: tuple[tuple[str, str], ...]
    stressed: tuple[tuple[int, int], ...]
    contrast: int
    regular: bool


@dataclass(frozen=True)
class PatternCard:
    """One card of a root in a sentence-taught pattern: a whole sentence to say.

    ``stressed`` is the focus as a span of ``english``, the words the narrator
    stresses and the card's front puts in bold; ``bold`` is the affixed verb
    and the focus as spans of ``text``, which the back puts in bold.
    """

    feature: str
    text: str
    english: str
    stressed: tuple[int, int]
    bold: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class A1Morphology:
    """The per-language A1 morphology contract.

    ``to_feature`` maps a UD ``TokenAnalysis`` to a TT feature string
    (e.g. ``"verb:1sg"``) or ``None`` when the token carries no A1 inflection.
    ``a1_prefixes`` is the whitelist of feature strings the language considers
    A1 — validation and cloze-gating consult it. ``format_hint`` renders
    ``(lemma, feature)`` as human hint text (e.g. ``"biti, 1st person
    singular"``).

    ``patterns`` are the affix contrasts the language can drill, in the order
    they are taught: a learner meets the next once the one before passes the
    pace gate (``app.generation.pattern_gate``). ``pattern_forms(root, pattern)`` spells one root across a pattern's
    features, or returns ``None`` when the language cannot vouch for every
    form. ``pattern_glosses(root, pattern)`` gives the root's English and the
    English of each form, or ``None`` when nobody has written it down: an affix
    can change a root's meaning, so this is never derived from the root's gloss.
    All three are empty for a language that registers no patterns.
    """

    to_feature: Callable[[TokenAnalysis], str | None]
    a1_prefixes: tuple[str, ...]
    format_hint: Callable[[str, str], str]
    patterns: tuple[AffixPattern, ...] = ()
    pattern_forms: Callable[[str, AffixPattern], tuple[str, ...] | None] | None = None
    pattern_glosses: Callable[[str, AffixPattern], tuple[str, tuple[str, ...]] | None] | None = None
    # Every root somebody has worded for the pattern, in the order they were
    # written down: what a drill may be built FROM, before the learner's own
    # cards decide what it is built of.
    pattern_roots: Callable[[AffixPattern], tuple[str, ...]] | None = None
    # Whole lines that show a root's forms doing what a gloss cannot say, each
    # ``(line, its English)``: empty for a root the language has none for.
    pattern_lines: Callable[[str, AffixPattern], tuple[tuple[str, str], ...]] | None = None
    pattern_sentences: Callable[[str, AffixPattern], PatternSentences | None] | None = None
    # A root's cards in a sentence pattern, one per ``card_features`` entry in
    # that order; empty for a root nobody has checked sentences for.
    pattern_cards: Callable[[str, AffixPattern], tuple[PatternCard, ...]] | None = None
