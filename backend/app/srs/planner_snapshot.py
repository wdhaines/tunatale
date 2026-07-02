"""Build a compact learner-vocabulary snapshot for LLM prompt embedding.

Determinism guarantee: output is a pure function of DB contents — no dates,
timestamps, dict-iteration order, or SQLite row order leaks. This ensures that
identical database contents produce identical prompt strings, which is critical
for cassette-keyed LLM test replay.
"""

from __future__ import annotations

from app.models.srs_item import Direction, SRSState
from app.srs.database import SRSDatabase


def build_learner_snapshot(
    db: SRSDatabase,
    *,
    known_limit: int = 30,
    learning_limit: int = 15,
    struggling_limit: int = 10,
) -> str:
    """Build the vocabulary-context block for the curriculum planner LLM prompt.

    Returns a formatted multi-line string that is purely a function of the DB
    contents (no temporal or ordering nondeterminism).
    """
    total = db.count_collocations()
    if total == 0:
        return "(no tracked vocabulary yet — assume a beginner at the stated CEFR level)"

    learning_count = db.count_learning()
    new_count = db.count_new_available()

    # ── Known sample (REVIEW + KNOWN states) ───────────────────────
    review_rows, review_total = db.list_collocations(
        state=SRSState.REVIEW,
        order_by="text",
        order_dir="asc",
        limit=known_limit,
    )
    known_rows, known_total = db.list_collocations(
        state=SRSState.KNOWN,
        order_by="text",
        order_dir="asc",
        limit=known_limit,
    )

    known_texts = sorted([item.syntactic_unit.text for _, item, _ in review_rows + known_rows])[:known_limit]
    known_denom = review_total + known_total

    # ── Learning sample (LEARNING + RELEARNING states) ─────────────
    learning_rows, learning_total = db.list_collocations(
        state=SRSState.LEARNING,
        order_by="text",
        order_dir="asc",
        limit=learning_limit,
    )
    relearning_rows, relearning_total = db.list_collocations(
        state=SRSState.RELEARNING,
        order_by="text",
        order_dir="asc",
        limit=learning_limit,
    )

    learning_texts = sorted([item.syntactic_unit.text for _, item, _ in learning_rows + relearning_rows])[
        :learning_limit
    ]
    learning_denom = learning_total + relearning_total

    # ── Struggling (highest lapses) ────────────────────────────────
    lapse_rows, _ = db.list_collocations(
        order_by="lapses",
        order_dir="desc",
        limit=struggling_limit * 2,
        order_direction=Direction.RECOGNITION,
    )

    struggling: list[tuple[int, str]] = []
    for _, item, _ in lapse_rows:
        rec = item.directions.get(Direction.RECOGNITION)
        lapses = rec.lapses if rec is not None else 0
        if lapses > 0:
            struggling.append((lapses, item.syntactic_unit.text))

    struggling.sort(key=lambda x: (-x[0], x[1]))
    struggling = struggling[:struggling_limit]

    # ── Build output strings ─────────────────────────────────────
    lines: list[str] = [
        "Learner vocabulary snapshot:",
        f"- Tracked collocations: {total}",
        f"- Currently learning: {learning_count}",
        f"- New (not yet introduced): {new_count}",
    ]

    if known_texts:
        lines.append(f"Known (sample of {len(known_texts)}/{known_denom}): {', '.join(known_texts)}")
    else:
        lines.append("Known: (none yet)")

    if learning_texts:
        lines.append(f"Learning (sample of {len(learning_texts)}/{learning_denom}): {', '.join(learning_texts)}")
    else:
        lines.append("Learning: (none yet)")

    if struggling:
        items_str = ", ".join(f"{text} ({lapse_count} lapses)" for lapse_count, text in struggling)
        lines.append(f"Struggling (most lapses): {items_str}")
    else:
        lines.append("Struggling: (none yet)")

    return "\n".join(lines)
