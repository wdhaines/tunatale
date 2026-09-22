#!/usr/bin/env python3
"""Extract a noun -> grammatical gender table from the raw NST lexicon (tunatale-vvb6).

    uv run python scripts/build_noun_genders.py        # raw .pron -> committed gz

Why: production lemmatizes Norwegian with the lookup-table lemmatizer (kbb.18),
which returns NO gender, so every card minted on prod got a blank article. The
raw NST dump carries gender in its morphology field; the committed
nst_lexicon extract dropped it. This keeps it, for base forms only.

Which rows count as the base form: a noun (``NN``) row whose morphology is
singular indefinite nominative (``SIN|IND|NOM|<G>``) or the lexicon row that
carries only the gender (``|||<G>``). ``<G>`` is ``MAS``, ``FEM``, ``NEU`` or a
hyphenated combination (``MAS-FEM``).

Mapped to the UD codes the language plugin's ``gender_articles`` already uses:
- any FEM reading, with or without MAS  -> ``Fem``  (renders ``ei/en``, the deck's own convention)
- MAS only                               -> ``Masc`` (``en``)
- NEU only                               -> ``Neut`` (``et``)
- NEU together with MAS or FEM           -> ``Amb``: recorded, not dropped. A
  wrong ``et``/``en`` is worse than a blank, and an OMITTED word would fall
  through to the suffix rules: ``ting`` (MAS+NEU) would become ``-ing`` -> Fem.

Measured against the user's deck (1,416 nouns with an en/et/ei-en article,
2026-09-22): the NST reading agreed on 1,239, disagreed on 50 (41 of those are
deck ``ei/en`` vs NST ``en``: grammatical, just less specific), 53 ambiguous,
74 absent. The raw dump is ~170 MB and gitignored (scripts/local/nst_source/).
"""

from __future__ import annotations

import gzip
import sys
from collections import defaultdict
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_PRON_PATH = _BACKEND / "scripts/local/nst_source/nor030224NST.pron"
DEFAULT_OUT_PATH = _BACKEND / "app/plugins/languages/no/data/noun_genders.tsv.gz"


def gender_code(tokens: set[str]) -> str:
    """Collapse the NST gender tokens seen for one word into a UD code, or ``""``."""
    parts = {p for t in tokens for p in t.split("-")}
    has_neu = "NEU" in parts
    has_common = bool(parts & {"MAS", "FEM"})
    if has_neu and has_common:
        return "Amb"
    if has_neu:
        return "Neut"
    if "FEM" in parts:
        return "Fem"
    if "MAS" in parts:
        return "Masc"
    return ""


def extract(pron_path: Path) -> dict[str, str]:
    seen: dict[str, set[str]] = defaultdict(set)
    with open(pron_path, encoding="latin-1") as fh:
        for line in fh:
            fields = line.rstrip("\r\n").split(";")
            if len(fields) < 3 or fields[1] != "NN":
                continue
            morph = fields[2]
            if not (morph.startswith("SIN|IND|NOM|") or morph.startswith("|||")):
                continue
            gender = morph.rsplit("|", 1)[-1]
            if gender:
                seen[fields[0].lower()].add(gender)
    return {w: code for w, toks in seen.items() if (code := gender_code(toks))}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    pron = Path(args[0]) if args else DEFAULT_PRON_PATH
    table = extract(pron)
    payload = "".join(f"{w}\t{g}\n" for w, g in sorted(table.items()))
    # mtime=0: a byte-identical rebuild from the same source, so git sees no churn.
    with open(DEFAULT_OUT_PATH, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(payload.encode("utf-8"))
    print(f"{len(table)} nouns -> {DEFAULT_OUT_PATH} ({DEFAULT_OUT_PATH.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
