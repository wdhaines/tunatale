#!/usr/bin/env python
"""Read-only report: which verb affixes each stored lesson put in front of the learner.

    uv run python scripts/report_affix_exposure.py --deck /path/to/copy/tunatale_ceb.db --language ceb

Affixes reach a learner only when a lesson happens to use them, so the first
question about affix teaching is what the lessons contain (tunatale-ve4p.3).
For each lesson in the deck this prints, from the natural-speed lines:

- verb tokens, split into bare roots and affixed forms;
- per A1 feature (``verb:nag``), its tokens and the forms behind them;
- affixed forms the language's A1 set does not explain;
- per affix pattern (``mag-nag``), its tokens and the roots met in at least
  two of its cells, a same-root pair being what makes a contrast visible;
- whether the lesson meets the target: ONE pattern with at least
  ``TARGET_PAIRS`` roots met in two of its cells.

**Why three pairs.** The target has to be something an unplanned lesson does
not already do. Measured 2026-10-07 on the two stored Cebuano lessons, written
with no affix plan: mag-nag had 9 and 10 tokens and 1 and 2 pairs. A first
draft of the target ("4+ tokens and one pair") was met by both, so it could
not have shown that planning a pattern changed anything. Three pairs is above
that baseline, and it is also what a drill needs: three roots to contrast.

**Run it on a copy.** It only reads, but it reads a learner's deck: copy the
file (and its ``-wal`` if one exists) somewhere else and point ``--deck`` there.

**It reads the deck's own analyses.** Verbs come from ``lemma_analysis_cache``,
the analyses the app stored for these very lines, so the report shows what the
app saw. A line with no cached analysis is counted and printed, never treated
as verb-free.

**Newest analysis wins.** The cache keeps one row per sentence PER lemma-table
build (a deck measured 2026-10-07 held six builds' worth), so a sentence can
have several analyses that disagree. The report takes the most recently
written one, which is what the app last computed for that line.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.languages import get_a1_morphology  # noqa: E402
from app.srs.a1_morphology import A1Morphology, AffixPattern  # noqa: E402
from app.srs.lemmatizer import TokenAnalysis  # noqa: E402

TARGET_PAIRS = 3


@dataclass
class Exposure:
    verbs: int = 0
    bare: int = 0
    by_feature: dict[str, Counter[str]] = field(default_factory=dict)
    unrecognised: Counter[str] = field(default_factory=Counter)
    pairs: dict[str, list[str]] = field(default_factory=dict)

    def tokens(self, pattern: AffixPattern) -> int:
        return sum(sum(self.by_feature.get(f, Counter()).values()) for f in pattern.features)


def _fold(word: str) -> str:
    return word.casefold().replace("-", "")


def natural_lines(lesson: dict, language_code: str) -> list[str]:
    """The target-language lines of the lesson's natural-speed section, in order."""
    for section in lesson.get("sections", []):
        if section.get("section_type") == "natural_speed":
            return [p["text"] for p in section["phrases"] if p.get("language_code") == language_code]
    return []


def exposure(analyses: Iterable[list[TokenAnalysis]], bundle: A1Morphology) -> Exposure:
    """What one lesson's analysed lines contain."""
    out = Exposure()
    cells: dict[str, set[str]] = {}  # root -> features met on it
    for line in analyses:
        for token in line:
            if token.upos != "VERB":
                continue
            out.verbs += 1
            if _fold(token.surface) == _fold(token.lemma):
                out.bare += 1
                continue
            form = f"{token.surface.lower()}<{token.lemma}"
            feature = bundle.to_feature(token)
            if feature is None:
                out.unrecognised[form] += 1
                continue
            out.by_feature.setdefault(feature, Counter())[form] += 1
            cells.setdefault(token.lemma, set()).add(feature)
    for pattern in bundle.patterns:
        out.pairs[pattern.key] = sorted(r for r, met in cells.items() if len(met & set(pattern.features)) >= 2)
    return out


def meets_target(found: Exposure, patterns: Iterable[AffixPattern]) -> bool:
    return any(len(found.pairs.get(p.key, [])) >= TARGET_PAIRS for p in patterns)


def format_exposure(title: str, found: Exposure, patterns: tuple[AffixPattern, ...], *, missing: int) -> str:
    recognised = sum(sum(forms.values()) for forms in found.by_feature.values())
    unrecognised = sum(found.unrecognised.values())
    lines = [
        f"--- {title}",
        f"verb tokens {found.verbs} = bare {found.bare} + affixed {recognised + unrecognised}"
        f" (recognised {recognised}, unrecognised {unrecognised})",
        f"lines with NO cached analysis: {missing}",
    ]
    for feature, forms in sorted(found.by_feature.items(), key=lambda kv: (-sum(kv[1].values()), kv[0])):
        lines.append(f"  {feature}  tokens {sum(forms.values())}  {' '.join(forms)}")
    if found.unrecognised:
        lines.append(f"  unrecognised: {' '.join(found.unrecognised)}")
    for pattern in patterns:
        pairs = " ".join(found.pairs.get(pattern.key, [])) or "none"
        lines.append(f"  {pattern.key}  tokens {found.tokens(pattern)}  pairs: {pairs}")
    verdict = "MET" if meets_target(found, patterns) else "not met"
    lines.append(f"  target (one pattern with {TARGET_PAIRS}+ same-root pairs): {verdict}")
    return "\n".join(lines)


def _connect(deck: Path) -> sqlite3.Connection:
    # A WAL-mode file with no -wal beside it cannot be opened mode=ro (SQLite
    # wants to create the -shm), and has nothing in a WAL to miss, so it is read
    # as immutable. With a -wal present, mode=ro reads it.
    mode = "mode=ro" if Path(f"{deck}-wal").exists() else "immutable=1"
    return sqlite3.connect(f"file:{deck}?{mode}", uri=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--deck", type=Path, required=True, help="a COPY of a learner's TunaTale deck")
    parser.add_argument("--language", required=True, help="language code of the deck, e.g. ceb")
    args = parser.parse_args(argv)

    bundle = get_a1_morphology(args.language)
    if bundle is None or not bundle.patterns:
        print(f"{args.language}: the language registers no affix patterns", file=sys.stderr)
        return 2

    con = _connect(args.deck)
    # Oldest first, so a later row for the same sentence replaces an earlier one.
    cached = dict(
        con.execute(
            "SELECT sentence, analyses_json FROM lemma_analysis_cache WHERE language_code = ?"
            " ORDER BY updated_at, rowid",
            (args.language,),
        )
    )
    lessons = con.execute("SELECT day, data_json FROM lessons ORDER BY day").fetchall()
    con.close()

    for day, data_json in lessons:
        lesson = json.loads(data_json)
        lines = natural_lines(lesson, args.language)
        analysed = [[TokenAnalysis(**token) for token in json.loads(cached[line])] for line in lines if line in cached]
        found = exposure(analysed, bundle)
        title = f"Day {day}: {lesson.get('title', '')}"
        print(format_exposure(title, found, bundle.patterns, missing=len(lines) - len(analysed)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
