#!/usr/bin/env python3
"""Read-only report: segmentation vs NST-lexicon syllable boundary disagreements.

``tunatale-9yd0`` suspects :func:`segment_compound` over-splits Norwegian
words and proposes the NST lexicon's orthographic syllable boundaries as an
independent signal. That suspicion cannot be settled mechanically — telling a
genuine over-split (``eks+per+t``) from a correct segmentation whose boundary a
syllable legitimately spans (``for+står``) is a linguistic judgement a human
makes from a list. This script builds that list; it changes no segmentation.

Corpus definition: the first ``--limit`` (default 4000) non-comment,
non-blank lines of ``no_wordlist.txt`` (frequency-ordered Bokmål). A word is
*compared* when :func:`segment_compound` splits it into >=2 parts that join
back to the surface AND the lexicon has a syllable split that joins back to it.
A word absent from the lexicon is ``lexicon_miss`` — unmeasured, never
agreement.

Discriminator: a piece-list's boundary set is the cumulative sum of piece
lengths, with the final offset removed. A part boundary is *disputed* when it
is not also a syllable boundary — i.e. it falls strictly inside one lexicon
syllable. The three-way classification then separates the inflectional cut
from stem disputes, because Norwegian syllabification never breaks before a
lone coda and that cut is disputed as a matter of course:

- ``fully_agree`` — no disputed boundary;
- ``only_infl_disputed`` — disputed, but exclusively at the inflectional cut
  (the last part boundary, and only when the peeled ending is a real
  inflection);
- ``stem_disputed`` — at least one disputed boundary that is not the
  inflectional cut.

Read-only: no database writes, no files written. Always exits 0 on a
successful run — this is a report for a human, not a CI gate.

Usage::

    uv run python scripts/report_segmentation_disputes.py [--limit N]

Output: a tally block, then one TSV row per ``stem_disputed`` word
(word, ``+``-joined parts, ``-``-joined syllables, disputed stem offsets).
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.plugins.languages.no.lexicon_syllables import lexicon_syllable_split
from app.plugins.languages.no.norwegian_breakdown import _INFLECTIONS, segment_compound

_WORDLIST = Path(__file__).resolve().parent.parent / "app" / "plugins" / "languages" / "no" / "data" / "no_wordlist.txt"

# The three-way split of compared words; ``compared`` is their sum.
_AGREE_BUCKETS = ("fully_agree", "only_infl_disputed", "stem_disputed")

# Tally-block print order — a human reading the report reads top to bottom.
_TALLY_ORDER = (
    "single_part",
    "parts_unalignable",
    "lexicon_miss",
    "syll_unalignable",
    "compared",
    "fully_agree",
    "only_infl_disputed",
    "stem_disputed",
)


@dataclass(frozen=True)
class DisputeRow:
    """One ``stem_disputed`` word: the pieces, the syllables, and the disputed
    stem offsets (inflectional cut excluded)."""

    word: str
    parts: tuple[str, ...]
    syllables: tuple[str, ...]
    disputed_offsets: tuple[int, ...]


def boundaries(pieces: list[str]) -> list[int]:
    """Character offsets where successive *pieces* meet (final offset removed).

    The boundary set of a piece list is the cumulative sum of piece lengths,
    with the final offset (``len("".join(pieces))``) removed.
    """
    offsets: list[int] = []
    acc = 0
    for piece in pieces[:-1]:
        acc += len(piece)
        offsets.append(acc)
    return offsets


def classify(word: str) -> tuple[str, DisputeRow | None]:
    """Bucket *word*; return ``(bucket, row)`` where *row* is non-None only for
    compared buckets.

    Buckets: ``single_part``, ``parts_unalignable``, ``lexicon_miss``,
    ``syll_unalignable``, ``fully_agree``, ``only_infl_disputed``,
    ``stem_disputed``.
    """
    parts = segment_compound(word)
    if len(parts) < 2:
        return "single_part", None
    if "".join(parts) != word:
        return "parts_unalignable", None
    syll = lexicon_syllable_split(word)
    if syll is None:
        return "lexicon_miss", None
    if "".join(syll) != word:
        return "syll_unalignable", None

    part_bounds = boundaries(parts)
    syll_set = set(boundaries(syll))
    disputed = [o for o in part_bounds if o not in syll_set]

    # The inflectional cut is the last part boundary, and only when the peeled
    # ending is a real inflection — segment_compound can also split a stem that
    # carries no ending at all.
    infl_cut = part_bounds[-1] if parts[-1] in _INFLECTIONS else None
    stem_disputed = [o for o in disputed if o != infl_cut]
    row = DisputeRow(word=word, parts=tuple(parts), syllables=tuple(syll), disputed_offsets=tuple(stem_disputed))
    if stem_disputed:
        return "stem_disputed", row
    if disputed:
        return "only_infl_disputed", row
    return "fully_agree", row


def read_words(path: Path, limit: int) -> list[str]:
    """The first *limit* non-comment, non-blank lines of *path*, NFC-normalized.

    Normalization is not cosmetic. Every comparison in this module is character
    -offset arithmetic, so a decomposed source (``a`` + U+030A instead of
    ``å``) shifts every offset past the first accented vowel. It does not raise
    — ``segment_compound`` simply fails to match any stem, every word lands in
    ``single_part``, and the report reads "nothing to compare". That is a clean
    negative: indistinguishable from a corpus that genuinely has no compounds.
    The committed wordlist is already NFC, so this is a no-op on it and a
    guard against whatever ``--wordlist`` is pointed at.
    """
    if limit <= 0:
        return []
    words: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        words.append(unicodedata.normalize("NFC", line))
        if len(words) >= limit:
            break
    return words


def build_report(words: list[str]) -> tuple[Counter[str], list[DisputeRow]]:
    """Bucket every word; return the tally and the ``stem_disputed`` rows."""
    counts: Counter[str] = Counter()
    rows: list[DisputeRow] = []
    for word in words:
        bucket, row = classify(word)
        counts[bucket] += 1
        if bucket == "stem_disputed" and row is not None:
            rows.append(row)
    return counts, rows


def print_tally(counts: Counter[str]) -> None:
    """Print the tally block; ``compared`` is derived, never double-counted."""
    compared = sum(counts[b] for b in _AGREE_BUCKETS)
    print(f"corpus: {sum(counts.values())} words (single_part + unalignable + lexicon_miss + compared)")
    for key in _TALLY_ORDER:
        print(f"{key}\t{compared if key == 'compared' else counts[key]}")


def print_rows(rows: list[DisputeRow]) -> None:
    """Print one TSV row per ``stem_disputed`` word, sorted by word."""
    if not rows:
        print("stem_disputed: none")
        return
    print("word\tparts\tsyllables\tdisputed_stem_offsets")
    for row in sorted(rows, key=lambda r: r.word):
        print(f"{row.word}\t{'+'.join(row.parts)}\t{'-'.join(row.syllables)}\t{list(row.disputed_offsets)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=4000, help="first N non-comment, non-blank wordlist lines")
    parser.add_argument("--wordlist", type=Path, default=_WORDLIST, help="Bokmål frequency wordlist (read-only)")
    args = parser.parse_args(argv)

    if not args.wordlist.is_file():
        print(f"FAIL: no such wordlist: {args.wordlist}", file=sys.stderr)
        return 1

    counts, rows = build_report(read_words(args.wordlist, args.limit))
    print_tally(counts)
    print_rows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
