#!/usr/bin/env python
"""Build the Cebuano affix-count table: how often native news uses each root with each pattern affix.

    uv run --with pyarrow python scripts/build_cebuano_affix_counts.py            # default source
    uv run --with pyarrow python scripts/build_cebuano_affix_counts.py --source X.parquet

``ceb/affix_patterns.py`` offers a root for an affix pair (``molakaw`` /
``milakaw``) only when real Cebuano uses both forms (tunatale-ve4p.2). This
writes the evidence, ``ceb/data/cebuano_affix_counts.tsv.gz``: one
``root<TAB>affix<TAB>count`` row per pair seen at least ``--min-count`` times.

**Source.** The same FineWeb-2 ``ceb_Latn`` shard and the same four native
news hosts as ``build_cebuano_frequency.py``, which documents why.

**Why raw surfaces.** The frequency table folds every surface onto its root,
so it cannot say which affix a root was seen with. Here the surface is the key.

**Which surfaces count.** ``affix + root`` must be a VERB form of that root in
the lemma table, and the surface's default reading must itself be a verb. The
second half drops homographs whose everyday word is something else: ``mahimo``
is the adverb "can" 2,816 times, which says nothing about ma- on ``himo``, and
``Magsaysay`` is a surname. It deliberately does NOT require the default LEMMA
to be the root, because the table files some real forms under themselves
(``mopalit``, ``mogawas``); requiring it lost ``moinom`` (70) and ``makaon``
(81) when measured. Roots under three letters are skipped: ``na`` + ``a`` is
the word ``naa``.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.plugins.languages.ceb.affix_patterns import COUNTS_PATH, PATTERN_AFFIXES, spell  # noqa: E402
from app.srs.lemma_table import Reading  # noqa: E402
from app.srs.tokenizer import tokenize  # noqa: E402
from scripts.build_cebuano_frequency import DEFAULT_SOURCE, LEMMA_TABLE, _native_texts  # noqa: E402

MIN_COUNT = 5
MIN_ROOT_LENGTH = 3


def _key(surface: str) -> str:
    """News writes ``mag-ampo`` and ``magampo`` for the same form."""
    return surface.lower().replace("-", "")


def count_surfaces(texts: Iterable[str]) -> Counter[str]:
    """Occurrences of each word surface over *texts*, case and hyphens folded."""
    counts: Counter[str] = Counter()
    for text in texts:
        counts.update(_key(token) for token in tokenize(text) if any(c.isalpha() for c in token))
    return counts


def is_verb_form_of(surface: str, root: str, readings: Callable[[str], list[Reading]]) -> bool:
    """Whether the table reads *surface* as a verb form of *root* and as a verb by default."""
    found = readings(surface)
    if not any(r.upos == "VERB" and r.lemma == root for r in found):
        return False
    return all(r.upos == "VERB" for r in found if r.is_default)


def affix_counts(
    surfaces: Counter[str], roots: Iterable[str], *, readings: Callable[[str], list[Reading]]
) -> dict[tuple[str, str], int]:
    """``(root, affix) -> count`` for every pattern affix a root was seen with."""
    out: dict[tuple[str, str], int] = {}
    for root in roots:
        if len(_key(root)) < MIN_ROOT_LENGTH:
            continue
        for affix in PATTERN_AFFIXES:
            surface = spell(root, affix)
            count = surfaces.get(_key(surface), 0)
            if count and is_verb_form_of(surface, root, readings):
                out[(root, affix)] = count
    return out


def write_table(counts: dict[tuple[str, str], int], out: Path, *, source: str, min_count: int) -> int:
    """Write *counts* as the gzipped TSV ``ceb/affix_patterns.py`` reads; return rows written."""
    rows = sorted((root, affix, c) for (root, affix), c in counts.items() if c >= min_count)
    out.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0: the same counts produce byte-identical output, so a rebuild with
    # no data change leaves git clean.
    with out.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(f"#source={source}\n".encode())
        gz.write("".join(f"{root}\t{affix}\t{c}\n" for root, affix, c in rows).encode())
    return len(rows)


def _verb_roots(lemma_table: Path) -> list[str]:
    roots: set[str] = set()
    with gzip.open(lemma_table, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.startswith("#"):
                _surface, upos, lemma, _default = line.rstrip("\n").split("\t")
                if upos == "VERB":
                    roots.add(lemma)
    return sorted(roots)


def main(argv: list[str] | None = None) -> int:
    from app.srs.lemma_table import TableLemmatizer

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="FineWeb-2 ceb_Latn parquet shard")
    parser.add_argument("--out", type=Path, default=COUNTS_PATH)
    parser.add_argument("--min-count", type=int, default=MIN_COUNT)
    args = parser.parse_args(argv)

    sha = hashlib.sha256(args.source.read_bytes()).hexdigest()
    surfaces = count_surfaces(_native_texts(args.source))
    lemmatizer = TableLemmatizer("ceb", LEMMA_TABLE)
    counts = affix_counts(surfaces, _verb_roots(LEMMA_TABLE), readings=lemmatizer.readings)
    lemmatizer.close()
    written = write_table(counts, args.out, source=f"fineweb-2/ceb_Latn sha256={sha[:12]}", min_count=args.min_count)
    print(f"{sum(surfaces.values())} tokens, {len(counts)} (root, affix) pairs seen, {written} written")
    print(f"source sha256 {sha}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
