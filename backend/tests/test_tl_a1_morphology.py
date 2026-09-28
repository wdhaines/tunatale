"""Tagalog affixed verb forms as inflection clozes on the root card (tunatale-w4m7.17).

User decisions (2026-09-27): the Tagalog match for the Cebuano set — actor -um-
and mag-, object -in, -an and i-, each in completed / progressive / contemplated
aspect. NOT A1: stative/ability (ma- maka-), mang-, causative pa-.

The table lemmatizer carries no features, so the feature is found by generating
the root's A1 forms and looking the surface up. The rows below are the ORACLE:
each (surface, root, feature) is one Wiktionary tags itself — the kaikki
extract's tl-infl-um / -mag / -in / -in-an / -i headword templates, which tag
every verb's completive, progressive and contemplative forms (measured
2026-09-27: 3,409 of those forms reproduced, 0 contradicted once 8 Wiktionary
mislabels were read by hand). The extract itself is local-only, so the rows are
pinned here as literals.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.common.guid import compute_guid
from app.languages import get_a1_morphology, get_lemma_table_path
from app.main import app
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.plugins.languages.tl.a1_morphology import affix_feature
from app.srs.function_words import format_morphology_hint, is_a1_morphology_feature, ud_feats_to_tt_feature
from app.srs.lemma_table import TableLemmatizer
from app.srs.lemmatizer import TokenAnalysis

# ── affix_feature: the root's A1 forms, generated and looked up ───────────────


@pytest.mark.parametrize(
    ("surface", "root", "feature"),
    [
        # -um-: the infinitive and the completed form are the SAME word
        # (kumain), so they share one feature.
        ("kumain", "kain", "um:base"),
        ("gumulo", "gulo", "um:base"),
        ("umalis", "alis", "um:base"),  # vowel-initial root: -um- is a prefix
        ("kumakain", "kain", "um:progressive"),
        ("umuulit", "ulit", "um:progressive"),
        ("bumibilis", "bilis", "um:progressive"),
        ("kakain", "kain", "um:contemplated"),  # bare reduplication
        ("hihirap", "hirap", "um:contemplated"),
        ("durungaw", "dungaw", "um:contemplated"),  # d -> r between vowels
        # mag-
        ("magdala", "dala", "mag:base"),
        ("magtanong", "tanong", "mag:base"),
        ("nagdala", "dala", "mag:completed"),
        ("nagluwas", "luwas", "mag:completed"),
        ("nagdadalaga", "dalaga", "mag:progressive"),
        ("nag-eeroplano", "eroplano", "mag:progressive"),  # hyphen before a vowel
        ("magdadala", "dala", "mag:contemplated"),
        ("magbabalatkayo", "balatkayo", "mag:contemplated"),
        # -in (object)
        ("kainin", "kain", "in:base"),
        ("kantahin", "kanta", "in:base"),  # h before the suffix on a vowel-final root
        ("kinain", "kain", "in:completed"),
        ("inusisa", "usisa", "in:completed"),  # vowel-initial root: -in- is a prefix
        ("dinala", "dala", "in:completed"),
        ("kinakain", "kain", "in:progressive"),
        ("binubulabog", "bulabog", "in:progressive"),
        ("kakainin", "kain", "in:contemplated"),
        ("tatantiyahin", "tantiya", "in:contemplated"),
        # -an (object, to/at)
        ("kapalan", "kapal", "an:base"),
        ("udyukan", "udyok", "an:base"),  # final-syllable o raises to u
        ("inabayan", "abay", "an:completed"),
        ("binayaan", "baya", "an:completed"),
        ("tinatamaan", "tama", "an:progressive"),
        ("inaabisuhan", "abiso", "an:progressive"),
        ("puputulan", "putol", "an:contemplated"),
        ("tatalikuran", "talikod", "an:contemplated"),  # o -> u and d -> r
        # i- (object, with/for)
        ("isauli", "sauli", "i:base"),
        ("ibinigay", "bigay", "i:completed"),
        ("isinuksok", "suksok", "i:completed"),
        ("ibinibili", "bili", "i:progressive"),
        ("ipapasok", "pasok", "i:contemplated"),
        # Case and hyphens are spelling.
        ("Magdadala", "dala", "mag:contemplated"),
        ("mag-aral", "aral", "mag:base"),
    ],
)
def test_affix_feature_matches_the_wiktionary_oracle(surface, root, feature):
    assert affix_feature(surface, root) == feature


@pytest.mark.parametrize(
    ("surface", "root"),
    [
        ("kain", "kain"),  # the bare root carries no affix
        ("matulog", "tulog"),  # ma-: stative, not A1 by decision
        ("makakain", "kain"),  # maka-: ability, not A1
        ("mangisda", "isda"),  # mang-: not A1
        ("pakainin", "kain"),  # causative pa-: not A1
        ("dinggin", "dinig"),  # syncope: no guess rather than a wrong one
        ("kinain", "dala"),  # a different root
        ("prumito", "prito"),  # cluster-initial loan: reduplication undefined, no guess
        ("", "kain"),
        ("kain", ""),
    ],
)
def test_affix_feature_is_none_outside_the_a1_set(surface, root):
    assert affix_feature(surface, root) is None


# ── the bundle, reached through the registry ─────────────────────────────────


def _analysis(surface: str, lemma: str, upos: str = "VERB") -> TokenAnalysis:
    return TokenAnalysis(surface=surface, lemma=lemma, upos=upos)


def test_tagalog_registers_a_bundle():
    assert get_a1_morphology("tl") is not None


@pytest.mark.parametrize(
    ("surface", "lemma", "feature"),
    [
        ("magdadala", "dala", "verb:mag:contemplated"),
        ("kumain", "kain", "verb:um:base"),
        ("ibinigay", "bigay", "verb:i:completed"),
    ],
)
def test_an_a1_form_maps_to_a_whitelisted_feature(surface, lemma, feature):
    got = ud_feats_to_tt_feature(_analysis(surface, lemma), "tl")
    assert got == feature
    assert is_a1_morphology_feature(got, "tl")


def test_only_a_verb_reading_carries_a_feature():
    assert ud_feats_to_tt_feature(_analysis("kinain", "kain", "NOUN"), "tl") is None


def test_a_non_a1_form_has_no_feature():
    assert ud_feats_to_tt_feature(_analysis("matulog", "tulog"), "tl") is None


def test_the_whitelist_holds_exactly_the_decided_affixes():
    assert set(get_a1_morphology("tl").a1_prefixes) == {
        "verb:um:",
        "verb:mag:",
        "verb:in:",
        "verb:an:",
        "verb:i:",
    }


def test_another_languages_feature_does_not_validate():
    assert not is_a1_morphology_feature("verb:mi", "tl")


@pytest.mark.parametrize(
    ("lemma", "feature", "hint"),
    [
        ("kain", "verb:um:base", "kain — -um-: did, or do! (base form)"),
        ("kain", "verb:um:progressive", "kain — -um-: is/was doing"),
        ("kain", "verb:um:contemplated", "kain — -um-: will do"),
        ("dala", "verb:mag:base", "dala — mag-: do! (base form)"),
        ("dala", "verb:mag:completed", "dala — mag-: did"),
        ("kain", "verb:in:completed", "kain — -in: was done (to it)"),
        ("kain", "verb:in:base", "kain — -in: be done (to it) (base form)"),
        ("kain", "verb:an:contemplated", "kain — -an: will be done (to/at it)"),
        ("bigay", "verb:i:progressive", "bigay — i-: is being done (with/for it)"),
    ],
)
def test_the_hint_names_the_affix_and_its_meaning(lemma, feature, hint):
    assert format_morphology_hint(lemma, feature, "tl") == hint


@pytest.mark.parametrize("feature", ["verb:zz:base", "verb:um:zz", "verb:um"])
def test_an_unknown_feature_falls_back_to_the_default_hint(feature):
    assert format_morphology_hint("kain", feature, "tl").startswith("kain")


# ── end to end through the real lemma table ──────────────────────────────────


def test_the_table_lemmatizer_yields_an_inflectable_analysis():
    """Built directly: tests pin settings.lemmatizer_type to "lowercase", and
    production runs the table, which is the path that matters here. The
    sentence is the wake lesson's own (tunatale-w4m7.17)."""
    lemmatizer = TableLemmatizer("tl", get_lemma_table_path("tl"))
    analyses = lemmatizer.analyze_sentence("Magdadala ako ng pagkain.", "tl")
    lemmatizer.close()
    verb = next(a for a in analyses if a.surface == "Magdadala")
    assert (verb.lemma, verb.upos) == ("dala", "VERB")
    assert ud_feats_to_tt_feature(verb, "tl") == "verb:mag:contemplated"


class TestInflectionClozeEndpoint:
    @pytest.fixture(autouse=True)
    def _mock_audio(self, monkeypatch: pytest.MonkeyPatch):
        import app.api.srs as srs_mod

        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", AsyncMock())

    async def test_a_learned_root_gets_an_affix_cloze_with_the_affix_hint(self, api_app_state):
        db = api_app_state
        db.add_collocation(
            SyntacticUnit(text="dala", translation="bring", word_count=1, difficulty=1, source="test"),
            language_code="tl",
        )
        root = db.get_collocation("dala")
        db.update_direction(
            root.guid,
            Direction.PRODUCTION,
            DirectionState(
                direction=Direction.PRODUCTION,
                due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
                stability=5.0,
                difficulty=4.0,
                reps=5,
                state=SRSState.REVIEW,
            ),
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "magdadala",
                    "lemma": "dala",
                    "feature": "verb:mag:contemplated",
                    "sentence": "Magdadala ako ng pagkain.",
                    "language_code": "tl",
                },
            )
        assert resp.status_code == 200
        cloze = db.get_collocation_by_guid(compute_guid("magdadala", "tl", "morph:verb-mag-contemplated"))
        assert cloze.syntactic_unit.card_type == "cloze"
        assert cloze.syntactic_unit.grammar == "dala — mag-: will do"
        assert cloze.syntactic_unit.source_sentence == "{{c1::Magdadala}} ako ng pagkain."
