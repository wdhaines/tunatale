#!/usr/bin/env python
"""Build the Cebuano corpus-frequency table from FineWeb-2's native Cebuano news.

    uv run --with pyarrow python scripts/build_cebuano_frequency.py            # default source
    uv run --with pyarrow python scripts/build_cebuano_frequency.py --source X.parquet

wordfreq has no ``ceb``, so the creation-candidate ranker had nothing to rank
Cebuano by (tunatale-u8nz.6). This writes ``ceb/data/cebuano_frequency.tsv.gz``,
read by ``app.srs.frequency_table``.

**Source.** FineWeb-2, subset ``ceb_Latn`` (HuggingFaceFW/fineweb-2, ODC-By
1.0), fetched by hand into ``scripts/local/fineweb2/`` (gitignored). Only four
native news hosts are counted (:data:`NATIVE_NEWS_HOSTS`): measured 2026-09-27,
the rest is Wikipedia bot stubs (~9%) or sites that are machine-translated
from English, the two traps the bead warns about. The user chose news only,
without an LLM-dialogue blend, knowing the register tilts to police/mayor/
barangay.

**Key.** Every token is counted under the lemma the runtime lemmatizer would
give it: the DEFAULT reading's lemma when the lemma table knows the surface, the
lowercased surface when it does not. That is the key ``_zipf_for`` looks up,
so a root collects all of its affixed forms.

**English.** Philippine news code-switches. A token the lemma table does not
know is English-looking when its wordfreq English zipf is at least 3.0 (4.5
when capitalized, so personal names do not qualify), and it is dropped only
inside an English RUN — when a neighbour is English-looking too. Measured on
this corpus: ``the``/``of``/``police`` lose 88-98% of their count, while
Cebuano words that collide with English (``ka``, ``o``, ``wa``, ``si``) lose
11-36%, which leaves their ranks intact. A flat zipf threshold could not tell
the two apart. It needs no Cebuano word list, which matters because
Wiktionary's Cebuano extract lacks ``mga``, ``ka``, ``si``, ``aron``, ``usab``
entirely.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.srs.lemma_table import Reading  # noqa: E402
from app.srs.tokenizer import tokenize  # noqa: E402

_BACKEND = Path(__file__).resolve().parents[1]
_CEB_DATA = _BACKEND / "app/plugins/languages/ceb/data"
DEFAULT_SOURCE = _BACKEND / "scripts/local/fineweb2/ceb_Latn-000_00000.parquet"
DEFAULT_OUT = _CEB_DATA / "cebuano_frequency.tsv.gz"
LEMMA_TABLE = _CEB_DATA / "cebuano_lemmas.tsv.gz"

NATIVE_NEWS_HOSTS = ("sunstar.com.ph", "rmn.ph", "rpnradio.com", "pia.gov.ph")
ENGLISH_ZIPF = 3.0
ENGLISH_ZIPF_CAPITALIZED = 4.5
MIN_COUNT = 5


def is_native_host(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return any(host == h or host.endswith("." + h) for h in NATIVE_NEWS_HOSTS)


def count_lemmas(
    texts: Iterable[str],
    *,
    readings: Callable[[str], list[Reading]],
    en_zipf: Callable[[str], float],
) -> Counter[str]:
    """Lemma counts over *texts*, English runs dropped (see the module docstring)."""
    keyed: dict[str, tuple[str, bool]] = {}

    def key(raw: str) -> tuple[str, bool]:
        if raw not in keyed:
            surface = raw.lower()
            found = readings(surface)
            if found:
                keyed[raw] = (next(r for r in found if r.is_default).lemma, False)
            else:
                bar = ENGLISH_ZIPF if raw == surface else ENGLISH_ZIPF_CAPITALIZED
                keyed[raw] = (surface, en_zipf(surface) >= bar)
        return keyed[raw]

    counts: Counter[str] = Counter()
    for text in texts:
        tokens = [key(t) for t in tokenize(text) if any(c.isalpha() for c in t)]
        for i, (lemma, english) in enumerate(tokens):
            in_run = english and ((i > 0 and tokens[i - 1][1]) or (i + 1 < len(tokens) and tokens[i + 1][1]))
            if not in_run:
                counts[lemma] += 1
    return counts


def write_table(counts: dict[str, int], out: Path, *, source: str, min_count: int) -> int:
    """Write *counts* as the gzipped TSV ``app.srs.frequency_table`` reads; return rows written.

    ``#total_tokens`` is the total over ALL counted tokens, rare ones included,
    so a zipf is a share of the corpus and not of the truncated list.
    """
    rows = sorted(((lemma, c) for lemma, c in counts.items() if c >= min_count), key=lambda r: (-r[1], r[0]))
    out.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0: the same counts produce byte-identical output, so a rebuild with
    # no data change leaves git clean.
    with open(out, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(f"#source={source}\n#total_tokens={sum(counts.values())}\n".encode())
        gz.write("".join(f"{lemma}\t{c}\n" for lemma, c in rows).encode())
    return len(rows)


def _native_texts(source: Path) -> Iterable[str]:
    import pyarrow.parquet as pq

    table = pq.read_table(source, columns=["text", "url"])
    for text, url in zip(table.column("text").to_pylist(), table.column("url").to_pylist(), strict=True):
        if is_native_host(url):
            yield text


def main(argv: list[str] | None = None) -> int:
    import wordfreq

    from app.srs.lemma_table import TableLemmatizer

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="FineWeb-2 ceb_Latn parquet shard")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--min-count", type=int, default=MIN_COUNT)
    args = parser.parse_args(argv)

    sha = hashlib.sha256(args.source.read_bytes()).hexdigest()
    lemmatizer = TableLemmatizer("ceb", LEMMA_TABLE)
    counts = count_lemmas(
        _native_texts(args.source),
        readings=lemmatizer.readings,
        en_zipf=lambda s: wordfreq.zipf_frequency(s, "en"),
    )
    lemmatizer.close()
    written = write_table(counts, args.out, source=f"fineweb-2/ceb_Latn sha256={sha[:12]}", min_count=args.min_count)
    print(f"{sum(counts.values())} tokens, {len(counts)} lemmas, {written} written (count >= {args.min_count})")
    print(f"source sha256 {sha}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
