"""Cebuano stage 2: affixed forms as inflection clozes on the root card (tunatale-u8nz.20).

User decisions (2026-09-27): A1 = the core actor affixes (mo- mi- mag- nag-)
and the core object affixes (gi- -on -an gi-...-an i-). NOT A1: stative/ability
(ma- na- maka- naka-) and imperatives (-a -i pag-). Hints name the affix and
its meaning: "lakaw — mi-: did (completed)".

The feature is recovered from (surface, lemma) by subtracting the root, because
Wiktionary's conjugation tags are too sparse to use (11 forms tagged "past").
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
from app.plugins.languages.ceb.a1_morphology import affix_of
from app.srs.function_words import format_morphology_hint, is_a1_morphology_feature, ud_feats_to_tt_feature
from app.srs.lemma_table import TableLemmatizer
from app.srs.lemmatizer import TokenAnalysis

# ── affix_of: the root subtracted from the surface ────────────────────────────


@pytest.mark.parametrize(
    ("surface", "root", "affix"),
    [
        ("molakaw", "lakaw", "mo"),
        ("milakaw", "lakaw", "mi"),
        ("maglakaw", "lakaw", "mag"),
        ("naglakaw", "lakaw", "nag"),
        ("gihatag", "hatag", "gi"),
        ("ihatag", "hatag", "i"),
        ("kaonon", "kaon", "on"),
        ("hatagan", "hatag", "an"),
        ("gihatagan", "hatag", "gi-an"),
        # A vowel-initial root takes a hyphen after mag-/nag-.
        ("mag-abot", "abot", "mag"),
        ("Nag-abot", "abot", "nag"),
        # A glottal-stop hyphen inside the root is not an affix boundary.
        ("mitan-aw", "tan-aw", "mi"),
        # h-insertion before a suffix on a vowel-final root.
        ("basahon", "basa", "on"),
        ("gibasahan", "basa", "gi-an"),
    ],
)
def test_affix_of_recovers_the_a1_affix(surface, root, affix):
    assert affix_of(surface, root) == affix


@pytest.mark.parametrize(
    ("surface", "root"),
    [
        ("lakaw", "lakaw"),  # no affix
        ("matulog", "tulog"),  # ma-: stative, not A1 by decision
        ("nakita", "kita"),  # na-: not A1
        ("makakaon", "kaon"),  # maka-: not A1
        ("kuhaa", "kuha"),  # -a imperative: not A1
        ("paglakaw", "lakaw"),  # pag-: not A1
        ("imnon", "inom"),  # syncope: the root is not a substring; no guess
        ("gilakaw", "hatag"),  # a different root
    ],
)
def test_affix_of_is_none_outside_the_a1_set(surface, root):
    assert affix_of(surface, root) is None


# ── the bundle, reached through the registry ─────────────────────────────────


def _analysis(surface: str, lemma: str, upos: str = "VERB") -> TokenAnalysis:
    return TokenAnalysis(surface=surface, lemma=lemma, upos=upos)


def test_cebuano_registers_a_bundle():
    assert get_a1_morphology("ceb") is not None


@pytest.mark.parametrize(
    ("surface", "lemma", "feature"),
    [("milakaw", "lakaw", "verb:mi"), ("kaonon", "kaon", "verb:on"), ("gihatagan", "hatag", "verb:gi-an")],
)
def test_an_a1_form_maps_to_a_whitelisted_feature(surface, lemma, feature):
    got = ud_feats_to_tt_feature(_analysis(surface, lemma), "ceb")
    assert got == feature
    assert is_a1_morphology_feature(got, "ceb")


def test_only_a_verb_reading_carries_a_feature():
    """``ihatag`` read as a noun would be a coincidence of spelling, not an affix."""
    assert ud_feats_to_tt_feature(_analysis("ihatag", "hatag", "NOUN"), "ceb") is None


def test_a_non_a1_form_has_no_feature():
    assert ud_feats_to_tt_feature(_analysis("matulog", "tulog"), "ceb") is None


def test_the_whitelist_holds_exactly_the_decided_affixes():
    bundle = get_a1_morphology("ceb")
    assert set(bundle.a1_prefixes) == {
        "verb:mo",
        "verb:mi",
        "verb:mag",
        "verb:nag",
        "verb:gi",
        "verb:on",
        "verb:an",
        "verb:gi-an",
        "verb:i",
    }


@pytest.mark.parametrize(
    ("lemma", "feature", "hint"),
    [
        ("lakaw", "verb:mi", "lakaw — mi-: did (completed)"),
        ("kaon", "verb:on", "kaon — -on: will be done (to it)"),
        ("hatag", "verb:gi", "hatag — gi-: was done (to it)"),
        ("hatag", "verb:gi-an", "hatag — gi-…-an: was done to/at"),
    ],
)
def test_the_hint_names_the_affix_and_its_meaning(lemma, feature, hint):
    assert format_morphology_hint(lemma, feature, "ceb") == hint


def test_an_unknown_feature_falls_back_to_the_default_hint():
    assert format_morphology_hint("lakaw", "verb:zz", "ceb").startswith("lakaw")


# ── end to end through the real lemma table ──────────────────────────────────


def test_the_table_lemmatizer_yields_an_inflectable_analysis():
    """Built directly: tests pin settings.lemmatizer_type to "lowercase", and
    production runs the table, which is the path that matters here."""
    lemmatizer = TableLemmatizer("ceb", get_lemma_table_path("ceb"))
    analyses = lemmatizer.analyze_sentence("Milakaw siya.", "ceb")
    lemmatizer.close()
    milakaw = next(a for a in analyses if a.surface == "Milakaw")
    assert (milakaw.lemma, milakaw.upos) == ("lakaw", "VERB")
    assert ud_feats_to_tt_feature(milakaw, "ceb") == "verb:mi"


class TestInflectionClozeEndpoint:
    @pytest.fixture(autouse=True)
    def _mock_audio(self, monkeypatch: pytest.MonkeyPatch):
        import app.api.srs as srs_mod

        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", AsyncMock())

    async def test_a_learned_root_gets_an_affix_cloze_with_the_affix_hint(self, api_app_state):
        db = api_app_state
        db.add_collocation(
            SyntacticUnit(text="lakaw", translation="walk", word_count=1, difficulty=1, source="test"),
            language_code="ceb",
        )
        root = db.get_collocation("lakaw")
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
                    "surface": "milakaw",
                    "lemma": "lakaw",
                    "feature": "verb:mi",
                    "sentence": "Milakaw siya sa eskwelahan.",
                    "language_code": "ceb",
                },
            )
        assert resp.status_code == 200
        cloze = db.get_collocation_by_guid(compute_guid("milakaw", "ceb", "morph:verb-mi"))
        assert cloze.syntactic_unit.card_type == "cloze"
        assert cloze.syntactic_unit.grammar == "lakaw — mi-: did (completed)"
        # A prefix form shares no leading letters with its root, so the whole
        # word is the blank; the hint carries the grammar.
        assert cloze.syntactic_unit.source_sentence == "{{c1::Milakaw}} siya sa eskwelahan."
