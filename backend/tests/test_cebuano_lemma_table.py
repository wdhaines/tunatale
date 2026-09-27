"""Oracle for tunatale-u8nz.5: the Cebuano lemma table (stage 1, ROOTS).

LOCKED TESTS — orchestrator-authored 2026-09-26. An executor implements against
them and never edits them; a literal that looks wrong is a finding to report.

The user's design (2026-09-26): learn ROOTS first, then layer affixed forms on
top as inflection clozes (stage 2, not this file). So every affixed verb form
lemmatizes to its root, and a word that is its own dictionary headword keeps
itself. Measurements behind every literal:
``.beads-tasks/briefs/findings-u8nz5-cebuano-lemma-2026-09-26.md``.

Four families, each measured against ``scripts/local/kaikki/Cebuano.jsonl``
(sha256 60b7806d…, the copy the w4m7.5 spike fetched):

* LESSON forms: affixed forms that occur in the two live Cebuano lessons and
  whose root has a card in the deck. Wiktionary attests none of them in a
  conjugation table, so the builder must GENERATE them from the root.
* ATTESTED forms: a sample of the 716 surface→root pairs in Wiktionary's own
  ``ceb-infl-*`` tables, chosen to cover each rule the generator would get
  wrong (mang- assimilation, syncope, h-insertion, circumfixes).
* SELF headwords: nouns/adjectives that LOOK affixed and must stay themselves,
  mostly from ``ceb/data/base625.tsv``.
* The ``-ng`` / ``-g`` linker.

Added the same day, after measuring the live decks: words from the plugin's own
625 list that Wiktionary lacks, which must also stay themselves.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.languages import get_lemma_table_path
from app.srs.lemma_table import TableLemmatizer
from app.srs.lemmatizer import get_lemmatizer

# ── lesson forms: generated from the root (no Wiktionary table lists them) ────

LESSON_FORMS = {
    "naglakaw": "lakaw",
    "mangaon": "kaon",  # also attested; mang- + k-initial root drops the k
    "miinom": "inom",
    "moadto": "adto",
    "mitan-aw": "tan-aw",
    "motan-aw": "tan-aw",
    "mag-ampo": "ampo",  # mag- takes a hyphen before a vowel-initial root
    "nag-ampo": "ampo",
    "nagdula": "dula",
    "mianhi": "anhi",
    "pag-anhi": "anhi",
    "mobalik": "balik",
    "mouli": "uli",
    "mouban": "uban",
    "mag-uban": "uban",
    "magkita": "kita",
    "nakahibalo": "hibalo",
    "ilubong": "lubong",
    # Roots Wiktionary lists ONLY as adjectives: generation must cover ADJ
    # roots, not just VERB ones (tulog is adj-only, hilom adj/noun).
    "matulog": "tulog",
    "mihilom": "hilom",
}

# ── attested forms: Wiktionary's own conjugation tables ───────────────────────

ATTESTED_FORMS = {
    "mokaon": "kaon",
    "mikaon": "kaon",
    "nikaon": "kaon",
    "gihulat": "hulat",
    "hulaton": "hulat",
    "maghulat": "hulat",
    "naghulat": "hulat",
    "susiha": "susi",
    "misusi": "susi",
    "gibali": "bali",
    "balia": "bali",
    "manag-om": "dag-om",  # mang- + d → n
    "panag-om": "dag-om",
    "mamatayan": "batayan",  # mang- + b → m
    "mangitlog": "itlog",  # mang- + vowel keeps ng
    "tabak-on": "tabako",  # syncope before -on
    "matibuk-an": "tibuok",
    "gikan-an": "kaon",
    "kan-an": "kaon",
    "insultohon": "insulto",  # h-insertion after a vowel-final root
    "nakapasaylo": "pasaylo",
    "pasayloa": "pasaylo",
    "makaala": "ala",
    "idagan": "dagan",
    "modagan": "dagan",
    "nagdagan": "dagan",
    "madaot": "daot",
    "nalipay": "lipay",
}

VERB_CASES = list(LESSON_FORMS.items()) + list(ATTESTED_FORMS.items())

# ── self headwords: affix-shaped, and their own lemma ─────────────────────────

SELF_HEADWORDS = [
    "maayo",  # adj "good", not ma- + ayo
    "pagkaon",  # noun "food" — ALSO the attested pag- imperative of kaon (below)
    "magtutudlo",  # "teacher" (noun + verb headword), not tudlo
    "magdudula",  # "player", not dula
    "magsusulat",  # "writer", not sulat
    "makina",
    "kabayo",
    "masuso",
    "nawong",
    "mata",
    "napulo",
    "manok",
    "ilong",  # "nose": a final -ng is not a linker when the word is a headword
    "ako",
    "sila",
]

# Words in the plugin's own 625 list (ceb/data/base625.tsv) that Wiktionary does
# NOT have. Every one is a card in both learners' decks, and the generator would
# otherwise take each for an affixed form of a DIFFERENT card: orasan "clock" →
# oras "hour", kainit "heat" → init "hot", pangita "search" → kita "see".
# Measured 2026-09-26 against both live Cebuano DBs.
BASE_LIST_SELF = ["orasan", "kainit", "pangita"]

# ── the linker: -ng after a vowel, -g after n ─────────────────────────────────

LINKER = {
    "akong": "ako",
    "imong": "imo",
    "inyong": "inyo",
    "maayong": "maayo",
    "silang": "sila",
    "daghang": "daghan",  # n + g, not + ng
    # `karo` (a procession carriage) is ALSO a headword, so karo+ng competes;
    # the n+g source wins, as in Tagalog (naming → namin, never nami).
    "karong": "karon",
}

_CEB_DATA = Path(__file__).resolve().parents[1] / "app/plugins/languages/ceb/data"


@pytest.fixture(scope="module")
def real():
    # No skipif: a missing committed table is a failure, not a skip.
    path = get_lemma_table_path("ceb")
    assert path is not None and path.exists(), f"ceb lemma table missing: {path}"
    lemmatizer = TableLemmatizer("ceb", path)
    yield lemmatizer
    lemmatizer.close()


@pytest.mark.parametrize("surface,root", VERB_CASES)
def test_affixed_forms_lemmatize_to_their_root(real, surface, root):
    assert real.lemmatize(surface, "ceb") == root


@pytest.mark.parametrize("word", SELF_HEADWORDS)
def test_an_affix_shaped_headword_is_its_own_lemma(real, word):
    assert real.lemmatize(word, "ceb") == word


@pytest.mark.parametrize("word", BASE_LIST_SELF)
def test_a_625_list_word_wiktionary_lacks_is_its_own_lemma(real, word):
    assert real.lemmatize(word, "ceb") == word


@pytest.mark.parametrize("surface,base", LINKER.items())
def test_the_linker_strips_to_the_headword(real, surface, base):
    assert real.lemmatize(surface, "ceb") == base


def test_a_final_g_after_a_vowel_is_not_a_linker(real):
    # The linker after a vowel is -ng, so `tulog` is never `tulo` + g.
    assert real.lemmatize("tulog", "ceb") == "tulog"


# ── homographs: kept apart by part of speech, never collapsed ─────────────────


def _default(readings):
    defaults = [r for r in readings if r.is_default]
    assert len(defaults) == 1, readings
    return defaults[0]


def test_kita_keeps_a_pronoun_and_a_verb_reading(real):
    # The user's call (2026-09-26): kita is two cards, "we" and "see/meet",
    # told apart by part of speech. Bare `kita` defaults to the pronoun.
    readings = real.readings("kita")
    assert {r.upos for r in readings} >= {"PRON", "VERB"}
    assert all(r.lemma == "kita" for r in readings if r.upos in {"PRON", "VERB"})
    assert _default(readings).upos == "PRON"


def test_an_affixed_kita_is_the_verb_reading(real):
    reading = _default(real.readings("magkita"))
    assert (reading.upos, reading.lemma) == ("VERB", "kita")


def test_pagkaon_defaults_to_food_but_keeps_its_verb_reading(real):
    readings = real.readings("pagkaon")
    assert (_default(readings).upos, _default(readings).lemma) == ("NOUN", "pagkaon")
    assert any(r.upos == "VERB" and r.lemma == "kaon" and not r.is_default for r in readings)


def test_affixed_forms_are_generated_for_verb_and_adjective_roots_only(real):
    # balay "house" is a noun and nothing else. Generating mo-/mag- forms from
    # noun roots would flood the table with non-words that can collide with
    # real ones; a noun root gets no generated forms.
    assert real.readings("mobalay") == []
    assert real.readings("nagbalay") == []


class TestFactory:
    @pytest.fixture(autouse=True)
    def _fresh_factory(self):
        get_lemmatizer.cache_clear()
        yield
        get_lemmatizer.cache_clear()

    # The id is not "stanza": conftest skips any test whose keywords contain the
    # word unless --run-stanza is given, and this test needs no model at all.
    @pytest.mark.parametrize("setting", [pytest.param("table", id="prod"), pytest.param("stanza", id="laptop")])
    def test_cebuano_is_served_by_its_table(self, monkeypatch, setting):
        # Cebuano has no sentence model, so its table IS its engine everywhere.
        monkeypatch.setattr(settings, "lemmatizer_type", setting)
        lem = get_lemmatizer("ceb")
        try:
            assert isinstance(lem, TableLemmatizer)
        finally:
            lem.close()


def test_the_wiktionary_derived_data_carries_attribution():
    text = (_CEB_DATA / "ATTRIBUTION.md").read_text(encoding="utf-8")
    assert "Wiktionary" in text
    assert "CC BY-SA 4.0" in text
