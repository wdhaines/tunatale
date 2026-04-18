"""GUID determinism tests for the Anki import path."""

from app.common.guid import compute_guid


def test_guid_deterministic_across_calls():
    assert compute_guid("banka", "sl") == compute_guid("banka", "sl")


def test_guid_nfc_stability():
    combined = "c\u030c"  # c + combining caron
    precomposed = "\u010d"  # č (NFC)
    assert compute_guid(combined, "sl") == compute_guid(precomposed, "sl")


def test_guid_locale_insensitive_via_casefold():
    """casefold() is applied before hashing; locale doesn't matter."""
    assert compute_guid("Banka", "sl") == compute_guid("banka", "sl")


def test_guid_different_language_different_hash():
    assert compute_guid("banka", "sl") != compute_guid("banka", "de")
