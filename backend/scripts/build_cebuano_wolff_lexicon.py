#!/usr/bin/env python
"""Build the Wolff lexicon the Cebuano cards read (tunatale-h0q3.2).

    uv run python scripts/build_cebuano_wolff_lexicon.py            # write data/wolff_lexicon.json.gz
    uv run python scripts/build_cebuano_wolff_lexicon.py --report   # also: how the deck's words match

Every paragraph of Wolff's dictionary (Project Gutenberg #40074, public domain)
is parsed by ``ceb/wolff_dictionary.py`` and stored by
``ceb/wolff_cards.py::entry_record``, with its forms and one example per sense
converted to the Handbook spelling. The conversion needs the news-corpus counts
and the loan lists, which is why it happens here and not at runtime; the inputs
are the oracle script's (``scripts/wolff_handbook_oracle.py``).

``--report`` runs every word of the deck's lists (``base625.tsv`` and the
reviewed rows of ``next_words.tsv``) through the card matcher and prints the
words that get nothing, by reason: those go to the owner.
"""

from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
from pathlib import Path

from app.plugins.languages.ceb.handbook_spelling import load_course_spellings
from app.plugins.languages.ceb.wolff_cards import LEXICON_PATH, Miss, WolffLexicon, entry_record
from app.plugins.languages.ceb.wolff_dictionary import parse_entry
from scripts.wolff_handbook_oracle import (
    DEFAULT_DICTIONARY,
    DEFAULT_EN_SOUNDS,
    Dictionary,
    entry_paragraphs,
    load_lexicon,
)

DATA = LEXICON_PATH.parent


def is_entry(paragraph: str) -> bool:
    """The appendix headings (BIRDS, PLANTS...) are not entries."""
    head = paragraph.split(" ")[0]
    return not head.isupper()


def build(dictionary_text: str, en_sounds: Path) -> list[dict]:
    import wordfreq

    paragraphs = [p for p in entry_paragraphs(dictionary_text) if is_entry(p)]
    english = set(wordfreq.top_n_list("en", 30000))
    lex = load_lexicon(Dictionary(paragraphs, english), en_sounds)
    lex = lex.__class__(freq=lex.freq, entries=lex.entries, loans=lex.loans, course=load_course_spellings())
    return [entry_record(parse_entry(p), lex) for p in paragraphs]


def deck_words() -> list[tuple[str, str]]:
    """(cebuano, english) for the deck's lists: the 625 base words and the reviewed next words."""
    out = []
    for name, keep in (("base625.tsv", None), ("next_words.tsv", "reviewed")):
        for line in (DATA / name).read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or line.startswith("category\t"):
                continue
            cols = line.split("\t")
            if keep is None or cols[3] == keep:
                out.append((cols[2], cols[1]))
    return out


def report(lexicon: WolffLexicon, words: list[tuple[str, str]]) -> str:
    misses: dict[str, list[str]] = collections.defaultdict(list)
    matched = 0
    for ceb, en in words:
        r = lexicon.card_fields(ceb, en)
        if isinstance(r, Miss):
            misses[r.reason].append(f"{ceb} '{en}'" + (f" -> {', '.join(r.candidates)}" if r.candidates else ""))
        else:
            matched += 1
    lines = [f"deck words: {len(words)}; matched {matched} ({100 * matched / max(len(words), 1):.1f}%)"]
    for reason, ws in sorted(misses.items()):
        lines.append(f"\n{reason}: {len(ws)}")
        lines.extend(f"  {w}" for w in ws)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dictionary", type=Path, default=DEFAULT_DICTIONARY)
    ap.add_argument("--en-sounds", type=Path, default=DEFAULT_EN_SOUNDS)
    ap.add_argument("--out", type=Path, default=LEXICON_PATH)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args(argv)

    raw = args.dictionary.read_bytes()
    records = build(raw.decode("utf-8"), args.en_sounds)
    meta = {
        "source": "John U. Wolff, A Dictionary of Cebuano Visayan (1972), Project Gutenberg #40074",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "entries": len(records),
    }
    payload = json.dumps({"meta": meta, "entries": records}, ensure_ascii=False, separators=(",", ":"))
    with gzip.GzipFile(args.out, "wb", mtime=0) as fh:
        fh.write(payload.encode("utf-8"))
    print(f"wrote {args.out} ({len(records)} entries, {args.out.stat().st_size // 1024} KiB)")
    if args.report:
        print(report(WolffLexicon(records), deck_words()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
