"""Cebuano affix patterns: the aspect pairs a root can be drilled in (tunatale-ve4p.2).

A pattern is a pair of affixes taught as one contrast on a single root:
``molakaw`` / ``milakaw``, ``mag-ampo`` / ``nag-ampo``, ``matulog`` /
``natulog``. Attaching a prefix is regular in Cebuano; what is NOT predictable
is which pairs a root takes. ``tulog`` is stative and never takes mo-;
``anhi`` never takes mag-.

Neither table already in this plugin can answer that. The lemma table lists
every root with every affix, because Wiktionary's conjugation templates are
generated; the frequency table is keyed by root. So this module ships its own
small table, ``data/cebuano_affix_counts.tsv.gz``: how often native news uses
each root with each pattern affix (``scripts/build_cebuano_affix_counts.py``).
A pair is offered only when BOTH forms clear :data:`VOUCH_FLOOR`.

The floor was chosen by measurement (2026-10-07, 9.48 M tokens): at 20, 21 of
the 22 root+affix forms the stored lessons use are vouched for, and the one
that is not, ``mihilom``, occurs 3 times in the corpus. At 50 five real lesson
forms are lost (``maglakaw`` 49, ``mianhi`` 40, ``mitan-aw`` 43).
"""

from __future__ import annotations

import gzip
from functools import cache
from pathlib import Path

from app.plugins.languages.ceb.focus_sentences import (
    DOERS,
    focus_roots,
    focus_row,
    focus_sentence,
)
from app.srs.a1_morphology import AffixPattern, PatternCard, PatternSentences

COUNTS_PATH = Path(__file__).parent / "data" / "cebuano_affix_counts.tsv.gz"
GLOSSES_PATH = Path(__file__).parent / "data" / "affix_glosses.tsv"
VOUCH_FLOOR = 20
_VOWELS = "aeiou"

# The two forms of a pair are not a future and a past (tunatale-ve4p.25). Wolff's
# course calls them UNREAL and REAL (Beginning Cebuano, lesson 4B): what has not
# begun, and what has begun or is done. The labels go over the page's columns;
# the notes are said by the narrator the first time a learner meets the pair.
_CELLS = ("not begun", "begun")

# In teaching order, which the owner approved on 2026-10-14 (tunatale-ve4p.26):
# the volitional, then the durative, then the states as review, then the
# potential. Later stages (the passives) join the end as their rows are checked.
PATTERNS: tuple[AffixPattern, ...] = (
    AffixPattern(
        "mo-mi",
        ("verb:mo", "verb:mi"),
        labels=_CELLS,
        notes=(
            "The first is for something that has not begun yet, or something you usually do.",
            "The second is for something that has begun, or is already done.",
        ),
    ),
    AffixPattern(
        "mag-nag",
        ("verb:mag", "verb:nag"),
        labels=_CELLS,
        notes=(
            "The first is for something that has not begun yet, and will go on for a while.",
            "The second is for something going on, now or before.",
        ),
    ),
    AffixPattern(
        "ma-na",
        ("verb:ma", "verb:na"),
        labels=_CELLS,
        notes=(
            "The first is for a state that has not come about yet.",
            "The second is for a state that has come about.",
        ),
        # Already the owner's from Tagalog: reviewed, not taught (tunatale-ve4p.26).
        review_only=True,
    ),
    # The potential (Wolff, maka-: "can", and "had the chance or managed to").
    # An A1 affix too since 2026-10-23 (the owner, tunatale-bi67): a lesson's
    # makakita gets a grammar hint and the Inflect button (``a1_morphology``).
    AffixPattern(
        "maka-naka",
        ("verb:maka", "verb:naka"),
        labels=_CELLS,
        notes=(
            "The first is for something you can do, or will get the chance to do.",
            "The second is for something you managed to do, or got the chance to do.",
        ),
    ),
    AffixPattern(
        "on-gi",
        ("verb:on", "verb:gi"),
        labels=_CELLS,
        notes=(
            "When it is the thing you mean, the verb takes -on or gi-, and the thing takes ang.",
            "Gi- is for what has begun. -on is for what has not begun yet.",
        ),
        sentences=True,
        spelled=("-on", "gi-"),
        # The focus card's order (tunatale-ve4p.9): the first two, one object
        # focus and one actor focus, already test the choice.
        card_features=("verb:gi", "verb:mi", "verb:on", "verb:mo"),
        # The whole drill ran 10:47, "too long for the end of a lesson" (the
        # owner, 2026-10-15), so it is split by tense: what has begun (mi-,
        # gi-), then what has not (mo-, -on). Each half still turns on the
        # focus, with only "the" to tell its two sentences apart.
        halves=((1, 3), (0, 2)),
        half_notes=(
            ("When it is the thing you mean, the verb takes gi-, and the thing takes ang.",),
            ("For what has not begun yet, the verb takes -on, and the thing still takes ang.",),
        ),
    ),
)
# What each cell of a focus row asks for, in ``focus_sentence``'s cell order.
_FOCUS_CELLS = ("verb:mo", "verb:mi", "verb:on", "verb:gi")

# Whole lines for the jobs of the unreal form that an English tense gets
# wrong, each ``(line, its English)`` with ``{form}`` the unreal form and
# ``{root}`` the root's English. Wolff's dictionary, under mu- and mag-:
# "Wala ku muadtu didtu, I did not go there"; "Mumata mi sa alas singku kada
# buntag, We wake up every morning at five"; "Waq mi maglisud, We did not have
# a hard time" (the course, lesson 4); and after a verb of wanting the form is
# the unreal one too (stage 4 of the curriculum, tunatale-ve4p.31). Under
# maka-: "Wà ku makasakay dáyun, I couldn't find transportation right away";
# "Dì ku makaalsa niánà, I cannot lift that". None for ma- / na-: the roots
# there are states, and "I didn't happy" is what the English frame would make
# of lipay.
_LINES: dict[str, tuple[tuple[str, str], ...]] = {
    **{
        key: (
            ("Wala ko {form}.", "I didn't {root}."),
            ("{Form} ko kada adlaw.", "I {root} every day."),
            ("Gusto ko {form}.", "I want to {root}."),
        )
        for key in ("mo-mi", "mag-nag")
    },
    "maka-naka": (("Wala ko {form}.", "I couldn't {root}."), ("Dili ko {form}.", "I can't {root}.")),
}
# How a pattern's unreal form has to be worded for the root's own English to
# stand in its lines, ``{verb}`` the root's English. An aspect pair's is "will"
# or, for a durative, "will be" and its -ing.
_UNREAL_ENGLISH: dict[str, tuple[str, ...]] = {"maka-naka": ("can {verb}",)}
_ASPECT_ENGLISH = ("will {verb}", "will be {verb}ing")
PATTERN_AFFIXES: tuple[str, ...] = tuple(
    f.removeprefix("verb:") for p in PATTERNS if not p.sentences for f in p.features
)


def spell(root: str, affix: str) -> str:
    """*affix* attached to *root*. A consonant-final prefix takes a hyphen before
    a vowel-initial root (``mag-ampo``); a vowel-final one does not (``moadto``)."""
    hyphen = "-" if affix[-1] not in _VOWELS and root[0] in _VOWELS else ""
    return f"{affix}{hyphen}{root}"


@cache
def load_affix_counts(path: Path) -> dict[tuple[str, str], int]:
    """``(root, affix) -> occurrences`` from *path*, read once per process."""
    counts: dict[tuple[str, str], int] = {}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line and not line.startswith("#"):
                root, affix, count = line.split("\t")
                counts[(root, affix)] = int(count)
    return counts


def pattern_forms(
    root: str, pattern: AffixPattern, *, counts: dict[tuple[str, str], int] | None = None
) -> tuple[str, ...] | None:
    """*root* spelled across *pattern*, or ``None`` unless native news uses every form."""
    if pattern.sentences:
        return None
    table = load_affix_counts(COUNTS_PATH) if counts is None else counts
    root = root.casefold()
    affixes = [feature.removeprefix("verb:") for feature in pattern.features]
    if any(table.get((root, affix), 0) < VOUCH_FLOOR for affix in affixes):
        return None
    return tuple(spell(root, affix) for affix in affixes)


@cache
def load_affix_glosses(path: Path) -> dict[tuple[str, str], tuple[str, tuple[str, ...]]]:
    """``(root, pattern key) -> (the root's English, the English of each form)`` from *path*.

    A row whose pattern is unknown, or that does not have exactly one English
    per form, raises: a half-written row must not become a half-worded drill.
    """
    cells = {pattern.key: len(pattern.features) for pattern in PATTERNS}
    glosses: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line or line.startswith("#"):
            continue
        root, key, root_english, *forms = line.split("\t")
        if len(forms) != cells.get(key):
            raise ValueError(
                f"{path.name}:{number}: {root} {key} needs {cells.get(key)} English forms, has {len(forms)}"
            )
        glosses[(root, key)] = (root_english, tuple(forms))
    return glosses


def pattern_glosses(root: str, pattern: AffixPattern) -> tuple[str, tuple[str, ...]] | None:
    """The hand-written English for *root* in *pattern*, or ``None`` when there is none."""
    if pattern.sentences:
        return None
    return load_affix_glosses(GLOSSES_PATH).get((root.casefold(), pattern.key))


def pattern_lines(root: str, pattern: AffixPattern) -> tuple[tuple[str, str], ...]:
    """The whole lines that show *root*'s unreal form after wala and as a habit, or none.

    None unless the unreal form is worded as "will" plus the root's own English,
    for a durative "will be" and its -ing form, and for the potential "can"
    (``_UNREAL_ENGLISH``): only then does the root's
    English stand in an English sentence as a verb meaning what the form means.
    ``mouban`` is "will go along" and ``uban`` is "accompany", so "I didn't
    accompany" would be a line about something else. An -ing that is not the
    verb plus "ing" (coming, sitting) loses the lines rather than gaining a
    wrong one.

    A sentence pattern has none: its cells are already whole lines.
    """
    if pattern.sentences:
        return ()
    forms, glosses = pattern_forms(root, pattern), pattern_glosses(root, pattern)
    if forms is None or glosses is None:
        return ()
    wordings = _UNREAL_ENGLISH.get(pattern.key, _ASPECT_ENGLISH)
    if glosses[1][0] not in [wording.format(verb=glosses[0]) for wording in wordings]:
        return ()
    form = forms[0]
    return tuple(
        (line.format(form=form, Form=form.capitalize()), english.format(root=glosses[0]))
        for line, english in _LINES.get(pattern.key, ())
    )


def pattern_sentences(root: str, pattern: AffixPattern) -> PatternSentences | None:
    """The checked sentences *root* can be drilled in, or ``None``.

    ``None`` unless *pattern* is taught as sentences and somebody has written
    the root's row. The doer alternates down the table so both are drilled;
    the object is the row's first. The first two cells are the actor forms
    the learner already has (``contrast``), said whole; the last two are the
    pattern's own.
    """
    if not pattern.sentences:
        return None
    row = focus_row(root)
    if row is None:
        return None
    roots = focus_roots()
    doer = DOERS[roots.index(row.root) % 2]
    obj = row.objects[0]
    cells = tuple(focus_sentence(row, i, doer, obj) for i in range(4))
    return PatternSentences(
        english=row.english,
        forms=row.forms,
        cells=tuple((cell.text, cell.english) for cell in cells),
        stressed=tuple(cell.stressed for cell in cells),
        contrast=2,
        regular=row.forms[2] == row.root + "on",
    )


def pattern_cards(root: str, pattern: AffixPattern) -> tuple[PatternCard, ...]:
    """*root*'s focus cards, one per cell in *pattern*'s card order, or none.

    The k-th card takes the row's k-th object round its list and the k-th doer
    round ``DOERS``, so a root's four cards do not all say one sentence.
    """
    row = focus_row(root) if pattern.sentences else None
    if row is None:
        return ()
    cards = []
    for k, feature in enumerate(pattern.card_features):
        cell = focus_sentence(
            row, _FOCUS_CELLS.index(feature), DOERS[k % len(DOERS)], row.objects[k % len(row.objects)]
        )
        cards.append(PatternCard(feature, cell.text, cell.english, cell.stressed, cell.bold))
    return tuple(cards)


def pattern_roots(pattern: AffixPattern) -> tuple[str, ...]:
    """The roots worded for *pattern*, in the gloss table's own order."""
    if pattern.sentences:
        return focus_roots()
    return tuple(root for root, key in load_affix_glosses(GLOSSES_PATH) if key == pattern.key)
