#!/usr/bin/env python3
"""Build the Tagalog pronunciation table from the kaikki.org Wiktionary extract.

Run from ``backend/``::

    uv run python scripts/build_kaikki_pronunciation_table.py
    uv run python scripts/build_kaikki_pronunciation_table.py --source <jsonl>

Why: the key-phrase breakdown plays syllable fragments ("nga", "lan") on their
own, and a voice reading a bare fragment as text guesses badly. The Tagalog
plugin sends those fragments as IPA instead (tunatale-w4m7.16), and this table
is where the IPA comes from: Wiktionary's NARROW (``[...]``) readings, which
mark vowel length (``[sɐˈlaː.mɐt̪̚]``). Length is the stress cue the key-phrase
voice actually honours; it ignores a bare stress mark (measured 2026-09-23).

Rows are ``word, upos, rank, ipa, is_default``:

1. kaikki ``pos`` → UPOS through the lemma builder's map; other pos are dropped
   (a ``character`` entry is a letter name: ``ng`` is ``[ˌʔɛn̪ ˈd͡ʒɪ]`` there).
2. Only single-word entries with narrow, space-free readings. A spaced reading
   is two words and cannot be sliced into one word's syllables.
3. ``rank`` keeps Wiktionary's order within a (word, UPOS); duplicates drop.
4. ``is_default`` marks the rows of the word's default UPOS in the committed
   lemma table (``tagalog_lemmas.tsv.gz``), so ``ako`` reads as the pronoun
   ``[ʔɐˈx̠o]`` and not the noun ``[ˈʔaː.x̠oʔ]``.

The data is Wiktionary's as published; adapting it to a voice (``ɾ`` → ``r``,
which the voice otherwise merges with ``d``) is the planner's job, not the table's.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_kaikki_lemma_table import DEFAULT_SOURCE, POS_TO_UPOS, UPOS_ORDER, _is_word, _sha256  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

_BACKEND = Path(__file__).resolve().parents[1]
LEMMA_TABLE = _BACKEND / "app/plugins/languages/tl/data/tagalog_lemmas.tsv.gz"
OUTPUT = _BACKEND / "app/plugins/languages/tl/data/tagalog_pronunciations.tsv.gz"

Readings = dict[tuple[str, str], list[str]]
Row = tuple[str, str, int, str, int]


def _narrow(ipa: str) -> str | None:
    """The body of a narrow ``[...]`` reading, or ``None`` for anything else."""
    if len(ipa) > 2 and ipa.startswith("[") and ipa.endswith("]") and " " not in ipa:
        return ipa[1:-1]
    return None


def extract_readings(entries: Iterable[dict]) -> Readings:
    """Rules 1-3: ``(word, upos)`` → narrow readings in Wiktionary's order."""
    readings: Readings = defaultdict(list)
    for entry in entries:
        upos = POS_TO_UPOS.get(entry.get("pos") or "")
        word = (entry.get("word") or "").lower()
        if upos is None or not _is_word(word):
            continue
        for sound in entry.get("sounds") or []:
            body = _narrow(sound.get("ipa") or "")
            if body is not None and body not in readings[(word, upos)]:
                readings[(word, upos)].append(body)
    return {key: value for key, value in readings.items() if value}


def read_default_upos(lemma_table: Path) -> dict[str, str]:
    """Each surface's default UPOS in the committed lemma table."""
    default: dict[str, str] = {}
    with gzip.open(lemma_table, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            surface, upos, _lemma, is_default = line.rstrip("\n").split("\t")
            if is_default == "1":
                default[surface] = upos
    return default


def table_rows(readings: Readings, default_upos: dict[str, str]) -> list[Row]:
    """Rule 4, emitted words-sorted: default UPOS first, then UPOS order, then rank."""
    by_word: dict[str, list[str]] = defaultdict(list)
    for word, upos in readings:
        by_word[word].append(upos)
    rows: list[Row] = []
    for word in sorted(by_word):
        default = default_upos.get(word)
        for upos in sorted(by_word[word], key=lambda u: (u != default, UPOS_ORDER.index(u))):
            rows.extend(
                (word, upos, rank, ipa, int(upos == default)) for rank, ipa in enumerate(readings[(word, upos)])
            )
    return rows


def build_from_path(source: Path, lemma_table: Path, output: Path) -> dict:
    """Build the table from *source* JSONL into *output* ``.tsv.gz``, print the report."""
    sha_hex = _sha256(source)
    with open(source, encoding="utf-8") as fh:
        readings = extract_readings(json.loads(line) for line in fh if line.strip())
    rows = table_rows(readings, read_default_upos(lemma_table))

    out = io.StringIO()
    out.write(f"#source_version=kaikki-{sha_hex[:12]}\n")
    for word, upos, rank, ipa, is_default in rows:
        out.write(f"{word}\t{upos}\t{rank}\t{ipa}\t{is_default}\n")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(out.getvalue().encode("utf-8"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(buf.getvalue())

    stats = {"sha256": sha_hex, "rows": len(rows), "words": len({r[0] for r in rows}), "size": output.stat().st_size}
    print(f"source: {source} ({sha_hex[:12]})")
    print(f"rows: {stats['rows']}; words: {stats['words']}")
    print(f"wrote {output} ({stats['size']} bytes)")
    return stats


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="kaikki Tagalog JSONL extract")
    args = parser.parse_args(argv)
    if not args.source.exists():
        parser.error(f"source extract not found: {args.source}")
    build_from_path(args.source, LEMMA_TABLE, OUTPUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
