#!/usr/bin/env python3
"""Build the Norwegian Stanza lemma table (the prod lemmatizer's data).

Run on a machine that HAS stanza and the ``nb`` model (the laptop), from
``backend/``::

    uv run python scripts/build_stanza_lemma_table.py

Why a table reproduces Stanza: Stanza's nb lemmatizer is a function of
``(word, UPOS)`` alone — ``stanza/pipeline/lemma_processor.py`` says so ("the
lemmatizer only looks at one word when making decisions"), and it was measured:
lemmatizing each cached ``(surface, upos)`` out of context reproduced in-context
Stanza on 1,439/1,439 distinct triples (tunatale-kbb.18, 2026-09-18). Sentence
context only matters through WHICH upos the tagger picks.

So this script lemmatizes every NST surface (the IPA lexicon's word list) under
each UPOS its NST tag can stand for, and marks ONE row per surface as the
default reading: the one Stanza's tagger picks for the bare word. The runtime
engine (``app.srs.lemma_table``) serves the default unless something with
context — the lesson-generation resolver — chooses another tag.

Output: a gzipped TSV committed beside the NST extract. Its first line is
``#source_version=<stanza version>`` so the runtime can key its cache and reuse rows the real
Stanza wrote under that version.
"""

from __future__ import annotations

import gzip
import io
import sys
import time
from collections import defaultdict
from importlib.metadata import version
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
DATA = _BACKEND / "app/plugins/languages/no/data"
NST_EXTRACT = DATA / "nst_lexicon.tsv.gz"
OUTPUT = DATA / "stanza_lemmas.tsv.gz"

# NST POS tag -> the UD tags Stanza may give such a word. NST's PN covers both
# UD PRON and DET (alle, noen, sin); VB covers VERB and AUX (er, må); KN both
# conjunction classes. Proper nouns are the compound PM|... tags.
UPOS_FOR_NST = {
    "NN": ("NOUN",),
    "VB": ("VERB", "AUX"),
    "JJ": ("ADJ",),
    "AB": ("ADV",),
    "PN": ("PRON", "DET"),
    "DT": ("DET",),
    "PP": ("ADP",),
    "KN": ("CCONJ", "SCONJ"),
    "RG": ("NUM",),
    "RO": ("ADJ",),
    "IN": ("INTJ",),
    "IE": ("PART",),
}
# Deterministic tie-break when the bare-word tag is none of a surface's rows.
UPOS_ORDER = (
    "NOUN",
    "VERB",
    "ADJ",
    "ADV",
    "PRON",
    "DET",
    "ADP",
    "AUX",
    "NUM",
    "PROPN",
    "CCONJ",
    "SCONJ",
    "INTJ",
    "PART",
)
CHUNK = 20_000


def _upos_for(nst_pos: str) -> tuple[str, ...]:
    if nst_pos.startswith("PM"):
        return ("PROPN",)
    return UPOS_FOR_NST.get(nst_pos, ())


def _read_pairs() -> list[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    with gzip.open(NST_EXTRACT, "rt", encoding="utf-8") as fh:
        for line in fh:
            word, pos = line.rstrip("\n").split("\t")[:2]
            if not word.strip() or " " in word:
                continue
            pairs.update((word, u) for u in _upos_for(pos))
    return sorted(pairs)


def main() -> None:
    import stanza
    from stanza.models.common.doc import Document

    stanza_version = version("stanza")
    pairs = _read_pairs()
    print(f"{len(pairs)} (surface, upos) pairs from {NST_EXTRACT.name}", flush=True)

    lem = stanza.Pipeline(
        lang="nb",
        processors="lemma",
        lemma_pretagged=True,
        tokenize_pretokenized=True,
        download_method=None,
        verbose=False,
    )
    rows: dict[str, dict[str, str]] = defaultdict(dict)
    t0 = time.perf_counter()
    for i in range(0, len(pairs), CHUNK):
        part = pairs[i : i + CHUNK]
        doc = lem(Document([[{"id": 1, "text": w, "upos": u}] for w, u in part]))
        for (w, u), sent in zip(part, doc.sentences, strict=True):
            rows[w][u] = sent.words[0].lemma or w.lower()
        print(f"  lemmatized {i + len(part)}/{len(pairs)} ({time.perf_counter() - t0:.0f}s)", flush=True)

    # Default reading: the tag Stanza's tagger gives the bare word, when the
    # surface has more than one row to choose from.
    multi = sorted(w for w, r in rows.items() if len(r) > 1)
    tagger = stanza.Pipeline(
        lang="nb", processors="tokenize,pos", tokenize_pretokenized=True, download_method=None, verbose=False
    )
    bare: dict[str, str] = {}
    for i in range(0, len(multi), CHUNK):
        part = multi[i : i + CHUNK]
        doc = tagger([[w] for w in part])
        bare.update((w, s.words[0].upos) for w, s in zip(part, doc.sentences, strict=True))
        print(f"  tagged {i + len(part)}/{len(multi)} bare words ({time.perf_counter() - t0:.0f}s)", flush=True)

    out = io.StringIO()
    out.write(f"#source_version={stanza_version}\n")
    for w in sorted(rows):
        readings = rows[w]
        default = bare.get(w)
        if default not in readings:
            default = next(u for u in UPOS_ORDER if u in readings)
        for u in sorted(readings, key=UPOS_ORDER.index):
            out.write(f"{w}\t{u}\t{readings[u]}\t{int(u == default)}\n")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(out.getvalue().encode("utf-8"))
    OUTPUT.write_bytes(buf.getvalue())
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes, {len(rows)} surfaces, stanza {stanza_version})")


if __name__ == "__main__":
    sys.exit(main())
