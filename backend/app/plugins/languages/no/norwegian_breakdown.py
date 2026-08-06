from __future__ import annotations

import functools
import os

from app.models.breakdown import BreakdownChunk
from app.plugins.languages.no.syllabify import syllabify_norwegian_word

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

# Leading prefixes that form their own syllable group, overriding onset
# maximization (mis|tenkt, not mi|stenkt). Gated on the remainder being a
# content stem so it fires on real prefixed words (mis+tenkt, mis+tanke) but
# NOT on homographs where "mis" is just the first syllable (mi|sjon — "jon" is
# a proper name, not a content stem).
_PREFIXES: tuple[str, ...] = ("mis",)

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
        "deres",
        "hvilken",
        "hvilket",
        "hvilke",
        "noen",
        "noe",
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

# Compound-productive homographs: words that are closed-class in isolation
# (determiner/pronoun/possessive) but are legitimate compound-initial elements
# (hverdag, selvtillit, vårsol).  Blocked only when *not* word-initial — the
# closed-class reading causes the original over-splits, but position separates
# the readings.
_COMPOUND_INITIAL_ONLY_STEMS: frozenset[str] = frozenset(["hver", "selv", "vår"])

# Compound-productive prepositions/particles that are so frequent they always
# win the ``min(part_ranks)`` comparison in :func:`_is_lexicalized_whole`,
# neutering the guard for ANY word they appear in.  Excluded from that
# comparison so the whole word competes against its *content* parts only.
_GUARD_EXEMPT_PREPOSITIONS: frozenset[str] = frozenset(
    [
        "for",
        "om",
        "etter",
        "over",
        "under",
        "inn",
        "ut",
        "opp",
        "ned",
        "av",
        "på",
        "til",
        "mot",
        "fra",
        "ved",
        "mellom",
        "gjennom",
    ]
)

# Human-ratified lexicalized wholes that the rank-based guard cannot catch
# (the whole word does not outrank all non-exempt parts).  Each entry is a
# one-off decision — this list must NOT grow into a general dumping ground.
_LEXICALIZED_WHOLE_OVERRIDES: frozenset[str] = frozenset(["forstand", "forbrytelsens"])


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


def _is_content_stem(word: str, ranks: dict[str, int], *, initial: bool = True) -> bool:
    """True if *word* is a lexicon entry common enough to be a real compound part.

    Rejects closed-class function words (pronouns, conjunctions, etc.) that are
    too frequent to be productive compound stems — their extreme rank causes
    ordinary words to be over-split (e.g. ``sommer`` → ``som``+``mer``).
    Compound-initial-only homographs (``hver``, ``selv``, ``vår``) are rejected
    when *not* word-initial — their closed-class reading causes spurious splits,
    but they are legitimate compound stems at the start of a word.
    """
    if not set(word) & _NORWEGIAN_VOWELS:
        return False
    if len(word) < _MIN_STEM_LEN or word in _DERIVATIONAL_SUFFIX_SET:
        return False
    if word in _CLOSED_CLASS_STEMS:
        return False
    if not initial and word in _COMPOUND_INITIAL_ONLY_STEMS:
        return False
    rank = ranks.get(word)
    return rank is not None and rank <= _MAX_STEM_RANK


def _is_loanword_monosyllable(word: str) -> bool:
    return any(d in word for d in _LOAN_DIGRAPHS)


def _strip_derivational_suffixes(word: str) -> tuple[str, list[str]]:
    """Strip trailing derivational suffixes from *word*.

    Returns ``(stem, [suffixes_reversed])`` where the suffix list is in
    document order (left-to-right as they appear in the original word).
    """
    remaining = word
    found: list[str] = []
    while True:
        matched = False
        for sfx in _DERIVATIONAL_SUFFIXES:
            if (
                remaining.endswith(sfx)
                and len(remaining) - len(sfx) >= _MIN_STEM_LEN
                and set(remaining[: -len(sfx)]) & _NORWEGIAN_VOWELS
            ):
                found.append(sfx)
                remaining = remaining[: -len(sfx)]
                matched = True
                break
        if not matched:
            break
    found.reverse()
    return remaining, found


def _find_derivational_with_inflection(
    word: str,
) -> tuple[str, list[str], list[str]] | None:
    """Find derivational suffix(es), possibly after stripping one inflection.

    Returns (stem_remainder, [deriv_suffixes], [inflections]) or None
    if no derivational boundary is found.
    """
    # Direct derivational match (possibly multiple layers)
    remaining, deriv_found = _strip_derivational_suffixes(word)
    if deriv_found:
        return remaining, deriv_found, []

    # Strip one inflection layer, then look for derivational suffix(es)
    for infl in sorted(_INFLECTIONS, key=len, reverse=True):
        if word.endswith(infl) and len(word) > len(infl):
            stripped = word[: -len(infl)]
            remaining, deriv_found = _strip_derivational_suffixes(stripped)
            if deriv_found:
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


def _segment_surface(text: str, ranks: dict[str, int], *, initial: bool = True) -> list[str] | None:
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

    *initial* is ``True`` for word-initial position (the first morpheme in a
    compound) and ``False`` for all deeper positions — compound-initial-only
    homographs (``hver``, ``selv``, ``vår``) are rejected at non-initial sites.
    """
    n = len(text)
    best: list[str] | None = None
    for end in range(n - 1, _MIN_STEM_LEN - 1, -1):
        # S-overlap candidate: at a doubled-consonant boundary (e.g. ``bus|stasjon``
        # where text[k-1]==text[k]=='s'), the surface part is ``text[:k]`` (``bus``)
        # but the stem-gate check runs on the doubled spoken form ``text[:k]+text[k]``
        # (``buss``).  Downstream spoken-form sites detect the overlap and re-double
        # the final consonant for voice-alone rendering.
        if end >= 2 and text[end - 1] == text[end] and text[end - 1] not in _NORWEGIAN_VOWELS:
            overlap_spoken = text[:end] + text[end]  # e.g. ``buss``
            if _is_content_stem(overlap_spoken, ranks, initial=initial):
                # ``rest`` is never empty here: end <= n-1, so text[end:] has
                # at least one character (no guard needed).
                sub = _segment_surface(text[end:], ranks, initial=False)
                if sub is not None:
                    candidate = [text[:end]] + sub  # surface slice, not spoken
                    if best is None:
                        best = candidate
                    else:
                        cand_anchor = _anchor_rank(candidate, ranks)
                        best_anchor = _anchor_rank(best, ranks)
                        if cand_anchor < best_anchor or (cand_anchor == best_anchor and len(candidate) < len(best)):
                            best = candidate
        first = text[:end]
        if not _is_content_stem(first, ranks, initial=initial):
            continue
        for link in _LINKING_ELEMENTS:
            if link and text[end : end + len(link)] != link:
                continue
            # A fuge-s attaches to a noun first-element (forsknings-, tings-,
            # arbeids-), never to a monosyllabic preposition/prefix — "for"+s+X
            # is a prefixed verb (forsvinne), not a fuge-s compound. Blocking it
            # keeps forsvunnet whole rather than splitting it fors|vunnet.
            if link == "s" and first in _GUARD_EXEMPT_PREPOSITIONS:
                continue
            rest = text[end + len(link) :]
            if not rest:
                continue
            sub = _segment_surface(rest, ranks, initial=False)
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
    if _is_content_stem(text, ranks, initial=initial):
        return [text]
    return None


def _is_lexicalized_whole(word: str, content_parts: list[str], ranks: dict[str, int]) -> bool:
    """True if *word* is a common simplex that only coincidentally decomposes.

    A genuine compound is rarer than its own building blocks (``flyplassen`` is
    rarer than ``fly``/``plass``); a lexicalized word like ``morgen`` (rank ~424)
    out-ranks both ``mor`` and ``gen``. When the whole word is more frequent than
    every part, it is not really that compound — keep it whole.

    Compound-productive prepositions (``for``, ``etter``, etc.) are so frequent
    they always win the min, so they are excluded from the comparison — the
    whole word competes against its *content* parts only.  A small human-ratified
    override set handles words where the rank data still can't catch the split.
    """
    if word in _LEXICALIZED_WHOLE_OVERRIDES:
        return True
    whole_rank = ranks.get(word)
    if whole_rank is None:
        return False
    part_ranks = [ranks[p] for p in content_parts if p in ranks and p not in _GUARD_EXEMPT_PREPOSITIONS]
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


# NO invented respellings live here any more. There used to be one: a short
# fragment sent to the nb-NO voice ALONE is read as the identically-spelled
# *word*, so the weak-past ending ``-de`` (``had|DE``, ``bøy|DE``) came out as
# the pronoun ``de`` /diː/ instead of the schwa /də/, and was respelled ``deh``
# to steer the voice (edge-tts XML-escapes its input and exposes no
# ``<phoneme>`` SSML, so orthography was the only lever).
#
# Syllable slicing removed the premise: the chunk is cut out of a whole-word
# render of ``hadde``, so no voice ever sees the fragment. What remained was
# pure cost — ``cues.py`` feeds ``Phrase.text`` to the caption, so the learner
# was shown a spelling that is not Norwegian.
#
# The geminate lengthening in :func:`_spoken_syllable` is NOT this and stays:
# a doubled consonant really is ambisyllabic, and ``ett`` is a real spelling.
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

    Nothing else is respelled. ``de`` used to become ``deh`` here, because the
    nb-NO voice reads an isolated ``de`` as the pronoun /diː/ instead of the
    schwa — a workaround for synthesising a fragment on its own, which slicing
    made unnecessary: the chunk is cut out of a whole-word render, so no voice
    ever sees the fragment. The invented spelling still reached the learner,
    since ``cues.py`` feeds ``Phrase.text`` straight to the caption.

    The geminate lengthening above stays, and is a different thing: a doubled
    consonant genuinely is ambisyllabic, and ``ett`` is a real Norwegian
    spelling rather than a phonetic hint.
    """
    s = syllables[i]
    if i + 1 < len(syllables):
        nxt = syllables[i + 1]
        if s and nxt and s[-1] == nxt[0] and s[-1] not in _NORWEGIAN_VOWELS:
            return s + s[-1]
    return s


def _spoken_part(parts: list[str], i: int) -> str:
    """Spoken form of compound part *i*, restoring an overlap-truncated consonant.

    When a compound is split at a doubled-consonant boundary (``buss``+``stasjon``
    surface as ``bus``+``stasjon``), the truncated part is voiced with its full
    doubled final consonant (``bus`` → ``buss``).  Partials/rebuilds always use
    raw surfaces so ``"".join(...)`` reproduces the original string.

    A matching consonant boundary alone does NOT mean overlap: ``bok|klubb`` and
    ``sol|lys`` have the same surface shape (part ends with the next part's
    onset) but their left parts are full lexemes with LONG vowels — voicing
    ``bokk``/``soll`` would be wrong.  The discriminator is the lexicon: an
    overlap truncation is by construction not a content stem (it lost a
    consonant; only its doubled form passed the stem gate in
    ``_segment_surface``), while a legitimate part is one.  So double only when
    the doubled form is a content stem AND the surface is not.
    """
    p = parts[i]
    if i + 1 < len(parts):
        nxt = parts[i + 1]
        if p and nxt and p[-1] == nxt[0] and p[-1] not in _NORWEGIAN_VOWELS:
            ranks = _load_ranked_lexicon()
            doubled = p + p[-1]
            if _is_content_stem(doubled, ranks) and not _is_content_stem(p, ranks):
                return doubled
    return p


def _syllabify_with_prefix(word: str) -> list[str] | None:
    """Split off a leading prefix as its own syllable group, or return None.

    Fires only when the remainder after a :data:`_PREFIXES` entry is a content
    stem, so ``mistenkt`` -> ``mis | tenkt`` (remainder ``tenkt`` is a stem) but
    ``misjon`` falls through to normal syllabification (``jon`` is not).
    """
    ranks = _load_ranked_lexicon()
    for pfx in _PREFIXES:
        if word.startswith(pfx) and _is_content_stem(word[len(pfx) :], ranks):
            return [pfx, *syllabify_norwegian_word(word[len(pfx) :])]
    return None


def _fold_vowelless_pieces(pieces: list[str]) -> list[str]:
    """Fold any vowel-less piece into the piece before it.

    A chunk with no syllable nucleus (-n, -t, -ns) cannot stand alone — its
    audio is a CTC-sliced consonant burst cut out of a whole-word render. A
    vowel-less piece rides the piece it follows (``else`` + ``ns`` ->
    ``elsens``, ``else`` + ``n`` -> ``elsen``); a piece containing a vowel
    (-en, -et, -er, -e, -a, -ene, -ne) is its own syllable and keeps its slot.
    Parts A and B guarantee every stem has a nucleus, so the only vowel-less
    pieces here are suffix groups — precisely what should fold. The merge is
    concatenation, so ``"".join(...)`` still reproduces the word exactly.
    """
    folded: list[str] = []
    for piece in pieces:
        if folded and not set(piece) & _NORWEGIAN_VOWELS:
            folded[-1] += piece
        else:
            folded.append(piece)
    return folded


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
        prefixed = _syllabify_with_prefix(word)
        if prefixed is not None:
            return prefixed
        return syllabify_norwegian_word(word)

    # Suffix boundary found — syllabify stem, append suffix groups
    if _is_loanword_monosyllable(stem) and stem in load_no_lexicon():
        stem_syllables = [stem]
    else:
        stem_syllables = syllabify_norwegian_word(stem) if stem else []

    suffix_groups: list[str] = deriv_found + infl_found

    if linking and suffix_groups:
        suffix_groups[-1] += linking

    return _fold_vowelless_pieces(stem_syllables + suffix_groups)


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
            pieces = _fold_vowelless_pieces(syllabify_morpheme(stem) + [inflection])
        else:
            pieces = syllabify_morpheme(part)
        units.append((part, pieces))
    return units


def build_norwegian_breakdown(phrase: str) -> list[str]:
    """Build a Pimsleur-style breakdown for Norwegian.

    Delegates to :func:`build_norwegian_breakdown_spans` and discards
    provenance — the spans variant is the single authoritative implementation.
    """
    return [c.text for c in build_norwegian_breakdown_spans(phrase)]


def flat_syllables(word: str) -> list[str] | None:
    word_lower = word.lower().strip()
    if not word_lower:
        return []

    morphemes = segment_compound(word_lower)
    if len(morphemes) >= 2:
        units = _compound_buildup_units(morphemes)
        pieces: list[str] = []
        for _, part_pieces in units:
            pieces.extend(part_pieces)
    else:
        pieces = syllabify_morpheme(word_lower)

    if "".join(pieces) != word_lower:
        return None
    return pieces


def _build_syllable_inner_spans(syllables: list[str], word: str) -> list[BreakdownChunk]:
    seq: list[BreakdownChunk] = []
    n = len(syllables)
    for i in range(n - 1, -1, -1):
        seq.append(BreakdownChunk(_spoken_syllable(syllables, i), word, (i, i + 1)))
        if i < n - 1:
            seq.append(BreakdownChunk("".join(syllables[i:]), word, (i, n)))
    return seq


def _build_syllable_sequence_spans(word: str, syllables: list[str]) -> list[BreakdownChunk]:
    return [
        BreakdownChunk(word, None, None),
        *_build_syllable_inner_spans(syllables, word),
        BreakdownChunk(word, None, None),
    ]


def _build_compound_sequence_spans(word: str, morphemes: list[str]) -> list[BreakdownChunk]:
    """Compound buildup carrying provenance into the WHOLE word's syllables.

    Spans index :func:`flat_syllables` of ``word`` — the entire compound — not
    of the part a chunk came from. The consumer renders ``source_word`` once and
    cuts every chunk out of that one render, so a part-local index would send it
    to an isolated render of a bare morpheme (``forsknings``), which is exactly
    the word-level-G2P misreading the whole-word render exists to avoid; and for
    a compound of monosyllables (``snø``+``mann``) every chunk would degenerate
    to a whole-word span and nothing would be cut at all.

    ``flat_syllables`` re-derives the same units from the same
    :func:`segment_compound` output, so when it returns a list that list *is*
    the concatenation of ``units``' pieces and ``offsets`` indexes straight into
    it. When it returns ``None`` the pieces do not rejoin the surface form and
    nothing here is sliceable.

    ``_spoken_syllable`` still looks ahead within the part, not across the whole
    word, so the emitted text stays byte-identical to
    :func:`build_norwegian_breakdown`.
    """
    units = _compound_buildup_units(morphemes)
    parts_list = [part for part, _ in units]

    offsets: list[int] = []
    total = 0
    for _, pieces in units:
        offsets.append(total)
        total += len(pieces)

    sliceable = flat_syllables(word) is not None
    source = word if sliceable else None

    def span(start: int, stop: int) -> tuple[int, int] | None:
        return (start, stop) if sliceable else None

    seq: list[BreakdownChunk] = [BreakdownChunk(word, None, None)]
    for i in range(len(units) - 1, -1, -1):
        part, pieces = units[i]
        base = offsets[i]
        seq.append(BreakdownChunk(_spoken_part(parts_list, i), source, span(base, base + len(pieces))))
        if len(pieces) > 1:
            for j in range(len(pieces) - 1, -1, -1):
                seq.append(BreakdownChunk(_spoken_syllable(pieces, j), source, span(base + j, base + j + 1)))
                if j < len(pieces) - 1:
                    seq.append(BreakdownChunk("".join(pieces[j:]), source, span(base + j, base + len(pieces))))
        partial = "".join(p for p, _ in units[i:])
        if partial != part:
            seq.append(BreakdownChunk(partial, source, span(base, total)))
    return seq


def build_norwegian_breakdown_spans(phrase: str) -> list[BreakdownChunk]:
    text = " ".join(phrase.strip().split())
    words = text.split()
    if not words:
        return []

    if len(words) == 1:
        word = words[0]
        morphemes = segment_compound(word)
        if len(morphemes) >= 2:
            return _build_compound_sequence_spans(text, morphemes)
        syllables = syllabify_morpheme(word)
        if len(syllables) <= 1:
            return [BreakdownChunk(text, None, None), BreakdownChunk(text, None, None)]
        return _build_syllable_sequence_spans(text, syllables)

    breakdown: list[BreakdownChunk] = [BreakdownChunk(text, None, None)]
    for word_index in range(len(words) - 1, -1, -1):
        word = words[word_index]
        morphemes = segment_compound(word)
        if len(morphemes) >= 2:
            word_seq = _build_compound_sequence_spans(word, morphemes)
            word_seq.pop(0)
            word_seq.pop()
            breakdown.extend(word_seq)
        else:
            syllables = syllabify_morpheme(word)
            if len(syllables) > 1:
                breakdown.extend(_build_syllable_inner_spans(syllables, word))
            else:
                breakdown.append(BreakdownChunk(word, None, None))

        if word_index < len(words) - 1:
            partial = " ".join(words[word_index:])
            if partial != text:
                breakdown.append(BreakdownChunk(partial, None, None))

        if word_index == 0:
            breakdown.append(BreakdownChunk(text, None, None))

    breakdown.append(BreakdownChunk(text, None, None))
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
        parts_list = [part for part, _ in units]
        core = ", ".join(_spoken_part(parts_list, i) for i in range(len(parts_list)))

    return f"{lead}{core}{trail}"
