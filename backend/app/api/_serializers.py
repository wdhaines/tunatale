"""Response serialization helpers for API endpoints."""

from app.models.lesson import Lesson


def serialize_lesson(lesson_id: str, lesson: Lesson, *, day: int | None = None) -> dict:
    """Build the standard lesson response dict shared by ``get_lesson`` and ``get_lesson_by_day``."""
    result: dict = {
        "id": lesson_id,
        "title": lesson.title,
        "language_code": lesson.language_code,
        "key_phrases": [{"phrase": kp.phrase, "translation": kp.translation} for kp in lesson.key_phrases],
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
    }
    if day is not None:
        result["day"] = day
    return result
