#!/usr/bin/env python
"""Build the English word relations the Cebuano card matcher reads (tunatale-h0q3.2).

    uv run --with nltk python scripts/build_english_relations.py [--nltk-data DIR]

A card is matched to its Wolff entry by its English. The owner, 2026-10-18,
on the words left blank: "You need to allow synonyms or close meanings"
(storm / typhoon, wing / wings, tire / wheel, sibling / brother, gun /
firearm). This writes ``ceb/data/english_relations.json.gz`` from WordNet 3.0,
so the app needs no WordNet at runtime:

- ``base``: a word's dictionary form, where it differs (``teeth`` -> ``tooth``).
- ``related``: for each word and two-word phrase in Wolff's glosses, the words
  that are its synonyms or a kind of it, or that it is a kind of (hypernyms up
  to 3 steps, hyponyms 1: two steps down from "person" reached "sibling").
  Never co-hyponyms: WordNet puts dog and cat,
  red and blue, as close as sibling and brother, so plain path distance cannot
  tell a synonym from a neighbour.
- ``defined_by``: for each common English word, the content words of its
  first noun and verb definitions that also occur in Wolff's glosses
  (``tire``: "hoop that covers a wheel" -> ``wheel``).

Light words (``like``, ``get``, ``give``...) never relate on their own:
WordNet lists "kind" with "like", which Wolff uses in "would like to". A
phrase containing one ("get out") still does.

Inputs: the committed Wolff lexicon, and wordfreq's top English words (the
words a card's English is written in). Re-run after rebuilding the lexicon.
"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from app.plugins.languages.ceb.wolff_cards import LEXICON_PATH, LIGHT_WORDS, RELATIONS_PATH, english_tokens

TOP_ENGLISH = 50_000


def build(wn, vocabulary: set[str], gloss_texts: list[str]) -> dict:
    def base(w: str) -> str:
        return wn.morphy(w) or w

    def lemmas(s) -> set[str]:
        return {lemma.name().lower().replace("_", " ") for lemma in s.lemmas()}

    def closure(s, step, depth: int) -> set[str]:
        out, frontier = set(), [s]
        for _ in range(depth):
            frontier = [r for x in frontier for r in step(x)]
            out |= {n for r in frontier for n in lemmas(r)}
        return out

    def is_a(unit: str) -> set[str]:
        key = unit.replace(" ", "_")
        out = set()
        for b in {base(key), key}:
            out.add(b.replace("_", " "))
            for s in wn.synsets(b):
                out |= lemmas(s)
                out |= closure(s, lambda x: x.hypernyms() + x.instance_hypernyms(), 3)
                out |= closure(s, lambda x: x.hyponyms(), 1)
        return out

    gloss_tokens = [english_tokens(g) for g in gloss_texts]
    singles = {t for toks in gloss_tokens for t in toks if t not in LIGHT_WORDS}
    bigrams = {f"{a} {b}" for toks in gloss_tokens for a, b in zip(toks, toks[1:], strict=False)}
    gloss_bases = {base(t) for t in singles}

    words = vocabulary | singles
    bases = {w: base(w) for w in words if base(w) != w}
    keep = vocabulary | {bases.get(w, w) for w in vocabulary}

    related = {}
    for unit in singles | {b for b in bigrams if wn.synsets(b.replace(" ", "_"))}:
        found = sorted((is_a(unit) & keep) - {unit})
        if found:
            related[unit] = found

    defined_by = {}
    for w in sorted(vocabulary):
        content = set()
        for pos in ("n", "v"):
            synsets = wn.synsets(base(w), pos)
            if synsets:
                content |= set(english_tokens(synsets[0].definition()))
        found = sorted({base(t) for t in content - LIGHT_WORDS} & gloss_bases)
        if found:
            defined_by[w] = found
    return {"base": dict(sorted(bases.items())), "related": related, "defined_by": defined_by}


def main() -> None:  # pragma: no cover - needs nltk + the WordNet corpus
    import nltk
    from wordfreq import top_n_list

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--nltk-data", type=Path, help="Where the WordNet corpus is (downloaded there if absent)")
    args = parser.parse_args()
    if args.nltk_data:
        nltk.data.path.insert(0, str(args.nltk_data))
    nltk.download("wordnet", download_dir=str(args.nltk_data) if args.nltk_data else None, quiet=True)
    from nltk.corpus import wordnet as wn

    # The entries straight from the file: load_lexicon() needs the table this writes.
    with gzip.open(LEXICON_PATH, "rt", encoding="utf-8") as fh:
        entries = json.load(fh)["entries"]
    glosses = [s["gloss"] for e in entries for f in e["forms"] for s in f["senses"] if s["gloss"]]
    vocabulary = {w for w in top_n_list("en", TOP_ENGLISH) if w.isalpha() and w.isascii()}
    table = build(wn, vocabulary, glosses)
    raw = json.dumps(table, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    with gzip.GzipFile(RELATIONS_PATH, "wb", mtime=0) as fh:
        fh.write(raw)
    print(
        f"wrote {RELATIONS_PATH} ({RELATIONS_PATH.stat().st_size // 1024} KB): "
        f"{len(table['related'])} related, {len(table['defined_by'])} defined_by, {len(table['base'])} base"
    )


if __name__ == "__main__":  # pragma: no cover
    main()
