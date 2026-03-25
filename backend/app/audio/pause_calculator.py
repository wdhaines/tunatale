"""Natural pause calculator — ports exact prototype ratios."""

from __future__ import annotations

from app.models.lesson import SectionType

# Word-count → multiplier table (exact from prototype CLAUDE.md)
_WORD_COUNT_MULTIPLIERS: dict[int, float] = {
    1: 1.5,
    2: 1.8,
    3: 2.2,
    4: 2.6,
    5: 3.0,
}
_DEFAULT_MULTIPLIER = 3.5  # 6+ words

# Fixed boundary pauses (ms)
_BOUNDARY_PAUSES: dict[str, int] = {
    "syllable": 300,
    "sentence": 2000,
}

_SECTION_BOUNDARY_PAUSE_MS = 3000
_SLOW_SPEED_FACTOR = 1.2

# Base pause ratio: pause = audio_duration * multiplier
_BASE_PAUSE_RATIO = 0.8


class NaturalPauseCalculator:
    """Calculates natural inter-phrase pauses matching prototype ratios."""

    def _get_word_count_multiplier(self, word_count: int) -> float:
        return _WORD_COUNT_MULTIPLIERS.get(word_count, _DEFAULT_MULTIPLIER)

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
    ) -> int:
        """Calculate the pause (ms) to insert after a phrase.

        Args:
            audio_duration_s: Duration of the synthesised phrase audio in seconds.
            word_count: Number of words in the phrase.
            section_type: The section this phrase belongs to.

        Returns:
            Pause duration in milliseconds (non-negative).
        """
        multiplier = self._get_word_count_multiplier(word_count)
        pause_s = audio_duration_s * _BASE_PAUSE_RATIO * multiplier

        if section_type == SectionType.SLOW_SPEED:
            pause_s *= _SLOW_SPEED_FACTOR

        return max(0, int(pause_s * 1000))
