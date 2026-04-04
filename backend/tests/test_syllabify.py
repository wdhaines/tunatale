"""Unit tests for Slovene syllabification."""

from app.generation.syllabify import syllabify_slovene_word

# --- Edge cases ---


def test_empty_string():
    assert syllabify_slovene_word("") == []


def test_single_vowel():
    assert syllabify_slovene_word("a") == ["a"]


def test_single_consonant():
    assert syllabify_slovene_word("r") == ["r"]


# --- Single-syllable words ---


def test_single_syllable_cvc():
    assert syllabify_slovene_word("dan") == ["dan"]


def test_single_syllable_syllabic_r_prst():
    assert syllabify_slovene_word("prst") == ["prst"]


def test_single_syllable_syllabic_r_trg():
    assert syllabify_slovene_word("trg") == ["trg"]


# --- Two-syllable words ---


def test_kavo():
    assert syllabify_slovene_word("kavo") == ["ka", "vo"]


def test_prosim():
    assert syllabify_slovene_word("prosim") == ["pro", "sim"]


def test_dober():
    assert syllabify_slovene_word("dober") == ["do", "ber"]


def test_hvala():
    assert syllabify_slovene_word("hvala") == ["hva", "la"]


def test_vecer():
    assert syllabify_slovene_word("večer") == ["ve", "čer"]


def test_lepo():
    assert syllabify_slovene_word("lepo") == ["le", "po"]


def test_eno():
    assert syllabify_slovene_word("eno") == ["e", "no"]


# --- Three-syllable words ---


def test_koliko():
    assert syllabify_slovene_word("koliko") == ["ko", "li", "ko"]


def test_razumem():
    assert syllabify_slovene_word("razumem") == ["ra", "zu", "mem"]


def test_hvala_with_three():
    # "dobro" -> do-bro ("br" is valid onset)
    assert syllabify_slovene_word("dobro") == ["do", "bro"]


# --- Four-syllable words ---


def test_oprostite():
    assert syllabify_slovene_word("oprostite") == ["o", "pro", "sti", "te"]


def test_slovenscina():
    assert syllabify_slovene_word("slovenščina") == ["slo", "ven", "šči", "na"]


# --- Hiatus (adjacent vowels) ---


def test_hiatus_nauk():
    # n-a-u-k: adjacent vowels a and u split
    assert syllabify_slovene_word("nauk") == ["na", "uk"]


# --- Case insensitivity ---


def test_case_lowercased():
    assert syllabify_slovene_word("Prosim") == ["pro", "sim"]
    assert syllabify_slovene_word("DOBER") == ["do", "ber"]


# --- Onset cluster examples ---


def test_str_cluster():
    # "estra": e-str-a → "str" is valid onset → ["e", "stra"]
    assert syllabify_slovene_word("estra") == ["e", "stra"]


def test_sk_cluster():
    # "laski": l-a-sk-i → "sk" is valid onset → ["la", "ski"]
    assert syllabify_slovene_word("laski") == ["la", "ski"]
