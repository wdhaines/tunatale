"""Pause calculator tests — matches prototype ratios exactly."""

import pytest

from app.audio.pause_calculator import NaturalPauseCalculator
from app.models.lesson import SectionType


@pytest.fixture
def calc():
    return NaturalPauseCalculator()


# ── Word-count multipliers (exact from prototype CLAUDE.md) ───────────────


def test_one_word_multiplier_is_1_5(calc):
    mult = calc._get_word_count_multiplier(1)
    assert mult == pytest.approx(1.5)


def test_two_word_multiplier_is_1_8(calc):
    assert calc._get_word_count_multiplier(2) == pytest.approx(1.8)


def test_three_word_multiplier_is_2_2(calc):
    assert calc._get_word_count_multiplier(3) == pytest.approx(2.2)


def test_four_word_multiplier_is_2_6(calc):
    assert calc._get_word_count_multiplier(4) == pytest.approx(2.6)


def test_five_word_multiplier_is_3_0(calc):
    assert calc._get_word_count_multiplier(5) == pytest.approx(3.0)


def test_six_plus_word_multiplier_is_3_5(calc):
    assert calc._get_word_count_multiplier(6) == pytest.approx(3.5)
    assert calc._get_word_count_multiplier(10) == pytest.approx(3.5)


# ── Section boundary pauses ───────────────────────────────────────────────


def test_section_boundary_pause_is_3000ms(calc):
    pause = calc.get_section_boundary_pause()
    assert pause == 3000


# ── Slow-speed adjustment ─────────────────────────────────────────────────


def test_slow_section_gets_1_2x_adjustment(calc):
    normal = calc.get_phrase_pause(audio_duration_s=1.0, word_count=2, section_type=SectionType.NATURAL_SPEED)
    slow = calc.get_phrase_pause(audio_duration_s=1.0, word_count=2, section_type=SectionType.SLOW_SPEED)
    assert slow == pytest.approx(normal * 1.2, rel=0.01)


# ── Dynamic pause calculation ─────────────────────────────────────────────


def test_longer_audio_gets_longer_pause(calc):
    short = calc.get_phrase_pause(audio_duration_s=0.5, word_count=3, section_type=SectionType.NATURAL_SPEED)
    long_ = calc.get_phrase_pause(audio_duration_s=2.0, word_count=3, section_type=SectionType.NATURAL_SPEED)
    assert long_ > short


def test_pause_is_non_negative(calc):
    pause = calc.get_phrase_pause(audio_duration_s=0.1, word_count=1, section_type=SectionType.NATURAL_SPEED)
    assert pause >= 0


# ── Fixed boundary pauses ─────────────────────────────────────────────────


def test_syllable_pause_is_300ms(calc):
    assert calc.get_boundary_pause("syllable") == 300


def test_sentence_pause_is_2000ms(calc):
    assert calc.get_boundary_pause("sentence") == 2000
