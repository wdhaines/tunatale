"""Oracle for tunatale-w4m7.6: the Tagalog lemma table and verb card fronts.

LOCKED TESTS — orchestrator-authored 2026-09-23. An executor implements against
them and never edits them; a literal that looks wrong is a finding to report.

Two oracles, both written from knowledge of the language BEFORE comparing with
any data:

* The lemma golden table (verbs keyed by ROOT, self-headword precedence, the
  ``-ng`` linker) comes from the w4m7.5 spike:
  ``.beads-tasks/briefs/design-philippine-morphology-2026-09.md`` § Golden table.
  Measured against the Wiktionary root table there: 51/53 verbs agreed, and
  both misses (``ibinigay``/``binigay``) are the shorter-root rule.
* The verb DISPLAY table: the user chose the actor-focus infinitive as a verb
  card's front (2026-09-23). Measured 2026-09-23 against the rule in
  ``brief-w4m7.6-tagalog-lemma-table.md``: 40/41, and the one miss, ``kita``, is
  a homograph root (``makita`` "see" vs ``kumita`` "earn"), which is what the
  override list is for. The same list scored 27/40 under the rule the brief
  rejects (most frequent Wiktionary actor headword), so it discriminates.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.languages import format_vocab_headword, get_lemma_table_path
from app.srs.lemma_table import TableLemmatizer
from app.srs.lemmatizer import get_lemmatizer

# ── lemma golden table (w4m7.5) ───────────────────────────────────────────────

VERBS_BY_ROOT = {
    "kain": ["kumain", "kumakain", "kakain", "kinain", "kinakain", "kakainin", "kainin"],
    "bili": ["bumili", "bumibili", "bibili", "binili", "bilhin"],
    "punta": ["pumunta", "pupunta", "pumupunta", "nagpunta"],
    "sabi": ["sinabi", "sabihin", "sinasabi", "magsabi", "nagsabi"],
    "hanap": ["hinahanap", "hanapin", "hinanap", "naghanap"],
    "trabaho": ["nagtrabaho", "nagtatrabaho", "magtatrabaho"],
    "uwi": ["umuwi", "umuuwi", "uuwi"],
    "inom": ["uminom", "iinom", "ininom"],
    "tulog": ["natulog", "natutulog", "matutulog", "matulog"],
    "kita": ["nakita", "nakikita", "makikita", "makita"],
    "intindi": ["naintindihan", "nakakaintindi", "intindihin"],
    "bigay": ["ibinigay", "ibibigay", "binigay"],
    "sulat": ["sumulat", "sinulat", "isusulat"],
    "laro": ["naglalaro", "maglaro"],
}
VERB_CASES = [(surface, root) for root, surfaces in VERBS_BY_ROOT.items() for surface in surfaces]

# A conjugation table lists forms that COINCIDE with common words (ako → akuin,
# wala → mawala, bukas → buksan). A surface that is itself a non-verb headword
# keeps that reading as its default.
SELF_HEADWORDS = ["ako", "siya", "bahay", "kaibigan", "bukas", "wala", "maganda", "marami", "mga", "ang"]

# Order-sensitive: `naming` and `noong` must NOT become `nami` / `noo`
# ("forehead"), which are headwords too.
LINKER = {
    "akong": "ako",
    "maraming": "marami",
    "dalawang": "dalawa",
    "magandang": "maganda",
    "mong": "mo",
    "walang": "wala",
    "naming": "namin",
    "noong": "noon",
    "kaunting": "kaunti",
}

# ── verb display table (card fronts) ──────────────────────────────────────────

DISPLAY = {
    "kain": "kumain",
    "bili": "bumili",
    "punta": "pumunta",
    "sabi": "magsabi",
    "hanap": "maghanap",
    "trabaho": "magtrabaho",
    "uwi": "umuwi",
    "inom": "uminom",
    "tulog": "matulog",
    "kita": "makita",
    "bigay": "magbigay",
    "sulat": "sumulat",
    "laro": "maglaro",
    "luto": "magluto",
    "basa": "magbasa",
    "aral": "mag-aral",
    "lakad": "maglakad",
    "alis": "umalis",
    "dating": "dumating",
    "balik": "bumalik",
    "tawag": "tumawag",
    "gawa": "gumawa",
    "bayad": "magbayad",
    "tanong": "magtanong",
    "sakay": "sumakay",
    "baba": "bumaba",
    "pasok": "pumasok",
    "upo": "umupo",
    "kanta": "kumanta",
    "sayaw": "sumayaw",
    "linis": "maglinis",
    "tira": "tumira",
    "hintay": "maghintay",
    "bukas": "magbukas",
    "sara": "magsara",
    "dala": "magdala",
    "kuha": "kumuha",
    "lipat": "lumipat",
    "tulong": "tumulong",
    "gising": "gumising",
    "ligo": "maligo",
}

_TL_DATA = Path(__file__).resolve().parents[1] / "app/plugins/languages/tl/data"


@pytest.fixture(scope="module")
def real():
    # No skipif: a missing committed table is a failure, not a skip.
    path = get_lemma_table_path("tl")
    assert path is not None and path.exists(), f"tl lemma table missing: {path}"
    lemmatizer = TableLemmatizer("tl", path)
    yield lemmatizer
    lemmatizer.close()


@pytest.mark.parametrize("surface,root", VERB_CASES)
def test_verb_forms_lemmatize_to_their_root(real, surface, root):
    assert real.lemmatize(surface, "tl") == root


@pytest.mark.parametrize("word", SELF_HEADWORDS)
def test_a_non_verb_headword_is_its_own_lemma(real, word):
    assert real.lemmatize(word, "tl") == word


@pytest.mark.parametrize("surface,base", LINKER.items())
def test_the_ng_linker_strips_to_the_headword(real, surface, base):
    assert real.lemmatize(surface, "tl") == base


@pytest.mark.parametrize("root,infinitive", DISPLAY.items())
def test_a_verb_card_front_is_the_actor_focus_infinitive(root, infinitive):
    assert format_vocab_headword(root, "VERB", "tl") == infinitive


def test_a_non_verb_front_is_the_bare_lemma():
    assert format_vocab_headword("bahay", "NOUN", "tl") == "bahay"


def test_a_root_with_no_attested_infinitive_is_shown_as_is():
    # Cannot tell → no guess, the registry's standing convention.
    assert format_vocab_headword("zzqx", "VERB", "tl") == "zzqx"


def test_norwegian_verb_fronts_are_unchanged():
    assert format_vocab_headword("lyve", "VERB", "no") == "å lyve"


class TestFactory:
    @pytest.fixture(autouse=True)
    def _fresh_factory(self):
        get_lemmatizer.cache_clear()
        yield
        get_lemmatizer.cache_clear()

    # The id is not "stanza": conftest skips any test whose keywords contain the
    # word unless --run-stanza is given, and this test needs no model at all.
    @pytest.mark.parametrize("setting", [pytest.param("table", id="prod"), pytest.param("stanza", id="laptop")])
    def test_tagalog_is_served_by_its_table(self, monkeypatch, setting):
        # "table" is production; any other opt-in value is the laptop. Tagalog
        # has no sentence model, so its table IS its engine in both.
        monkeypatch.setattr(settings, "lemmatizer_type", setting)
        lem = get_lemmatizer("tl")
        try:
            assert isinstance(lem, TableLemmatizer)
        finally:
            lem.close()


def test_the_wiktionary_derived_data_carries_attribution():
    text = (_TL_DATA / "ATTRIBUTION.md").read_text(encoding="utf-8")
    assert "Wiktionary" in text
    assert "CC BY-SA 4.0" in text
