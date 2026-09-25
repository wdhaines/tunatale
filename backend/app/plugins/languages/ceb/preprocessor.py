"""Cebuano-specific text preprocessing for TTS synthesis."""

from __future__ import annotations

from app.models.lesson import SectionType


class CebuanoPreprocessor:
    """Cebuano text preprocessor (pass-through; reserved for future transforms)."""

    def preprocess(self, text: str, section_type: SectionType) -> str:
        return text
