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

# Closed-class words that can never be a *free compound stem*. These are
# pronouns, conjunctions, degree adverbs, and similar function words whose
# extreme frequency causes the segmenter to split ordinary words (e.g. "sommer"
# -> "som"+"mer"). Prepositions and particles MUST stay eligible — words like
# "etter", "inn", "ut", "over", "under", "til", "mot", "for", "om" are
# productive compound first-elements ("etterforskning" = "etter"+"forskning").
_CLOSED_CLASS_STEMS: frozenset[str] = frozenset(
    [
        # pronouns
        "jeg",
        "du",
        "han",
        "hun",
        "vi",
        "de",
        "seg",
        "dem",
        "deg",
        "meg",
        "oss",
        "min",
        "din",
        "sin",
        "vår",
        "deres",
        "hvilken",
        "hvilket",
        "hvilke",
        "noen",
        "noe",
        "hver",
        "selv",
        # conjunctions (note: "for" and "om" are omitted — they are
        # prepositions/particles that are productive compound stems, e.g.
        # "forstand", "omgang")
        "men",
        "og",
        "eller",
        "så",
        "at",
        "dersom",
        "fordi",
        "mens",
        "derfor",
        # degree adverbs / intensifiers
        "mer",
        "mest",
        "meget",
        "ganske",
        "temmelig",
        "litt",
        # subordinators / auxiliaries / particles (NOT prepositions)
        "som",
        "ikke",
        "har",
        "er",
        "var",
        "skal",
        "kan",
        "vil",
        "bør",
        "må",
        "hadde",
        "skulle",
        "kunne",
        "ville",
        "maatte",
        # demonstratives
        "den",
        "det",
        "denne",
        "dette",
        "disse",
        # interrogatives
        "hva",
        "hvor",
        "hvorfor",
        "hvordan",
        "hvem",
        # common adverbs / particles (not prepositions)
        "også",
        "bare",
        "når",
        "der",
        "her",
        "da",
        "daa",
        "nå",
        "allerede",
        "ennå",
        "fremdeles",
        # articles / determiners
        "en",
        "et",
        "ei",
        "ett",
        # proper names that appear in the wordlist at compound-stem rank but
        # are never productive compound first-elements
        "jon",
    ]
)


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
    """True if *word* is a lexicon entry common enough to be a real compound part.

    Rejects closed-class function words (pronouns, conjunctions, etc.) that are
    too frequent to be productive compound stems — their extreme rank causes
    ordinary words to be over-split (e.g. ``sommer`` → ``som``+``mer``).
    """
    if len(word) < _MIN_STEM_LEN or word in _DERIVATIONAL_SUFFIX_SET:
        return False
    if word in _CLOSED_CLASS_STEMS:
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
            if remaining.endswith(sfx) and len(remaining) - len(sfx) >= _MIN_STEM_LEN:
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
                    if remaining.endswith(sfx) and len(remaining) - len(sfx) >= _MIN_STEM_LEN:
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


def _anchor_rank(parts: list[str], ranks: dict[str, int]) -> int:
    """Return the rank of the *most* frequent part (lowest rank number).

    This is the split's "anchor": the strongest, most established stem in it.
    Parts missing from the lexicon are treated as rank ``_MAX_STEM_RANK``.
    NOT worst-part scoring (max rank) — that would punish legitimate linked
    forms like ``forsknings`` (rank ~18k) and flip the pinned
    ``etter|forsknings|team`` decomposition.
    """
    return min(ranks.get(p, _MAX_STEM_RANK) for p in parts)


def _segment_surface(text: str, ranks: dict[str, int]) -> list[str] | None:
    """Split a content region (no trailing inflection) into surface morphemes.

    Recursive, frequency-gated compound splitter. Prefers the decomposition
    anchored on the most frequent part (lowest :func:`_anchor_rank`); on ties,
    fewer parts wins — so ``togstasjon`` resolves to ``tog | stasjon`` rather
    than ``tog | stas | jon`` (same anchor ``tog``, fewer parts). A Norwegian
    linking element (fuge-s / fuge-e) may sit between two parts and stays
    attached to the part on its left.  Every free part must clear the
    frequency floor (:func:`_is_content_stem`), which is what keeps simplex
    roots like ``politi`` from splitting into the rare junk fragments
    ``poli`` + ``tie``.  Returns ``None`` when *text* cannot be covered by any
    content stem.
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
            candidate = [first + link] + sub
            if best is None:
                best = candidate
            else:
                cand_anchor = _anchor_rank(candidate, ranks)
                best_anchor = _anchor_rank(best, ranks)
                if cand_anchor < best_anchor or (cand_anchor == best_anchor and len(candidate) < len(best)):
                    best = candidate
    if best is not None:
        return best
    if _is_content_stem(text, ranks):
        return [text]
    return None


def _is_lexicalized_whole(word: str, content_parts: list[str], ranks: dict[str, int]) -> bool:
    """True if *word* is a common simplex that only coincidentally decomposes.

    A genuine compound is rarer than its own building blocks (``flyplassen`` is
    rarer than ``fly``/``plass``); a lexicalized word like ``morgen`` (rank ~424)
    out-ranks both ``mor`` and ``gen``. When the whole word is more frequent than
    every part, it is not really that compound — keep it whole.
    """
    whole_rank = ranks.get(word)
    if whole_rank is None:
        return False
    part_ranks = [ranks[p] for p in content_parts if p in ranks]
    return bool(part_ranks) and whole_rank < min(part_ranks)


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
                if _is_lexicalized_whole(word_lower, parts, ranks):
                    return [word_lower]
                return parts + [infl]
            if _is_content_stem(base, ranks):
                # Single content stem + inflection -> not a compound.
                return [word_lower]

    parts = _segment_surface(word_lower, ranks)
    if parts is not None and len(parts) >= 2:
        if _is_lexicalized_whole(word_lower, parts, ranks):
            return [word_lower]
        return parts
    return [word_lower]


_NORWEGIAN_VOWELS: frozenset[str] = frozenset("aeiouyæøå")


def _spoken_syllable(syllables: list[str], i: int) -> str:
    """Spoken form of syllable *i*, lengthening a geminate when spoken alone.

    A doubled consonant in Norwegian marks the preceding vowel as *short*, and
    the geminate is ambisyllabic — heard in both syllables. Onset-maximization
    splits it one consonant per side (``et | ter``, ``man | nen``). When a chunk
    is voiced ALONE we lengthen the left half so its vowel stays short
    (``et`` -> ``ett``), while the right half keeps its own onset (``ter``) — so
    the pair is heard ``ett`` / ``ter`` rather than ``ett`` / ``er``.
    Reconstruction still uses the raw syllables, so joins remain exact
    (``et`` + ``ter`` = ``etter``, never ``ettter``).
    """
    s = syllables[i]
    if i + 1 < len(syllables):
        nxt = syllables[i + 1]
        if s and nxt and s[-1] == nxt[0] and s[-1] not in _NORWEGIAN_VOWELS:
            return s + s[-1]
    return s


def syllabify_morpheme(part: str) -> list[str]:
    """Syllabify a single morpheme, honoring derivational-suffix boundaries.

    Derivational suffixes (-ning, -het, etc.) form their own syllable groups.
    Inflectional suffixes are only stripped when they help reveal a hidden
    derivational suffix. Words without derivational boundaries fall through to
    the standard onset-maximization syllabifier. Syllables are returned raw
    (``et | ter``); geminate lengthening for isolated chunks happens at buildup
    time via :func:`_spoken_syllable`, so reconstruction joins stay exact.
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
        return syllabify_norwegian_word(word)

    # Suffix boundary found — syllabify stem, append suffix groups
    if _is_loanword_monosyllable(stem) and stem in load_no_lexicon():
        stem_syllables = [stem]
    else:
        stem_syllables = syllabify_norwegian_word(stem) if stem else []

    suffix_groups: list[str] = deriv_found + infl_found

    if linking and suffix_groups:
        suffix_groups[-1] += linking

    return stem_syllables + suffix_groups


def _build_syllable_sequence(word: str, syllables: list[str]) -> list[str]:
    """Classic per-syllable backward buildup for a single word."""
    seq: list[str] = [word]
    n = len(syllables)
    for i in range(n - 1, -1, -1):
        seq.append(_spoken_syllable(syllables, i))
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
                seq.append(_spoken_syllable(pieces, j))
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
                    breakdown.append(_spoken_syllable(syllables, i))
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


# Leading/trailing punctuation peeled off a slow-section token before
# segmentation. `build_slow_speed_section` splits dialogue on whitespace, so a
# compound at a sentence boundary arrives as e.g. ``etterforskningsteam.`` — with
# the period attached, the lexicon lookup misses and the compound wouldn't split.
_SLOW_PUNCT = ".,!?;:…»«\"'()[]—–-"


def slow_norwegian_word(word: str) -> str:
    """Produce a slowed version of a Norwegian word.

    Only true compounds (>=2 content stems) are split, at their morpheme
    boundaries, with ', ' as an intra-word micro-pause. The inflectional article
    stays attached to its stem (``fly, plassen`` — not ``fly, plass, en``).
    Non-compound words are returned unchanged, however long (``informasjon``,
    ``kjærlighet``): syllable-splitting them in the slow section is too
    aggressive. Leading/trailing punctuation is peeled before segmenting and
    reattached after, so ``flyplassen.`` -> ``fly, plassen.``.
    """
    stripped = word.strip()
    if not stripped:
        return ""

    lead = stripped[: len(stripped) - len(stripped.lstrip(_SLOW_PUNCT))]
    core_end = len(stripped.rstrip(_SLOW_PUNCT))
    trail = stripped[core_end:]
    core = stripped[len(lead) : core_end].lower()
    if not core:
        # Token is all punctuation — nothing to slow.
        return stripped

    morphemes = segment_compound(core)
    if len(morphemes) >= 2:
        units = _compound_buildup_units(morphemes)
        core = ", ".join(part for part, _ in units)

    return f"{lead}{core}{trail}"
