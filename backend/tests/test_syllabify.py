"""Unit tests for syllabification."""

import pytest

from app.generation.syllabify import syllabify_word
from app.plugins.languages.no.syllabify import syllabify_norwegian_word
from app.plugins.languages.sl.syllabify import syllabify_slovene_word
from app.plugins.languages.tl.syllabify import syllabify_tagalog_word

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
        # Stop+nasal (kn/gn) is a valid onset word-initially but never medially,
        # so it must not open the preceding syllable (tek·nisk, not te·knisk).
        ("teknisk", ["tek", "nisk"]),
        ("regne", ["reg", "ne"]),
        # Guard: a word-INITIAL kn onset is preserved (it never routes through
        # the medial onset split).
        ("knekke", ["knek", "ke"]),
    ],
)
def test_norwegian_syllabification(word, expected):
    assert syllabify_norwegian_word(word) == expected, f"word={word!r}"


# --- Language-dispatching syllabifier ---


def test_syllabify_word_routes_slovene():
    assert syllabify_word("prosim", "sl") == ["pro", "sim"]


def test_syllabify_word_routes_norwegian():
    assert syllabify_word("snakke", "no") == ["snak", "ke"]


def test_syllabify_word_unknown_code_falls_back_to_default():
    # Unknown codes use the generic default syllabifier rather than raising.
    from app.generation.syllabify import default_syllabifier

    assert syllabify_word("prosim", "xx") == default_syllabifier("prosim")


# --- Tagalog ---
#
# Golden table measured by the orchestrator (tunatale-w4m7.7). The target is
# how a word is SAID in chunks for the audio buildup, not KWF hyphenation:
# ``problema`` is ``pro|ble|ma`` here, where KWF hyphenates ``prob-le-ma``.


@pytest.mark.parametrize(
    "word, expected",
    [
        # Plain CV(C) structure
        ("ako", ["a", "ko"]),
        ("salamat", ["sa", "la", "mat"]),
        ("magandang", ["ma", "gan", "dang"]),
        ("kumusta", ["ku", "mus", "ta"]),
        ("magkano", ["mag", "ka", "no"]),
        ("bantay", ["ban", "tay"]),
        ("hinahanap", ["hi", "na", "ha", "nap"]),
        ("katagal", ["ka", "ta", "gal"]),
        # Vowel hiatus: the unwritten glottal stop separates adjacent vowels
        ("kain", ["ka", "in"]),
        ("kumain", ["ku", "ma", "in"]),
        ("kakain", ["ka", "ka", "in"]),
        ("paano", ["pa", "a", "no"]),
        ("maalat", ["ma", "a", "lat"]),
        ("nasaan", ["na", "sa", "an"]),
        ("kaibigan", ["ka", "i", "bi", "gan"]),
        ("nakakaintindi", ["na", "ka", "ka", "in", "tin", "di"]),
        # y and w are consonants: codas and onsets, never nuclei
        ("bahay", ["ba", "hay"]),
        ("ikaw", ["i", "kaw"]),
        ("siya", ["si", "ya"]),
        ("kailan", ["ka", "i", "lan"]),
        ("baywang", ["bay", "wang"]),
        ("aywan", ["ay", "wan"]),
        ("diyos", ["di", "yos"]),
        ("kuwarto", ["ku", "war", "to"]),
        # ng is ONE consonant: an onset between vowels, a coda before a consonant
        ("ngayon", ["nga", "yon"]),
        ("pangalan", ["pa", "nga", "lan"]),
        ("sangkap", ["sang", "kap"]),
        ("mangga", ["mang", "ga"]),
        ("pinggan", ["ping", "gan"]),
        ("tanghali", ["tang", "ha", "li"]),
        ("anghel", ["ang", "hel"]),
        # One written vowel is one syllable, even though mga is said /maˈŋa/
        ("mga", ["mga"]),
        ("ng", ["ng"]),
        # Loan onsets are valid anywhere
        ("trabaho", ["tra", "ba", "ho"]),
        ("prutas", ["pru", "tas"]),
        ("problema", ["pro", "ble", "ma"]),
        ("tsinelas", ["tsi", "ne", "las"]),
        ("kwarto", ["kwar", "to"]),
        ("pwede", ["pwe", "de"]),
        ("eskwela", ["es", "kwe", "la"]),
        ("sentro", ["sen", "tro"]),
        ("sombrero", ["som", "bre", "ro"]),
        ("restawran", ["res", "taw", "ran"]),
        # Consonant + y opens a word but splits medially
        ("syudad", ["syu", "dad"]),
        ("dyip", ["dyip"]),
        ("kanyang", ["kan", "yang"]),
        ("istasyon", ["is", "tas", "yon"]),
    ],
)
def test_tagalog_syllabification(word, expected):
    result = syllabify_tagalog_word(word)
    assert result == expected, f"{word}: expected {expected}, got {result}"
    assert "".join(result) == word


def test_tagalog_case_lowercased():
    assert syllabify_tagalog_word("Magandang") == ["ma", "gan", "dang"]


def test_syllabify_word_routes_tagalog():
    """The registry wires tl to its own syllabifier, not the default: the
    default splits ``pangalan`` as ``pan|ga|lan`` because it knows nothing
    about ``ng``."""
    assert syllabify_word("pangalan", "tl") == ["pa", "nga", "lan"]
