"""Pause calculator tests — matches prototype ratios exactly."""

import pytest

from app.audio.pause_calculator import NaturalPauseCalculator
from app.models.lesson import SectionType


@pytest.fixture
def calc():
    return NaturalPauseCalculator()


class TestWordCountMultipliers:
    """Tests for word-count multipliers (exact from prototype CLAUDE.md)."""

    @pytest.mark.parametrize(
        "word_count, expected",
        [
            (1, 1.5),
            (2, 1.8),
            (3, 2.2),
            (4, 2.6),
            (5, 3.0),
            (6, 3.5),
            (10, 3.5),
        ],
    )
    def test_word_count_multiplier(self, calc, word_count, expected):
        assert calc._get_word_count_multiplier(word_count) == pytest.approx(expected), f"word_count={word_count}"


class TestSectionBoundaryPauses:
    """Tests for fixed section boundary pauses."""

    def test_section_boundary_pause_is_3000ms(self, calc):
        pause = calc.get_section_boundary_pause()
        assert pause == 3000

    def test_syllable_pause_is_300ms(self, calc):
        assert calc.get_boundary_pause("syllable") == 300

    def test_sentence_pause_is_2000ms(self, calc):
        assert calc.get_boundary_pause("sentence") == 2000


class TestDynamicPauseCalculation:
    """Tests for dynamic phrase pause calculation."""

    def test_slow_section_gets_1_2x_adjustment(self, calc):
        normal = calc.get_phrase_pause(audio_duration_s=1.0, word_count=2, section_type=SectionType.NATURAL_SPEED)
        slow = calc.get_phrase_pause(audio_duration_s=1.0, word_count=2, section_type=SectionType.SLOW_SPEED)
        assert slow == pytest.approx(normal * 1.2, rel=0.01)

    def test_longer_audio_gets_longer_pause(self, calc):
        short = calc.get_phrase_pause(audio_duration_s=0.5, word_count=3, section_type=SectionType.NATURAL_SPEED)
        long_ = calc.get_phrase_pause(audio_duration_s=2.0, word_count=3, section_type=SectionType.NATURAL_SPEED)
        assert long_ > short

    def test_pause_is_non_negative(self, calc):
        pause = calc.get_phrase_pause(audio_duration_s=0.1, word_count=1, section_type=SectionType.NATURAL_SPEED)
        assert pause >= 0
