"""Actor-focus infinitive for a Tagalog verb-card front (tunatale-w4m7.6).

The Tagalog lemma table keys verbs by ROOT (``kain``); a TT vocab card fronts
the actor-focus infinitive (``kumain``) — what the learner actually sees. This
module maps a root to that infinitive at mint time.

The decision rule was MEASURED on 2026-09-23 against the contracted golden list
in ``tests/test_tagalog_lemma_table.py::DISPLAY`` (41 roots): 40/41 agreed, and
the one miss, ``kita``, is a homograph root (``makita`` "see" vs ``kumita``
"earn"), which is what the override file is for.
"""

from __future__ import annotations

import functools
from pathlib import Path

import wordfreq

_OVERRIDES_PATH = Path(__file__).parent / "data" / "verb_infinitive_overrides.tsv"
_VOWELS = frozenset("aeiou")
_Z_THRESHOLD = 3.0


def _um(root: str) -> str:
    """The ``-um-`` focus infinitive: ``um`` + root for vowel-initial roots,
    else the infix after the first consonant (``kain`` → ``kumain``)."""
    return "um" + root if root and root[0] in _VOWELS else root[0] + "um" + root[1:]


def _mag(root: str) -> str:
    """The ``mag-`` focus infinitive: ``mag-`` + root for vowel-initial roots
    (``aral`` → ``mag-aral``), else ``mag`` + root (``hanap`` → ``maghanap``)."""
    return "mag-" + root if root and root[0] in _VOWELS else "mag" + root


def _z(form: str) -> float:
    """Corpus Zipf frequency of the candidate form (wordfreq, Filipino)."""
    return wordfreq.zipf_frequency(form, "fil")


@functools.cache
def _overrides() -> dict[str, str]:
    """The hand override table (root → infinitive), loaded lazily.

    Why it exists: ``kita`` is a homograph root — ``makita`` "to see" vs
    ``kumita`` "to earn" both start from ``kita``, and no frequency rule can
    tell them apart, so the ambiguity is resolved by hand. TSV: ``root\\tinfinitive``,
    lines starting with ``#`` are comments. No module-level side effects: the
    file is only read on first call.
    """
    overrides: dict[str, str] = {}
    for line in _OVERRIDES_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        root, infinitive = stripped.split("\t", 1)
        overrides[root] = infinitive
    return overrides


def verb_headword(root: str) -> str:
    """The actor-focus infinitive a card front shows for a verb *root*.

    Order, exactly as measured:
    1. Hand override (``kita`` → ``makita``).
    2. Among ``um(root)`` and ``mag(root)``, those with a corpus frequency
       ``>= 3.0`` zipf — the most frequent wins, the ``um`` form on a tie.
    3. Else ``"ma" + root`` when that form clears the threshold.
    4. Else *root* unchanged (cannot tell → no guess).
    """
    if not root:
        # Nothing to derive from; `_um` would index into an empty string.
        return root
    override = _overrides().get(root)
    if override is not None:
        return override
    um_form, mag_form = _um(root), _mag(root)
    z_um, z_mag = _z(um_form), _z(mag_form)
    if z_um >= _Z_THRESHOLD and z_um >= z_mag:
        return um_form
    if z_mag >= _Z_THRESHOLD:
        return mag_form
    ma_form = "ma" + root
    if _z(ma_form) >= _Z_THRESHOLD:
        return ma_form
    return root
