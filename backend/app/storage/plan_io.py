"""Curriculum plan export/import for day-plan authoring.

Day plans become an editable source artifact, exactly like lessons already are
(see lesson_io.py). Export reconstructs the editable plan dict from a stored
Curriculum; import rebuilds a Curriculum from a plan dict.

Design mirrors ``lesson_io.py``: plain module, no side effects, validation
raises ``ValueError`` with field-path messages.
"""

from __future__ import annotations

import copy
import re
import uuid
from dataclasses import asdict

from app.models.curriculum import Curriculum, CurriculumDay
from app.storage.store import ContentStore


def mint_curriculum_id(topic: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:50]
    return f"{slug}-{uuid.uuid4().hex[:8]}"


def validate_plan_days(days: object, *, start_day: int | None = None) -> None:
    """Validate a list of day-plan dicts.

    Required per day: ``day`` (int >= 1), ``title``, ``focus``,
    ``learning_objective`` (non-empty str), ``collocations`` (non-empty list of
    non-empty str). ``story_guidance`` is optional (defaults to "").

    When *start_day* is given, day numbers must be exactly ``start_day,
    start_day+1, …`` in order.
    """
    if not isinstance(days, list):
        raise ValueError("days must be a list")
    if not days:
        raise ValueError("days must be a non-empty list")
    for i, entry in enumerate(days):
        _validate_one_day(entry, i)
    if start_day is not None:
        for i, entry in enumerate(days):
            expected = start_day + i
            actual = entry["day"]
            if actual != expected:
                raise ValueError(f"days[{i}].day must be {expected} (got {actual})")


_DAY_FIELDS = frozenset({"day", "title", "focus", "collocations", "learning_objective", "story_guidance"})


def _validate_one_day(entry: object, index: int) -> None:
    path = f"days[{index}]"
    if not isinstance(entry, dict):
        raise ValueError(f"{path} must be an object")

    for key in entry:
        if key not in _DAY_FIELDS:
            raise ValueError(f"{path} has unknown field '{key}'")

    _require_field(
        entry,
        path,
        "day",
        lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 1,
        "must be an integer >= 1",
    )
    _require_field(entry, path, "title", lambda v: isinstance(v, str) and v.strip(), "must be a non-empty string")
    _require_field(entry, path, "focus", lambda v: isinstance(v, str) and v.strip(), "must be a non-empty string")
    _validate_collocations(entry, path)
    _require_field(
        entry, path, "learning_objective", lambda v: isinstance(v, str) and v.strip(), "must be a non-empty string"
    )
    if "story_guidance" in entry and not isinstance(entry["story_guidance"], str):
        raise ValueError(f"{path}.story_guidance must be a string")


def _validate_collocations(entry: dict, path: str) -> None:
    coll = entry.get("collocations")
    if coll is None:
        raise ValueError(f"{path} is missing required field 'collocations'")
    if not isinstance(coll, list) or not coll:
        raise ValueError(f"{path}.collocations must be a non-empty list of non-empty strings")
    for j, c in enumerate(coll):
        if not isinstance(c, str) or not c.strip():
            raise ValueError(f"{path}.collocations[{j}] must be a non-empty string")


def _require_field(
    entry: dict,
    path: str,
    field: str,
    predicate,
    msg: str,
) -> None:
    if field not in entry:
        raise ValueError(f"{path} is missing required field '{field}'")
    if not predicate(entry[field]):
        raise ValueError(f"{path}.{field} {msg}")


def export_plan(store: ContentStore, curriculum_id: str) -> dict:
    """Export a curriculum as a self-describing editable plan dict.

    Deliberately excludes ``metadata`` (the planner chat is scaffolding, not
    source). Days are sorted by ``day``.
    """
    curriculum = store.get_curriculum(curriculum_id)
    if curriculum is None:
        raise KeyError(f"Curriculum not found: {curriculum_id}")
    days = sorted(
        (asdict(d) for d in curriculum.days),
        key=lambda d: d["day"],
    )
    return {
        "id": curriculum.id,
        "topic": curriculum.topic,
        "language_code": curriculum.language_code,
        "cefr_level": curriculum.cefr_level,
        "days": list(days),
    }


def import_plan(store: ContentStore, file: dict) -> tuple[str, Curriculum]:
    """Rebuild and save a Curriculum from a self-describing plan dict.

    If ``file["id"]`` is present the curriculum MUST already exist (otherwise
    ``KeyError`` -> 404); its existing ``metadata`` is preserved unchanged so a
    hand-edit round-trip does not wipe chat state. If ``id`` is absent a new id
    is minted via ``mint_id`` and ``metadata`` starts as ``{}``.
    """
    topic = file.get("topic")
    if not isinstance(topic, str) or not topic.strip():
        raise ValueError("topic must be a non-empty string")

    cefr_level = file.get("cefr_level")
    if not isinstance(cefr_level, str) or not cefr_level.strip():
        raise ValueError("cefr_level must be a non-empty string")

    language_code = file.get("language_code")
    if not isinstance(language_code, str) or not language_code.strip():
        raise ValueError("language_code must be a non-empty string")

    days = file.get("days", [])
    validate_plan_days(days, start_day=1)

    curriculum_id = file.get("id")
    if curriculum_id is not None:
        existing = store.get_curriculum(curriculum_id)
        if existing is None:
            raise KeyError(f"Curriculum not found: {curriculum_id}")
        metadata = copy.deepcopy(existing.metadata)
        # A pending proposal was numbered against the pre-import day list; the
        # import may renumber/remove days, so committing it afterwards would
        # produce colliding day numbers. Chat and feedback stay (the hand-edit
        # round-trip contract); the proposal is dropped.
        if isinstance(metadata.get("planner"), dict):
            metadata["planner"]["proposed"] = None
    else:
        curriculum_id = mint_curriculum_id(topic)
        metadata = {}

    curriculum = Curriculum(
        id=curriculum_id,
        topic=topic,
        language_code=language_code,
        cefr_level=cefr_level,
        days=[CurriculumDay(**d) for d in days],
        metadata=metadata,
    )
    store.save_curriculum(curriculum_id, curriculum)
    return curriculum_id, curriculum


def get_planner_state(curriculum: Curriculum) -> dict:
    """Return the planner state dict (or a default without mutating)."""
    planner = curriculum.metadata.get("planner")
    if planner is not None:
        return copy.deepcopy(planner)
    return {"chat": [], "proposed": None, "feedback": []}
