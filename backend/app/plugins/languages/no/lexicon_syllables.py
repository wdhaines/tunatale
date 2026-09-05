"""Align orthographic syllable boundaries with the NST pronunciation lexicon.

The NST lexicon provides syllable boundaries in *phoneme* space; captions need
them in *letter* space.  This module infers the letter↔phone correspondence via
a least-cost dynamic-programming alignment, then cuts the orthography at the
lexicon's syllable boundaries.

The invariant this enforces: **boundaries and phonemes must come from the SAME
source, per word, never crossed.**  Today the audio follows the NST lexicon and
the captions follow spelling — exactly "crossed".  After this change a word
either gets BOTH its boundaries and its phonemes from the lexicon, or NEITHER.

The grapheme table and the refusal guards below are load-bearing data measured
against the real 44 MB lexicon.  Do not "improve" the table without re-measuring
over ``no_wordlist.txt``.
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path

from app.plugins.languages.no.lexicon import NstLexicon, nst_lexicon_installed

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Grapheme → phone(s) table
# ---------------------------------------------------------------------------

TABLE: dict[str, list[tuple[str, ...]]] = {}


def _add(graph: str, *phones: str) -> None:
    TABLE.setdefault(graph, []).extend((p,) for p in phones)


_add("a", "ɑ", "ɑː", "æ", "æː", "ə")
_add("e", "eː", "ɛ", "ə", "æ", "æː")
_add("i", "ɪ", "ɪː", "ə", "j")
_add("o", "uː", "ʊ", "oː", "ɔ", "ʉ", "ɔ")
_add("u", "ʉ", "ʉː", "ʊ", "uː", "œ")
_add("y", "ʏ", "yː", "j")
_add("æ", "æ", "æː", "ɛ")
_add("ø", "øː", "œ", "ə")
_add("å", "oː", "ɔ")
_add("ei", "æ͡ɪ")
_add("ea", "ɪː")
_add("eu", "æ͡ʉ", "ɔ͡ʊ")
_add("øu", "æ͡ʉ")
_add("eg", "æ͡ɪ")
_add("ai", "ɑ͡ɪ")
_add("øy", "œ͡ʏ")
_add("au", "æ͡ʉ", "ɔ͡ʊ")
_add("oy", "ɔ͡ʏ")
_add("øg", "œ͡ʏ")
_add("ou", "ɔ͡ʊ")
_add("b", "b", "p")
_add("c", "k", "s")
_add("d", "d", "ɖ", "t")
_add("f", "f")
_add("g", "g", "j", "k")
_add("h", "h")
_add("j", "j")
_add("k", "k", "ç")
_add("l", "l", "ɭ")
_add("m", "m")
_add("n", "n", "ŋ", "ɳ")
_add("p", "p")
_add("q", "k")
_add("r", "r")
_add("s", "s", "ʂ", "ʃ")
_add("t", "t", "ʈ")
_add("v", "v")
_add("w", "v", "w")
_add("z", "s")
_add("ng", "ŋ")
_add("sj", "ʃ")
_add("skj", "ʃ")
_add("sk", "ʃ")
_add("stj", "ʃ")
_add("sch", "ʃ")
_add("kj", "ç")
_add("tj", "ç")
_add("gj", "j")
_add("hj", "j")
_add("lj", "j")
_add("hv", "v")
_add("rs", "ʂ")
_add("rt", "ʈ")
_add("rd", "ɖ")
_add("rn", "ɳ")
_add("rl", "ɭ")
_add("x", "s")
TABLE["x"].append(("k", "s"))
for _c in "bdfgklmnprstv":
    _add(_c * 2, *[p[0] for p in TABLE.get(_c, [])])
for _v in "aeiouyæøå":
    _add(_v + "n", "n̩", "ɳ̩")
    _add(_v + "l", "l̩", "ɭ̩")
    _add(_v + "m", "m̩")
    _add(_v + "r", "r̩")
_add("n", "n̩")
_add("l", "l̩")
_add("m", "m̩")
_add("r", "r̩")
_add("rn", "ɳ̩")
_add("rl", "ɭ̩")
_add("nn", "n̩")
_add("ll", "l̩")
_add("mm", "m̩")

# East Norwegian retroflex assimilation ACROSS a written-but-unpronounced letter.
# An r fuses with a FOLLOWING coronal even when the spelling keeps a vowel
# between them: sporene is /'spu:n`=@/, faren /'fA:n`=/, morgen /'mo:n`=/. The
# plain "rn"/"rl" graphemes above cannot match those, because a grapheme's
# letters must be ADJACENT -- so the r aligned to nothing, and a silent letter
# sitting immediately before a cut is exactly what REFUSE_SILENT_AT_CUT rejects.
# The whole word was therefore refused, which is why 'sporene' got no IPA.
#
# Measured over the first 20000 wordlist words: 182 words newly align, ZERO lose
# their alignment, and only 3 splits change (læreren/føreren/ordføreren, which
# move from lære|ren to læ|reren -- a correction: the old path needed two silent
# letters at cost 8, the new one costs 0, and /'l{:r@n`/ does divide after the
# long vowel).
for _v in "aeiouyæøå":
    _add("r" + _v + "n", "ɳ̩", "ɳ")
    _add("r" + _v + "l", "ɭ̩", "ɭ")
    _add("r" + _v + "s", "ʂ")
    _add("r" + _v + "t", "ʈ")
    _add("r" + _v + "d", "ɖ")
# Two letters may intervene (morgen, hjernen). These are enumerated rather than
# looped: the pattern is not productive enough to generate safely, and _MAXG is
# derived from TABLE below, so a longer grapheme widens the DP for every word.
_add("rgen", "ɳ̩")
_add("rnen", "ɳ̩")
_add("rden", "ɖ̩")
_add("rten", "ʈ̩")
_add("rgel", "ɭ̩")

_MAXG = max(len(g) for g in TABLE)
SILENT_COST = 4

# ---------------------------------------------------------------------------
# Refusal reasons
# ---------------------------------------------------------------------------

REFUSE_NO_PATH = "no-path"
REFUSE_CUT_IN_GRAPHEME = "cut-inside-grapheme"
REFUSE_SILENT_AT_CUT = "silent-letter-merges-right"

# Phones whose realisation can swallow a preceding letter across the boundary.
RETROFLEX = frozenset("ʂɳɖʈɭ")
REFUSE_EMPTY = "empty-syllable"


# ---------------------------------------------------------------------------
# Dynamic programming alignment
# ---------------------------------------------------------------------------


def _steps(word: str, phones: list[str]) -> list[tuple[int, int, int, int]] | None:
    """Least-cost alignment as steps ``(l0, l1, p0, p1)``; ``None`` when none exists.

    Each step maps a letter range ``word[l0:l1]`` to a phone range
    ``phones[p0:p1]``.  A letter can be skipped (silent) at cost
    :data:`SILENT_COST`; a phone cannot be skipped.
    """
    n, m = len(word), len(phones)
    INF = float("inf")
    best = [[INF] * (m + 1) for _ in range(n + 1)]
    back: dict[tuple[int, int], tuple[int, int]] = {}
    best[0][0] = 0
    for i in range(n + 1):
        for j in range(m + 1):
            c = best[i][j]
            if c == INF:
                continue
            if i < n and c + SILENT_COST < best[i + 1][j]:
                best[i + 1][j] = c + SILENT_COST
                back[(i + 1, j)] = (i, j)
            for glen in range(1, min(_MAXG, n - i) + 1):
                for pseq in TABLE.get(word[i : i + glen], ()):
                    k = len(pseq)
                    if j + k <= m and tuple(phones[j : j + k]) == pseq and c < best[i + glen][j + k]:
                        best[i + glen][j + k] = c
                        back[(i + glen, j + k)] = (i, j)
    if best[n][m] == INF:
        return None
    steps = []
    i, j = n, m
    while (i, j) != (0, 0):
        pi, pj = back[(i, j)]
        steps.append((pi, i, pj, j))
        i, j = pi, pj
    steps.reverse()
    return steps


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _parse_sampa_to_phones(sampa: str) -> list[str]:
    """Parse a SAMPA syllable into individual IPA phone symbols.

    Each SAMPA segment maps to exactly one IPA symbol; this function returns
    a list of individual IPA phones, one per SAMPA segment, so the DP
    alignment can match individual phones to graphemes.
    """
    from app.plugins.languages.no.sampa import _FULL_MAPPING, UnknownSegmentError, _parse

    phones: list[str] = []
    for segment in _parse(sampa):
        if segment not in _FULL_MAPPING:
            raise UnknownSegmentError(f"Unknown SAMPA segment: {segment!r}")
        phones.append(_FULL_MAPPING[segment])
    return phones


def orthographic_syllables(word: str, transcription: str) -> tuple[list[str] | None, str]:
    """Cut *word* at ONE NST transcription's syllable boundaries.

    Returns ``(pieces, reason)`` where *pieces* is the letter-space syllable
    split when the alignment succeeds, or ``None`` when it is refused.  *reason*
    is the refusal tag (empty string on success).

    *transcription* is the raw X-SAMPA string from the NST database (including
    tone marks and syllable separators).
    """
    from app.plugins.languages.no.lexicon import _syllables
    from app.plugins.languages.no.sampa import UnknownSegmentError

    # Parse the NST transcription into syllables.
    raw_syllables = _syllables(transcription)
    if not raw_syllables:
        return None, REFUSE_NO_PATH

    # Convert each SAMPA syllable into individual IPA phones, tracking
    # syllable boundaries in the flat phone list.  Strip tone marks ("" and ")
    # before alignment — they are suprasegmental, not phone segments.
    phones: list[str] = []
    syllable_boundaries: list[int] = []  # phone index after each syllable
    for syl in raw_syllables:
        try:
            # Strip suprasegmental markers: "" (tone-2), " (stress), % (secondary stress).
            phonetic = syl.replace('""', "").replace('"', "").replace("%", "")
            syl_phones = _parse_sampa_to_phones(phonetic)
        except UnknownSegmentError:
            return None, REFUSE_NO_PATH
        phones.extend(syl_phones)
        syllable_boundaries.append(len(phones))

    # No `if not phones` guard: a syllable that strips to nothing makes
    # _parse_sampa_to_phones raise UnknownSegmentError, which the except above
    # already turns into REFUSE_NO_PATH. The list cannot be empty here.
    steps = _steps(word, phones)
    if steps is None:
        return None, REFUSE_NO_PATH

    # phone index → the step that produced it
    phone_step: dict[int, tuple[int, int, int, int]] = {}
    for s in steps:
        for p in range(s[2], s[3]):
            phone_step[p] = s
    silent_letters = {i for (l0, l1, p0, p1) in steps if p0 == p1 for i in range(l0, l1)}

    # Compute letter-space cut offsets at each syllable boundary.
    cuts: list[int] = []
    for boundary in syllable_boundaries[:-1]:
        # The boundary is the phone index AFTER the last phone of the
        # current syllable — the first phone of the NEXT syllable.
        st = phone_step[boundary]
        if st[2] != boundary:
            return None, REFUSE_CUT_IN_GRAPHEME
        c = st[0]
        if c - 1 in silent_letters:
            nxt = phones[boundary]
            if word[c - 1] == "r" or (nxt and nxt[0] in RETROFLEX):
                return None, REFUSE_SILENT_AT_CUT
        cuts.append(c)

    return _pieces_from_cuts(word, cuts)


def _pieces_from_cuts(word: str, cuts: list[int]) -> tuple[list[str] | None, str]:
    """Slice *word* at *cuts*, refusing rather than emitting an empty piece.

    Split out so the refusal is reachable from a test with synthetic cuts. The
    alignment above should never produce a non-increasing cut or one at the end
    of the word — cuts are the letter offsets of successive phones — but an
    empty caption is silent and wrong, so the check is cheap insurance rather
    than dead code.
    """
    out: list[str] = []
    prev = 0
    for c in cuts:
        if c <= prev:
            return None, REFUSE_EMPTY
        out.append(word[prev:c])
        prev = c
    if prev >= len(word):
        return None, REFUSE_EMPTY
    out.append(word[prev:])
    return out, ""


@functools.lru_cache(maxsize=4096)
def lexicon_reading(word: str, db_path: Path | None = None) -> tuple[list[str], str | None] | None:
    """The orthographic split plus the reading that produced it, or ``None``.

    No UPOS parameter, deliberately: boundaries must not depend on a POS tag
    the breakdown builder does not have, and the tiebreak below is
    POS-independent.  This is the SINGLE fact both consumers share —
    ``phoneme_plan.py`` uses the SAME reading for sound that the caption's cut
    used — so **boundaries and phonemes can never cross** (the module invariant).

    Three-way contract (tunatale-k318.5):
    - ``None``: no adoptable split — every case where
      :func:`lexicon_syllable_split` returns ``None``.
    - ``(split, None)``: every candidate reading AGREED on *split*. No single
      reading was chosen; they may still differ in SOUND, so a phoneme consumer
      must keep applying the all-candidates-agree gate.
    - ``(split, <transcription>)``: the readings DISAGREED on the split and the
      most-enunciated tiebreak CHOSE one reading; *transcription* is that
      winner's raw X-SAMPA. Boundaries and sound BOTH come from this reading.

    Opens a fresh :class:`NstLexicon` per call (the function is cached, so
    this only runs once per distinct word).  Never opens the database on the
    import path.

    *db_path* exists so a test can point at a fixture database or at a missing
    one; production callers leave it ``None`` and get ``DB_PATH``.
    """
    from app.plugins.languages.no.lexicon import DB_PATH

    path = DB_PATH if db_path is None else db_path
    if not nst_lexicon_installed(path):
        return None

    # No try/except around the query: the probe above already returned for a
    # missing build, and NstLexicon only raises FileNotFoundError when the file
    # is absent. The remaining window — the file vanishing between the probe and
    # the query — is not reachable through this function, and an untested
    # except: is worse than no except:.
    # Context-managed: this constructs a lexicon PER WORD, so leaking the
    # connection to the collector exhausts file descriptors on a whole-
    # wordlist sweep (the lru_cache below is 4096 against ~50k entries).
    with NstLexicon(path) as _lex:
        candidates = _lex.candidate_transcriptions(word)
    if not candidates:
        return None

    # Align every candidate; adopt only the split all readings agree on.
    pairs: list[tuple[str, list[str]]] = []
    for transcription in candidates:
        pieces, reason = orthographic_syllables(word, transcription)
        if pieces is None:
            return None
        pairs.append((transcription, pieces))

    # `pairs` cannot be empty: `candidates` is non-empty above and the loop
    # either returns or appends for each one.
    first = pairs[0][1]
    if all(split == first for _transcription, split in pairs[1:]):
        # Every reading agrees on the split; none was chosen.
        return first, None

    # Readings cut the word differently, so the agree-or-refuse rule is out of
    # answers. tunatale-k318: pick the most-enunciated reading — the one that
    # elides least. Measured over the whole 50006-word wordlist, this decides
    # 43 of the 54 disagreeing words and leaves 11 ties as refusals.
    vowels = frozenset("aeiouyæøå")

    def _vowelless(split: list[str]) -> bool:
        """Whether any piece would caption a syllable with no vowel."""
        return any(not any(ch in vowels for ch in piece) for piece in split)

    # Stress marks and syllable dots are NOT segments. Counting them measured
    # stress-mark count and syllable count rather than elision, which decided
    # gylden/tanger/grunder on the stress mark alone — the one signal this
    # project explicitly does not caption on.
    _MARKS = frozenset("ˈˌ.")

    def _segments(transcription: str) -> int:
        return sum(1 for phone in _parse_sampa_to_phones(transcription) if phone not in _MARKS)

    # The vowelless discard is load-bearing: without it the naive count picks
    # flat -> ['fla','t'], studerte -> ['stu','de','rt','e'], tekstil ->
    # ['tek','sti','l'] — all with a clean alternative. Fall back to the full
    # set when discarding empties it.
    remaining = [(t, s) for t, s in pairs if not _vowelless(s)]
    if not remaining:
        remaining = pairs

    def _one_winner(lvl: list[tuple[str, list[str]]]) -> tuple[list[str], str | None]:
        """Return ``(split, transcription)`` when one reading won, else ``(split, None)``.

        Callers guarantee every pair in ``lvl`` shares ONE distinct split (the
        winning one).  When several readings share that split — same length,
        but possibly different sounds — the split is decided but no single
        SOUND won, so return ``(winning, None)``: the phoneme gate keeps the
        right to refuse rather than pin an arbitrary sound.  This keeps
        ``lexicon_syllable_split``'s answer identical in every case (LOST=0)
        while never crossing boundaries with a sound a different reading
        produced.
        """
        winning = lvl[0][1]
        same = [t for t, s in lvl if tuple(s) == tuple(winning)]
        return (winning, same[0]) if len(same) == 1 else (winning, None)

    # Level 1 — fewer phonemic segments = more elision, so the most-segments
    # reading is the careful citation form a fragment heard alone should be.
    most = max(_segments(t) for t, _s in remaining)
    l1 = [(t, s) for t, s in remaining if _segments(t) == most]
    if len({tuple(s) for _t, s in l1}) == 1:
        return _one_winner(l1)

    # Level 2 — the readings say the SAME sounds and disagree only on where the
    # boundary falls, so no caption built from either can lie about the audio.
    # Prefer the finer split: a buildup drill wants the smaller rungs.
    finest = max(len(s) for _t, s in l1)
    l2 = [(t, s) for t, s in l1 if len(s) == finest]
    if len({tuple(s) for _t, s in l2}) == 1:
        return _one_winner(l2)

    # Still tied: same sounds, same syllable count, different cut. The rule does
    # not decide, and refusing stays correct.
    return None


@functools.lru_cache(maxsize=4096)
def lexicon_has_secondary_stress(word: str, db_path: Path | None = None) -> bool | None:
    """Whether ANY of *word*'s readings carries a ``%`` secondary-stress mark.

    Returns ``None`` when *word* is ABSENT from the lexicon — no transcription,
    no ``%`` signal, and the caller must leave the word untouched. Otherwise a
    boolean: ``True`` when at least one (minimum-certainty) reading carries
    ``%``, ``False`` when none does.

    ``tunatale-9yd0``: the NST transcriptions' ``%`` mark is Norwegian's own
    compound signal — a prosodic compound carries secondary stress on its second
    element; a simplex word does not. ``segment_compound`` uses this to refuse
    an over-split: a KNOWN word with no ``%`` reading is not a compound.

    The ``any`` (not ``all``) is deliberate and conservative: a word with
    several readings keeps its split if ANY reading carries ``%``. Mirrors
    ``lexicon_syllable_split``'s caching and signature — opens a fresh lexicon
    per distinct word, cached at module level, never opened on the import path.
    """
    from app.plugins.languages.no.lexicon import DB_PATH

    path = DB_PATH if db_path is None else db_path
    if not nst_lexicon_installed(path):
        return None
    with NstLexicon(path) as _lex:
        candidates = _lex.all_transcriptions(word)
    if not candidates:
        return None
    return any("%" in transcription for transcription in candidates)


@functools.lru_cache(maxsize=4096)
def lexicon_syllable_split(word: str, db_path: Path | None = None) -> list[str] | None:
    """The split every reading agrees on — or, when they disagree, the most
    enunciated one — else ``None``.

    Thin wrapper over :func:`lexicon_reading` returning only the split, so the
    most-enunciated tiebreak has exactly ONE implementation: the boundary half
    and the phoneme half (``phoneme_plan.py``) consume the same decision, and
    two copies cannot drift.  Signature, caching, and every existing behaviour
    are identical to the old standalone implementation.

    No UPOS parameter, deliberately: boundaries must not depend on a POS tag
    the breakdown builder does not have.  Phonemes may still use POS (that is
    ``phoneme_plan.py``) — but only on the ``(split, None)`` path, where readings
    agreed.  Where the tiebreak CHOSE a reading, ``phoneme_plan`` takes its
    sound from that same reading, so boundaries and phonemes still cannot cross.

    Opens a fresh :class:`NstLexicon` per call (the function is cached, so
    this only runs once per distinct word).  Never opens the database on the
    import path.

    *db_path* exists so a test can point at a fixture database or at a missing
    one; production callers leave it ``None`` and get ``DB_PATH``.
    """
    reading = lexicon_reading(word, db_path)
    if reading is None:
        return None
    split, _transcription = reading
    return split
