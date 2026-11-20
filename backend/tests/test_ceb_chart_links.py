"""Links into the verb chart (tunatale-h0q3.47, slice 2).

The owner's design (https://claude.ai/artifact/EY8AJSwKupGKtkWhZ5P5ks, verdict 2026-11-09):
the card's verb forms get a "chart ›" link per row, and the reader's word popover names the
form's place in the chart and opens it there. Links in TT's own review and reader only; an
Anki note field carries none until a fixed address reaches whichever side is live, so the
card's links are written as TT serves the card, never into the note.

A link names a chart row and the affixes to mark in it. Marking the affix rather than naming
a column is what keeps it honest: ka- is both realis and irrealis, and mo- also stands in the
dependent column. Silent rather than guessing: no reading, a causative (the chart has no pa-
rows), a mutual verb, or a form two chart rows share.

Every expected row below was written from verb_chart.json and Wolff's class codes before
the code existed.
"""

from __future__ import annotations

import re

import pytest

from app.languages import ChartLink, chart_card_html, chart_link
from app.plugins.languages.ceb.chart_links import ROWS
from app.plugins.languages.ceb.wolff_cards import load_lexicon


@pytest.fixture(scope="module")
def lexicon():
    return load_lexicon()


@pytest.mark.parametrize(
    ("word", "english", "link"),
    [
        ("miabot", "", ChartLink("agent-mo", ("mi-",), "Agent · Perfective · Realis")),
        ("moadto", "", ChartLink("agent-mo", ("mo-",), "Agent · Perfective · Irrealis")),
        # maka- is the potential side of the same row
        ("makaadto", "", ChartLink("agent-mo", ("maka-",), "Agent · Perfective · Potential · Irrealis")),
        ("gikaon", "", ChartLink("patient-on", ("gi-",), "Patient · Perfective · Realis")),
        # the chart puts the patient's -a in the dependent column
        ("kuhaa", "", ChartLink("patient-on", ("-a",), "Patient · Perfective · Dependent")),
        # dakù's verb sense is a verb of state: mo- is the stative row's
        ("modako", "", ChartLink("stative-mo", ("mo-",), "Stative · Perfective · Irrealis")),
    ],
)
def test_a_word_links_to_its_row_and_affix(word, english, link):
    assert chart_link("ceb", word, english) == link


@pytest.mark.parametrize(
    ("word", "english"),
    [
        ("pakan-a", ""),  # a causative: the chart has no pa- rows
        ("magkita", ""),  # a mutual verb: the chart has no mutual rows
        ("balay", ""),  # no affix at all
        ("mahímò", "can be obtained"),  # the parts line is silent, so the link is too
    ],
)
def test_silent_where_the_chart_has_no_place_for_it(word, english):
    assert chart_link("ceb", word, english) is None


def test_a_language_with_no_chart_has_no_links():
    assert chart_link("no", "spiser", "eats") is None
    assert chart_card_html("no", "<b>x</b>") == "<b>x</b>"


def test_every_place_wolffs_codes_give_is_linked_or_listed_as_having_no_row(lexicon):
    """A subject the decoder starts giving is a loud failure here, never a silent no-link."""
    from app.plugins.languages.ceb.chart_links import UNLINKED
    from app.plugins.languages.ceb.wolff_cards import _licenses

    given = {
        place
        for rec in lexicon.entries
        for form in rec["forms"]
        for sense in form["senses"]
        if sense.get("cls")
        for place in _licenses(sense["cls"])
    }
    assert given == set(ROWS) | UNLINKED


def test_a_word_moved_or_conveyed_is_the_instrument_rows():
    """Wolff's class c gives i- 'what is moved or conveyed'; the chart's Instrument row is
    'what is moved, given or used'."""
    assert chart_link("ceb", "ibilin", "") == ChartLink("instrument-i", ("i-",), "Instrument · Perfective · Irrealis")


def test_a_reading_by_a_roots_own_row_still_finds_its_subject():
    """tugnaw's own row labels gi- 'feels it'; Wolff's general i- also licenses gi-, but
    the parts line never reads it, so the patient's 'who is affected' row stands alone."""
    link = chart_link("ceb", "gitugnaw", "")
    assert link is not None and link.row == "patient-on"


def test_an_affix_in_two_cells_names_no_column():
    """ka- is maka-'s and naka-'s short form: both potential cells show it."""
    from app.plugins.languages.ceb.chart_links import label

    assert label("agent-mo", "ka-") == "Agent · Perfective · Potential"


def test_every_row_a_link_names_is_in_the_chart():
    chart = _chart()
    ids = {r["id"] for g in chart["groups"] for r in g["rows"]}
    assert {row for row, _ in ROWS.values()} <= ids


def test_every_affix_a_link_marks_is_a_chip_in_its_row():
    """A link that marks an affix the row does not show highlights nothing."""
    chart = _chart()
    chips = {r["id"]: _row_chips(r) for g in chart["groups"] for r in g["rows"]}
    for (pair, _), (row, mode) in ROWS.items():
        from app.plugins.languages.ceb.chart_links import pair_affixes

        assert set(pair_affixes(pair)) <= chips[row][mode], (pair, row, mode)


# ── the card's verb forms ───────────────────────────────────────────────────────────────────

_LINK = re.compile(r'<b>([^<]*)</b> <a class="chart-link" href="/reference/verb-chart\?([^"]*)">chart ›</a>')


def test_each_row_of_a_cards_verb_forms_links_to_its_chart_row(lexicon):
    verb = lexicon.card_fields("palit", "buy").verb
    linked = _LINK.findall(chart_card_html("ceb", verb))
    assert linked == [
        ("perfective · mo-/mi-", "row=agent-mo&amp;affix=mo-&amp;affix=mi-"),
        ("progressive · mag-/nag-", "row=agent-mag&amp;affix=mag-&amp;affix=nag-"),
        ("potential · maka-/naka-", "row=agent-mo&amp;affix=maka-&amp;affix=naka-"),
        ("-on/gi-", "row=patient-on&amp;affix=-on&amp;affix=gi-"),
        ("-an/gi-an", "row=circumstance-an&amp;affix=-an&amp;affix=gi-%E2%80%A6-an"),
        ("i-/gi-", "row=instrument-i&amp;affix=i-&amp;affix=gi-"),
    ]


def test_a_stative_row_links_to_the_stative_rows_and_an_unknown_one_gets_none(lexicon):
    verb = lexicon.card_fields("init", "hot").verb
    out = chart_card_html("ceb", verb)
    linked = dict(_LINK.findall(out))
    # ínit's verb of state: ma-/na- is the stative row's potential, magka- its own row
    assert linked["ma-/na-"] == "row=stative-mo&amp;affix=ma-&amp;affix=na-"
    assert linked["magka-/nagka-"] == "row=stative-magka&amp;affix=magka-&amp;affix=nagka-"
    # hi-an (by accident) has no chart row: the row is left as it was
    assert "<b>by accident · hi-an</b></td>" in out


def test_the_rest_of_the_card_is_unchanged(lexicon):
    verb = lexicon.card_fields("palit", "buy").verb
    out = chart_card_html("ceb", verb)
    assert re.sub(r' <a class="chart-link"[^>]*>chart ›</a>', "", out) == verb
    assert chart_card_html("ceb", "<p>no verb table here</p>") == "<p>no verb table here</p>"


# ── helpers ─────────────────────────────────────────────────────────────────────────────────


def _chart() -> dict:
    import json

    from app.languages import get_verb_chart_path

    return json.loads(get_verb_chart_path("ceb").read_text())


def _row_chips(row: dict) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for mode in ("volitional", "potential"):
        cells = row[mode] if isinstance(row[mode], list) else []
        out[mode] = {part for cell in cells if cell for a in cell["affixes"] for part in a["label"].split(", ")}
    return out


# ── the payloads ────────────────────────────────────────────────────────────────────────────


def test_a_review_item_serves_its_verb_forms_with_chart_links(lexicon):
    """The review screen renders the item's extras; the links are added there, and the
    stored extras (the Anki note's fields) keep none."""
    from app.api import srs as srs_api
    from app.models.syntactic_unit import BackField, SyntacticUnit
    from app.srs.database import SRSDatabase

    verb = lexicon.card_fields("palit", "buy").verb
    db = SRSDatabase(":memory:")
    unit = SyntacticUnit(
        text="palít",
        translation="buy",
        word_count=1,
        difficulty=1,
        source="test",
        extras=(BackField(label="Verb", html=verb, tier="details"),),
    )
    db.add_collocation(unit, language_code="ceb")
    item = db.get_collocation("palít")
    served = srs_api._item_to_dict(1, item, "ceb", db=db)["extras"][0]["html"]
    assert len(_LINK.findall(served)) == 6
    assert db.get_collocation("palít").syntactic_unit.extras[0].html == verb
