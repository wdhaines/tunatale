#!/usr/bin/env python
"""Propose the Cebuano "next words" seed: frequent roots after the 625 list.

    uv run python scripts/build_cebuano_next_words.py            # rewrites ceb/data/next_words.tsv
    uv run python scripts/seed_base_list.py --language ceb \\
        --list app/plugins/languages/ceb/data/next_words.tsv --list-name "Cebuano next words"

Use 1 of the user's u8nz.6 decision: a ranked seed minted like the 625 list and
queued behind it. The output is a base list (``app.srs.base_list``) carrying a
``#rank_offset=`` header, so its positions start after the 625's.

⚠️ **It only PROPOSES.** Frequency counts surfaces, not senses: ``apan`` is the
22nd commonest word in the corpus as the conjunction "but", and Wiktionary's
first sense for it is "grasshopper". So every row is written ``unconfirmed`` —
which ``seed_base_list.py`` will not mint — unless ``next_words_review.tsv``, a
hand-kept overlay, says otherwise:

- ``reviewed`` — mint it, with the overlay's English;
- ``dropped`` — leave it out (wrong sense, a name, English, a function word).

Rebuilding never loses a review, because the decisions live in the overlay and
not in the generated file. Review a chunk before each ``--add``.

Candidates, in corpus-frequency order (``cebuano_frequency.tsv.gz``): the
default reading of a surface the lemma table knows, when that reading is its own
root (an affixed form is proposed at its root's own row), is a content word
(NOUN/VERB/ADJ/ADV/NUM), has a Wiktionary gloss, and is neither a 625-list word
nor a function word.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.srs.lemma_table import Reading  # noqa: E402
from scripts.build_kaikki_lemma_table import POS_TO_UPOS  # noqa: E402

_BACKEND = Path(__file__).resolve().parents[1]
_CEB_DATA = _BACKEND / "app/plugins/languages/ceb/data"
KAIKKI = _BACKEND / "scripts/local/kaikki/Cebuano.jsonl"
FREQUENCY = _CEB_DATA / "cebuano_frequency.tsv.gz"
LEMMA_TABLE = _CEB_DATA / "cebuano_lemmas.tsv.gz"
BASE_LIST = _CEB_DATA / "base625.tsv"
REVIEW = _CEB_DATA / "next_words_review.tsv"
OUT = _CEB_DATA / "next_words.tsv"

CONTENT_UPOS = frozenset({"NOUN", "VERB", "ADJ", "ADV", "NUM"})
REVIEW_STATUSES = frozenset({"reviewed", "dropped"})
# Behind the 625 list, whose ranks end below 1000 (514 rows).
RANK_OFFSET = 1000
LIMIT = 1000


def short_gloss(gloss: str, upos: str) -> str:
    """The first comma/semicolon clause, parentheticals and a leading article removed; a verb loses its "to "."""
    text = re.split(r"[;,]", re.sub(r"\([^)]*\)", "", gloss))[0].strip()
    text = re.sub(r"^(a|an|the) ", "", text)
    if upos == "VERB" and text.startswith("to "):
        text = text[3:]
    return text


def first_glosses(entries: Iterable[dict]) -> dict[tuple[str, str], str]:
    """``(word, UPOS)`` → the first gloss of the first sense that is not a form-of/alt-of."""
    out: dict[tuple[str, str], str] = {}
    for entry in entries:
        upos = POS_TO_UPOS.get(entry.get("pos", ""))
        if upos is None or (entry["word"], upos) in out:
            continue
        for sense in entry.get("senses", []):
            if "form_of" in sense or "alt_of" in sense or not sense.get("glosses"):
                continue
            out[(entry["word"], upos)] = sense["glosses"][0]
            break
    return out


def candidates(
    ranked: Iterable[str],
    *,
    readings: Callable[[str], list[Reading]],
    glosses: dict[tuple[str, str], str],
    skip: Callable[[str], bool],
    limit: int,
) -> list[tuple[str, str, str]]:
    """``(UPOS, root, dictionary gloss)`` for the first *limit* proposable lemmas of *ranked*."""
    out: list[tuple[str, str, str]] = []
    for lemma in ranked:
        found = readings(lemma)
        if not found:
            continue
        default = next(r for r in found if r.is_default)
        gloss = glosses.get((lemma, default.upos))
        if default.lemma != lemma or default.upos not in CONTENT_UPOS or gloss is None or skip(lemma):
            continue
        out.append((default.upos, lemma, gloss))
        if len(out) == limit:
            break
    return out


def load_review(lines: Iterable[str]) -> dict[str, tuple[str, str]]:
    """``cebuano`` → ``(english, status)`` from the overlay; raises on an unknown status."""
    body = [line for line in lines if line.strip() and not line.startswith("#")]
    out: dict[str, tuple[str, str]] = {}
    for row in csv.DictReader(body, delimiter="\t"):
        if row["status"] not in REVIEW_STATUSES:
            raise ValueError(f"{row['cebuano']}: status {row['status']!r} is not one of {sorted(REVIEW_STATUSES)}")
        out[row["cebuano"]] = (row["english"], row["status"])
    return out


def render(
    cands: Iterable[tuple[str, str, str]], review: dict[str, tuple[str, str]], *, rank_offset: int, source: str
) -> str:
    lines = [
        "# Cebuano next words (tunatale-u8nz.6): frequent roots after the 625 list, most frequent first.",
        f"# Generated by scripts/build_cebuano_next_words.py from {source}. Do not edit by hand:",
        "# decisions go in next_words_review.tsv. unconfirmed = proposed, NOT minted.",
        f"#rank_offset={rank_offset}",
        "category\tenglish\tcebuano\tstatus\tdictionary_gloss",
    ]
    for upos, lemma, gloss in cands:
        english, status = review.get(lemma, (short_gloss(gloss, upos), "unconfirmed"))
        if status == "dropped":
            continue
        lines.append(f"{upos.lower()}\t{english}\t{lemma}\t{status}\t{gloss}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    from app.srs.base_list import load_base_list
    from app.srs.cognate_seed import normalize
    from app.srs.function_words import is_function_word
    from app.srs.lemma_table import TableLemmatizer

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--limit", type=int, default=LIMIT)
    args = parser.parse_args(argv)

    lemmatizer = TableLemmatizer("ceb", LEMMA_TABLE)
    with BASE_LIST.open(encoding="utf-8") as fh:
        base = {normalize(lemmatizer.lemmatize(w.text.lower(), "ceb")) for w in load_base_list(fh)}
    with KAIKKI.open(encoding="utf-8") as fh:
        glosses = first_glosses(json.loads(line) for line in fh)
    with gzip.open(FREQUENCY, "rt", encoding="utf-8") as fh:
        ranked = [line.split("\t")[0] for line in fh if not line.startswith("#")]
    review = {}
    if REVIEW.exists():
        with REVIEW.open(encoding="utf-8") as fh:
            review = load_review(fh)

    cands = candidates(
        ranked,
        readings=lemmatizer.readings,
        glosses=glosses,
        skip=lambda w: normalize(w) in base or is_function_word(w, "ceb"),
        limit=args.limit,
    )
    lemmatizer.close()
    OUT.write_text(render(cands, review, rank_offset=RANK_OFFSET, source=FREQUENCY.name), encoding="utf-8")
    reviewed = sum(1 for _, lemma, _ in cands if review.get(lemma, ("", ""))[1] == "reviewed")
    dropped = sum(1 for _, lemma, _ in cands if review.get(lemma, ("", ""))[1] == "dropped")
    print(
        f"{len(cands)} candidates: {reviewed} reviewed, {dropped} dropped, {len(cands) - reviewed - dropped} unconfirmed"
    )
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
