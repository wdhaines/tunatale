"""Slovene-specific text preprocessing for TTS synthesis."""

from __future__ import annotations

from app.models.lesson import SectionType


class SlovenePreprocessor:
    """Prepares Slovene (and translated English) text for TTS synthesis.

    - NATURAL_SPEED / KEY_PHRASES / TRANSLATED: pass text through unchanged.
    - SLOW_SPEED: insert ellipses between syllable groups to slow delivery.
    """

    def preprocess(self, text: str, section_type: SectionType) -> str:
        """Preprocess text for the given section type.

        Args:
            text: Input text to preprocess.
            section_type: Determines what transformations to apply.

        Returns:
            Preprocessed text suitable for TTS.
        """
        if section_type == SectionType.SLOW_SPEED:
            return self._add_slow_pauses(text)
        return text

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _add_slow_pauses(self, text: str) -> str:
        """Insert pause markers between words to produce slower delivery."""
        # Insert an ellipsis between each word so TTS inserts natural gaps
        words = text.split()
        return " ... ".join(words)
