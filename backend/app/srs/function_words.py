"""Function-word detection (POS-first, per-language config) + morphology-cloze hints.

Function-word policy is data-driven, one swappable JSON file per language under
``data/function_words/`` (``pos`` / ``include`` / ``exclude`` — see the file's
``_comment``). A language with no file simply has no function words (clozes are
capability-driven). Surfaces for ``include`` are seeded by build_function_word_list.py
and hand-curated; the classla UPOS ``pos`` set is the primary signal when an
analyzer is present.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path

_FUNCTION_WORD_DATA_DIR = Path(__file__).parent / "data" / "function_words"


@cache
def _load_function_word_config(language_code: str) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Load ``(pos, include, exclude)`` for *language_code* from its data file.

    Returns three empty sets when no file exists — a language without a curated
    config produces no function-word clozes (capability-driven). ``include`` /
    ``exclude`` are casefolded for case-insensitive matching; ``pos`` is the raw
    UPOS tag set (already uppercase).
    """
    path = _FUNCTION_WORD_DATA_DIR / f"{language_code}.json"
    if not path.exists():
        return frozenset(), frozenset(), frozenset()
    data = json.loads(path.read_text(encoding="utf-8"))
    pos = frozenset(data.get("pos", []))
    include = frozenset(w.casefold() for w in data.get("include", []))
    exclude = frozenset(w.casefold() for w in data.get("exclude", []))
    return pos, include, exclude


def is_function_word(token: str, language_code: str, *, upos: str | None = None) -> bool:
    """Return True if *token* is a function word in *language_code*.

    POS-first: when an analyzer supplies *upos*, a token whose classla UPOS is in
    the language's closed-class ``pos`` set counts — so the whole biti AUX paradigm
    (sem/si/je/smo/ste/so) is caught without enumerating surfaces. The curated
    ``include`` set adds words POS misses or mistags (the open-class adverbs
    kje/kako/tam; ``ni``, which classla tags VERB) and is the *sole* signal when no
    analyzer is present (LowercaseLemmatizer emits ``upos=""``), exactly reproducing
    the legacy surface-list behavior. ``exclude`` force-removes. Case-insensitive.
    """
    pos, include, exclude = _load_function_word_config(language_code)
    t = token.casefold()
    if t in exclude:
        return False
    if t in include:
        return True
    return upos is not None and upos in pos


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
