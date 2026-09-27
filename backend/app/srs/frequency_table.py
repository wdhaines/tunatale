"""Corpus frequency for a language wordfreq does not cover (tunatale-u8nz.6).

A plugin ships a gzipped TSV of ``lemma<TAB>count`` rows under two header lines,
``#source=…`` and ``#total_tokens=N``, and registers its path as
``frequency_table_path``. :meth:`FrequencyTable.zipf` answers on wordfreq's
scale — log10 of occurrences per billion tokens, ``0.0`` when absent — so the
creation-candidate ranker cannot tell the two sources apart.

The table is keyed by the lemma the language's lemmatizer produces (a root for
a table lemmatizer, the lowercased surface for a word it does not know), because
that is the key the ranker looks up.
"""

from __future__ import annotations

import gzip
import math
from functools import cache
from pathlib import Path


class FrequencyTable:
    def __init__(self, counts: dict[str, int], total_tokens: int) -> None:
        self._counts = counts
        self._total = total_tokens

    def zipf(self, lemma: str) -> float:
        count = self._counts.get(lemma.casefold())
        if not count:
            return 0.0
        return round(math.log10(count * 1e9 / self._total), 2)


@cache
def load_frequency_table(path: Path) -> FrequencyTable:
    """Read *path* once per process; raises ``ValueError`` when the total is missing."""
    counts: dict[str, int] = {}
    total: int | None = None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith("#total_tokens="):
                total = int(line.partition("=")[2])
            elif line and not line.startswith("#"):
                lemma, _, count = line.partition("\t")
                counts[lemma] = int(count)
    if total is None:
        raise ValueError(f"{path}: no #total_tokens= header, so no zipf can be computed")
    return FrequencyTable(counts, total)
