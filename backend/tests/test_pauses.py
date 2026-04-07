"""Pause calculator tests — matches prototype ratios exactly."""

import pytest

from app.audio.pause_calculator import NaturalPauseCalculator
from app.models.lesson import SectionType


@pytest.fixture
def calc():
    return NaturalPauseCalculator()


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
    """Tests for dynamic phrase pause calculation matching prototype rules."""

    def test_natural_speed_uses_base_500ms(self, calc):
        pause = calc.get_phrase_pause(
            audio_duration_s=1.0,
            word_count=3,
            section_type=SectionType.NATURAL_SPEED,
            language_code="sl",
        )
        assert pause == 500

    def test_natural_speed_english_uses_base_500ms(self, calc):
        pause = calc.get_phrase_pause(
            audio_duration_s=1.0,
            word_count=3,
            section_type=SectionType.NATURAL_SPEED,
            language_code="en",
        )
        assert pause == 500

    def test_translated_uses_base_500ms(self, calc):
        pause = calc.get_phrase_pause(
            audio_duration_s=2.0,
            word_count=5,
            section_type=SectionType.TRANSLATED,
            language_code="en",
        )
        assert pause == 500

    def test_key_phrases_l2_uses_audio_duration(self, calc):
        pause = calc.get_phrase_pause(
            audio_duration_s=1.5,
            word_count=2,
            section_type=SectionType.KEY_PHRASES,
            language_code="sl",
        )
        assert pause == 1500

    def test_key_phrases_l2_has_500ms_floor(self, calc):
        pause = calc.get_phrase_pause(
            audio_duration_s=0.2,
            word_count=1,
            section_type=SectionType.KEY_PHRASES,
            language_code="sl",
        )
        assert pause == 500

    def test_key_phrases_english_uses_base_500ms(self, calc):
        pause = calc.get_phrase_pause(
            audio_duration_s=2.0,
            word_count=3,
            section_type=SectionType.KEY_PHRASES,
            language_code="en",
        )
        assert pause == 500

    def test_slow_speed_l2_applies_1_2x_factor(self, calc):
        pause = calc.get_phrase_pause(
            audio_duration_s=1.0,
            word_count=2,
            section_type=SectionType.SLOW_SPEED,
            language_code="sl",
        )
        assert pause == 600

    def test_slow_speed_english_no_factor(self, calc):
        pause = calc.get_phrase_pause(
            audio_duration_s=1.0,
            word_count=2,
            section_type=SectionType.SLOW_SPEED,
            language_code="en",
        )
        assert pause == 500

    def test_pause_is_non_negative(self, calc):
        pause = calc.get_phrase_pause(
            audio_duration_s=0.0,
            word_count=1,
            section_type=SectionType.NATURAL_SPEED,
            language_code="sl",
        )
        assert pause >= 0
