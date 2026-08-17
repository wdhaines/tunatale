"""Turn a note's own fields into a sentence that can carry a cloze.

A word that images badly gets a cloze production card instead of a picture one
(``.beads-tasks/briefs/design-no-production-cards-2026-08.md``), and the cheapest
sentence source is the note itself — ``Example sentences`` is 98.7% populated on
the Norwegian deck and already glossed.

The hard part is the **surface to blank**, not the sentence. A dictionary
headword usually appears inflected in its own example; measured over the 1550
words awaiting promotion, the headword appears verbatim in 65.7% of cases and
only an inflected form in a further 31.8%. So the chooser falls back to the
forms the deck lists in its own ``Inflections`` table — deck-authored ground
truth, which is why no lemmatizer is needed on the sync path.

A stem-prefix rule would cover the same cases and is rejected: it also matches
``for`` against ``fordi`` and would blank the wrong word. Every ambiguity here
declines instead, inheriting ``parse_example_sentences``'s discipline — a wrong
cloze lands on a card the learner studies and is far more expensive than a
missing one.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.cards.example_sentences import parse_example_sentences

#: The deck's ``Inflections`` field is a styled HTML table: grammar labels
#: (``entall``, ``presens``, …) in ``<thead>``, the actual forms in ``<tbody>``
#: cells. Reading the body only excludes the labels structurally, with no
#: per-language stoplist to maintain.
_TBODY_RE = re.compile(r"<tbody[^>]*>(.*?)</tbody>", re.DOTALL | re.IGNORECASE)
_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _dedup(surfaces: list[str]) -> list[str]:
    """*surfaces* in order, first occurrence wins.

    The headword is usually also its own first variant, and an inflection table
    often lists the base form again; a duplicate would only re-run a search that
    already failed.
    """
    seen: list[str] = []
    for surface in surfaces:
        if surface and surface not in seen:
            seen.append(surface)
    return seen


@dataclass(frozen=True)
class ClozeChoice:
    """A sentence, its gloss, and the surface within it to blank."""

    sentence: str
    gloss: str
    surface: str


def parse_inflection_forms(raw: str) -> tuple[str, ...]:
    """Every surface form the note's ``Inflections`` table lists, in source order.

    A cell may carry an article or auxiliary (``en valp``, ``å oppføre``, ``har
    oppført``), so the form is the cell's **last** token. Anything that is not a
    table yields ``()`` — this is a reader of one specific deck's markup, not a
    general HTML parser.
    """
    forms: list[str] = []
    for body in _TBODY_RE.findall(raw):
        for cell in _CELL_RE.findall(body):
            text = _TAG_RE.sub(" ", cell).replace("&nbsp;", " ").replace("\xa0", " ")
            tokens = [t for t in text.split() if t.isalpha()]
            if tokens and tokens[-1] not in forms:
                forms.append(tokens[-1])
    return tuple(forms)


def choose_cloze_sentence(
    word: str,
    examples_raw: str,
    inflections_raw: str,
    *,
    variants: Sequence[str] = (),
) -> ClozeChoice | None:
    """Pick the first example sentence that contains *word* in a blankable form.

    Candidate surfaces are the headword itself, then any alternate spellings the
    card front lists, then the deck's own inflected forms. Sentences are tried in
    source order, and within a sentence the headword wins — an example that
    spells the word plainly is a better prompt than one that inflects it.

    *variants* is the caller's registry-resolved ``card_surface_variants``: a
    front like ``mot, imot`` is ONE lexical item wearing two spellings, and the
    example sentence naturally uses one of them rather than the comma-joined
    headword. Without them such a word can never be clozed — searching the
    literal string ``mot, imot`` matches nothing — and 10 of the real deck's
    1550 candidates are that shape.

    Returns ``None`` when nothing matches: the word needs the LLM tier, which is
    not built. The surface is returned as the *sentence* spells it (casing and
    all), because that is the string the cloze has to blank.
    """
    if not word.strip():
        return None

    candidates = _dedup([word, *variants, *parse_inflection_forms(inflections_raw)])
    for example in parse_example_sentences(examples_raw):
        for candidate in candidates:
            match = re.search(rf"\b{re.escape(candidate)}\b", example.l2, re.IGNORECASE)
            if match is not None:
                return ClozeChoice(sentence=example.l2, gloss=example.gloss, surface=match.group(0))
    return None
