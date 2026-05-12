"""Syntactic unit (collocation) domain model."""

from __future__ import annotations

from dataclasses import dataclass


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
    grammar: str = ""
    note: str = ""
    source_sentence: str = ""
    source_lesson_id: str | None = None
    source_line_index: int | None = None
    card_type: str = "vocab"  # "vocab" | "cloze"

    def __post_init__(self) -> None:
        if self.word_count < 1:
            raise ValueError(f"word_count must be ≥ 1, got {self.word_count}")
        if not 1 <= self.difficulty <= 5:
            raise ValueError(f"difficulty must be 1–5, got {self.difficulty}")
