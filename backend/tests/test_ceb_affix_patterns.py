"""Cebuano affix patterns: which affix pairs a root can be drilled in (tunatale-ve4p.2).

A pattern is a small set of affixes taught as a contrast on one root
(``molakaw`` / ``milakaw``). ``pattern_forms`` returns the forms only when
native news uses EVERY one of them, because neither in-repo table can say
which affixes a root really takes: the lemma table lists every root with every
affix (Wiktionary's conjugation templates are generated), and the frequency
table is keyed by root.

Oracles below were measured on 2026-10-07 from the FineWeb-2 ``ceb_Latn``
native-news shard (9.48 M tokens) and are the committed count table's own rows.
"""

from __future__ import annotations

import gzip

import pytest

from app.languages import get_a1_morphology
from app.plugins.languages.ceb.a1_morphology import affix_of
from app.plugins.languages.ceb.affix_patterns import (
    COUNTS_PATH,
    GLOSSES_PATH,
    PATTERN_AFFIXES,
    PATTERNS,
    VOUCH_FLOOR,
    load_affix_counts,
    load_affix_glosses,
    pattern_forms,
    pattern_glosses,
    spell,
)

_BY_KEY = {p.key: p for p in PATTERNS}


def test_the_patterns_are_the_three_aspect_pairs():
    assert {p.key: p.features for p in PATTERNS} == {
        "mo-mi": ("verb:mo", "verb:mi"),
        "mag-nag": ("verb:mag", "verb:nag"),
        "ma-na": ("verb:ma", "verb:na"),
    }
    assert PATTERN_AFFIXES == ("mo", "mi", "mag", "nag", "ma", "na")


# ── spell: how an affix attaches ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("root", "affix", "surface"),
    [
        ("lakaw", "mag", "maglakaw"),
        ("lakaw", "mo", "molakaw"),
        # A consonant-final prefix takes a hyphen before a vowel-initial root...
        ("ampo", "mag", "mag-ampo"),
        ("uban", "nag", "nag-uban"),
        # ...a vowel-final one does not.
        ("adto", "mo", "moadto"),
        ("anhi", "mi", "mianhi"),
        ("inom", "na", "nainom"),
        # A glottal-stop hyphen inside the root is kept.
        ("tan-aw", "mo", "motan-aw"),
    ],
)
def test_spell_attaches_the_prefix(root, affix, surface):
    assert spell(root, affix) == surface


@pytest.mark.parametrize("root", ["lakaw", "ampo", "tan-aw", "adto", "tulog", "gutom", "inom"])
@pytest.mark.parametrize("affix", ["mo", "mi", "mag", "nag", "ma", "na"])
def test_every_spelled_form_is_recognised_back_as_its_own_affix(root, affix):
    """The round trip the drill and the card depend on: a form this module
    writes must be read by ``affix_of`` as the affix it was written with.
    ``gutom`` guards the ma-/mag- boundary (``magutom`` is ma- + gutom)."""
    assert affix_of(spell(root, affix), root) == affix


# ── pattern_forms against the committed table ────────────────────────────────


@pytest.mark.parametrize(
    ("root", "pattern", "forms"),
    [
        ("lakaw", "mo-mi", ("molakaw", "milakaw")),  # 98 / 85
        ("lakaw", "mag-nag", ("maglakaw", "naglakaw")),  # 49 / 254
        ("ampo", "mag-nag", ("mag-ampo", "nag-ampo")),  # 107 / 82
        ("hulat", "mag-nag", ("maghulat", "naghulat")),  # 108 / 213
        ("anhi", "mo-mi", ("moanhi", "mianhi")),  # 101 / 40
        ("tulog", "ma-na", ("matulog", "natulog")),  # 133 / 230
        ("lipay", "ma-na", ("malipay", "nalipay")),  # 204 / 542
        ("tan-aw", "mo-mi", ("motan-aw", "mitan-aw")),  # 169 / 43
        ("Lakaw", "mo-mi", ("molakaw", "milakaw")),  # the root's case is spelling
    ],
)
def test_a_pair_native_news_uses_is_vouched_for(root, pattern, forms):
    assert pattern_forms(root, _BY_KEY[pattern]) == forms


@pytest.mark.parametrize(
    ("root", "pattern"),
    [
        ("tulog", "mo-mi"),  # 0 / 0: sleeping is stative, never mo-
        ("tulog", "mag-nag"),  # 0 / 0
        ("anhi", "mag-nag"),  # 0 / 0
        ("hulat", "ma-na"),  # 0 / 2
        ("ampo", "mo-mi"),  # 11 / 10: attested, under the floor
        ("hilom", "mo-mi"),  # the lesson's mihilom: 3 occurrences
        ("zzzzz", "mo-mi"),  # not a root at all
    ],
)
def test_a_pair_native_news_does_not_use_returns_none(root, pattern):
    assert pattern_forms(root, _BY_KEY[pattern]) is None


def test_one_strong_cell_does_not_carry_a_weak_one():
    """Both cells must clear the floor: a pair with one real form is not a pair."""
    counts = {("dula", "mag"): VOUCH_FLOOR - 1, ("dula", "nag"): 500}
    assert pattern_forms("dula", _BY_KEY["mag-nag"], counts=counts) is None
    counts[("dula", "mag")] = VOUCH_FLOOR
    assert pattern_forms("dula", _BY_KEY["mag-nag"], counts=counts) == ("magdula", "nagdula")


# ── the committed table ──────────────────────────────────────────────────────


def test_every_committed_row_round_trips_through_affix_of():
    """The whole table, not a sample: every (root, affix) it holds must spell a
    form ``affix_of`` reads back as that affix on that root."""
    table = load_affix_counts(COUNTS_PATH)
    assert len(table) > 1000
    wrong = [(root, affix) for root, affix in table if affix_of(spell(root, affix), root) != affix]
    assert wrong == []


def test_the_committed_table_only_holds_pattern_affixes_and_counted_rows():
    table = load_affix_counts(COUNTS_PATH)
    assert {affix for _root, affix in table} == set(PATTERN_AFFIXES)
    assert min(table.values()) >= 5


def test_the_loader_reads_rows_and_skips_headers(tmp_path):
    path = tmp_path / "counts.tsv.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write("#source=test\n\nlakaw\tmag\t49\nlakaw\tnag\t254\n")
    assert load_affix_counts(path) == {("lakaw", "mag"): 49, ("lakaw", "nag"): 254}


def test_a_missing_table_is_an_error_not_an_empty_answer(tmp_path):
    """An absent data file must not read as 'no root takes any affix'."""
    with pytest.raises(FileNotFoundError):
        load_affix_counts(tmp_path / "absent.tsv.gz")


# ── the hand-written English ─────────────────────────────────────────────────


def test_the_english_of_a_form_is_written_down_not_derived():
    """The case that ruled out wording a form from its root's gloss: kita is
    "see", and its mag- pair means meeting (Wolff: "Magkita ra tag usab, We'll
    meet again")."""
    assert pattern_glosses("kita", _BY_KEY["mag-nag"]) == ("see", ("will meet", "met"))
    assert pattern_glosses("Lakaw", _BY_KEY["mo-mi"]) == ("walk", ("will walk", "walked"))


def test_a_pair_nobody_has_worded_has_no_english():
    # Native news uses andam with mag-/nag- (26 / 77), but no row says what it means.
    assert pattern_forms("andam", _BY_KEY["mag-nag"]) == ("mag-andam", "nag-andam")
    assert pattern_glosses("andam", _BY_KEY["mag-nag"]) is None
    # dala's ma- pair is "can be carried / got carried": deliberately not in the table.
    assert pattern_glosses("dala", _BY_KEY["ma-na"]) is None


def test_makita_is_left_out_because_it_is_object_focus():
    """Native news uses makita / nakita thousands of times, and it does mean
    seeing. But the one who sees is in the genitive (Wolff: "Makita ba nimu
    ang ayruplanu?"), so it is not an actor pair like matulog / natulog, and
    drilling it as one would teach "makita ko" ("I will be seen")."""
    assert pattern_forms("kita", _BY_KEY["ma-na"]) == ("makita", "nakita")
    assert pattern_glosses("kita", _BY_KEY["ma-na"]) is None


def test_mosulti_is_speaking_not_telling():
    """Wolff: sulti "tell" is class A1 (no mu-/mi-); mosulti is the A2 sense, "speak"."""
    assert pattern_glosses("sulti", _BY_KEY["mo-mi"]) == ("speak", ("will speak", "spoke"))


def test_every_worded_pair_is_one_native_news_uses():
    """The table cannot word a pair the count table would refuse to drill."""
    table = load_affix_glosses(GLOSSES_PATH)
    assert len(table) >= 20
    unvouched = [(root, key) for root, key in table if pattern_forms(root, _BY_KEY[key]) is None]
    assert unvouched == []


def test_a_row_with_the_wrong_number_of_forms_is_an_error(tmp_path):
    path = tmp_path / "glosses.tsv"
    path.write_text("# header\n\nlakaw\tmag-nag\twalk\twill walk\twalked\nampo\tmag-nag\tpray\twill pray\n")
    with pytest.raises(ValueError, match=r"glosses.tsv:4: ampo mag-nag needs 2 English forms, has 1"):
        load_affix_glosses(path)


def test_a_row_naming_an_unknown_pattern_is_an_error(tmp_path):
    path = tmp_path / "glosses.tsv"
    path.write_text("lakaw\tmag-mi\twalk\twill walk\twalked\n")
    with pytest.raises(ValueError, match=r"glosses.tsv:1: lakaw mag-mi needs None English forms, has 2"):
        load_affix_glosses(path)


# ── reached through the registry ─────────────────────────────────────────────


def test_cebuano_registers_its_patterns_on_the_bundle():
    bundle = get_a1_morphology("ceb")
    assert bundle.patterns == PATTERNS
    assert bundle.pattern_forms("tulog", _BY_KEY["ma-na"]) == ("matulog", "natulog")
    assert bundle.pattern_glosses("tulog", _BY_KEY["ma-na"]) == ("sleep", ("will sleep", "slept"))


def test_a_language_without_patterns_registers_none():
    bundle = get_a1_morphology("no")
    assert bundle.patterns == ()
    assert bundle.pattern_forms is None
    assert bundle.pattern_glosses is None
