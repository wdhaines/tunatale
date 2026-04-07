"""Natural pause calculator — ports exact prototype ratios."""

from __future__ import annotations

from app.models.lesson import SectionType

_BASE_PHRASE_PAUSE_MS = 500  # prototype's silence_between_phrases (0.5 s)
_SLOW_SPEED_FACTOR = 1.2
_SECTION_BOUNDARY_PAUSE_MS = 3000
_ENGLISH_LANG = "en"

_BOUNDARY_PAUSES: dict[str, int] = {
    "syllable": 300,
    "sentence": 2000,
}


class NaturalPauseCalculator:
    """Inter-phrase pause calculator matching the micro-demo-0.0 prototype."""

    def get_section_boundary_pause(self) -> int:
        """Return the pause (ms) inserted between lesson sections."""
        return _SECTION_BOUNDARY_PAUSE_MS

    def get_boundary_pause(self, boundary_type: str) -> int:
        """Return a fixed pause (ms) for the given boundary type."""
        return _BOUNDARY_PAUSES[boundary_type]

    def get_phrase_pause(
        self,
        audio_duration_s: float,
        word_count: int,
        section_type: SectionType,
        language_code: str = _ENGLISH_LANG,
    ) -> int:
        """Pause in ms to insert after a phrase.

        - Key Phrases + L2: audio-duration-based (1:1), floor 500 ms.
        - Key Phrases + English narrator: base 500 ms.
        - Slow Speed + L2: base 500 ms × 1.2.
        - Slow Speed + English narrator: base 500 ms (no slow factor).
        - Natural Speed / Translated (any language): base 500 ms.

        `word_count` is retained for backward compatibility with the renderer
        call site and is currently unused.
        """
        del word_count  # unused; kept for API stability

        is_l2 = language_code != _ENGLISH_LANG

        if section_type == SectionType.KEY_PHRASES and is_l2:
            return max(_BASE_PHRASE_PAUSE_MS, int(audio_duration_s * 1000))

        if section_type == SectionType.SLOW_SPEED and is_l2:
            return int(_BASE_PHRASE_PAUSE_MS * _SLOW_SPEED_FACTOR)

        return _BASE_PHRASE_PAUSE_MS
