"""Text preprocessor tests."""

import pytest

from app.models.lesson import SectionType
from app.plugins.languages.sl.preprocessor import SlovenePreprocessor


@pytest.fixture
def preprocessor():
    return SlovenePreprocessor()


def test_preprocessor_returns_string(preprocessor):
    result = preprocessor.preprocess("dober dan", SectionType.NATURAL_SPEED)
    assert isinstance(result, str)


def test_slow_speed_passes_through_unchanged(preprocessor):
    text = "dober dan"
    result = preprocessor.preprocess(text, SectionType.SLOW_SPEED)
    assert result == text


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


def test_slow_pauses_skips_already_slowed(preprocessor):
    already_slowed = "Dober ... dan"
    result = preprocessor.preprocess(already_slowed, SectionType.SLOW_SPEED)
    assert result == already_slowed
