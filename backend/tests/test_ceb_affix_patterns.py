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
    pattern_lines,
    spell,
)

_BY_KEY = {p.key: p for p in PATTERNS}


def test_the_patterns_are_the_teaching_order():
    """The owner's order (2026-10-14, tunatale-ve4p.26): volitional, durative,
    the states as review, then the potential (stage 5, ve4p.31)."""
    assert [(p.key, p.features) for p in PATTERNS] == [
        ("mo-mi", ("verb:mo", "verb:mi")),
        ("mag-nag", ("verb:mag", "verb:nag")),
        ("ma-na", ("verb:ma", "verb:na")),
        ("maka-naka", ("verb:maka", "verb:naka")),
        ("on-gi", ("verb:on", "verb:gi")),
    ]
    assert PATTERN_AFFIXES == ("mo", "mi", "mag", "nag", "ma", "na", "maka", "naka")
    assert [p.key for p in PATTERNS if p.review_only] == ["ma-na"]


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
        # maka- / naka-, measured 2026-10-14 on the same shard.
        ("kita", "maka-naka", ("makakita", "nakakita")),  # 457 / 783
        ("sulod", "maka-naka", ("makasulod", "nakasulod")),  # 581 / 246
        ("gawas", "maka-naka", ("makagawas", "nakagawas")),  # 345 / 381
        ("inom", "maka-naka", ("makainom", "nakainom")),  # 32 / 134
        ("adto", "maka-naka", ("makaadto", "nakaadto")),  # 44 / 23
        ("dala", "maka-naka", ("makadala", "nakadala")),  # 49 / 23
        ("bayad", "maka-naka", ("makabayad", "nakabayad")),  # 123 / 60
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
        ("lakaw", "maka-naka"),  # 40 / 10
        ("anhi", "maka-naka"),  # 7 / 5
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


# maka- / naka- is drilled as a pattern but is not an A1 affix: a lesson's
# makakita gets no grammar hint and no Inflect card (the owner, 2026-09-27).
_POTENTIAL = {"maka", "naka"}


def test_every_committed_row_round_trips_through_affix_of():
    """The whole table, not a sample: every (root, affix) it holds of an A1
    affix must spell a form ``affix_of`` reads back as that affix on that root."""
    table = load_affix_counts(COUNTS_PATH)
    assert len(table) > 1000
    wrong = [
        (root, affix)
        for root, affix in table
        if affix not in _POTENTIAL and affix_of(spell(root, affix), root) != affix
    ]
    assert wrong == []


def test_a_potential_form_is_drilled_but_not_read_as_an_a1_affix():
    table = load_affix_counts(COUNTS_PATH)
    potential = [(root, affix) for root, affix in table if affix in _POTENTIAL]
    assert len(potential) > 100
    assert [(root, affix) for root, affix in potential if affix_of(spell(root, affix), root) is not None] == []


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


# ── aspect, not tense (tunatale-ve4p.25) ─────────────────────────────────────
#
# The owner, 2026-10-13: "some of these prefixes are more about aspect than
# tense. Would be amazing to get that across somehow." Wolff's course calls the
# two forms REAL and UNREAL (Beginning Cebuano, lesson 4B): real is what has
# begun or is done, and his own example of a nag- form is "it is raining". The
# table worded every real form as an English past, so the drill asked for
# naglakaw as "walked" while the lesson it closed said "Paul is walking".

# Mutual action (Wolff's class C) takes mag- / nag- as its only pair, so there
# nag- is also the done act: nagkita is "met".
_MUTUAL = {"kita", "uban"}


def test_a_nag_form_is_worded_as_going_on_not_as_a_past():
    table = load_affix_glosses(GLOSSES_PATH)
    real = {root: forms[1] for (root, key), (_, forms) in table.items() if key == "mag-nag" and root not in _MUTUAL}

    assert len(real) >= 5
    assert [(root, english) for root, english in real.items() if not english.startswith("is ")] == []
    assert real["lakaw"] == "is walking"
    assert {table[(root, "mag-nag")][1][1] for root in _MUTUAL} == {"met", "went together"}


def test_a_mag_form_is_worded_as_something_that_will_go_on():
    """mo- and mag- are both unreal, so both are "will". What tells them apart
    in English is the durative: Wolff's mag- is an action that lasts. The owner
    approved "will be walking" on 2026-10-14, when maglakaw and molakaw were
    both "will walk" and a card could not say which it wanted."""
    table = load_affix_glosses(GLOSSES_PATH)
    unreal = {root: forms[0] for (root, key), (_, forms) in table.items() if key == "mag-nag" and root not in _MUTUAL}

    assert len(unreal) >= 5
    assert [(root, english) for root, english in unreal.items() if not english.startswith("will be ")] == []
    assert unreal["lakaw"] == "will be walking"


def test_no_root_words_two_of_its_forms_the_same():
    """A contrast card asks for a form by its English. lakaw in mo- / mi- and
    in mag- / nag- is four forms, and each needs English of its own."""
    table = load_affix_glosses(GLOSSES_PATH)
    worded: dict[str, list[str]] = {}
    for (root, _), (_, forms) in table.items():
        worded.setdefault(root, []).extend(forms)

    assert [(root, forms) for root, forms in worded.items() if len(set(forms)) < len(forms)] == []


def test_a_maka_form_is_can_and_a_naka_form_is_managed_to():
    """Wolff, maka-: {1} what one can do, {2} what one had the chance or
    managed to do ("Nakapalit mig barátung kutsi, We managed to buy a car
    cheap"). kità takes only the potential in the active (class A123S), so
    nakakita is the plain "saw". Approved by the owner, 2026-10-14."""
    table = load_affix_glosses(GLOSSES_PATH)
    worded = {root: (english, forms) for (root, key), (english, forms) in table.items() if key == "maka-naka"}

    assert worded == {
        "kita": ("see", ("can see", "saw")),
        "sulod": ("inside", ("can get in", "managed to get in")),
        "gawas": ("go out", ("can get out", "managed to get out")),
        "inom": ("drink", ("can drink", "managed to drink")),
        "adto": ("go", ("can go", "managed to go")),
        "dala": ("bring", ("can bring", "managed to bring")),
        "bayad": ("pay", ("can pay", "managed to pay")),
    }


def test_a_na_form_is_worded_as_the_state_it_names():
    """Wolff: "Natulug na ba siya? Is he asleep yet?"; "Nalipay ku nga nia ka, I am glad that you're here"."""
    table = load_affix_glosses(GLOSSES_PATH)
    real = {root: forms[1] for (root, key), (_, forms) in table.items() if key == "ma-na"}

    assert real == {"tulog": "is asleep", "lipay": "is happy", "hurot": "has run out"}


def test_a_mi_form_stays_the_done_act_and_every_unreal_form_stays_will():
    """mi- is the punctual real form, an act seen whole: Wolff, "Miadtu ku didtu
    gahapun, I went there yesterday". And the unreal form of every aspect pair
    is asked for as what has not begun; the potential's is what one can do."""
    table = load_affix_glosses(GLOSSES_PATH)

    assert table[("adto", "mo-mi")][1] == ("will go", "went")
    unreal = {"maka-naka": "can "}
    assert [key for key, (_, forms) in table.items() if not forms[0].startswith(unreal.get(key[1], "will "))] == []


def test_every_pattern_names_its_two_cells_by_whether_the_thing_has_begun():
    """What the page puts over the two columns of forms. Nothing tense-like."""
    assert {pattern.key: pattern.labels for pattern in PATTERNS} == {
        "mo-mi": ("not begun", "begun"),
        "mag-nag": ("not begun", "begun"),
        "ma-na": ("not begun", "begun"),
        "maka-naka": ("not begun", "begun"),
        "on-gi": ("not begun", "begun"),
    }


def test_every_pattern_says_what_its_two_forms_are_for():
    """Two sentences the narrator says once the first root has been modelled, so
    'the first' and 'the second' are forms the learner has just heard. No
    sentence names a tense. A pattern taught as whole sentences words its two
    notes in its own way instead."""
    worded = [pattern for pattern in PATTERNS if not pattern.sentences]
    for pattern in worded:
        first, second = pattern.notes
        assert first.startswith("The first is for ")
        assert second.startswith("The second is for ")
        assert not any(word in f"{first} {second}".casefold() for word in ("past", "future", "tense"))
    assert len({pattern.notes for pattern in worded}) == len(worded)
    # The other job of the unreal form, which an English future hides.
    assert "usually" in _BY_KEY["mo-mi"].notes[0]


# The two places an English tense picks the wrong Cebuano form, taught as whole
# lines: after wala the form is the unreal one though the English is a past
# (Wolff: "Wala ku muadtu didtu, I did not go there"), and so is the habitual
# ("Mumata mi sa alas singku kada buntag, We wake up every morning at five").


def test_a_root_gets_a_line_for_what_did_not_happen_and_one_for_what_usually_does():
    assert pattern_lines("adto", _BY_KEY["mo-mi"]) == (
        ("Wala ko moadto.", "I didn't go."),
        ("Moadto ko kada adlaw.", "I go every day."),
        ("Gusto ko moadto.", "I want to go."),
    )
    assert pattern_lines("Ampo", _BY_KEY["mag-nag"]) == (
        ("Wala ko mag-ampo.", "I didn't pray."),
        ("Mag-ampo ko kada adlaw.", "I pray every day."),
        ("Gusto ko mag-ampo.", "I want to pray."),
    )
    # "will be walking" is still the root's own English, gone on for a while.
    assert pattern_lines("lakaw", _BY_KEY["mag-nag"]) == (
        ("Wala ko maglakaw.", "I didn't walk."),
        ("Maglakaw ko kada adlaw.", "I walk every day."),
        ("Gusto ko maglakaw.", "I want to walk."),
    )


def test_a_potential_root_gets_a_line_for_what_could_not_happen_and_what_cannot():
    """After wala the potential is the unreal form: Wolff, "Wà ku makasakay
    dáyun, I couldn't find transportation right away"; and after dili, "Dì ku
    makaalsa niánà, I cannot lift that". Neither is a habit, so no kada line."""
    assert pattern_lines("adto", _BY_KEY["maka-naka"]) == (
        ("Wala ko makaadto.", "I couldn't go."),
        ("Dili ko makaadto.", "I can't go."),
    )


@pytest.mark.parametrize(
    ("root", "pattern", "why"),
    [
        ("uban", "mo-mi", "mouban is 'will go along': 'I didn't accompany' is not what the line says"),
        ("kita", "mag-nag", "magkita is 'will meet', not 'will see'"),
        ("lipay", "ma-na", "a state: 'I didn't happy'"),
        ("sulod", "maka-naka", "sulod is 'inside': 'I couldn't inside'"),
        ("tulog", "ma-na", "no ma- / na- line is written down"),
        ("andam", "mag-nag", "nobody has worded the root"),
        ("tulog", "mo-mi", "the root does not take the pair"),
    ],
)
def test_a_root_whose_english_would_not_come_out_right_gets_no_lines(root, pattern, why):
    assert pattern_lines(root, _BY_KEY[pattern]) == (), why


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
    assert bundle.pattern_glosses("tulog", _BY_KEY["ma-na"]) == ("sleep", ("will sleep", "is asleep"))
    assert bundle.pattern_lines("adto", _BY_KEY["mo-mi"])[0] == ("Wala ko moadto.", "I didn't go.")


def test_a_language_without_patterns_registers_none():
    bundle = get_a1_morphology("no")
    assert bundle.patterns == ()
    assert bundle.pattern_forms is None
    assert bundle.pattern_glosses is None
