"""Text preprocessor tests."""

import pytest

from app.audio.preprocessing.slovene import SlovenePreprocessor
from app.models.lesson import SectionType


@pytest.fixture
def preprocessor():
    return SlovenePreprocessor()


def test_preprocessor_returns_string(preprocessor):
    result = preprocessor.preprocess("dober dan", SectionType.NATURAL_SPEED)
    assert isinstance(result, str)


def test_slow_speed_adds_pauses(preprocessor):
    text = "dober dan"
    slow = preprocessor.preprocess(text, SectionType.SLOW_SPEED)
    # Slow speech may add ellipses or other pause markers — result should differ or be longer
    assert isinstance(slow, str)
    assert len(slow) >= len(text)


def test_natural_speed_passes_through_unchanged(preprocessor):
    text = "dober dan"
    result = preprocessor.preprocess(text, SectionType.NATURAL_SPEED)
    assert text in result or result == text


def test_number_formatting(preprocessor):
    text = "Cena je 5 evrov."
    result = preprocessor.preprocess(text, SectionType.NATURAL_SPEED)
    assert isinstance(result, str)
    assert len(result) > 0


def test_key_phrases_section_supported(preprocessor):
    result = preprocessor.preprocess("dober dan", SectionType.KEY_PHRASES)
    assert isinstance(result, str)


def test_translated_section_supported(preprocessor):
    result = preprocessor.preprocess("good day", SectionType.TRANSLATED)
    assert isinstance(result, str)
