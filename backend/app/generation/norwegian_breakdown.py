from __future__ import annotations

import functools
import os

from app.generation.syllabify import syllabify_norwegian_word

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_WORDLIST_PATH = os.path.join(_DATA_DIR, "no_wordlist.txt")

# Known Bokmål inflectional suffixes (definite articles, plural markers)
_INFLECTIONS: frozenset[str] = frozenset(
    [
        "en",
        "et",
        "a",
        "er",
        "ene",
        "ne",
        "n",
        "t",
        "e",
    ]
)

# Derivational suffixes that form their own syllable groups
_DERIVATIONAL_SUFFIXES: list[str] = [
    "ning",
    "ing",
    "het",
    "else",
    "skap",
    "dom",
    "lig",
    "inne",
]

# A derivational suffix is a syllable-level unit, never a free compound part —
# so kjærlig|HET / arbeids|ledig|HET don't isolate the suffix as a chunk. (These
# also happen to be standalone words: het="hot", lig, dom — hence the guard.)
_DERIVATIONAL_SUFFIX_SET: frozenset[str] = frozenset(_DERIVATIONAL_SUFFIXES)

# Non-native vowel digraphs indicating loanword monosyllables
_LOAN_DIGRAPHS: frozenset[str] = frozenset(["ea", "ou", "ai", "oa"])

# Minimum length for a content stem (prevents over-segmentation)
_MIN_STEM_LEN = 3

# Frequency floor for a *free-standing* content stem in a compound split.
# The bundled wordlist is frequency-ordered, so line number == rank (1 = most
# frequent). Empirically every real compound stem in the vocabulary ranks well
# under ~5000 (hage=4139, skap=4687); the junk fragments that cause bogus
# splits sit far down the tail — poli=46998, tie=13483, pol=10489 — with a
# clean empty gap between the two populations. A floor in that gap kills
# gibberish (politiet -> poli|tie|t) with no hand-maintained blocklist.
# Linking-forms (forsknings=18348) and derivational suffixes (lig=30734) are
# never matched as free stems, so the floor never blocks a legitimate morpheme.
_MAX_STEM_RANK = 8000

# Norwegian linking elements (fuger) between compound parts: fuge-s, fuge-e,
# or none. Kept attached to the part on their left.
_LINKING_ELEMENTS = ("", "s", "e")


@functools.cache
def _load_ranked_lexicon() -> dict[str, int]:
    """Load the Bokmål wordlist as {surface_form: rank} (1 = most frequent)."""
    ranks: dict[str, int] = {}
    i = 0
    with open(_WORDLIST_PATH, encoding="utf-8") as f:
        for line in f:
            word = line.strip().lower()
            if not word or word.startswith("#"):
                continue
            i += 1
            ranks.setdefault(word, i)
    return ranks


@functools.cache
def load_no_lexicon() -> frozenset[str]:
    """Load the Norwegian Bokmål lexicon as a frozenset of surface forms."""
    return frozenset(_load_ranked_lexicon())


def _is_content_stem(word: str, ranks: dict[str, int]) -> bool:
    """True if *word* is a lexicon entry common enough to be a real compound part."""
    if len(word) < _MIN_STEM_LEN or word in _DERIVATIONAL_SUFFIX_SET:
        return False
    rank = ranks.get(word)
    return rank is not None and rank <= _MAX_STEM_RANK


def _is_loanword_monosyllable(word: str) -> bool:
    return any(d in word for d in _LOAN_DIGRAPHS)


def _find_derivational_with_inflection(
    word: str,
) -> tuple[str, list[str], list[str]] | None:
    """Find derivational suffix(es), possibly after stripping one inflection.

    Returns (stem_remainder, [deriv_suffixes_reversed], [inflections]) or None
    if no derivational boundary is found.
    """
    # Direct derivational match (possibly multiple layers)
    remaining = word
    deriv_found: list[str] = []
    while True:
        matched = False
        for sfx in _DERIVATIONAL_SUFFIXES:
            if remaining.endswith(sfx) and len(remaining) > len(sfx):
                deriv_found.append(sfx)
                remaining = remaining[: -len(sfx)]
                matched = True
                break
        if not matched:
            break
    if deriv_found:
        deriv_found.reverse()
        return remaining, deriv_found, []

    # Strip one inflection layer, then look for derivational suffix(es)
    for infl in sorted(_INFLECTIONS, key=len, reverse=True):
        if word.endswith(infl) and len(word) > len(infl):
            stripped = word[: -len(infl)]
            deriv_found = []
            remaining = stripped
            while True:
                matched = False
                for sfx in _DERIVATIONAL_SUFFIXES:
                    if remaining.endswith(sfx) and len(remaining) > len(sfx):
                        deriv_found.append(sfx)
                        remaining = remaining[: -len(sfx)]
                        matched = True
                        break
                if not matched:
                    break
            if deriv_found:
                deriv_found.reverse()
                return remaining, deriv_found, [infl]

    return None


def _segment_surface(text: str, ranks: dict[str, int]) -> list[str] | None:
    """Split a content region (no trailing inflection) into surface morphemes.

    Recursive, frequency-gated compound splitter. Prefers the *deepest* valid
    decomposition (most parts) so ``etterforskning`` -> ``etter | forskning``
    rather than staying whole. A Norwegian linking element (fuge-s / fuge-e)
    may sit between two parts and stays attached to the part on its left.
    Every free part must clear the frequency floor (:func:`_is_content_stem`),
    which is what keeps simplex roots like ``politi`` from splitting into the
    rare junk fragments ``poli`` + ``tie``. Returns ``None`` when *text* cannot
    be covered by any content stem.
    """
    n = len(text)
    best: list[str] | None = None
    for end in range(n - 1, _MIN_STEM_LEN - 1, -1):
        first = text[:end]
        if not _is_content_stem(first, ranks):
            continue
        for link in _LINKING_ELEMENTS:
            if link and text[end : end + len(link)] != link:
                continue
            rest = text[end + len(link) :]
            if not rest:
                continue
            sub = _segment_surface(rest, ranks)
            if sub is None:
                continue
            # Only the remainder recurses; trying every `first` length lets the
            # deepest split emerge (first="etter" -> rest recurses to
            # "forsknings"+"team"). The linking element stays on the left part.
            candidate = [first + link] + sub
            if best is None or len(candidate) > len(best):
                best = candidate
    if best is not None:
        return best
    if _is_content_stem(text, ranks):
        return [text]
    return None


def segment_compound(word: str) -> list[str]:
    """Split a word into compound morphemes (deepest valid decomposition).

    Peels one trailing inflectional ending, then recursively splits the base
    into frequency-vetted content stems joined by optional linking elements.
    Returns ``[word]`` when the word is a single stem (no valid >=2-part split),
    so simplex roots (``mannen``, ``politiet``) are never over-segmented.
    """
    word_lower = word.lower().strip()
    if not word_lower:
        return []

    ranks = _load_ranked_lexicon()

    for infl in sorted(_INFLECTIONS, key=lambda x: (len(x), x), reverse=True):
        if word_lower.endswith(infl) and len(word_lower) > len(infl):
            base = word_lower[: -len(infl)]
            # Don't peel a single-consonant ending that is really half of a stem
            # geminate: snømann is snø|mann, not snø|man|n. (A real "-n" article
            # only attaches to an -e-final stem, e.g. hage -> hagen.)
            if infl == base[-1] and infl not in _NORWEGIAN_VOWELS:
                continue
            parts = _segment_surface(base, ranks)
            if parts is not None and len(parts) >= 2:
                return parts + [infl]
            if _is_content_stem(base, ranks):
                # Single content stem + inflection -> not a compound.
                return [word_lower]

    parts = _segment_surface(word_lower, ranks)
    if parts is not None and len(parts) >= 2:
        return parts
    return [word_lower]


_NORWEGIAN_VOWELS: frozenset[str] = frozenset("aeiouyæøå")


def _merge_geminates(syllables: list[str]) -> list[str]:
    """Keep a doubled consonant with the preceding syllable, for TTS.

    A doubled consonant in Norwegian marks the preceding vowel as *short*, so an
    isolated ``et`` (single t) would cue TTS to a long vowel — wrong for
    ``etter``. Moving the split so the whole geminate closes the first syllable
    (``ett | er``, ``mann | en``, ``plass | en``) preserves the short vowel when
    each chunk is pronounced alone. Onset-maximization does the opposite, so
    this post-processes its output on the Norwegian path only.
    """
    out = list(syllables)
    for i in range(len(out) - 1):
        cur, nxt = out[i], out[i + 1]
        if cur and len(nxt) > 1 and cur[-1] == nxt[0] and cur[-1] not in _NORWEGIAN_VOWELS:
            out[i] = cur + nxt[0]
            out[i + 1] = nxt[1:]
    return out


def syllabify_morpheme(part: str) -> list[str]:
    """Syllabify a single morpheme, honoring derivational-suffix boundaries.

    Derivational suffixes (-ning, -het, etc.) form their own syllable groups.
    Inflectional suffixes are only stripped when they help reveal a hidden
    derivational suffix. Words without derivational boundaries fall through to
    the standard onset-maximization syllabifier. A geminate-merge pass then
    keeps doubled consonants with the preceding syllable for correct TTS vowel
    length.
    """
    word = part.lower().strip()
    if not word:
        return []

    # Handle linking element (-s) before suffix processing
    linking = ""
    if word.endswith("s") and len(word) > 2:
        result = _find_derivational_with_inflection(word[:-1])
        if result is not None:
            linking = "s"
            stem, deriv_found, infl_found = result
        else:
            stem, deriv_found, infl_found = word, [], []
    else:
        result = _find_derivational_with_inflection(word)
        if result is not None:
            stem, deriv_found, infl_found = result
        else:
            stem, deriv_found, infl_found = word, [], []

    if not deriv_found and not infl_found:
        # No suffix boundary — standard syllabification
        if _is_loanword_monosyllable(word) and word in load_no_lexicon():
            return [word]
        return _merge_geminates(syllabify_norwegian_word(word))

    # Suffix boundary found — syllabify stem, append suffix groups
    if _is_loanword_monosyllable(stem) and stem in load_no_lexicon():
        stem_syllables = [stem]
    else:
        stem_syllables = _merge_geminates(syllabify_norwegian_word(stem)) if stem else []

    suffix_groups: list[str] = deriv_found + infl_found

    if linking and suffix_groups:
        suffix_groups[-1] += linking

    return stem_syllables + suffix_groups


def _build_syllable_sequence(word: str, syllables: list[str]) -> list[str]:
    """Classic per-syllable backward buildup for a single word."""
    seq: list[str] = [word]
    n = len(syllables)
    for i in range(n - 1, -1, -1):
        seq.append(syllables[i])
        if i < n - 1:
            seq.append("".join(syllables[i:]))
    seq.append(word)
    return seq


def _compound_buildup_units(morphemes: list[str]) -> list[tuple[str, list[str]]]:
    """Group compound morphemes into (surface, pieces) buildup units.

    A trailing inflection is merged back onto the final stem so the tail is
    spoken as a whole word (``team`` + ``et`` -> ``teamet``); its pieces are the
    stem's syllables plus the inflection. Every other part's pieces are its
    syllables.
    """
    parts = list(morphemes)
    inflection: str | None = None
    if len(parts) >= 2 and parts[-1] in _INFLECTIONS:
        inflection = parts.pop()
        parts[-1] = parts[-1] + inflection

    units: list[tuple[str, list[str]]] = []
    for idx, part in enumerate(parts):
        if inflection is not None and idx == len(parts) - 1:
            stem = part[: -len(inflection)]
            pieces = syllabify_morpheme(stem) + [inflection]
        else:
            pieces = syllabify_morpheme(part)
        units.append((part, pieces))
    return units


def _build_compound_sequence(phrase: str, morphemes: list[str]) -> list[str]:
    """Morpheme-first backward buildup for compounds.

    Tail-first, each part is spoken whole, broken into its pieces (backward),
    rebuilt, then the running partial toward the full phrase is added.
    """
    units = _compound_buildup_units(morphemes)
    seq: list[str] = [phrase]

    for i in range(len(units) - 1, -1, -1):
        part, pieces = units[i]
        seq.append(part)
        if len(pieces) > 1:
            for j in range(len(pieces) - 1, -1, -1):
                seq.append(pieces[j])
                if j < len(pieces) - 1:
                    seq.append("".join(pieces[j:]))
        partial = "".join(p for p, _ in units[i:])
        if partial != part:
            seq.append(partial)

    return seq


def build_norwegian_breakdown(phrase: str) -> list[str]:
    """Build a Pimsleur-style breakdown for Norwegian.

    For compounds (>=2 morphemes): morpheme-first backward buildup.
    For single-stem words: per-syllable backward buildup with morpheme-aware
    syllabification.
    Multi-word phrases: right-to-left per-word processing (current algorithm)
    with Norwegian-specific syllabification per word.
    """
    text = " ".join(phrase.strip().split())
    words = text.split()
    if not words:
        return []

    if len(words) == 1:
        word = words[0]
        morphemes = segment_compound(word)
        if len(morphemes) >= 2:
            return _build_compound_sequence(text, morphemes)
        syllables = syllabify_morpheme(word)
        if len(syllables) <= 1:
            return [text, text]
        return _build_syllable_sequence(text, syllables)

    # Multi-word phrase: right-to-left, Norwegian syllabification per word
    breakdown: list[str] = [text]
    for word_index in range(len(words) - 1, -1, -1):
        word = words[word_index]
        morphemes = segment_compound(word)
        if len(morphemes) >= 2:
            word_seq = _build_compound_sequence(word, morphemes)
            word_seq.pop(0)
            word_seq.pop()
            breakdown.extend(word_seq)
        else:
            syllables = syllabify_morpheme(word)
            if len(syllables) > 1:
                for i in range(len(syllables) - 1, -1, -1):
                    breakdown.append(syllables[i])
                    if i < len(syllables) - 1:
                        breakdown.append("".join(syllables[i:]))
            else:
                breakdown.append(word)

        if word_index < len(words) - 1:
            partial = " ".join(words[word_index:])
            if partial != text:
                breakdown.append(partial)

        if word_index == 0:
            breakdown.append(text)

    breakdown.append(text)
    return breakdown


def slow_norwegian_word(word: str) -> str:
    """Produce a slowed version of a Norwegian word.

    Only true compounds (>=2 content stems) are split, at their morpheme
    boundaries, with ', ' as an intra-word micro-pause. The inflectional article
    stays attached to its stem (``fly, plassen`` — not ``fly, plass, en``).
    Non-compound words are returned unchanged, however long (``informasjon``,
    ``kjærlighet``): syllable-splitting them in the slow section is too
    aggressive.
    """
    w = word.strip().lower()
    if not w:
        return ""

    morphemes = segment_compound(w)
    if len(morphemes) >= 2:
        units = _compound_buildup_units(morphemes)
        return ", ".join(part for part, _ in units)

    return w
