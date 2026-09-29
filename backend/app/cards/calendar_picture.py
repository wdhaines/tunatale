"""Which calendar words get a drawn page, and which page (tunatale-xykz).

A month and a weekday are the one vocabulary where a photo search is not merely
weak but actively misleading: asked for "March" it returns a march of people,
asked for "Monday" a photographer's choice of weekday, and asked for "Sunday"
often a picture of the sun. Neither is the thing the word names, which is a
**position in a structure** — the seventh cell of a year, the first of a week.
So these are drawn, like the numbers, the spatial words and the pronouns, by
``app.cards.calendar_scenes``.

**Which word means which concept is DATA**, in each language's ``calendar.json``,
and this module holds no word of any language. The same file carries
``week_start``, because which day opens a week is a local custom and not a fact
about calendars: the layout here follows Monday for the European decks and
Sunday for the Philippine ones. It is a data value rather than a module constant
so that a language's week is a thing the file can say, and so that the renderer's
copy of the order proves nothing.

**The gloss guard, which earns its keep on two real homographs in the live
decks.** Norwegian ``mars`` is the third month and also the planet; Tagalog
``linggo`` is "week" AND "Sunday", which the tl notetype config in
``app/languages.py`` records as an actual collision between two of the Pimsleur
notes. A router keyed on the WORD alone would draw a Sunday for a card that means
a week, and there is no other place on the card that can say which was meant. So
the word picks the concept and the card's English gloss confirms it: ``linggo``
glossed "week" draws nothing and takes the route it was already on, while
``linggo`` glossed "Sunday" draws Sunday. An empty gloss refuses too — a picture
that cannot be confirmed is not drawn.

**The filename carries the week start, and that is not tidiness.** Two languages
with the same concept and different week orders draw DIFFERENT pages (a Sunday
opens one and a Monday the other), so a name keyed on the concept alone would put
two languages' Monday cards in one file and serve one the other's page.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import cache
from typing import NamedTuple

from app.cards.calendar_scenes import render_calendar_svg
from app.cards.number_picture import NumberPicture
from app.languages import get_calendar_path

#: The concept's own English names, used to check a card's GLOSS. Built from
#: name tuples rather than written out as 19 frozensets, because the two are
#: positional: ``weekday_3`` is Wednesday ONLY because it is the third name. A
#: hand-written table would let those drift apart silently, and a keyword set
#: that said "Tuesday" for a Wednesday would draw a confidently wrong page.
_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

#: A gloss matches when one of its whole words is the concept's name — "March"
#: confirms the third month, and "marseille" does not. Casefolded, because a
#: card's English field is written by hand.
_CONCEPT_GLOSSES: dict[str, frozenset[str]] = {
    **{f"month_{n}": frozenset({name.casefold()}) for n, name in enumerate(_MONTH_NAMES, start=1)},
    **{f"weekday_{d}": frozenset({name.casefold()}) for d, name in enumerate(_WEEKDAY_NAMES, start=1)},
}

_WORD = re.compile(r"[^\W\d_]+")

#: A language that registers no file has no drawn months, and every one of its
#: words keeps the routing it has today. The default is the ISO order's own
#: start, so an absent file is inert rather than a second way to be wrong.
_DEFAULT_WEEK_START = "monday"


class CalendarTable(NamedTuple):
    """*language_code*'s calendar data: its week order and its words."""

    week_start: str
    words: dict[str, str]


@cache
def load_calendar(language_code: str) -> CalendarTable:
    """*language_code*'s calendar data, both halves read from the same file.

    Empty words when the language registers no file, or registers one that is not
    there: a moved or deleted data file leaves every word on the route it was
    already on, which is the correct outcome for a card add and not a crash.
    """
    path = get_calendar_path(language_code)
    if path is None or not path.exists():
        return CalendarTable(week_start=_DEFAULT_WEEK_START, words={})
    data = json.loads(path.read_text(encoding="utf-8"))
    return CalendarTable(
        week_start=str(data.get("week_start", _DEFAULT_WEEK_START)),
        words={str(word).casefold(): str(concept) for word, concept in data.get("words", {}).items()},
    )


def is_calendar_word(text: str, language_code: str) -> bool:
    """True if *text* is one of *language_code*'s drawn calendar words.

    Stripped and casefolded, because this is handed whatever is in the card
    field. Whole-token: a phrase is a different card with a different meaning,
    and a substring match would give ``sa susunod na Myerkules`` a Wednesday
    page.
    """
    return text.strip().casefold() in load_calendar(language_code).words


def gloss_matches(concept: str, gloss: str) -> bool:
    """True if the English *gloss* names *concept* in one of its whole words.

    A ``KeyError`` for a concept with no keyword is a refusal rather than a
    crash on the add path, and the caller resolves concepts from the word table,
    so an unknown one means the data and this module disagree.
    """
    return bool(_CONCEPT_GLOSSES[concept] & {w.casefold() for w in _WORD.findall(gloss)})


def calendar_picture(text: str, language_code: str, gloss: str) -> NumberPicture | None:
    """The drawn page for calendar word *text*, or ``None``.

    ``None`` means "take the route you were already on": not a calendar word, or
    one whose *gloss* does not confirm the sense the file mapped it to. The week
    order comes from the same file as the words, so the picture is the language's
    own week and not a default.
    """
    table = load_calendar(language_code)
    concept = table.words.get(text.strip().casefold())
    if concept is None or not gloss_matches(concept, gloss):
        return None
    svg = render_calendar_svg(concept, week_start=table.week_start)
    digest = hashlib.sha256(svg).hexdigest()[:8]
    return NumberPicture(f"calendar_{concept}_{table.week_start}_{digest}.svg", svg)
