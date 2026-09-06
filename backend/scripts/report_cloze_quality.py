"""Judge every cloze already in a TT language DB, and print what fits each blank.

READ-ONLY. Touches no Anki collection and writes nothing anywhere — this is the
acceptance evidence for tunatale-keb0, not a feature. The repair path is the
``/review`` "try again" control; there is deliberately no bulk rewrite here.

Why this exists: the unit tests for ``app.llm.cloze_quality`` pin the verdict
RULE against fixed replies, which proves the rule turns a given reply into the
right verdict — and proves nothing about whether the live model returns those
replies. A cassette has the same limit: it pins replay, not quality. So the
claim "the judge discriminates" has to be measured against real sentences, with
predictions registered BEFORE the run (.claude/rules/tdd.md — run the control
before you write the finding).

Usage::

    cd backend
    uv run python -m scripts.report_cloze_quality --language no
    uv run python -m scripts.report_cloze_quality --language no --limit 12

Needs ``GROQ_API_KEY`` and makes one live call per cloze, so it is paced against
the free tier's request budget.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

from app.config import settings
from app.languages import card_surface_variants, discover, get_language, resolve_db_path
from app.llm.client import LLMClient, reasoning_params_for_model
from app.llm.cloze_quality import blank_out, judge_cloze

#: Groq's free tier is ~30 requests/minute; stay under it without a token bucket.
_CONCURRENCY = 4

_SYMBOL = {"determined": "OK  ", "underdetermined": "BAD ", "unknown": "??  "}


def _load_clozes(db_path: Path) -> list[tuple[int, str, str]]:
    """``(collocation_id, word, stored cloze sentence)`` for every cloze row.

    Opened read-only through a URI, deliberately NOT through ``SRSDatabase``:
    its constructor runs ``_init_schema``, so merely instantiating it to read a
    report would apply pending migrations to the live language DB. Same hazard
    as a dev-server reload migrating ``tunatale_no.db`` before the migration is
    committed — and a report has no business writing at all.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            """
            SELECT id, text, source_sentence
              FROM collocations
             WHERE card_type = 'cloze'
               AND source_sentence IS NOT NULL
               AND source_sentence != ''
             ORDER BY text
            """
        ).fetchall()
    finally:
        conn.close()
    return [(r[0], r[1], r[2]) for r in rows]


async def _run(language_code: str, limit: int | None) -> int:
    discover()
    language = get_language(language_code)
    db_path = resolve_db_path(language_code, settings)

    clozes = _load_clozes(db_path)
    if limit is not None:
        clozes = clozes[:limit]
    if not clozes:
        # An empty result is what both "no clozes" and "wrong DB" look like, and
        # the singular `database_url` is the Slovene one — a script that reads it
        # by accident reports a vacuous, confident zero. Name the file it read.
        print(f"No cloze rows in {db_path}", file=sys.stderr)
        return 1

    client = LLMClient(
        groq_api_key=settings.groq_api_key,
        groq_model=settings.llm_model,
        groq_extra_body_params=reasoning_params_for_model(settings.llm_model),
    )

    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def _judge(row):
        coll_id, word, sentence = row
        async with semaphore:
            verdict = await judge_cloze(
                client,
                sentence=sentence,
                surface=word,
                language=language.name,
                also_accept=card_surface_variants(language_code, word),
            )
        return coll_id, word, sentence, verdict

    results = await asyncio.gather(*(_judge(r) for r in clozes), return_exceptions=True)

    counts = {"determined": 0, "underdetermined": 0, "unknown": 0}
    print(f"{'':4} {'word':<14} {'blank':<46} also fits")
    print("-" * 100)
    for row, result in zip(clozes, results, strict=True):
        if isinstance(result, BaseException):
            counts["unknown"] += 1
            print(f"{'ERR ':4} {row[1]:<14} {type(result).__name__}: {result}")
            continue
        _coll_id, word, sentence, verdict = result
        counts[verdict.status] += 1
        blank = blank_out(sentence, word)
        blank = blank if len(blank) <= 45 else blank[:42] + "..."
        others = ", ".join(verdict.competitors) or ("—" if verdict.status == "determined" else "")
        print(f"{_SYMBOL[verdict.status]:4} {word:<14} {blank:<46} {others}")

    total = len(clozes)
    bad = counts["underdetermined"]
    print("-" * 100)
    print(
        f"{total} clozes: {counts['determined']} determined, "
        f"{bad} underdetermined ({bad / total:.0%}), {counts['unknown']} unknown"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Judge every stored cloze's determinacy (read-only)")
    parser.add_argument("--language", required=True, help="language code, e.g. the target_language in .env")
    parser.add_argument("--limit", type=int, default=None, help="judge only the first N, cheapest first look")
    args = parser.parse_args()
    return asyncio.run(_run(args.language, args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
