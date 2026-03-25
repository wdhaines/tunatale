"""Text preprocessor protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.models.lesson import SectionType


@runtime_checkable
class TextPreprocessor(Protocol):
    """Protocol for language-specific text preprocessing before TTS synthesis."""

    def preprocess(self, text: str, section_type: SectionType) -> str: ...
