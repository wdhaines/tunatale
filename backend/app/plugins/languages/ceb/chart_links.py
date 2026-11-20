"""Links into the verb chart (tunatale-h0q3.47, slice 2): from a reader word, and from each
row of a card's verb forms.

A link names a chart row (``verb_chart.json``) and the affixes to mark in it. A card row is
keyed as the card's verb table keys it, (affix pair, Wolff's subject). The chart's passive
groups are its triggers, so every subject Wolff gives -an, -on or i- stands in that affix's
row; a verb of state's maka- splits by the chart's glosses ('came to be' under mag-,
'caused to become' under magka-). UNLINKED lists the rest: the chart has no row for them. The card's links are written as TT serves the card, never into the Anki note: a
link inside AnkiDroid needs a fixed address that reaches whichever side is live (owner,
2026-11-09).
"""

from __future__ import annotations

import html
import json
import re
from functools import cache
from pathlib import Path
from urllib.parse import quote

from app.plugins.languages.ceb.wolff_dictionary import CAUSE, DIRECT, DOER, PLACE, TOGETHER, UNDERGOER, USED

CHART_PATH = Path(__file__).parent / "data" / "verb_chart.json"

# (card pair, subject) -> (chart row, the side of it the pair stands in)
ROWS: dict[tuple[str, str], tuple[str, str]] = {
    ("mo-/mi-", DOER): ("agent-mo", "volitional"),
    ("mag-/nag-", DOER): ("agent-mag", "volitional"),
    ("maka-/naka-", DOER): ("agent-mo", "potential"),
    ("maN-/naN-", DOER): ("agent-pan", "volitional"),
    ("-on/gi-", DIRECT): ("patient-on", "volitional"),
    ("-on/gi-", "who is affected"): ("patient-on", "volitional"),
    ("ma-/na-", DIRECT): ("patient-on", "potential"),
    **{
        ("-an/gi-an", subject): ("circumstance-an", "volitional")
        for subject in (
            PLACE,
            DIRECT,
            "who or what it is done to",
            "where it is done, or who it is done to",
            "the reason for it",
            "what it affects",
            "what is taken from or added to",
        )
    },
    ("ma-an/na-an", PLACE): ("circumstance-an", "potential"),
    ("ma-an/na-an", "who it is done to"): ("circumstance-an", "potential"),
    ("i-/gi-", USED): ("instrument-i", "volitional"),
    ("i-/gi-", "what is moved or conveyed"): ("instrument-i", "volitional"),
    ("i-/gi-", "the reason for it"): ("instrument-i", "volitional"),
    ("ika-/gika-", "who it is done to"): ("instrument-i", "potential"),
    ("mo-/mi-", UNDERGOER): ("stative-mo", "volitional"),
    ("ma-/na-", UNDERGOER): ("stative-mo", "potential"),
    ("mag-/nag-", UNDERGOER): ("stative-mag", "volitional"),
    ("maka-/naka-", UNDERGOER): ("stative-mag", "potential"),
    ("magka-/nagka-", UNDERGOER): ("stative-magka", "volitional"),
    ("maka-/naka-", CAUSE): ("stative-magka", "potential"),
}
# The (pair, subject) rows the chart has no place for: mutual verbs, the accidental hi-, a
# verb of state's maN-, and a maka- whose code gives both stative subjects at once.
UNLINKED: frozenset[tuple[str, str]] = frozenset(
    {
        ("makig-/nakig-", TOGETHER),
        ("magka-/nagka-", TOGETHER),
        ("mag-/nag-", TOGETHER),
        ("hi-an", "who it happens to by accident"),
        ("maN-/naN-", UNDERGOER),
        ("maka-/naka-", f"{UNDERGOER}, or {CAUSE}"),
    }
)

# The decoder's spelling of a piece -> the chart's (Wolff writes u where the chart writes o).
_CHART_SPELLING = {"mu-": "mo-", "-un": "-on", "gi-an": "gi-…-an", "ma-an": "ma-…-an", "na-an": "na-…-an"}


def pair_affixes(pair: str) -> tuple[str, ...]:
    """A card pair's affixes as the chart spells its chips ('-an/gi-an' -> -an, gi-…-an)."""
    return tuple(_CHART_SPELLING.get(a, a) for a in pair.split("/"))


def href(row: str, affixes: tuple[str, ...]) -> str:
    return "/reference/verb-chart?" + "&".join([f"row={row}", *(f"affix={quote(a)}" for a in affixes)])


@cache
def _chart() -> dict:
    return json.loads(CHART_PATH.read_text())


def label(row_id: str, affix: str) -> str:
    """Where *affix* stands in *row_id*, in the chart's own words: 'Agent · Perfective ·
    Realis', with ' · Potential' on the potential side and the column only when one cell
    shows the affix (ka- is both realis and irrealis)."""
    chart = _chart()
    group, row = next((g, r) for g in chart["groups"] for r in g["rows"] if r["id"] == row_id)
    cells = [
        (mode, col["label"])
        for mode in chart["modes"]
        if isinstance(row[mode["id"]], list)
        for col, cell in zip(mode["columns"], row[mode["id"]], strict=False)
        if cell and affix in {p for a in cell["affixes"] for p in a["label"].split(", ")}
    ]
    out = [group["label"], row["aspect"]]
    if {mode["id"] for mode, _ in cells} == {"potential"}:
        out.append("Potential")
    if len(cells) == 1:
        out.append(cells[0][1])
    return " · ".join(out)


def word_link(word: str, english: str) -> tuple[str, tuple[str, ...], str] | None:
    """A reader word's link: (chart row, the affix to mark, where it stands), or None."""
    from app.plugins.languages.ceb.wolff_cards import load_lexicon

    place = load_lexicon().chart_place(word, english)
    if place is None:
        return None
    piece, places = place
    rows = {ROWS.get(p) for p in places}
    if len(rows) != 1 or None in rows:
        return None  # two chart rows, or a place the chart has none for (a mutual verb)
    ((row, _),) = rows
    affix = _CHART_SPELLING.get(piece, piece)
    return row, (affix,), label(row, affix)


_TRIGGER = re.compile(r'<tr class="wolff-trigger"><td colspan="3"><b>[^<]*</b> · subject: ([^<]*)</td></tr>')
_HEAD = re.compile(r'(<tr class="wolff-row[^"]*"><td><b>([^<]*)</b>)')


def card_html(text: str) -> str:
    """*text* with a 'chart ›' link after the head of each verb-forms row the chart has."""
    if "wolff-verb" not in text:
        return text
    out: list[str] = []
    subject = ""
    pos = 0
    for m in re.finditer(f"{_TRIGGER.pattern}|{_HEAD.pattern}", text):
        out.append(text[pos : m.end()])
        pos = m.end()
        if m.group(1) is not None:
            subject = html.unescape(m.group(1))
            continue
        pair = html.unescape(m.group(3)).rpartition(" · ")[2]
        found = ROWS.get((pair, subject))
        if found is not None:
            link = html.escape(href(found[0], pair_affixes(pair)))
            out.append(f' <a class="chart-link" href="{link}">chart ›</a>')
    out.append(text[pos:])
    return "".join(out)
