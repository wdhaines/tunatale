"""Cebuano object focus: the checked sentences of stage 6 (tunatale-ve4p.32).

gi- / -on keep the not-begun / begun split of the earlier patterns and add a
second choice, which noun is the ``ang`` noun. A lone verb cannot be asked for
("ate" is both ``mikaon`` and ``gikaon``), so this stage is taught as whole
sentences, built by rule from one hand-checked row per root.

Oracles: the rows were checked on 2026-10-15 against Wolff, *A Dictionary of
Cebuano Visayan* (every root is class ``a``, which takes the direct passive),
and counted in the FineWeb-2 ``ceb_Latn`` native-news shard (9.48 M tokens)
as the exact spellings below. Design: .beads-tasks/briefs/
design-ve4p-stage6-object-focus-2026-10.md.
"""

from __future__ import annotations

import pytest

from app.plugins.languages.ceb.a1_morphology import affix_of
from app.plugins.languages.ceb.affix_patterns import COUNTS_PATH, VOUCH_FLOOR, load_affix_counts, spell
from app.plugins.languages.ceb.focus_sentences import (
    DOERS,
    FOCUS_COUNTS_PATH,
    FOCUS_ROWS_PATH,
    HOMOGRAPHS,
    FocusObject,
    focus_roots,
    focus_row,
    focus_sentence,
    load_focus_counts,
    load_focus_rows,
)

_I, _HE = DOERS


def test_the_four_checked_rows():
    """The owner's roots, 2026-10-15, less hulat: its actor forms are durative only
    (mohulat 12, mihulat 4 in news), so its prompts would differ by tense too."""
    rows = load_focus_rows(FOCUS_ROWS_PATH)
    assert [(r.root, r.english, r.past, r.forms, [(o.noun, o.english) for o in r.objects]) for r in rows.values()] == [
        (
            "inom",
            "drink",
            "drank",
            ("moinom", "miinom", "imnon", "giinom"),
            [("tubig", "water"), ("kape", "coffee"), ("gatas", "milk")],
        ),
        (
            "kaon",
            "eat",
            "ate",
            ("mokaon", "mikaon", "kan-on", "gikaon"),
            [("pan", "bread"), ("pagkaon", "food"), ("isda", "fish")],
        ),
        (
            "dala",
            "bring",
            "brought",
            ("modala", "midala", "dad-on", "gidala"),
            [("pagkaon", "food"), ("pan", "bread"), ("kape", "coffee")],
        ),
        (
            "tan-aw",
            "look at",
            "looked at",
            ("motan-aw", "mitan-aw", "tan-awon", "gitan-aw"),
            [("balay", "a house"), ("sakyanan", "a vehicle")],
        ),
    ]
    assert focus_roots() == ("inom", "kaon", "dala", "tan-aw")


def test_the_doers_are_i_and_he():
    """``ko`` is both "I" and "by me" (Wolff, under aku), so it does not move;
    siya moves to niya. The owner asked for siya -> niya from the start."""
    assert [(d.actor, d.agent, d.english) for d in DOERS] == [("ko", "ko", "I"), ("siya", "niya", "he")]


# ── the sentences ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("root", "cell", "doer", "text", "english", "stressed", "bold"),
    [
        # inom, all four cells, with I
        ("inom", 0, _I, "Moinom ko og tubig.", "I'll drink water.", "I'll", ("Moinom", "ko")),
        ("inom", 1, _I, "Miinom ko og tubig.", "I drank water.", "I", ("Miinom", "ko")),
        ("inom", 2, _I, "Imnon ko ang tubig.", "I'll drink the water.", "the water", ("Imnon", "ang tubig")),
        ("inom", 3, _I, "Giinom ko ang tubig.", "I drank the water.", "the water", ("Giinom", "ang tubig")),
        # kaon, all four cells, with he: siya becomes niya
        ("kaon", 0, _HE, "Mokaon siya og pan.", "He'll eat bread.", "He'll", ("Mokaon", "siya")),
        ("kaon", 1, _HE, "Mikaon siya og pan.", "He ate bread.", "He", ("Mikaon", "siya")),
        ("kaon", 2, _HE, "Kan-on niya ang pan.", "He'll eat the bread.", "the bread", ("Kan-on", "ang pan")),
        ("kaon", 3, _HE, "Gikaon niya ang pan.", "He ate the bread.", "the bread", ("Gikaon", "ang pan")),
        # a count noun: "a house" when it is not the one meant, "the house" when it is
        ("tan-aw", 1, _I, "Mitan-aw ko og balay.", "I looked at a house.", "I", ("Mitan-aw", "ko")),
        (
            "tan-aw",
            3,
            _HE,
            "Gitan-aw niya ang balay.",
            "He looked at the house.",
            "the house",
            ("Gitan-aw", "ang balay"),
        ),
        ("dala", 2, _HE, "Dad-on niya ang pagkaon.", "He'll bring the food.", "the food", ("Dad-on", "ang pagkaon")),
    ],
)
def test_a_cell_is_one_sentence(root, cell, doer, text, english, stressed, bold):
    row = focus_row(root)
    sentence = focus_sentence(row, cell, doer, row.objects[0])
    assert sentence.text == text
    assert sentence.english == english
    # What the narrator stresses and the card bolds is the focus: the doer in
    # actor focus, the thing in object focus. The affix agrees with it.
    assert sentence.english[slice(*sentence.stressed)] == stressed
    assert tuple(sentence.text[slice(*span)] for span in sentence.bold) == bold


def test_a_sentence_takes_the_object_it_is_given():
    row = focus_row("inom")
    assert focus_sentence(row, 3, _HE, row.objects[2]).text == "Giinom niya ang gatas."
    assert focus_sentence(row, 3, _HE, row.objects[2]).english == "He drank the milk."


@pytest.mark.parametrize("cell", [-1, 4])
def test_a_cell_out_of_range_is_an_error(cell):
    row = focus_row("inom")
    with pytest.raises(IndexError):
        focus_sentence(row, cell, _I, row.objects[0])


def test_an_object_says_the_when_it_is_the_one_meant():
    assert FocusObject("balay", "a house").the_english == "the house"
    assert FocusObject("itlog", "an egg").the_english == "the egg"
    assert FocusObject("tubig", "water").the_english == "the water"


def test_a_root_with_no_row_has_none():
    assert focus_row("lakaw") is None
    assert focus_row("hulat") is None


# ── the evidence each row stands on ──────────────────────────────────────────


@pytest.mark.parametrize("root", ["inom", "kaon", "dala", "tan-aw"])
def test_the_prefix_forms_are_the_prefix_attached(root):
    """mo-, mi- and gi- are regular: only -on has to be written down."""
    mo, mi, _on, gi = focus_row(root).forms
    assert (mo, mi, gi) == (spell(root, "mo"), spell(root, "mi"), spell(root, "gi"))


def test_the_on_forms_are_written_down_because_three_are_irregular():
    """``spell`` cannot make these, and the lemma table lists dalaon/dalhaon
    but not dad-on, inomon but not imnon. Wolff: dad-un, kan-un."""
    irregular = {r.root: r.forms[2] for r in load_focus_rows(FOCUS_ROWS_PATH).values() if r.forms[2] != r.root + "on"}
    assert irregular == {"inom": "imnon", "kaon": "kan-on", "dala": "dad-on"}


def test_every_actor_form_is_one_native_news_uses():
    counts = load_affix_counts(COUNTS_PATH)
    for row in load_focus_rows(FOCUS_ROWS_PATH).values():
        for affix in ("mo", "mi"):
            assert counts.get((row.root, affix), 0) >= VOUCH_FLOOR, (row.root, affix)


def test_every_object_form_is_one_native_news_uses_as_spelled():
    counts = load_focus_counts(FOCUS_COUNTS_PATH)
    assert counts == {
        "giinom": 45,
        "imnon": 51,
        "gikaon": 106,
        "kan-on": 117,
        "gidala": 2850,
        "dad-on": 309,
        "gitan-aw": 355,
        "tan-awon": 544,
    }
    assert all(count >= VOUCH_FLOOR for count in counts.values())


def test_a_count_a_homograph_inflates_is_named():
    """``kan-on`` is also cooked rice, so its 117 says little about "will be
    eaten". Wolff vouches for the verb (kaun: "Unsa may kan-un sa masakitun?").
    No other form's count stands on a second word."""
    assert HOMOGRAPHS == {"kan-on": "cooked rice"}


def test_no_object_is_spelled_like_a_form_of_its_own_verb():
    """Never ``Kan-on ko ang kan-on``: the owner has a card for kan-on the rice."""
    for row in load_focus_rows(FOCUS_ROWS_PATH).values():
        assert not {o.noun for o in row.objects} & {f.casefold() for f in row.forms}, row.root


@pytest.mark.parametrize(
    ("root", "affixes"),
    [
        ("inom", ("mo", "mi", "on", "gi")),
        ("kaon", ("mo", "mi", "on", "gi")),
        ("dala", ("mo", "mi", "on", "gi")),
        ("tan-aw", ("mo", "mi", "on", "gi")),
    ],
)
def test_every_form_is_read_back_as_its_own_affix(root, affixes):
    """The round trip lesson hints depend on: a lesson's ``imnon`` is -on on inom."""
    assert tuple(affix_of(form, root) for form in focus_row(root).forms) == affixes


def test_an_irregular_spelling_is_only_its_own_roots():
    assert affix_of("imnon", "kaon") is None
    assert affix_of("kan-on", "inom") is None


# ── the loaders ──────────────────────────────────────────────────────────────


def test_a_row_with_the_wrong_number_of_columns_is_an_error(tmp_path):
    path = tmp_path / "rows.tsv"
    path.write_text("# header\ninom\tdrink\tdrank\tmoinom\tmiinom\timnon\ttubig=water\n", encoding="utf-8")
    with pytest.raises(ValueError, match="rows.tsv:2"):
        load_focus_rows(path)


def test_an_object_without_its_english_is_an_error(tmp_path):
    path = tmp_path / "rows.tsv"
    path.write_text("inom\tdrink\tdrank\tmoinom\tmiinom\timnon\tgiinom\ttubig\n", encoding="utf-8")
    with pytest.raises(ValueError, match="rows.tsv:1"):
        load_focus_rows(path)


def test_the_counts_loader_skips_headers(tmp_path):
    path = tmp_path / "counts.tsv"
    path.write_text("#source=x\ngiinom\t45\n\n", encoding="utf-8")
    assert load_focus_counts(path) == {"giinom": 45}


def test_a_missing_table_is_an_error_not_an_empty_answer(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_focus_rows(tmp_path / "absent.tsv")
    with pytest.raises(FileNotFoundError):
        load_focus_counts(tmp_path / "absent.tsv")
