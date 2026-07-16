"""Unit tests for Slovene syllabification."""

import pytest

from app.generation.syllabify import (
    syllabify_norwegian_word,
    syllabify_slovene_word,
    syllabify_word,
)

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


# --- Norwegian syllabification ---


def test_norwegian_empty_string():
    assert syllabify_norwegian_word("") == []


def test_norwegian_single_vowel():
    assert syllabify_norwegian_word("å") == ["å"]


def test_norwegian_case_lowercased():
    assert syllabify_norwegian_word("Norge") == ["nor", "ge"]


@pytest.mark.parametrize(
    "word, expected",
    [
        # Single-syllable words (one or zero vowels)
        ("takk", ["takk"]),
        ("norsk", ["norsk"]),
        ("jeg", ["jeg"]),
        ("nytt", ["nytt"]),
        # Two-consonant medial cluster → split before the last consonant
        ("hallo", ["hal", "lo"]),
        ("snakke", ["snak", "ke"]),
        ("kaffe", ["kaf", "fe"]),
        ("gutten", ["gut", "ten"]),
        ("vannet", ["van", "net"]),
        ("bytte", ["byt", "te"]),  # y is a vowel
        # Medial cluster ending in a valid onset → onset goes with next vowel
        ("elske", ["el", "ske"]),  # "sk" is a valid onset
        ("ekstra", ["ek", "stra"]),  # "str" is a valid 3-consonant onset
        # Norwegian special vowels æ/ø/å
        ("lære", ["læ", "re"]),
        ("kjøre", ["kjø", "re"]),
        ("måne", ["må", "ne"]),
        # Diphthongs (øy/ei/au) are a single nucleus — the glide is not stranded
        # as its own syllable.
        ("bøyde", ["bøy", "de"]),
        ("bøye", ["bøy", "e"]),
        ("høy", ["høy"]),  # monosyllabic diphthong
        ("øy", ["øy"]),
        ("leilig", ["lei", "lig"]),  # the "leilighet" stem
        ("veien", ["vei", "en"]),
        ("august", ["au", "gust"]),
        ("restaurant", ["re", "stau", "rant"]),
        # Guard: genuine hiatus must stay split (the second vowel is not a glide)
        ("noe", ["no", "e"]),
        ("intuisjon", ["in", "tu", "i", "sjon"]),  # "ui" is not a diphthong
    ],
)
def test_norwegian_syllabification(word, expected):
    assert syllabify_norwegian_word(word) == expected, f"word={word!r}"


# --- Language-dispatching syllabifier ---


def test_syllabify_word_routes_slovene():
    assert syllabify_word("prosim", "sl") == ["pro", "sim"]


def test_syllabify_word_routes_norwegian():
    assert syllabify_word("snakke", "no") == ["snak", "ke"]


def test_syllabify_word_unknown_code_falls_back_to_slovene():
    # Unknown codes use the default (Slovene) onset rules rather than raising.
    assert syllabify_word("prosim", "xx") == syllabify_slovene_word("prosim")
