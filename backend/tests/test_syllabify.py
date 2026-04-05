"""Unit tests for Slovene syllabification."""

import pytest

from app.generation.syllabify import syllabify_slovene_word

# --- Edge cases ---


def test_empty_string():
    assert syllabify_slovene_word("") == []


def test_single_vowel():
    assert syllabify_slovene_word("a") == ["a"]


def test_single_consonant():
    assert syllabify_slovene_word("r") == ["r"]


# --- Case insensitivity ---


def test_case_lowercased():
    assert syllabify_slovene_word("Prosim") == ["pro", "sim"]
    assert syllabify_slovene_word("DOBER") == ["do", "ber"]


# --- Parametrized word tests ---


@pytest.mark.parametrize(
    "word, expected",
    [
        # Single-syllable words
        ("dan", ["dan"]),
        ("prst", ["prst"]),
        ("trg", ["trg"]),
        # Two-syllable words
        ("kavo", ["ka", "vo"]),
        ("prosim", ["pro", "sim"]),
        ("dober", ["do", "ber"]),
        ("hvala", ["hva", "la"]),
        ("večer", ["ve", "čer"]),
        ("lepo", ["le", "po"]),
        ("eno", ["e", "no"]),
        # Three-syllable words
        ("koliko", ["ko", "li", "ko"]),
        ("razumem", ["ra", "zu", "mem"]),
        ("dobro", ["do", "bro"]),
        # Four-syllable words
        ("oprostite", ["o", "pro", "sti", "te"]),
        ("slovenščina", ["slo", "ven", "šči", "na"]),
        # Hiatus (adjacent vowels)
        ("nauk", ["na", "uk"]),
        # Onset cluster examples
        ("estra", ["e", "stra"]),
        ("laski", ["la", "ski"]),
    ],
)
def test_syllabification(word, expected):
    assert syllabify_slovene_word(word) == expected, f"word={word!r}"
