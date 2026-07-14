"""Syntactic unit (collocation) domain model."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

# Where a rich back-of-card field is shown on the drill card's answer side.
#   "summary" — always visible inline (e.g. IPA, a one-line meaning)
#   "details" — inside a collapsed "Details" disclosure (inflections, examples…)
#   "deep"    — its own nested disclosure, opened on demand (the big dictionary entry)
BackFieldTier = Literal["summary", "details", "deep"]


@dataclass(frozen=True)
class BackField:
    """One extracted rich back-of-card field: a labelled HTML fragment + its tier.

    Sourced from an Anki notetype's secondary fields (see
    ``app.cards.field_map.NotetypeProfile.back_fields``); display-only, never
    edited in TT. ``html`` is already sanitized at extraction time.
    """

    label: str
    html: str
    tier: BackFieldTier = "details"


def serialize_extras(extras: tuple[BackField, ...]) -> str:
    """Serialize ``extras`` to a JSON string for storage. Empty → ``""``."""
    if not extras:
        return ""
    return json.dumps([{"label": e.label, "html": e.html, "tier": e.tier} for e in extras])


def deserialize_extras(raw: str | None) -> tuple[BackField, ...]:
    """Parse a stored extras JSON string back into ``BackField``s.

    Tolerant by design: blank/None or malformed JSON yields ``()`` so a bad row
    never breaks a card render.
    """
    if not raw:
        return ()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError, ValueError:
        return ()
    if not isinstance(data, list):
        return ()
    return tuple(
        BackField(label=str(d["label"]), html=str(d["html"]), tier=d.get("tier", "details"))
        for d in data
        if isinstance(d, dict) and "label" in d and "html" in d
    )


@dataclass
class SyntacticUnit:
    """A collocation in the target language (L2) with its L1 translation.

    word_count must be ≥ 1. difficulty must be 1-5. The earlier
    `word_count <= 8` upper bound was a sanity guard against importing long
    English questions from reference/Q&A Anki notes; it turned out to drop
    legitimate phonics cards whose front field is a >8-word question. The
    filter is now only at the lower bound — single-token empty extractions
    still get rejected; long-form items pass through.
    source is "corpus" (frequency-derived), "llm" (generated), "anki", "test",
    or "user".
    """

    text: str  # L2 text
    translation: str  # L1 translation
    word_count: int
    difficulty: int  # 1–5
    source: str  # "corpus" | "llm" | "user" | "anki" | "test"
    frequency: int = 0
    lemma: str | None = None
    guid: str | None = None
    disambig_key: str = ""
    article: str = ""  # gender/indefinite article (en/ei/et), display-only prefix
    # Rich back-of-card fields (IPA, inflections, examples, dictionary entry…)
    # sourced from the Anki notetype's secondary fields. Display-only, optional;
    # empty for languages/notetypes without a profile that declares them.
    extras: tuple[BackField, ...] = field(default_factory=tuple)
    grammar: str = ""
    note: str = ""
    source_sentence: str = ""
    source_sentence_translation: str = ""
    source_lesson_id: str | None = None
    source_line_index: int | None = None
    card_type: str = "vocab"  # "vocab" | "cloze"

    def __post_init__(self) -> None:
        if self.word_count < 1:
            raise ValueError(f"word_count must be ≥ 1, got {self.word_count}")
        if not 1 <= self.difficulty <= 5:
            raise ValueError(f"difficulty must be 1–5, got {self.difficulty}")
