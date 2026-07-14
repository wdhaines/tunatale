"""Tests for notetype field-role profiles (app.cards.field_map)."""

from app.cards.field_map import NotetypeProfile, get_profile


class TestGetProfile:
    def test_norwegian_notetype_has_profile(self):
        profile = get_profile("6000 Most Frequent Norwegian Words")
        assert isinstance(profile, NotetypeProfile)
        assert profile.l2 == "Norwegian word"
        assert profile.translation == "English translation"
        assert profile.disambig == "Word class"  # disambiguates homographs (løfte noun vs verb)
        assert profile.article == "Article"  # gender article (en/ei/et) prefix for nouns
        # Rich back-of-card fields: ordered, the big dictionary entry tiered "deep".
        triples = [(b.field_name, b.label, b.tier) for b in profile.back_fields]
        assert ("IPA", "IPA", "summary") in triples
        assert ("Inflections", "Inflections", "details") in triples
        assert ("Dictionary entry", "Dictionary entry", "deep") in triples
        # Audio/frequency fields are intentionally NOT surfaced as text back fields.
        field_names = {b.field_name for b in profile.back_fields}
        assert "Audio, word (Forvo)" not in field_names
        assert "Frequency index" not in field_names

    def test_slovene_vocabulary_has_no_profile(self):
        # Slovene deliberately keeps the heuristics — no profile.
        assert get_profile("Slovene Vocabulary") is None

    def test_unknown_notetype_has_no_profile(self):
        assert get_profile("Some Random Notetype") is None

    def test_empty_name_has_no_profile(self):
        assert get_profile("") is None
