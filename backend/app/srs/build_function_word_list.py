"""Generate a candidate function-word ``include`` list from a curriculum's lessons.

Usage:
    uv run python -m app.srs.build_function_word_list --curriculum-id <id>

Prints (to stdout) a JSON array of casefolded tokens that occur frequently across
NATURAL_SPEED phrases and pass cheap heuristics — review, strip obvious content
words, and paste into the ``include`` field of the language plugin's
``data/function_words.json``. A per-token frequency table is written
to stderr. This proposes only the surface ``include`` list; the ``pos`` set
(closed-class UPOS tags) is hand-maintained.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter

from app.models.lesson import SectionType
from app.srs.tokenizer import tokenize
from app.storage.store import ContentStore

_TOKEN_RE = re.compile(r"^[a-zčšž]+$")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curriculum-id", required=True, help="Curriculum ID")
    parser.add_argument(
        "--db-path",
        default="tunatale.db",
        help="Path to the TunaTale SQLite DB (default: tunatale.db)",
    )
    args = parser.parse_args()

    store = ContentStore(args.db_path)
    curriculum = store.get_curriculum(args.curriculum_id)
    if curriculum is None:
        print(f"Curriculum {args.curriculum_id!r} not found", file=sys.stderr)
        sys.exit(1)

    # Gather all lesson IDs
    lesson_days = store.get_lesson_days(args.curriculum_id)
    lesson_ids = [ld["lesson_id"] for ld in lesson_days]
    print(f"# Found {len(lesson_ids)} lessons in curriculum {args.curriculum_id!r}", file=sys.stderr)

    # Collect all L2 tokens from NATURAL_SPEED phrases
    token_counter: Counter[str] = Counter()
    per_lesson: dict[str, set[str]] = {}

    for _i, lesson_id in enumerate(lesson_ids):
        lesson = store.get_lesson(lesson_id)
        if lesson is None:
            print(f"# Skipping missing lesson {lesson_id}", file=sys.stderr)
            continue

        lesson_tokens: set[str] = set()
        for section in lesson.sections:
            if section.section_type != SectionType.NATURAL_SPEED:
                continue
            for phrase in section.phrases:
                if phrase.language_code != lesson.language_code:
                    continue
                for surface in tokenize(phrase.text):
                    t = surface.casefold()
                    if _TOKEN_RE.match(t):
                        token_counter[t] += 1
                        lesson_tokens.add(t)

        per_lesson[lesson_id] = lesson_tokens

    # Filter: keep tokens that are short AND appear in >= 2 lessons or >= 4 times
    candidates: list[tuple[str, int, int]] = []
    for token, count in token_counter.most_common():
        if len(token) > 4:
            continue
        lesson_count = sum(1 for tokens in per_lesson.values() if token in tokens)
        if lesson_count >= 2 or count >= 4:
            candidates.append((token, count, lesson_count))

    # Frequency table + provenance → stderr (keeps stdout cleanly pasteable).
    print(
        f"# Candidate 'include' list from curriculum {args.curriculum_id!r} over "
        f"{len(lesson_ids)} lessons. Review, strip content words, paste into the "
        f"'include' field of the language plugin's data/function_words.json.",
        file=sys.stderr,
    )
    print("# token  count  lessons", file=sys.stderr)
    for token, count, lesson_count in candidates:
        print(f"#   {token}  {count}  {lesson_count}", file=sys.stderr)

    # Pasteable JSON 'include' array → stdout.
    print(json.dumps([token for token, _count, _lc in candidates], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
