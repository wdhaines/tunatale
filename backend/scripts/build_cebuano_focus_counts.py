#!/usr/bin/env python
"""Build the Cebuano object-focus count table: how often native news spells each gi- and -on form.

    uv run --with pyarrow python scripts/build_cebuano_focus_counts.py            # default source
    uv run --with pyarrow python scripts/build_cebuano_focus_counts.py --source X.parquet

``ceb/focus_sentences.py`` teaches a root in object focus only when native
news uses its gi- and -on forms (tunatale-ve4p.32). Unlike the affix-count
table, this one counts the spellings the checked rows give
(``ceb/data/focus_rows.tsv``), not prefix + root: three -on forms drop a vowel
(imnon, kan-on, dad-on), so no rule can produce the surface to count.

**Source.** The same FineWeb-2 ``ceb_Latn`` shard and native news hosts as
``build_cebuano_frequency.py``.

**No verb-reading check.** The lemma table does not list imnon or dad-on at
all, so the check ``build_cebuano_affix_counts.is_verb_form_of`` makes would
drop real forms. A count a homograph inflates is named in
``focus_sentences.HOMOGRAPHS`` instead (kan-on, cooked rice).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.plugins.languages.ceb.focus_sentences import FOCUS_COUNTS_PATH, FOCUS_ROWS_PATH, load_focus_rows  # noqa: E402
from scripts.build_cebuano_affix_counts import _key, count_surfaces  # noqa: E402
from scripts.build_cebuano_frequency import DEFAULT_SOURCE, _native_texts  # noqa: E402

_OBJECT_CELLS = (2, 3)  # -on, gi-


def object_forms(rows_path: Path) -> list[str]:
    """Every gi- and -on spelling the checked rows give, in the table's order."""
    return [row.forms[cell] for row in load_focus_rows(rows_path).values() for cell in _OBJECT_CELLS]


def focus_counts(surfaces: Counter[str], forms: Iterable[str]) -> dict[str, int]:
    """``form -> count`` for each of *forms*, hyphens and case folded as the counter folds them."""
    return {form: surfaces.get(_key(form), 0) for form in forms}


def write_table(counts: dict[str, int], out: Path, *, source: str) -> int:
    """Write *counts* as the TSV ``focus_sentences`` reads, sorted by form; return rows written.

    A form news never uses is written with its 0: the table says what was
    counted, and the test that reads it is what refuses a weak form.
    """
    rows = sorted(counts.items())
    out.write_text(f"#source={source}\n" + "".join(f"{form}\t{c}\n" for form, c in rows), encoding="utf-8")
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="FineWeb-2 ceb_Latn parquet shard")
    parser.add_argument("--rows", type=Path, default=FOCUS_ROWS_PATH)
    parser.add_argument("--out", type=Path, default=FOCUS_COUNTS_PATH)
    args = parser.parse_args(argv)

    sha = hashlib.sha256(args.source.read_bytes()).hexdigest()
    surfaces = count_surfaces(_native_texts(args.source))
    counts = focus_counts(surfaces, object_forms(args.rows))
    written = write_table(counts, args.out, source=f"fineweb-2/ceb_Latn sha256={sha[:12]}")
    print(f"{sum(surfaces.values())} tokens, {written} forms written")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
