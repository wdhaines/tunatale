"""Syntactic unit (collocation) domain model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SyntacticUnit:
    """A collocation in the target language (L2) with its L1 translation.

    word_count must be 1-8. difficulty must be 1-5.
    source is either "corpus" (frequency-derived) or "llm" (generated).
    """

    text: str  # L2 text
    translation: str  # L1 translation
    word_count: int
    difficulty: int  # 1–5
    source: str  # "corpus" | "llm"
    frequency: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.word_count <= 8:
            raise ValueError(f"word_count must be 1–8, got {self.word_count}")
        if not 1 <= self.difficulty <= 5:
            raise ValueError(f"difficulty must be 1–5, got {self.difficulty}")
