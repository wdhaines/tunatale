"""Tagalog A1 morphology bundle — verb affixes as inflection clozes (tunatale-w4m7.17).

Roots first, affixes on top, as for Cebuano: the lemma table keys every verb
form on its root (``magdadala`` → ``dala``), and once the root's production
card is in review, a lesson's affixed form becomes an inflection cloze on it,
hinted with the affix and its aspect.

Tagalog marks aspect with reduplication and infixes as well as affixes, so the
root cannot simply be subtracted from the surface the way Cebuano's is. Instead
the root's A1 forms are GENERATED and the surface looked up among them. A form
the rules below cannot produce (syncope: ``dinggin`` from ``dinig``; a
cluster-initial loan like ``prito``, whose reduplication varies) gets no
feature and stays a plain word, because a wrong hint on a card is worse than
none.

A1 by the user's decision (2026-09-27), the Tagalog match for Cebuano's set:
actor -um- and mag-, object -in, -an and i-, each in completed / progressive /
contemplated aspect. Stative/ability (ma- maka-), mang- and causative pa- are
deliberately NOT recognised.

Feature strings are ``verb:<affix>:<aspect>``. ``base`` is the infinitive,
which is also the imperative; for -um- verbs it is also the completed form
(``kumain`` is both "eat!" and "ate"), so -um- has no separate ``completed``.
"""

from app.srs.a1_morphology import A1Morphology
from app.srs.function_words import _default_format_morphology_hint
from app.srs.lemmatizer import TokenAnalysis

_VOWELS = "aeiou"


def _redup(root: str) -> str | None:
    """The reduplicated syllable: the first CV (``kain`` → ``ka``), or the first
    vowel of a vowel-initial root (``alis`` → ``a``). ``None`` for a cluster-
    initial loan, whose reduplication is not predictable from the spelling."""
    if root[0] in _VOWELS:
        return root[0]
    if len(root) > 1 and root[1] in _VOWELS:
        return root[:2]
    return None


def _infix(word: str, infix: str) -> str:
    """-um-/-in- after the first consonant; a vowel-initial word takes it as a prefix."""
    return infix + word if word[0] in _VOWELS else word[0] + infix + word[1:]


def _reduplicated(root: str, syllable: str) -> list[str]:
    """Syllable + root. A root-initial d between vowels may become r
    (``dungaw`` → ``durungaw``), so both spellings are candidates."""
    forms = [syllable + root]
    if root[0] == "d" and syllable[-1] in _VOWELS:
        forms.append(syllable + "r" + root[1:])
    return forms


def _suffixed(stem: str, suffix: str) -> list[str]:
    """Stem + -in/-an. Before a suffix a final-syllable o raises to u
    (``bulabog`` → ``bulabugin``), a final d between vowels becomes r
    (``talikod`` → ``talikuran``), and a vowel-final stem takes an h
    (``kanta`` → ``kantahin``)."""
    stems = [stem]
    o = stem.rfind("o")
    if o != -1 and not any(c in _VOWELS for c in stem[o + 1 :]):
        stems.append(stem[:o] + "u" + stem[o + 1 :])
    stems += [s[:-1] + "r" for s in list(stems) if s.endswith("d")]
    out: list[str] = []
    for s in stems:
        out += [s + suffix, s + "h" + suffix] if s[-1] in _VOWELS else [s + suffix]
    return out


def _a1_forms(root: str) -> list[tuple[str, str]]:
    """Every A1 (surface, feature) pair for *root*, first match winning."""
    forms: list[tuple[str, str]] = []

    def add(surfaces: list[str], feature: str) -> None:
        forms.extend((s, feature) for s in surfaces)

    syllable = _redup(root)
    reduplicated = _reduplicated(root, syllable) if syllable else []
    # A reduplicated form with the infix inside its first syllable
    # (kakain → kumakain, kinakain).
    infixed_redup = [_infix(syllable, x) + r[len(syllable) :] for x in ("um", "in") for r in reduplicated]
    um_redup = infixed_redup[: len(reduplicated)]
    in_redup = infixed_redup[len(reduplicated) :]

    add([_infix(root, "um")], "um:base")
    add(um_redup, "um:progressive")
    add(reduplicated, "um:contemplated")

    add(["mag" + root], "mag:base")
    add(["nag" + root], "mag:completed")
    add(["nag" + r for r in reduplicated], "mag:progressive")
    add(["mag" + r for r in reduplicated], "mag:contemplated")

    add(_suffixed(root, "in"), "in:base")
    add([_infix(root, "in")], "in:completed")
    add(in_redup, "in:progressive")
    add([f for r in reduplicated for f in _suffixed(r, "in")], "in:contemplated")

    add(_suffixed(root, "an"), "an:base")
    add(_suffixed(_infix(root, "in"), "an"), "an:completed")
    add([f for r in in_redup for f in _suffixed(r, "an")], "an:progressive")
    add([f for r in reduplicated for f in _suffixed(r, "an")], "an:contemplated")

    add(["i" + root], "i:base")
    add(["i" + _infix(root, "in")], "i:completed")
    add(["i" + r for r in in_redup], "i:progressive")
    add(["i" + r for r in reduplicated], "i:contemplated")
    return forms


def affix_feature(surface: str, root: str) -> str | None:
    """The A1 ``<affix>:<aspect>`` that turns *root* into *surface*, or ``None``.

    Case and hyphens are spelling, not boundaries (``Mag-aral``, ``nag-eeroplano``).
    """
    s = surface.casefold().replace("-", "")
    r = root.casefold().replace("-", "")
    if not s or not r or s == r:
        return None
    return next((feature for form, feature in _a1_forms(r) if form == s), None)


def _to_feature(analysis: TokenAnalysis) -> str | None:
    if analysis.upos != "VERB":
        return None
    feature = affix_feature(analysis.surface, analysis.lemma)
    return f"verb:{feature}" if feature else None


# affix → (how the hint spells it, whose action it is). None = actor focus.
_AFFIXES: dict[str, tuple[str, str | None]] = {
    "um": ("-um-", None),
    "mag": ("mag-", None),
    "in": ("-in", "to it"),
    "an": ("-an", "to/at it"),
    "i": ("i-", "with/for it"),
}
_ACTOR_MEANINGS = {
    "base": "do! (base form)",
    "completed": "did",
    "progressive": "is/was doing",
    "contemplated": "will do",
}
_OBJECT_MEANINGS = {
    "base": "be done ({}) (base form)",
    "completed": "was done ({})",
    "progressive": "is being done ({})",
    "contemplated": "will be done ({})",
}


def _format_hint(lemma: str, feature: str) -> str:
    """``("dala", "verb:mag:contemplated")`` → ``"dala — mag-: will do"``."""
    parts = feature.split(":")
    affix = _AFFIXES.get(parts[1]) if len(parts) == 3 else None
    if affix is None or parts[2] not in _ACTOR_MEANINGS:
        return _default_format_morphology_hint(lemma, feature)
    spelled, target = affix
    aspect = parts[2]
    if target is None:
        meaning = "did, or do! (base form)" if feature == "verb:um:base" else _ACTOR_MEANINGS[aspect]
    else:
        meaning = _OBJECT_MEANINGS[aspect].format(target)
    return f"{lemma} — {spelled}: {meaning}"


TAGALOG_A1_MORPHOLOGY = A1Morphology(
    to_feature=_to_feature,
    a1_prefixes=tuple(f"verb:{affix}:" for affix in _AFFIXES),
    format_hint=_format_hint,
)
