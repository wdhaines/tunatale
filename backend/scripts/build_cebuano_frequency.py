#!/usr/bin/env python
"""Build the Cebuano corpus-frequency table from FineWeb-2's native Cebuano news.

    uv run --with pyarrow python scripts/build_cebuano_frequency.py            # default source
    uv run --with pyarrow python scripts/build_cebuano_frequency.py --source X.parquet

wordfreq has no ``ceb``, so the creation-candidate ranker had nothing to rank
Cebuano by (tunatale-u8nz.6). This writes ``ceb/data/cebuano_frequency.tsv.gz``,
read by ``app.srs.frequency_table``, and beside it
``cebuano_surface_frequency.tsv.gz``: the same tokens counted by the word as
written, for whatever needs whole-word evidence (the Handbook spelling
converter, the Wolff parser's English test; see :func:`count_tokens`).

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
SURFACE_OUT = _CEB_DATA / "cebuano_surface_frequency.tsv.gz"
LEMMA_TABLE = _CEB_DATA / "cebuano_lemmas.tsv.gz"

NATIVE_NEWS_HOSTS = ("sunstar.com.ph", "rmn.ph", "rpnradio.com", "pia.gov.ph")
ENGLISH_ZIPF = 3.0
ENGLISH_ZIPF_CAPITALIZED = 4.5
MIN_COUNT = 5


def is_native_host(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return any(host == h or host.endswith("." + h) for h in NATIVE_NEWS_HOSTS)


def count_tokens(
    texts: Iterable[str],
    *,
    readings: Callable[[str], list[Reading]],
    en_zipf: Callable[[str], float],
) -> tuple[Counter[str], Counter[str]]:
    """(lemma counts, surface counts) over *texts*, English runs dropped (see the module docstring).

    One pass, two keys for the same tokens. Lemma counts rank words for learning:
    a root collects its affixed forms. Surface counts say how often the corpus
    WRITES a word, which is what whole-word evidence needs (the Handbook spelling
    converter, the Wolff parser's English test). Keyed by lemma, a form the lemma
    table knows vanishes into its root (magsige 307 into sige), while a
    misspelling the table does not know (magsigi 48) stays the only whole word.
    """
    keyed: dict[str, tuple[str, str, bool]] = {}

    def key(raw: str) -> tuple[str, str, bool]:
        if raw not in keyed:
            surface = raw.lower()
            found = readings(surface)
            if found:
                keyed[raw] = (next(r for r in found if r.is_default).lemma, surface, False)
            else:
                bar = ENGLISH_ZIPF if raw == surface else ENGLISH_ZIPF_CAPITALIZED
                keyed[raw] = (surface, surface, en_zipf(surface) >= bar)
        return keyed[raw]

    lemmas: Counter[str] = Counter()
    surfaces: Counter[str] = Counter()
    for text in texts:
        tokens = [key(t) for t in tokenize(text) if any(c.isalpha() for c in t)]
        for i, (lemma, surface, english) in enumerate(tokens):
            in_run = english and ((i > 0 and tokens[i - 1][2]) or (i + 1 < len(tokens) and tokens[i + 1][2]))
            if not in_run:
                lemmas[lemma] += 1
                surfaces[surface] += 1
    return lemmas, surfaces


def count_lemmas(
    texts: Iterable[str], *, readings: Callable[[str], list[Reading]], en_zipf: Callable[[str], float]
) -> Counter[str]:
    """Lemma counts over *texts* (see :func:`count_tokens`)."""
    return count_tokens(texts, readings=readings, en_zipf=en_zipf)[0]


def count_surfaces(
    texts: Iterable[str], *, readings: Callable[[str], list[Reading]], en_zipf: Callable[[str], float]
) -> Counter[str]:
    """Surface (whole-word, lowercased) counts over *texts* (see :func:`count_tokens`)."""
    return count_tokens(texts, readings=readings, en_zipf=en_zipf)[1]


_DIGEST_HEADER = "#lemma_defaults_sha256="


def lemma_defaults_digest(table: Path) -> str:
    """A digest of what the lemma table contributes to a count: each known surface and its default lemma.

    :func:`count_lemmas` keys a token by its surface's DEFAULT reading, or by the
    surface itself when the table does not know it, so the counts are stale
    exactly when this mapping moves. Non-default readings and row order key no
    token and are left out, so a table rebuilt with only those changes does not
    call for a corpus rebuild.
    """
    pairs = []
    with gzip.open(table, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            surface, _upos, lemma, is_default = line.rstrip("\n").split("\t")
            if is_default == "1":
                pairs.append(f"{surface}\t{lemma}\n")
    return hashlib.sha256("".join(sorted(pairs)).encode("utf-8")).hexdigest()


def recorded_lemma_defaults(table: Path) -> str | None:
    """The lemma-table digest a frequency table was built against, or ``None`` if it records none."""
    with gzip.open(table, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.startswith("#"):
                return None
            if line.startswith(_DIGEST_HEADER):
                return line[len(_DIGEST_HEADER) :].strip()
    return None


def write_table(counts: dict[str, int], out: Path, *, source: str, min_count: int, lemma_defaults: str) -> int:
    """Write *counts* as the gzipped TSV ``app.srs.frequency_table`` reads; return rows written.

    ``#total_tokens`` is the total over ALL counted tokens, rare ones included,
    so a zipf is a share of the corpus and not of the truncated list.
    *lemma_defaults* is :func:`lemma_defaults_digest` of the lemma table that
    keyed the counts, recorded so a later change to that table shows up as a
    failing test rather than as quietly wrong ranks.
    """
    rows = sorted(((lemma, c) for lemma, c in counts.items() if c >= min_count), key=lambda r: (-r[1], r[0]))
    out.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0: the same counts produce byte-identical output, so a rebuild with
    # no data change leaves git clean.
    with open(out, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(f"#source={source}\n#total_tokens={sum(counts.values())}\n{_DIGEST_HEADER}{lemma_defaults}\n".encode())
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
    parser.add_argument("--surface-out", type=Path, default=SURFACE_OUT)
    parser.add_argument("--min-count", type=int, default=MIN_COUNT)
    args = parser.parse_args(argv)

    sha = hashlib.sha256(args.source.read_bytes()).hexdigest()
    lemmatizer = TableLemmatizer("ceb", LEMMA_TABLE)
    counts, surfaces = count_tokens(
        _native_texts(args.source),
        readings=lemmatizer.readings,
        en_zipf=lambda s: wordfreq.zipf_frequency(s, "en"),
    )
    lemmatizer.close()
    digest = lemma_defaults_digest(LEMMA_TABLE)
    source = f"fineweb-2/ceb_Latn sha256={sha[:12]}"
    written = write_table(counts, args.out, source=source, min_count=args.min_count, lemma_defaults=digest)
    written_s = write_table(surfaces, args.surface_out, source=source, min_count=args.min_count, lemma_defaults=digest)
    print(f"{sum(counts.values())} tokens, {len(counts)} lemmas, {written} written (count >= {args.min_count})")
    print(f"{len(surfaces)} surfaces, {written_s} written")
    print(f"source sha256 {sha}")
    print(f"wrote {args.out} and {args.surface_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
