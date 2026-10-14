"""Response serialization helpers for API endpoints."""

from app.generation.grammar_lesson import drill_table, find_pattern
from app.generation.pattern_gate import progress
from app.models.lesson import Lesson, project_key_phrases
from app.storage.store import ContentStore


def serialize_lesson(
    lesson_id: str, lesson: Lesson, *, day: int | None = None, srs_db=None, store: ContentStore | None = None
) -> dict:
    """Build the standard lesson response dict shared by ``get_lesson`` and ``get_lesson_by_day``.

    With the learner's *srs_db* and *store*, a lesson's drill also carries how
    far the learner is with its pattern (``pattern_gate.Progress.summary``),
    read at fetch time.
    """
    result: dict = {
        "id": lesson_id,
        "title": lesson.title,
        "language_code": lesson.language_code,
        "key_phrases": project_key_phrases(lesson.key_phrases),
        "sections": [
            {
                "type": s.section_type.value,
                "phrases": [
                    {"text": p.text, "role": p.role, "language_code": p.language_code, "voice_id": p.voice_id}
                    for p in s.phrases
                ],
            }
            for s in lesson.sections
        ],
        # The review meter, and ONLY it — `generation_metadata` also carries
        # token_glosses, verb_base_glosses, sentence_translations and the full
        # Story-JSON source, which would bloat every lesson fetch on the reading
        # path. Always present and always lists: empty means UNMEASURABLE (an
        # authored import, or a lesson generated before the meter existed), which
        # must not read as a failed generation.
        "review_requested": list(lesson.generation_metadata.get("review_requested", [])),
        "review_used": list(lesson.generation_metadata.get("review_used", [])),
        "gloss_entry_count": lesson.generation_metadata.get("gloss_entry_count"),
    }
    if day is not None:
        result["day"] = day
    # Present only on a lesson that has an affix drill, like ``day`` above.
    drill = drill_table(lesson)
    if drill is not None:
        if srs_db is not None and store is not None:
            pattern = find_pattern(lesson.language_code, drill["pattern"])
            drill["progress"] = progress(pattern, srs_db=srs_db, store=store).summary()
        result["drill"] = drill
    return result
