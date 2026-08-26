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
def lexicon_syllable_split(word: str, db_path: Path | None = None) -> list[str] | None:
    """The orthographic split EVERY candidate reading agrees on, or ``None``.

    No UPOS parameter, deliberately: boundaries must not depend on a POS tag
    the breakdown builder does not have.  Phonemes still use POS (that is
    ``phoneme_plan.py``, already shipped) — but only within a split all readings
    already agree on, so the two can never cross.

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
    candidates = NstLexicon(path).candidate_transcriptions(word)
    if not candidates:
        return None

    # Align every candidate; adopt only the split all readings agree on.
    splits: list[list[str]] = []
    for transcription in candidates:
        pieces, reason = orthographic_syllables(word, transcription)
        if pieces is None:
            return None
        splits.append(pieces)

    # `splits` cannot be empty: `candidates` is non-empty above and the loop
    # either returns or appends for each one.
    first = splits[0]
    if all(s == first for s in splits[1:]):
        return first
    return None
