"""Which calendar words get a drawn page, and which page (tunatale-xykz).

A month and a weekday are the one vocabulary where a photo search is not merely
weak but actively misleading: asked for "March" it returns a march of people,
asked for "Monday" a photographer's choice of weekday, and asked for "week" it
returns a picture of a calendar in general — the wrong LEVEL of the structure
altogether. So these are drawn, like the numbers, the spatial words and the
pronouns, by ``app.cards.calendar_scenes``.

**Which word means which concept is DATA**, in each language's ``calendar.json``,
and this module holds no word of any language. The same file carries
``week_start``, because which day opens a week is a local custom and not a fact
about calendars.

**The gloss guard is what the two real homographs in the live decks need.** The
veto is asked about a bare token and cannot see a SENSE, so the word picks the
concept and the card's English gloss has to confirm it. Norwegian ``mars`` is the
third month and also the planet; Tagalog ``linggo`` is "week" AND "Sunday", the
same collision ``app/languages.py`` records in the tl notetype config for two
Pimsleur notes. Keyed on the word alone, a card for the week would get a Sunday
page, and there is nowhere else on the card that could say which was meant.

**The week order is data too, and that is the property the pictures are pinned
by.** ``week_start`` arrives at the renderer as an argument, so nothing here
reads an order off the renderer: the expected lit position comes from the
brief's transcription below, and the two starts are asserted to disagree on
every one of the seven days.

Every property is read off the FILES and off the RENDERS — a word table is
pinned by whole-dict equality against the brief's list, and a picture is pinned
by parsing the SVG that came out. The probes at the end feed each guard a
*corrupted* table and require a named refusal, so the checks are shown to
discriminate rather than to agree with the module that produced their input.
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from collections import Counter
from typing import Any

import pytest

from app.cards import calendar_picture as module
from app.cards.calendar_picture import calendar_picture, gloss_matches, is_calendar_word, load_calendar
from app.cards.number_picture import NumberPicture
from app.languages import get_calendar_path, get_function_words_path

_SVG = "{http://www.w3.org/2000/svg}"

# ── The brief's ORACLE B2, transcribed here ──────────────────────────────────
# Held as literals rather than imported, for the same reason every other suite in
# this family does it: "the data file agrees with the module that reads it" is
# not what pins a word list, and only an independent copy can fail.
_WORDS: dict[str, dict[str, str]] = {
    "no": {
        "januar": "month_1",
        "februar": "month_2",
        "mars": "month_3",
        "april": "month_4",
        "mai": "month_5",
        "juni": "month_6",
        "juli": "month_7",
        "august": "month_8",
        "september": "month_9",
        "oktober": "month_10",
        "november": "month_11",
        "desember": "month_12",
        "mandag": "weekday_1",
        "tirsdag": "weekday_2",
        "onsdag": "weekday_3",
        "torsdag": "weekday_4",
        "fredag": "weekday_5",
        "lørdag": "weekday_6",
        "søndag": "weekday_7",
    },
    "sl": {
        "januar": "month_1",
        "februar": "month_2",
        "marec": "month_3",
        "april": "month_4",
        "maj": "month_5",
        "junij": "month_6",
        "julij": "month_7",
        "avgust": "month_8",
        "september": "month_9",
        "oktober": "month_10",
        "november": "month_11",
        "december": "month_12",
        "ponedeljek": "weekday_1",
        "torek": "weekday_2",
        "sreda": "weekday_3",
        "četrtek": "weekday_4",
        "petek": "weekday_5",
        "sobota": "weekday_6",
        "nedelja": "weekday_7",
    },
    "ceb": {
        "enero": "month_1",
        "pebrero": "month_2",
        "marso": "month_3",
        "abril": "month_4",
        "mayo": "month_5",
        "hunyo": "month_6",
        "hulyo": "month_7",
        "agosto": "month_8",
        "septiyembre": "month_9",
        "oktubre": "month_10",
        "nobyembre": "month_11",
        "disyembre": "month_12",
        "lunes": "weekday_1",
        "martes": "weekday_2",
        "miyerkoles": "weekday_3",
        "huwebes": "weekday_4",
        "biyernes": "weekday_5",
        "sabado": "weekday_6",
        "domingo": "weekday_7",
    },
    "tl": {
        "enero": "month_1",
        "pebrero": "month_2",
        "marso": "month_3",
        "abril": "month_4",
        "mayo": "month_5",
        "hunyo": "month_6",
        "hulyo": "month_7",
        "agosto": "month_8",
        "setyembre": "month_9",
        "oktubre": "month_10",
        "nobyembre": "month_11",
        "disyembre": "month_12",
        "lunes": "weekday_1",
        "martes": "weekday_2",
        "miyerkules": "weekday_3",
        "huwebes": "weekday_4",
        "biyernes": "weekday_5",
        "sabado": "weekday_6",
        "linggo": "weekday_7",
        # The spellings the LIVE tl deck uses, alongside the Wiktionary ones. The
        # deck is what the user studies, so its spellings have to route too.
        "myerkules": "weekday_3",
        "hwebes": "weekday_4",
        "byernes": "weekday_5",
    },
}

_WEEK_START = {"no": "monday", "sl": "monday", "ceb": "sunday", "tl": "sunday"}

#: ORACLE B2's two orders, transcribed: ISO 1 = Monday … 7 = Sunday, so a
#: Sunday-first week opens with 7. Nothing here reads an order off the renderer.
_ORDER = {"monday": (1, 2, 3, 4, 5, 6, 7), "sunday": (7, 1, 2, 3, 4, 5, 6)}

_CODES = sorted(_WORDS)
_PAIRS = [(code, word) for code in _CODES for word in sorted(_WORDS[code])]
_CONCEPTS = tuple(f"month_{n}" for n in range(1, 13)) + tuple(f"weekday_{d}" for d in range(1, 8))
_MONTH_CONCEPTS = tuple(c for c in _CONCEPTS if c.startswith("month"))
_WEEKDAY_CONCEPTS = tuple(c for c in _CONCEPTS if c.startswith("weekday"))

#: The concept's English name, for glosses. Derived from the same tuples the
#: concept ids are numbered off, so a reordering cannot desynchronise the two.
_ENGLISH: dict[str, str] = {
    **{
        f"month_{n}": name
        for n, name in enumerate(
            (
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
            ),
            start=1,
        )
    },
    **{
        f"weekday_{d}": name
        for d, name in enumerate(
            ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"), start=1
        )
    },
}

_LIT = "#2f5d9e"  # number_image._DOT_FILL
_WEEKEND = "#e2b79c"  # calendar_scenes.WEEKEND_FILL
_CELL_RX = "5"  # a cell's corner radius: the page rounds at 10, the band at 9
_WEEKEND_ISO = frozenset({6, 7})  # Saturday, Sunday


def _word_for(code: str, concept: str) -> str:
    """A word of *code*'s that maps to *concept* — the Pimsleur one where there
    are two, since two spellings of a day are the same card to a learner."""
    return next(w for w, c in sorted(_WORDS[code].items()) if c == concept)


# ── Reading the picture back ────────────────────────────────────────────────


def _cells(picture: NumberPicture) -> list[tuple[float, float, str]]:
    """The cells in reading order as ``(centre_x, centre_y, fill)``."""
    root = ET.fromstring(picture.svg.decode())
    boxes = [
        (
            float(el.get("x")) + float(el.get("width")) / 2,
            float(el.get("y")) + float(el.get("height")) / 2,
            el.get("fill"),
        )
        for el in root.iter()
        if el.tag == f"{_SVG}rect" and el.get("rx") == _CELL_RX
    ]
    return sorted(boxes, key=lambda c: (c[1], c[0]))


def _texts(picture: NumberPicture) -> list[str]:
    root = ET.fromstring(picture.svg.decode())
    return [(el.text or "") for el in root.iter() if el.tag == f"{_SVG}text"]


def _lit_indices(picture: NumberPicture) -> list[int]:
    return [i for i, (_, _, fill) in enumerate(_cells(picture)) if fill == _LIT]


def _tinted(picture: NumberPicture) -> set[int]:
    return {i for i, (_, _, fill) in enumerate(_cells(picture)) if fill == _WEEKEND}


def _drawn(code: str, word: str) -> NumberPicture:
    """The picture a card for this word would get, glossed correctly."""
    concept = _WORDS[code][word]
    picture = calendar_picture(word, code, _ENGLISH[concept])
    assert picture is not None, (code, word)
    return picture


def _use_table(monkeypatch: pytest.MonkeyPatch, tmp_path: Any, data: dict[str, Any]) -> None:
    """Point the loader at a hand-written table, so a probe can corrupt it."""
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(module, "get_calendar_path", lambda code: path)
    module.load_calendar.cache_clear()


@pytest.fixture(autouse=True)
def _clear_the_table_cache() -> Any:
    """The loader is ``@cache``d, so a probe that repoints it has to clear it or
    it would be reading the real file and passing for the wrong reason."""
    module.load_calendar.cache_clear()
    yield
    module.load_calendar.cache_clear()


# ── The word tables, against the brief ──────────────────────────────────────


@pytest.mark.parametrize("code", _CODES)
def test_the_word_table_is_exactly_the_briefs_list(code: str) -> None:
    """ORACLE B2, pinned WHOLE. Whole-dict equality rather than containment, so
    a word DROPPED from a file is a failure and not a silent gap: a deck that
    lost `lørdag` would otherwise pass everything else in this file."""
    assert load_calendar(code).words == _WORDS[code]


@pytest.mark.parametrize("code", _CODES)
def test_the_week_start_is_the_one_the_brief_names(code: str) -> None:
    assert load_calendar(code).week_start == _WEEK_START[code]


@pytest.mark.parametrize("code", _CODES)
def test_every_concept_is_reachable_in_every_language(code: str) -> None:
    """ORACLE B2's invariant. All nineteen, at least once: a concept no word
    reaches is a picture nothing can be asked for, and it would go unnoticed
    because the other eighteen still work."""
    assert set(_WORDS[code].values()) == set(_CONCEPTS)


@pytest.mark.parametrize("code", _CODES)
def test_no_word_maps_to_a_concept_the_renderer_cannot_draw(code: str) -> None:
    assert set(load_calendar(code).words.values()) <= set(_CONCEPTS)


def test_the_four_decks_cover_thirty_six_months_and_thirty_two_days() -> None:
    """The tables as a whole, which is where a copy-paste between two files
    would show: tl's `sabado` and ceb's `sabado` are the same six letters and
    would be one typo away from a shared list."""
    months = [c for words in _WORDS.values() for c in words.values() if c.startswith("month")]
    days = [c for words in _WORDS.values() for c in words.values() if c.startswith("weekday")]
    assert len(months) == 48, "twelve months in each of four decks"
    assert len(days) == 31, "twenty-eight days, plus the tl deck's three extra spellings"
    # Every day is listed once per deck; only tl's three alternative spellings
    # make a day appear twice, so exactly three days are listed five times.
    per_day = Counter(days)
    assert set(per_day) == set(_WEEKDAY_CONCEPTS)
    assert {d for d, n in per_day.items() if n == 5} == {"weekday_3", "weekday_4", "weekday_5"}
    assert {n for n in per_day.values()} == {4, 5}


# ── The word → concept route ────────────────────────────────────────────────


@pytest.mark.parametrize(("code", "word"), _PAIRS)
def test_every_word_is_recognised_stripped_and_casefolded(code: str, word: str) -> None:
    """The router is handed whatever is in the card field, so lookup has to
    survive surrounding whitespace and any casing — and the picture it then
    returns must be the one its own file maps that word to."""
    concept = _WORDS[code][word]
    assert is_calendar_word(word, code)
    assert is_calendar_word(word.upper(), code)
    assert is_calendar_word(f"  {word}  ", code)
    for spelling in (word, word.upper(), f"  {word}  "):
        picture = calendar_picture(spelling, code, _ENGLISH[concept])
        assert picture is not None, (code, spelling)
        assert picture.filename.startswith(f"calendar_{concept}_{_WEEK_START[code]}_"), (code, spelling)


@pytest.mark.parametrize("code", _CODES)
def test_a_word_that_is_not_in_the_file_draws_nothing(code: str) -> None:
    assert not is_calendar_word("blurple", code)
    assert calendar_picture("blurple", code, "Blue") is None


def test_a_multiword_text_draws_nothing() -> None:
    """ORACLE B3. The word is a whole lexical item, and a phrase is a different
    card with a different meaning: a substring match would give the Tagalog
    phrase `sa susunod na Myerkules` a Wednesday page."""
    assert not is_calendar_word("sa susunod na Myerkules", "tl")
    assert calendar_picture("sa susunod na Myerkules", "tl", "next Wednesday") is None


def test_a_trailing_space_does_not_become_its_own_word() -> None:
    """The other half of stripping, which a substring match would get wrong in
    the other direction: a card whose field has a trailing space is the same
    card, and must not be a MISS."""
    assert "mandag" in load_calendar("no").words
    assert "mandag " not in load_calendar("no").words
    assert is_calendar_word("mandag ", "no")
    assert is_calendar_word("mandag", "no") == is_calendar_word("mandag ", "no")
    assert calendar_picture("mandag ", "no", "Monday") == calendar_picture("mandag", "no", "Monday")


# ── The pictures, read off the render ───────────────────────────────────────


@pytest.mark.parametrize(("code", "concept"), [(c, m) for c in _CODES for m in _MONTH_CONCEPTS])
def test_a_month_page_lights_its_own_cell_and_says_only_its_number(code: str, concept: str) -> None:
    """ORACLE B1.1 through the picture layer: twelve cells in four columns and
    three rows, exactly one lit at reading-order position n-1, carrying n as the
    only text on the page."""
    n = int(concept.removeprefix("month_"))
    picture = _drawn(code, _word_for(code, concept))
    cells = _cells(picture)
    assert len(cells) == 12
    assert len({c[0] for c in cells}) == 4, "four columns"
    assert len({c[1] for c in cells}) == 3, "three rows"
    assert _lit_indices(picture) == [n - 1]
    assert _texts(picture) == [str(n)]


@pytest.mark.parametrize(("code", "day"), [(c, d) for c in _CODES for d in range(1, 8)])
def test_a_weekday_page_lights_its_own_day_in_THIS_DECKS_week_order(code: str, day: int) -> None:
    """ORACLE B1.2, which is the whole reason `week_start` is data: seven cells
    in one row, exactly one lit, at the position THIS language's week puts the
    day in, and no text at all. The expected index is the brief's order, never
    the renderer's."""
    picture = _drawn(code, _word_for(code, f"weekday_{day}"))
    cells = _cells(picture)
    assert len(cells) == 7
    assert len({c[1] for c in cells}) == 1, "one row"
    assert [c[0] for c in cells] == sorted(c[0] for c in cells), "left to right"
    assert _lit_indices(picture) == [_ORDER[_WEEK_START[code]].index(day)], (code, day)
    assert _texts(picture) == [], "a day is read by position, never by reading it"


@pytest.mark.parametrize(("code", "day"), [(c, d) for c in _CODES for d in range(1, 8)])
def test_the_weekend_days_are_tinted_and_nothing_else_is(code: str, day: int) -> None:
    """Saturday and Sunday carry the tint and every other unlit cell does not.
    The lit day is exempt, which is the case a Sunday-first deck hits on two of
    its seven cards, so it is checked on its own rather than assumed."""
    picture = _drawn(code, _word_for(code, f"weekday_{day}"))
    order = _ORDER[_WEEK_START[code]]
    expected = {i for i, iso in enumerate(order) if iso in _WEEKEND_ISO and iso != day}
    assert _tinted(picture) == expected, (code, day)


def test_the_two_week_starts_disagree_on_every_day() -> None:
    """The property that makes `week_start` load-bearing, stated as a shape: for
    each day, the Monday-first deck and the Sunday-first deck light different
    cells, so getting the order wrong cannot pass by coincidence."""
    for day in range(1, 8):
        monday = _lit_indices(_drawn("no", _word_for("no", f"weekday_{day}")))
        sunday = _lit_indices(_drawn("ceb", _word_for("ceb", f"weekday_{day}")))
        assert monday == [_ORDER["monday"].index(day)]
        assert sunday == [_ORDER["sunday"].index(day)]
        assert monday != sunday, day


def test_one_filename_means_exactly_one_picture() -> None:
    """The card-writing collision, in the direction that is actually a defect: a
    name is content-addressed, so two cards may share a file ONLY when their
    renders are byte-identical. Norwegian `april` and Slovene `april` do share
    one, and must — the pages are the same bytes. A Norwegian Monday and a
    Cebuano Monday must NOT, because `monday` and `sunday` draw different pages
    for the same `weekday_1`; that is why the week start is in the name, and this
    is the check that would notice it leaving."""
    by_name: dict[str, list[tuple[str, bytes]]] = {}
    for code, word in _PAIRS:
        picture = _drawn(code, word)
        by_name.setdefault(picture.filename, []).append((f"{code}/{word}", picture.svg))
    shared = {name: cards for name, cards in by_name.items() if len(cards) > 1}
    assert shared, "the cross-language months really do share files"
    for name, cards in shared.items():
        first = cards[0][1]
        for label, svg in cards[1:]:
            assert svg == first, f"{name} is holding two different pages ({label})"
    # And the property is not vacuous: the seven days are NOT all one file.
    mondays = {_drawn("no", "mandag").filename, _drawn("ceb", "lunes").filename}
    assert len(mondays) == 2, "same concept, different week, different file"


def test_the_filename_carries_the_concept_the_week_start_and_the_content() -> None:
    picture = calendar_picture("mandag", "no", "Monday")
    assert picture is not None
    digest = hashlib.sha256(picture.svg).hexdigest()[:8]
    assert picture.filename == f"calendar_weekday_1_monday_{digest}.svg"
    cebuano = calendar_picture("lunes", "ceb", "Monday")
    assert cebuano is not None
    assert cebuano.filename.startswith("calendar_weekday_1_sunday_")
    assert cebuano.filename != picture.filename, "same concept, different week, different page"


def test_the_picture_is_a_drawn_picture_and_is_deterministic() -> None:
    """The return type is the contract every caller in `drawn_picture` relies on,
    and determinism is what makes the content-hash in the filename a cache key
    rather than a fresh file on every add."""
    picture = calendar_picture("januar", "no", "January")
    assert isinstance(picture, NumberPicture)
    assert picture.svg.startswith(b"<svg")
    assert picture.svg == calendar_picture("januar", "no", "January").svg


# ── The gloss guard, on the cases that would otherwise ship a wrong page ────


def test_a_word_glossed_with_the_wrong_sense_draws_nothing() -> None:
    """ORACLE B3's headline case. Norwegian `mars` is the third month and also
    the planet and the deck holds both, and the WORD cannot say which — so a
    card glossed "Mars" takes the route it was already on rather than a March
    page with the wrong sense on it."""
    assert is_calendar_word("mars", "no"), "the word is listed either way"
    assert calendar_picture("mars", "no", "Mars") is None
    assert calendar_picture("mars", "no", "the planet Mars") is None
    assert calendar_picture("marso", "tl", "Mars") is None
    assert calendar_picture("marec", "sl", "Mars") is None
    assert calendar_picture("marso", "ceb", "Mars") is None


def test_a_homograph_week_and_sunday_is_told_apart_by_its_gloss() -> None:
    """ORACLE B3's other case, and the one specific to this family: Tagalog
    `linggo` is BOTH "week" and "Sunday" — the collision `app/languages.py`
    records in the tl notetype config for two of the Pimsleur notes. Keyed on
    the word alone, the card for the week would get a Sunday page."""
    assert calendar_picture("linggo", "tl", "week") is None
    picture = calendar_picture("linggo", "tl", "Sunday")
    assert picture is not None
    assert picture.filename.startswith("calendar_weekday_7_sunday_")
    assert _lit_indices(picture) == [0], "and it is a Sunday-first deck's Sunday"


def test_an_empty_gloss_draws_nothing() -> None:
    """A picture that cannot be confirmed is not drawn, the same veto the pronoun
    family holds: the guard is a refusal, and a blank gloss is not a
    confirmation."""
    assert calendar_picture("mandag", "no", "") is None
    assert calendar_picture("mandag", "no", "   ") is None
    assert calendar_picture("januar", "no", "") is None


def test_the_gloss_matches_a_whole_word_only() -> None:
    """`mars` inside `marseille` must not confirm March: the check is tokenised,
    not a substring search, which is the whole reason the regex splits on
    non-letters."""
    assert not gloss_matches("month_3", "marseille")
    assert not gloss_matches("weekday_1", "mondayish")
    assert not gloss_matches("month_1", "januar-dictated")
    assert gloss_matches("month_3", "in March")
    assert gloss_matches("weekday_1", "MONDAY")
    assert gloss_matches("weekday_7", "on Sunday evening")


def test_a_gloss_naming_a_different_month_is_refused() -> None:
    """A card whose gloss says April draws nothing rather than a January page:
    the guard is a veto, so a mismatch is a refusal and not a best guess."""
    assert calendar_picture("januar", "no", "April") is None
    assert calendar_picture("januar", "no", "April and May") is None
    assert calendar_picture("desember", "no", "November") is None


def test_a_concept_with_no_keyword_is_a_refusal_not_a_crash() -> None:
    """The keyword table is the other half of the guard, and a concept missing
    from it is a picture nothing can confirm. A ``KeyError`` is a loud refusal at
    the point of use; a silent `False` would be a card quietly off the picture
    route with nothing to say why."""
    with pytest.raises(KeyError):
        gloss_matches("month_13", "January")
    with pytest.raises(KeyError):
        gloss_matches("weekday_0", "Monday")


def test_the_tl_alternative_spellings_route_to_their_own_days() -> None:
    """The live deck spells three days differently from Wiktionary, and it is the
    deck the user studies, so both spellings have to route — to the SAME picture,
    because two spellings of one day are one card to a learner."""
    for word, concept in (("myerkules", "weekday_3"), ("hwebes", "weekday_4"), ("byernes", "weekday_5")):
        drawn = calendar_picture(word, "tl", _ENGLISH[concept])
        standard = calendar_picture(_word_for("tl", concept), "tl", _ENGLISH[concept])
        assert drawn is not None and standard is not None, word
        assert drawn.filename == standard.filename, f"{word} is the same day as {concept}"
        assert drawn.filename.startswith(f"calendar_{concept}_sunday_")


# ── ORACLE B2's invariants, and the guards that keep the families apart ─────


@pytest.mark.parametrize("code", _CODES)
def test_no_calendar_word_is_drawn_by_another_picture_family(code: str) -> None:
    """All four families draw, and a word in two tables would be routed by
    dispatch order — so the later family's picture would be unreachable with
    nothing anywhere saying so. Asked of the sibling tables, by their own public
    lookups, so the claim is about the DATA rather than about the dispatch."""
    from app.cards.number_image import load_number_config
    from app.cards.pronoun_picture import is_pronoun_word
    from app.cards.spatial_picture import is_spatial_word

    numbers = load_number_config(code).values
    for word in _WORDS[code]:
        assert word not in numbers, (code, word)
        assert not is_spatial_word(word, code), (code, word)
        assert not is_pronoun_word(word, code), (code, word)


@pytest.mark.parametrize("code", _CODES)
def test_no_calendar_word_is_in_the_function_word_include_list(code: str) -> None:
    """ORACLE B2's last invariant, and the one that makes the picture reachable
    at all. The include list is the signal the closed-class router consults, so a
    word there is a function word on every add path and its picture is never
    drawn — the listing would be dead data, which is exactly what the pronoun
    suite found in Cebuano's list."""
    path = get_function_words_path(code)
    assert path is not None and path.exists(), "every calendar deck curates its function words"
    include = {w.casefold() for w in json.loads(path.read_text(encoding="utf-8")).get("include", [])}
    assert include & set(_WORDS[code]) == set(), (code, include & set(_WORDS[code]))


# ── The probes: each guard, shown to reject a corrupted real table ──────────


def test_a_language_registering_no_calendar_file_gets_nothing() -> None:
    """The ``None`` path, which every registered language but these four takes. A
    picture that cannot be confirmed is not drawn, and "this language has no
    calendar table" is the extreme case of that."""
    assert get_calendar_path("en") is None
    assert load_calendar("en").words == {}
    assert load_calendar("en").week_start == "monday"
    assert not is_calendar_word("januar", "en")
    assert calendar_picture("januar", "en", "January") is None


def test_a_registered_path_that_is_not_there_reads_as_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """The file moved or the plugin lost it: an unreadable table is an empty one,
    not a crash on a card add. The other branch of the same guard as the ``None``
    path above, which is why it is tested separately."""
    monkeypatch.setattr(module, "get_calendar_path", lambda code: tmp_path / "gone.json")
    module.load_calendar.cache_clear()
    assert module.load_calendar("no").words == {}
    assert module.calendar_picture("mandag", "no", "Monday") is None


def test_a_word_dropped_from_the_file_stops_reaching_a_picture(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """The probe for the table pin: a file with one day removed, showing that the
    equality assertion above is what refuses it. Without the check, a lost
    `lørdag` would be invisible — every other word still routes."""
    data = {"week_start": "monday", "words": {k: v for k, v in _WORDS["no"].items() if k != "lørdag"}}
    assert data["words"] != _WORDS["no"], "the probe is a real near-miss"
    _use_table(monkeypatch, tmp_path, data)
    assert not module.is_calendar_word("lørdag", "no")
    assert module.calendar_picture("lørdag", "no", "Saturday") is None
    assert module.calendar_picture("søndag", "no", "Sunday") is not None, "the rest still work"


def test_a_concept_pasted_into_the_wrong_word_shows_in_the_picture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A copy-paste in a data file, and the probe that catches it from the RENDER
    side rather than from the table: `fredag` mapped to Thursday draws Thursday's
    page, and the lit cell sits in position 4 while the file is named
    `weekday_4`. The gloss guard is what keeps the mistake off a real card — one
    glossed "Friday" is refused — so the whole-dict pin and the guard cover the
    failure from two sides."""
    _use_table(
        monkeypatch,
        tmp_path,
        {"week_start": "monday", "words": {"torsdag": "weekday_4", "fredag": "weekday_4"}},
    )
    thursday = module.calendar_picture("torsdag", "no", "Thursday")
    assert thursday is not None
    assert thursday.filename == "calendar_weekday_4_monday_1d6b7e83.svg"
    assert _lit_indices(thursday) == [_ORDER["monday"].index(4)] == [3]
    # `fredag` glossed "Thursday" now draws the same page — a user reading Friday
    # would see Thursday's cell lit. The word is genuinely listed, so the guard
    # that has to catch this is the file pin above, not the router.
    assert module.is_calendar_word("fredag", "no")
    assert module.calendar_picture("fredag", "no", "Friday") is None, (
        "and the gloss guard refuses the card that would expose the mistake"
    )


def test_a_week_start_the_renderer_cannot_lay_out_is_refused_at_the_point_of_use(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The value is data, and data can be wrong. A file claiming a week start the
    renderer does not know is refused rather than drawn Monday-first, which
    would be a confidently wrong page for every card in the deck."""
    _use_table(monkeypatch, tmp_path, {"week_start": "tuesday", "words": {"mandag": "weekday_1"}})
    with pytest.raises(ValueError, match="week start"):
        module.calendar_picture("mandag", "no", "Monday")


def test_the_week_start_actually_reaches_the_render(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """A week start that is read but then ignored would leave every test above
    passing, because the two decks happen to differ in their word lists. This
    takes ONE word, draws it under each order, and requires the lit cell to
    move — the same word, the same gloss, two files."""
    _use_table(monkeypatch, tmp_path, {"week_start": "monday", "words": {"x": "weekday_1"}})
    as_monday = module.calendar_picture("x", "no", "Monday")
    _use_table(monkeypatch, tmp_path, {"week_start": "sunday", "words": {"x": "weekday_1"}})
    as_sunday = module.calendar_picture("x", "no", "Monday")
    assert as_monday is not None and as_sunday is not None
    assert _lit_indices(as_monday) == [0] and _lit_indices(as_sunday) == [1]
    assert as_monday.filename.startswith("calendar_weekday_1_monday_")
    assert as_sunday.filename.startswith("calendar_weekday_1_sunday_")


def test_a_non_string_concept_in_the_data_is_coerced_and_then_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The likeliest mistake in a hand-edited JSON file: a number written where a
    concept belongs. It is coerced to text by the loader — so the lookup does not
    crash — and then refused, because no such concept has a keyword. Two
    different guards, and the probe shows which one is actually doing the work."""
    _use_table(monkeypatch, tmp_path, {"week_start": "monday", "words": {"mandag": 1}})
    assert load_calendar("no").words == {"mandag": "1"}
    with pytest.raises(KeyError):
        module.calendar_picture("mandag", "no", "Monday")


def test_an_unlisted_weekday_falls_through_to_no_picture(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """A table that lost its Sunday: seven-day vocab on a six-day page, with the
    other six days still routing perfectly. The probe that says the file pin is
    what catches it, since no render of the six remaining is wrong."""
    words = {k: v for k, v in _WORDS["no"].items() if not v.startswith("weekday_7")}
    _use_table(monkeypatch, tmp_path, {"week_start": "monday", "words": words})
    assert module.calendar_picture("søndag", "no", "Sunday") is None
    assert module.calendar_picture("lørdag", "no", "Saturday") is not None
