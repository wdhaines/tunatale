"""Tagalog pronunciation readings: one Wiktionary reading per word.

The key-phrase breakdown cuts a word into syllables and plays each fragment as
IPA (tunatale-w4m7.16). Both halves must come from the SAME reading. A caption
cut by spelling rules while its IPA comes from a reading with a different
syllable count names a sound the audio does not make; ``siya`` is spelled with
two vowels and said in one syllable, ``[ˈʃa]``.

So this module resolves a word to ONE :class:`Reading`: Wiktionary's narrow IPA
(``data/tagalog_pronunciations.tsv.gz``, built by
``scripts/build_kaikki_pronunciation_table.py``) plus the spelling cut at that
reading's syllable boundaries. The boundaries come from aligning the spelling
letter by letter to the reading's phones, which works because Tagalog spelling
is close to phonemic.

**Which reading.** Wiktionary lists the careful form first in 155 of the 239
words whose readings disagree on syllable count, so "first" gives ``ka-i-lan``,
which the user heard as wrong, and "fewest syllables" gives ``san`` for
``saan``. The rule is the spelling's own: every written vowel keeps its
syllable unless the spelling marks it as a glide (:func:`_licensed`), and
among the readings that honour that, one that USES a licensed glide wins
(``kai-lan``, ``siya`` = ``[ˈʃa]``). Wiktionary's order breaks a tie.
"""

from __future__ import annotations

import gzip
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).parent / "data" / "tagalog_pronunciations.tsv.gz"

_IPA_VOWELS = frozenset("aɐeɛiɪoʊuəæʌœɔ")
_TIE = "\u0361"  # d͡ʒ: one phone written with two letters
_NON_SYLLABIC = "\u032f"  # aɪ̯: the second vowel is a glide, not a nucleus
_STRESS = "ˈˌ"

# Letter → the phones it may be read as. Deliberately generous within the
# language (d is also the tap ɾ between vowels, k also the fricative x, e/i and
# o/u trade freely) and closed outside it, so a reading of a DIFFERENT word
# fails to align instead of being forced onto this spelling.
_LETTER_PHONES = {
    "a": {"a", "ɐ", "ə", "æ", "ʌ"},
    "b": {"b", "β"},
    "c": {"k", "s"},
    "d": {"d", "ɾ", "r"},
    "e": {"ɛ", "e", "ɪ", "i", "ə"},
    "f": {"f", "p"},
    "g": {"ɡ", "ɣ", "g"},
    "h": {"h"},
    "i": {"i", "ɪ", "ɛ", "e", "j"},
    "j": {"d͡ʒ", "h"},
    "k": {"k", "x"},
    "l": {"l"},
    "m": {"m"},
    "n": {"n", "ŋ", "ɲ", "m"},
    "o": {"o", "ʊ", "u", "ɔ"},
    "p": {"p", "f"},
    "q": {"k"},
    "r": {"ɾ", "r", "ɹ"},
    "s": {"s", "ʃ", "z"},
    "t": {"t", "t͡ʃ", "t͡s"},
    "u": {"u", "ʊ", "o", "w"},
    "v": {"v", "b", "β"},
    "w": {"w", "ʊ", "u"},
    "x": {"h", "s"},
    "y": {"j", "ɪ", "i"},
    "z": {"z", "s"},
    "ñ": {"ɲ"},
    "-": {"ʔ"},
    "'": {"ʔ"},
}
# Two letters, one phone.
_DIGRAPH_PHONES = {
    "ng": {"ŋ"},
    "ts": {"t͡s", "t͡ʃ"},
    "sy": {"ʃ"},
    "ty": {"t͡ʃ"},
    "dy": {"d͡ʒ"},
    "ny": {"ɲ"},
    "ch": {"t͡ʃ"},
    "ph": {"f"},
}
# One letter, two phones.
_SPLIT_PHONES = {"x": ("k", "s")}
# Letters that may be silent. A dropped VOWEL is then judged by _licensed.
_SILENT = frozenset("iyuwh-'")
_SILENT_COST = 0.5
# Phones with no letter: the glottal stop Tagalog puts before every
# vowel-initial syllable, and a hiatus glide Wiktionary writes out.
_INSERT_COST = {"ʔ": 0.1, "j": 1.0, "w": 1.0}

_VOWEL_LETTERS = frozenset("aeiou")


@dataclass(frozen=True)
class Reading:
    """One word's pronunciation, cut into syllables on both sides.

    ``spelling`` rejoins to the lowercased word; ``syllables`` are the narrow
    IPA syllables exactly as Wiktionary writes them, one per spelling syllable.
    """

    ipa: str
    spelling: tuple[str, ...]
    syllables: tuple[str, ...]


def ipa_syllables(ipa: str) -> list[str]:
    """Split a narrow reading into syllables: at ``.``, and before a stress mark."""
    return [piece for chunk in ipa.split(".") for piece in re.split(f"(?=[{_STRESS}])", chunk) if piece]


def _phones(syllable: str) -> list[tuple[str, bool]] | None:
    """``(phone, is_nucleus)`` for one IPA syllable, or ``None`` for an unknown symbol.

    Stress and length modify the syllable, not the phone sequence, and the
    place diacritics (dental, retracted, unreleased) do not change which
    letter wrote the sound, so all of those are skipped.
    """
    phones: list[tuple[str, bool]] = []
    chars = list(syllable)
    i = 0
    while i < len(chars):
        c = chars[i]
        if c == _TIE and phones and i + 1 < len(chars):
            phones[-1] = (phones[-1][0] + c + chars[i + 1], False)
            i += 2
            continue
        if c == _NON_SYLLABIC and phones:
            phones[-1] = (phones[-1][0], False)
        elif c in _STRESS or c in "ːʰ" or unicodedata.combining(c):
            pass
        elif c in _IPA_VOWELS:
            phones.append((c, True))
        elif unicodedata.category(c).startswith("L"):
            phones.append((c, False))
        else:
            return None
        i += 1
    return phones


def _licensed(letters: str, i: int) -> bool:
    """May the written vowel at ``letters[i]`` be read as no syllable?

    Only an ``i`` or ``u`` the spelling marks as a glide: between a consonant
    and its own glide letter (``siya`` = ``[ˈʃa]``, ``buwan`` = ``[ˈbwan]``), or
    after a vowel and before a consonant or the end (``kailan`` = ``kai-lan``,
    the way ``bahay`` ends in ``-hay``). Anything else is a contraction
    (``saan`` read ``[ˈsan]``), which the careful reading is preferred over.
    """
    c = letters[i]
    if c not in "iu":
        return False
    prev = letters[i - 1] if i > 0 else ""
    nxt = letters[i + 1] if i + 1 < len(letters) else ""
    glide = "y" if c == "i" else "w"
    if prev and prev not in _VOWEL_LETTERS and nxt == glide:
        return True
    return prev in _VOWEL_LETTERS and prev != "" and (nxt == "" or nxt not in _VOWEL_LETTERS)


def _fold(word: str) -> str:
    """Lowercase, with accents stripped (Tagalog marks stress with them only in dictionaries)."""
    decomposed = unicodedata.normalize("NFD", word.lower())
    return unicodedata.normalize("NFC", "".join(c for c in decomposed if not unicodedata.combining(c) or c == "\u0303"))


def align_spelling(word: str, ipa: str) -> tuple[tuple[str, ...], int, int] | None:
    """Cut *word* at *ipa*'s syllable boundaries: ``(spelling, unlicensed, licensed)``.

    The counts are the written vowels the reading gives no syllable, split by
    whether the spelling marks them as glides. ``None`` when the reading cannot
    be aligned to the spelling at all: it has a sound no letter here writes, or
    a syllable with no written letter (``mga`` is ``[mɐˈŋa]``).
    """
    letters = _fold(word)
    syllables = ipa_syllables(ipa)
    phones: list[tuple[str, bool]] = []
    phone_syllable: list[int] = []
    for index, syllable in enumerate(syllables):
        parsed = _phones(syllable)
        if parsed is None or sum(nucleus for _, nucleus in parsed) != 1:
            return None
        phones.extend(parsed)
        phone_syllable.extend([index] * len(parsed))

    groups = _align(letters, [p for p, _ in phones])
    if groups is None:
        return None

    # Each letter takes the syllable of the first phone it wrote; a silent
    # letter joins the letter before it (the y of istasyon's "syon").
    letter_syllable: list[int | None] = [None] * len(letters)
    for start, stop, first_phone in groups:
        for i in range(start, stop):
            letter_syllable[i] = None if first_phone is None else phone_syllable[first_phone]
    for i, syl in enumerate(letter_syllable):
        if syl is None:
            letter_syllable[i] = letter_syllable[i - 1] if i > 0 else None
    if letter_syllable and letter_syllable[0] is None:
        following = next((s for s in letter_syllable if s is not None), None)
        letter_syllable = [following if s is None else s for s in letter_syllable]
    # Holds by construction, so it is asserted rather than branched on: every
    # nucleus is a vowel, vowels are never inserted, so each syllable's nucleus
    # is the first phone of some letter, and letters take phones in order.
    assert letter_syllable == sorted(letter_syllable) and set(letter_syllable) == set(range(len(syllables)))
    spelling = tuple(
        "".join(c for c, s in zip(letters, letter_syllable, strict=True) if s == k) for k in range(len(syllables))
    )

    unlicensed = licensed = 0
    for start, stop, first_phone in groups:
        if stop - start != 1 or letters[start] not in _VOWEL_LETTERS:
            continue
        if first_phone is not None and phones[first_phone][1]:
            continue
        if _licensed(letters, start):
            licensed += 1
        else:
            unlicensed += 1
    return spelling, unlicensed, licensed


def _align(letters: str, phones: list[str]) -> list[tuple[int, int, int | None]] | None:
    """Cheapest letter-to-phone alignment as ``(letter_start, letter_stop, first_phone)`` groups.

    A plain edit-distance lattice over the correspondences above; ``None``
    when no path exists. ``first_phone`` is ``None`` for a silent letter.
    """
    n, m = len(letters), len(phones)
    inf = float("inf")
    cost = [[inf] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple[int, int, int | None] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0

    def relax(i: int, j: int, ni: int, nj: int, step: float, first_phone: int | None) -> None:
        if cost[i][j] + step < cost[ni][nj]:
            cost[ni][nj] = cost[i][j] + step
            back[ni][nj] = (i, j, first_phone)

    for i in range(n + 1):
        for j in range(m + 1):
            if cost[i][j] == inf:
                continue
            if j < m and phones[j] in _INSERT_COST:
                relax(i, j, i, j + 1, _INSERT_COST[phones[j]], None)
            if i == n:
                continue
            letter = letters[i]
            if j < m and phones[j] in _LETTER_PHONES.get(letter, ()):
                relax(i, j, i + 1, j + 1, 0.0, j)
            if j < m and i + 1 < n and phones[j] in _DIGRAPH_PHONES.get(letters[i : i + 2], ()):
                relax(i, j, i + 2, j + 1, 0.0, j)
            if j + 1 < m and tuple(phones[j : j + 2]) == _SPLIT_PHONES.get(letter):
                relax(i, j, i + 1, j + 2, 0.0, j)
            if letter in _SILENT:
                relax(i, j, i + 1, j, _SILENT_COST, None)
    if cost[n][m] == inf:
        return None

    groups: list[tuple[int, int, int | None]] = []
    i, j = n, m
    while (i, j) != (0, 0):
        step = back[i][j]
        assert step is not None  # every reachable cell but the origin has a predecessor
        pi, pj, first_phone = step
        if pi != i:  # consumed letters (a pure phone insertion consumes none)
            groups.append((pi, i, first_phone))
        i, j = pi, pj
    return groups[::-1]


def choose_reading(word: str, candidates: list[str]) -> Reading | None:
    """The reading of *word* its spelling best supports, or ``None`` if none aligns.

    Fewest dropped vowels the spelling does not license, then most glides it
    does, then Wiktionary's order.
    """
    best: tuple[tuple[int, int, int], Reading] | None = None
    for rank, ipa in enumerate(candidates):
        aligned = align_spelling(word, ipa)
        if aligned is None:
            continue
        spelling, unlicensed, licensed = aligned
        key = (unlicensed, -licensed, rank)
        if best is None or key < best[0]:
            best = (key, Reading(ipa=ipa, spelling=spelling, syllables=tuple(ipa_syllables(ipa))))
    return None if best is None else best[1]


@lru_cache(maxsize=4)
def _load(path: Path) -> dict[str, list[tuple[str, str, bool]]]:
    """``word`` → ``(upos, ipa, is_default)`` rows, in the file's order."""
    table: dict[str, list[tuple[str, str, bool]]] = {}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            word, upos, _rank, ipa, is_default = line.rstrip("\n").split("\t")
            table.setdefault(word, []).append((upos, ipa, is_default == "1"))
    return table


def candidates(word: str, upos: str | None = None, path: Path = DATA_PATH) -> list[str]:
    """*word*'s readings for *upos*, else for its default part of speech, else all."""
    rows = _load(path).get(_fold(word), [])
    for keep in (lambda r: r[0] == upos, lambda r: r[2], lambda r: True):
        chosen = [ipa for r in rows if keep(r) for ipa in [r[1]]]
        if chosen:
            return list(dict.fromkeys(chosen))
    return []


@lru_cache(maxsize=8192)
def resolve_reading(word: str, upos: str | None = None, path: Path = DATA_PATH) -> Reading | None:
    """The one reading that sets *word*'s syllable boundaries AND its fragment IPA."""
    return choose_reading(_fold(word), candidates(word, upos, path))


def whole_word_ipa(word: str, upos: str | None = None, path: Path = DATA_PATH) -> str | None:
    """IPA for *word* spoken whole: the chosen reading, else the first listed.

    A whole word is one utterance and needs no syllable alignment, so ``mga``
    (``[mɐˈŋa]``, two syllables from one written vowel) still has an answer.
    """
    reading = resolve_reading(word, upos, path)
    if reading is not None:
        return reading.ipa
    listed = candidates(word, upos, path)
    return listed[0] if listed else None
