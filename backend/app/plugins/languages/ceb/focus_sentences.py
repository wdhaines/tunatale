"""Cebuano object focus: the sentences stage 6 is taught with (tunatale-ve4p.32).

The earlier patterns teach one choice, whether the thing has begun. gi- / -on
keep that split and add a second one: which noun is the ``ang`` noun. It is
Tagalog's focus system (*Kinain niya ang isda*), spelled the Cebuano way::

    Mikaon siya og pan.     He ate bread.          the doer is the ang noun
    Gikaon niya ang pan.    He ate the bread.      the thing is the ang noun

Three things move together: the affix, the doer (siya -> niya; ``ko`` is both
"I" and "by me", so it stays) and og -> ang. English shows the choice only in
the object's article, so a lone verb cannot be asked for and every cell is a
whole sentence.

The sentences are built by rule from one hand-checked row per root
(``data/focus_rows.tsv``); nothing here asks a model. What the narrator
stresses and what a card puts in bold is the focus: the doer in actor focus,
the thing in object focus (the owner, 2026-10-15), returned as spans so the
renderer and the card writer mark the same words.

Design: .beads-tasks/briefs/design-ve4p-stage6-object-focus-2026-10.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

_DATA = Path(__file__).parent / "data"
FOCUS_ROWS_PATH = _DATA / "focus_rows.tsv"
FOCUS_COUNTS_PATH = _DATA / "cebuano_focus_counts.tsv"

# A form whose news count also counts another word, and that word.
HOMOGRAPHS = {"kan-on": "cooked rice"}

_ACTOR, _OBJECT = "og", "ang"
_COLUMNS = 8
_ARTICLES = ("a ", "an ")


@dataclass(frozen=True)
class Doer:
    actor: str  # the doer as the ang noun: ko, siya
    agent: str  # the doer when the thing is the ang noun: ko, niya
    english: str


DOERS = (Doer("ko", "ko", "I"), Doer("siya", "niya", "he"))


@dataclass(frozen=True)
class FocusObject:
    noun: str
    english: str  # when it is not the one meant: "water", "a house"

    @property
    def the_english(self) -> str:
        """The object when it is the one meant: "the water", "the house"."""
        bare = next((self.english[len(a) :] for a in _ARTICLES if self.english.startswith(a)), self.english)
        return f"the {bare}"


@dataclass(frozen=True)
class FocusRow:
    root: str
    english: str  # "drink"
    past: str  # "drank"
    forms: tuple[str, str, str, str]  # mo-, mi-, -on, gi-: actor then object, not begun then begun
    objects: tuple[FocusObject, ...]


@dataclass(frozen=True)
class FocusSentence:
    text: str
    english: str
    stressed: tuple[int, int]  # the focus, as a span of ``english``
    bold: tuple[tuple[int, int], ...]  # the affixed verb and the focus, as spans of ``text``


def focus_sentence(row: FocusRow, cell: int, doer: Doer, obj: FocusObject) -> FocusSentence:
    """*row*'s sentence for *cell* (0 mo-, 1 mi-, 2 -on, 3 gi-) with *doer* doing it to *obj*."""
    if not 0 <= cell < len(row.forms):
        raise IndexError(f"{row.root} has cells 0-{len(row.forms) - 1}, not {cell}")
    on_thing, begun = cell >= 2, cell % 2 == 1
    verb = row.forms[cell].capitalize()
    subject = doer.english.capitalize()
    said = subject if begun else f"{subject}'ll"
    english_verb = row.past if begun else row.english
    if on_thing:
        focus = f"{_OBJECT} {obj.noun}"
        text = f"{verb} {doer.agent} {focus}."
        english = f"{said} {english_verb} {obj.the_english}."
        stressed = (len(english) - 1 - len(obj.the_english), len(english) - 1)
    else:
        focus = doer.actor
        text = f"{verb} {focus} {_ACTOR} {obj.noun}."
        english = f"{said} {english_verb} {obj.english}."
        stressed = (0, len(said))
    start = text.index(f" {focus}", len(verb)) + 1
    return FocusSentence(text, english, stressed, ((0, len(verb)), (start, start + len(focus))))


def _object(spec: str) -> FocusObject:
    noun, sep, english = spec.strip().partition("=")
    if not sep or not noun or not english:
        raise ValueError(f"{spec!r} is not noun=English")
    return FocusObject(noun, english)


@cache
def load_focus_rows(path: Path) -> dict[str, FocusRow]:
    """``root -> FocusRow`` from *path*, read once per process.

    A row with the wrong number of columns, or an object without its English,
    raises: a half-written row must not become a half-worded drill.
    """
    rows: dict[str, FocusRow] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line or line.startswith("#"):
            continue
        cells = line.split("\t")
        try:
            if len(cells) != _COLUMNS:
                raise ValueError(f"needs {_COLUMNS} columns, has {len(cells)}")
            root, english, past, mo, mi, on, gi, objects = cells
            rows[root] = FocusRow(root, english, past, (mo, mi, on, gi), tuple(map(_object, objects.split(";"))))
        except ValueError as error:
            raise ValueError(f"{path.name}:{number}: {error}") from error
    return rows


@cache
def load_focus_counts(path: Path) -> dict[str, int]:
    """``form -> occurrences in native news`` from *path*, read once per process."""
    counts: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            form, count = line.split("\t")
            counts[form] = int(count)
    return counts


def focus_row(root: str) -> FocusRow | None:
    """The checked row for *root*, or ``None`` when nobody has written one."""
    return load_focus_rows(FOCUS_ROWS_PATH).get(root.casefold())


def focus_roots() -> tuple[str, ...]:
    """Every root with a checked row, in the table's order."""
    return tuple(load_focus_rows(FOCUS_ROWS_PATH))


def irregular_forms() -> dict[tuple[str, str], str]:
    """``(surface, root) -> affix`` for the forms no rule spells: the -on forms that drop a vowel."""
    return {
        (row.forms[2].casefold().replace("-", ""), row.root.replace("-", "")): "on"
        for row in load_focus_rows(FOCUS_ROWS_PATH).values()
    }
