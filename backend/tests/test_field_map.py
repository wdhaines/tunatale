"""Tests for notetype field-role profiles (app.cards.field_map)."""

import pytest

from app.cards.field_map import NotetypeProfile, direction_for_ord, get_profile
from app.models.srs_item import Direction


class TestGetProfile:
    def test_norwegian_notetype_has_profile(self):
        profile = get_profile("6000 Most Frequent Norwegian Words", None)
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
        assert get_profile("Slovene Vocabulary", None) is None

    def test_unknown_notetype_has_no_profile(self):
        assert get_profile("Some Random Notetype", None) is None

    def test_empty_name_has_no_profile(self):
        assert get_profile("", None) is None


class TestInflectionLabels:
    """The extras labels under which a notetype's inflection table is stored.

    Derived from the profiles rather than hardcoded, so a second deck that
    names its table something else is picked up by declaring a profile —
    the reader never grows a per-deck literal.
    """

    def test_derives_the_label_from_the_profile_that_declares_an_inflections_role(self):
        from app.cards.field_map import inflection_labels

        assert "Inflections" in inflection_labels()

    def test_excludes_labels_of_fields_that_are_not_the_inflections_role(self):
        from app.cards.field_map import inflection_labels

        labels = inflection_labels()
        # "Comparison" (Gradbøying) is a table too, and its <tbody> carries the
        # grammar labels komparativ/superlativ as cells — reading it would put
        # them in the form index.
        assert "Comparison" not in labels
        assert "Examples" not in labels
        assert "IPA" not in labels


class TestLanguageScopedProfiles:
    """A profile can belong to one language (tunatale-w4m7.8).

    The Pimsleur notetype's name, ``Basic (and reversed card) (genanki)``, is
    what any genanki export is called, so a global profile under that name
    would capture another language's deck. Tagalog registers it for itself.
    """

    PIMSLEUR = "Basic (and reversed card) (genanki)"

    def test_tagalog_reads_the_pimsleur_notetype_back_side_as_the_l2(self):
        profile = get_profile(self.PIMSLEUR, "tl")
        assert profile is not None
        assert (profile.l2, profile.translation) == ("Back", "Front")

    def test_pimsleur_card_2_is_the_recognition_card(self):
        # Card 1 (ord 0) is {{Front}} -> {{Back}}{{Audio}}: English -> Tagalog.
        assert get_profile(self.PIMSLEUR, "tl").recognition_ord == 1

    @pytest.mark.parametrize("code", ["sl", "no", None])
    def test_no_other_language_sees_the_pimsleur_profile(self, code):
        assert get_profile(self.PIMSLEUR, code) is None

    def test_a_global_profile_is_still_found_under_a_language(self):
        assert get_profile("6000 Most Frequent Norwegian Words", "no") is not None

    def test_the_default_recognition_card_is_ord_0(self):
        assert get_profile("6000 Most Frequent Norwegian Words", "no").recognition_ord == 0


class TestDirectionForOrd:
    PIMSLEUR = "Basic (and reversed card) (genanki)"

    @pytest.mark.parametrize("name", ["Slovene Vocabulary", "6000 Most Frequent Norwegian Words", ""])
    def test_without_a_profile_override_ord_0_is_recognition(self, name):
        assert direction_for_ord(name, 0, "sl") == Direction.RECOGNITION
        assert direction_for_ord(name, 1, "sl") == Direction.PRODUCTION

    def test_the_pimsleur_notetype_is_reversed_for_tagalog(self):
        assert direction_for_ord(self.PIMSLEUR, 0, "tl") == Direction.PRODUCTION
        assert direction_for_ord(self.PIMSLEUR, 1, "tl") == Direction.RECOGNITION

    def test_the_pimsleur_notetype_is_not_reversed_for_slovene(self):
        assert direction_for_ord(self.PIMSLEUR, 0, "sl") == Direction.RECOGNITION


class TestProfilesAcrossLanguages:
    def test_derived_label_sets_see_language_scoped_profiles_too(self, monkeypatch):
        # inflection_labels / upos_for_disambig search EVERY profile; a
        # language-scoped one must not be invisible to them.
        from app.cards.field_map import BackFieldSpec, inflection_labels, upos_for_disambig
        from app.languages import _CONFIGS

        scoped = NotetypeProfile(
            l2="W",
            translation="E",
            back_fields=(BackFieldSpec("Tbl", "Scoped table"),),
            inflections="Tbl",
            disambig_upos={"pangngalan": "NOUN"},
        )
        monkeypatch.setattr(_CONFIGS["sl"], "notetype_profiles", {"Scoped Notetype": scoped})
        assert "Scoped table" in inflection_labels()
        assert upos_for_disambig("pangngalan") == "NOUN"
