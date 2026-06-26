"""Tests for notetype field-role profiles (app.anki.field_map)."""

from app.anki.field_map import NotetypeProfile, get_profile


class TestGetProfile:
    def test_norwegian_notetype_has_profile(self):
        profile = get_profile("6000 Most Frequent Norwegian Words")
        assert isinstance(profile, NotetypeProfile)
        assert profile.l2 == "Norwegian word"
        assert profile.translation == "English translation"
        assert profile.disambig == "Word class"  # disambiguates homographs (løfte noun vs verb)

    def test_slovene_vocabulary_has_no_profile(self):
        # Slovene deliberately keeps the heuristics — no profile.
        assert get_profile("Slovene Vocabulary") is None

    def test_unknown_notetype_has_no_profile(self):
        assert get_profile("Some Random Notetype") is None

    def test_empty_name_has_no_profile(self):
        assert get_profile("") is None
