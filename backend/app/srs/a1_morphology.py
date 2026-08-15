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
class A1Morphology:
    """The per-language A1 morphology contract.

    ``to_feature`` maps a UD ``TokenAnalysis`` to a TT feature string
    (e.g. ``"verb:1sg"``) or ``None`` when the token carries no A1 inflection.
    ``a1_prefixes`` is the whitelist of feature strings the language considers
    A1 — validation and cloze-gating consult it. ``format_hint`` renders
    ``(lemma, feature)`` as human hint text (e.g. ``"biti, 1st person
    singular"``).
    """

    to_feature: Callable[[TokenAnalysis], str | None]
    a1_prefixes: tuple[str, ...]
    format_hint: Callable[[str, str], str]
