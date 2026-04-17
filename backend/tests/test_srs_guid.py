"""Tests for deterministic GUID computation."""

from app.common.guid import compute_guid


class TestComputeGuid:
    def test_deterministic(self):
        assert compute_guid("banka", "sl") == compute_guid("banka", "sl")

    def test_is_16_hex_chars(self):
        guid = compute_guid("banka", "sl")
        assert len(guid) == 16
        assert all(c in "0123456789abcdef" for c in guid)

    def test_nfc_stability_combined_vs_precomposed(self):
        combined = "c\u030c"  # c + combining caron
        precomposed = "\u010d"  # č
        assert compute_guid(combined, "sl") == compute_guid(precomposed, "sl")

    def test_different_texts_different_guids(self):
        assert compute_guid("banka", "sl") != compute_guid("hi\u0161a", "sl")

    def test_different_languages_different_guids(self):
        assert compute_guid("banka", "sl") != compute_guid("banka", "de")

    def test_casefold(self):
        assert compute_guid("Banka", "sl") == compute_guid("banka", "sl")

    def test_nfc_applied_before_casefold(self):
        # German ß casefolds to ss; NFC should not affect the casefold result
        assert compute_guid("Straße", "de") == compute_guid("straße", "de")
