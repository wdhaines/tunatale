"""Cebuano A1 morphology bundle — verb affixes as inflection clozes (tunatale-u8nz.20).

Stage 2 of "roots first, affixes on top": once a root's production card is in
review, a lesson's affixed form of it (``milakaw`` from ``lakaw``) becomes an
inflection cloze on that root, hinted with the affix and what it means.

The table lemmatizer carries no morphological features — Wiktionary's Cebuano
conjugation tables tag almost nothing (11 forms "past", 1 "future" across the
whole extract) — so the affix is recovered from the surface by subtracting the
root. A form the subtraction cannot explain (syncope: ``imnon`` from ``inom``)
gets no feature and stays a plain word; guessing would put a wrong hint on a
card.

A1 by the user's decision (2026-09-27): the core actor affixes mo- mi- mag- nag-
and the core object affixes gi- -on -an gi-…-an i-. Stative ma- na- joined them
on 2026-10-07 (tunatale-ve4p.1): they were 5 of the 14 affixed verbs the first
two lessons left unexplained (``matulog``, ``malipay``, ``nalipay``, ``nakita``).
Ability (maka- naka-) and imperatives (-a -i pag-) are deliberately NOT
recognised.

``ni-`` is read as ``mi-`` (the user, 2026-10-07): it is the same affix in
another spelling, and the commoner one in native news (``niadto`` 1,058 against
``miadto`` 231 in 9.5 M tokens). It gets no feature of its own, so the hint for
``verb:mi`` names both spellings. The affix-count table still counts ``mi-``
only: ``niadto`` is also the demonstrative "back then", so its count would
vouch for forms on the strength of a different word.
"""

from app.plugins.languages.ceb.affix_patterns import PATTERNS, pattern_forms, pattern_glosses
from app.srs.a1_morphology import A1Morphology
from app.srs.function_words import _default_format_morphology_hint
from app.srs.lemmatizer import TokenAnalysis

# Longest first: "mag"/"nag" before the "ma"/"na" they begin with, "i" last.
_PREFIXES = ("mag", "nag", "ma", "na", "mo", "mi", "gi", "i")
_SUFFIXES = ("on", "an")
# Another spelling of an affix above → the affix it is.
_SPELLINGS = {"ni": "mi"}

# affix → (how the hint spells it, what it means)
_AFFIXES: dict[str, tuple[str, str]] = {
    "mo": ("mo-", "will do (not yet done)"),
    "mi": ("mi- / ni-", "did (completed)"),
    "mag": ("mag-", "will do (not yet done)"),
    "nag": ("nag-", "did / was doing"),
    "ma": ("ma-", "will be / will happen (not yet)"),
    "na": ("na-", "is / was / happened"),
    "gi": ("gi-", "was done (to it)"),
    "on": ("-on", "will be done (to it)"),
    "an": ("-an", "will be done to/at"),
    "gi-an": ("gi-…-an", "was done to/at"),
    "i": ("i-", "will be done with/for"),
}


def _stems(root: str) -> tuple[str, ...]:
    """The root as it can appear before a suffix: itself, or with an inserted h
    after a final vowel (``basa`` → ``basahon``)."""
    return (root, root + "h") if root[-1:] in "aeiou" else (root,)


def affix_of(surface: str, root: str) -> str | None:
    """The A1 affix that turns *root* into *surface*, or ``None``.

    Hyphens are dropped from both first: ``mag-abot`` (a vowel-initial root) and
    ``tan-aw`` (a glottal stop inside the root) are spelling, not boundaries.
    """
    s = surface.casefold().replace("-", "")
    r = root.casefold().replace("-", "")
    if not r or s == r:
        return None
    if s.startswith("gi") and s.endswith("an") and s[2:-2] in _stems(r):
        return "gi-an"
    for suffix in _SUFFIXES:
        if s.endswith(suffix) and s[: -len(suffix)] in _stems(r):
            return suffix
    for prefix in _PREFIXES:
        if s == prefix + r:
            return prefix
    for spelling, affix in _SPELLINGS.items():
        if s == spelling + r:
            return affix
    return None


def _to_feature(analysis: TokenAnalysis) -> str | None:
    if analysis.upos != "VERB":
        return None
    affix = affix_of(analysis.surface, analysis.lemma)
    return f"verb:{affix}" if affix else None


def _format_hint(lemma: str, feature: str) -> str:
    """``("lakaw", "verb:mi")`` → ``"lakaw — mi- / ni-: did (completed)"``."""
    spelled = _AFFIXES.get(feature.removeprefix("verb:"))
    if spelled is None:
        return _default_format_morphology_hint(lemma, feature)
    affix, meaning = spelled
    return f"{lemma} — {affix}: {meaning}"


CEBUANO_A1_MORPHOLOGY = A1Morphology(
    to_feature=_to_feature,
    a1_prefixes=tuple(f"verb:{affix}" for affix in _AFFIXES),
    format_hint=_format_hint,
    patterns=PATTERNS,
    pattern_forms=pattern_forms,
    pattern_glosses=pattern_glosses,
)
