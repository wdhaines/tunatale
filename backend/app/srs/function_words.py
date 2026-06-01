"""Slovene function words used by Phase F's cloze-card spike, plus morphology-cloze hint generation.

This set was generated from the user's 7-day curriculum by
build_function_word_list.py and manually curated to remove obvious
content words. To extend after adding new lessons, re-run the generator
and merge new entries.
"""

from __future__ import annotations

import re

# Generated 2026-05-12 from curriculum 'arrival-in-ljubljana-5f8c0f52'; manually curated.
SLOVENE_FUNCTION_WORDS: frozenset[str] = frozenset(
    {
        "je",  # 38 occurrences across 7 lessons — copula "is"
        "kje",  # 17 / 6 — "where"
        "v",  # 12 / 6 — "in"
        "kaj",  # 10 / 6 — "what"
        "sem",  # 9 / 4 — "am"
        "si",  # 7 / 3 — "are" (singular)
        "da",  # 7 / 4 — "that", "yes"
        "za",  # 6 / 3 — "for"
        "tam",  # 6 / 5 — "there"
        "na",  # 6 / 3 — "on"
        "kako",  # 5 / 3 — "how"
        "ni",  # 5 / 3 — "is not"
        "se",  # 4 / 4 — reflexive pronoun
        "to",  # 3 / 2 — "this", "that"
        "vam",  # 3 / 3 — "you" (plural/formal dative)
        "z",  # 3 / 2 — "with"
        "mi",  # 3 / 2 — "me" (dative)
        "še",  # 2 / 2 — "still", "yet"
        "pa",  # 2 / 2 — "and", "but", "so"
        "ti",  # 2 / 2 — "you" (singular dative)
        "po",  # 2 / 2 — "after", "along"
        "vi",  # lemma of "vam" — "you" (plural/formal)
        # NOTE: "biti" (copula lemma) is intentionally excluded. Its surface forms
        # je/sem/si/ni are in the set above; adding "biti" would create ambiguity
        # with verb-conjugation cloze targets for the same root.
    }
)


def is_function_word(lemma: str, language_code: str) -> bool:
    """Return True if *lemma* is a known function word in *language_code*.

    Phase F scope: Slovene only (language_code == "sl").
    Case-insensitive (casefold) lookup against the curated set.
    """
    if language_code == "sl":
        return lemma.casefold() in SLOVENE_FUNCTION_WORDS
    return False


_CLOZE_RE = re.compile(r"\{\{c1::")


def make_cloze_text(surface: str, source_sentence: str) -> str:
    """Wrap every occurrence of ``surface`` in ``source_sentence`` with {{c1::surface}}.

    Word boundaries respected (regex ``\\b``). Case-insensitive,
    case-preserving (e.g. ``\\bkJe\\b`` matches ``Kje`` → ``{{c1::Kje}}``).
    Idempotent: already-clozed text (``{{c1::...}}``) passes through unchanged.

    Returns empty string when ``source_sentence`` is empty (caller must skip).
    """
    if not source_sentence:
        return ""
    if not surface:
        return source_sentence
    if _CLOZE_RE.search(source_sentence):
        return source_sentence
    pattern = re.compile(rf"\b{re.escape(surface)}\b", re.IGNORECASE)

    def _replacer(m: re.Match) -> str:
        return f"{{{{c1::{m.group(0)}}}}}"

    return pattern.sub(_replacer, source_sentence)


# ── Morphology-cloze hint helpers ────────────────────────────────────────


def _ending_blank_split(matched: str, lemma: str) -> tuple[str, str] | None:
    """Split *matched* into (visible_stem, blanked_tail) for a Fluent-Forever cloze.

    Computes the longest common prefix (LCP) of ``matched.casefold()`` and
    ``lemma.casefold()``. If the LCP is at least 2 characters and shorter
    than the full matched word, returns ``(matched[:n], matched[n:])`` so the
    stem stays visible. Returns ``None`` for suppletive forms (LCP < 2) or
    when *matched* is a prefix of *lemma* (no blankable tail).
    """
    cf_matched = matched.casefold()
    cf_lemma = lemma.casefold()
    n = 0
    for a, b in zip(cf_matched, cf_lemma, strict=False):
        if a == b:
            n += 1
        else:
            break
    if 2 <= n < len(matched):
        return (matched[:n], matched[n:])
    return None


def _format_morphology_feature(feature: str) -> str:
    """Turn a feature key into a concise hint label.

    Examples:
      ``verb:1sg``      -> ``1sg``
      ``noun:loc:sg``   -> ``loc sg``
      ``noun:nom:f:pl`` -> ``nom f pl``
      ``adj:nom:m:sg``  -> ``nom m sg``

    The POS prefix is dropped — the hint is shown alongside the lemma, which
    already implies the part of speech. Returns ``""`` for empty/malformed.
    """
    if not feature or ":" not in feature:
        return ""
    return " ".join(p for p in feature.split(":")[1:] if p)


def make_morphology_cloze_text(
    surface: str,
    lemma: str,
    feature: str,
    source_sentence: str,
) -> str:
    """Wrap ``surface`` with a hinted cloze: ``{{c1::sem::biti, 1sg}}``.

    The hint (``::hint``) tells the learner which lemma + morphology to
    produce. Anki renders the blank as ``[biti, 1sg]``.

    Idempotent: already-clozed text passes through unchanged.
    Returns empty string when ``source_sentence`` is empty.
    """
    if not source_sentence:
        return ""
    if not surface:
        return source_sentence
    if _CLOZE_RE.search(source_sentence):
        return source_sentence
    label = _format_morphology_feature(feature)
    pattern = re.compile(rf"\b{re.escape(surface)}\b", re.IGNORECASE)

    def _replacer(m: re.Match) -> str:
        matched = m.group(0)
        split = _ending_blank_split(matched, lemma)
        if split is None:
            hint = f"{lemma}, {label}" if label else lemma
            return f"{{{{c1::{matched}::{hint}}}}}"
        visible, tail = split
        hint = label or lemma
        return f"{visible}{{{{c1::{tail}::{hint}}}}}"

    return pattern.sub(_replacer, source_sentence)


# ── UD feats → TT feature mapping ──────────────────────────────────────────


_CASE_MAP: dict[str, str] = {
    "Nom": "nom",
    "Acc": "acc",
    "Gen": "gen",
    "Dat": "dat",
    "Loc": "loc",
    "Ins": "ins",
}

_NUMBER_MAP: dict[str, str] = {
    "Sing": "sg",
    "Plur": "pl",
    "Dual": "du",
}

_GENDER_MAP: dict[str, str] = {
    "Masc": "m",
    "Fem": "f",
    "Neut": "n",
}


# ── A1 morphology feature detection (moved from app/api/srs.py, Phase 4b) ──


_A1_MORPHOLOGY_PREFIXES: tuple[str, ...] = (
    "verb:",
    "noun:nom:",
    "noun:acc:",
    "noun:loc:",
    "adj:nom:",
)


def is_a1_morphology_feature(feature: str) -> bool:
    return any(feature.startswith(p) for p in _A1_MORPHOLOGY_PREFIXES)


def ud_feats_to_tt_feature(
    upos: str,
    case: str = "",
    number: str = "",
    person: str = "",
    gender: str = "",
) -> str | None:
    """Map Universal Dependencies POS + morphological features to a TT feature string.

    Returns ``None`` when the combination is not A1-mappable (e.g., genitive nouns,
    non-nominative adjectives).

    TT feature format (matches ``_A1_MORPHOLOGY_PREFIXES`` in ``srs.py``):
      * ``verb:1sg``  — verb with Person=1, Number=Sing
      * ``noun:loc:sg`` — noun with Case=Loc, Number=Sing
      * ``adj:nom:m:sg`` — adjective with Case=Nom, Gender=Masc, Number=Sing

    A1 whitelist: all verbs; nouns in nom/acc/loc; adjectives in nom.
    """
    n = _NUMBER_MAP.get(number, "")
    if upos in ("VERB", "AUX"):
        p = person
        if p and n:
            return f"verb:{p}{n}"
        return None

    if upos == "NOUN":
        c = _CASE_MAP.get(case, "")
        if c in ("nom", "acc", "loc") and n:
            return f"noun:{c}:{n}"
        return None

    if upos == "ADJ":
        c = _CASE_MAP.get(case, "")
        g = _GENDER_MAP.get(gender, "")
        if c == "nom" and g and n:
            return f"adj:{c}:{g}:{n}"
        return None

    return None
