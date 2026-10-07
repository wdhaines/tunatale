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

from app.srs.a1_morphology import AffixPattern

COUNTS_PATH = Path(__file__).parent / "data" / "cebuano_affix_counts.tsv.gz"
GLOSSES_PATH = Path(__file__).parent / "data" / "affix_glosses.tsv"
VOUCH_FLOOR = 20
_VOWELS = "aeiou"

PATTERNS: tuple[AffixPattern, ...] = (
    AffixPattern("mo-mi", ("verb:mo", "verb:mi")),
    AffixPattern("mag-nag", ("verb:mag", "verb:nag")),
    AffixPattern("ma-na", ("verb:ma", "verb:na")),
)
PATTERN_AFFIXES: tuple[str, ...] = tuple(f.removeprefix("verb:") for p in PATTERNS for f in p.features)


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
    return load_affix_glosses(GLOSSES_PATH).get((root.casefold(), pattern.key))


def pattern_roots(pattern: AffixPattern) -> tuple[str, ...]:
    """The roots worded for *pattern*, in the gloss table's own order."""
    return tuple(root for root, key in load_affix_glosses(GLOSSES_PATH) if key == pattern.key)
