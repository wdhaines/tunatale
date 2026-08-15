"""Norwegian A1 morphology bundle — the definite/tense-driven vocabulary.

Norwegian's A1 inflections are marked by definiteness + number on nouns
(``noun:def:sg`` etc.) and by tense on verbs (``verb:pres`` / ``verb:past``),
with past participles as ``verb:perf`` — none of them person/number inflections
like Slovene's ``verb:1sg``. The mapping, the A1 whitelist, and the hint text
all live here; core treats the bundle as an opaque ``A1Morphology``.
"""

from app.srs.a1_morphology import A1Morphology
from app.srs.function_words import _default_format_morphology_hint
from app.srs.lemmatizer import TokenAnalysis

_NUMBER_SHORT: dict[str, str] = {
    "Sing": "sg",
    "Plur": "pl",
}


def _to_feature(analysis: TokenAnalysis) -> str | None:
    """Map a Norwegian UD analysis to a TT feature string (or ``None``).

    NOUN: definite (Def/Ind) + number → ``noun:{definite}:{number}``.
    VERB/AUX: ``Tense=Pres`` → ``verb:pres``, ``Tense=Past`` → ``verb:past``;
    a past participle (``VerbForm=Part``) without a tense → ``verb:perf``.
    Infinitives (``VerbForm=Inf``) and anything else → ``None``.
    """
    if analysis.upos == "NOUN":
        number = _NUMBER_SHORT.get(analysis.number, "")
        if analysis.definite in ("Def", "Ind") and number:
            return f"noun:{analysis.definite.lower()}:{number}"
        return None
    if analysis.upos in ("VERB", "AUX"):
        if analysis.tense == "Pres":
            return "verb:pres"
        if analysis.tense == "Past":
            return "verb:past"
        if analysis.verbform == "Part":
            return "verb:perf"
        return None
    return None


_A1_PREFIXES: tuple[str, ...] = (
    "noun:ind:sg",
    "noun:def:sg",
    "noun:ind:pl",
    "noun:def:pl",
    "verb:pres",
    "verb:past",
    "verb:perf",
)

_HINT_LABELS: dict[str, str] = {
    "noun:ind:sg": "indefinite singular",
    "noun:def:sg": "definite singular",
    "noun:ind:pl": "indefinite plural",
    "noun:def:pl": "definite plural",
    "verb:pres": "present tense",
    "verb:past": "past tense",
    "verb:perf": "perfect (past participle)",
}


def _format_hint(lemma: str, feature: str) -> str:
    """Render ``(lemma, feature)`` as Norwegian hint text, e.g.
    ``("bilen", "noun:def:sg")`` → ``"bilen, definite singular"``."""
    if not feature:
        return lemma or ""
    label = _HINT_LABELS.get(feature)
    if label:
        return f"{lemma}, {label}"
    return _default_format_morphology_hint(lemma, feature)


NORWEGIAN_A1_MORPHOLOGY = A1Morphology(
    to_feature=_to_feature,
    a1_prefixes=_A1_PREFIXES,
    format_hint=_format_hint,
)
