"""Story-JSON export/import for lesson authoring.

Story JSON is source; the Lesson blob in ``lessons.data_json`` is a build
artifact. Export reconstructs the editable Story JSON from a stored Lesson
(or returns the persisted exact source when present); import rebuilds a
Lesson through ``build_lesson_from_story`` — the same build step generation
uses — so authored and generated lessons are identical in shape.

Design: docs/lesson-authoring.md.
"""

from __future__ import annotations

from app.generation.ids import mint_id
from app.generation.story import build_lesson_from_story
from app.models.language import Language
from app.models.lesson import (
    Lesson,
    SectionType,
    extract_sentence_translations_from_translated,
)
from app.storage.store import ContentStore


def _require(entry: object, path: str, fields: tuple[str, ...]) -> None:
    if not isinstance(entry, dict):
        raise ValueError(f"{path} must be an object")
    for field in fields:
        if field not in entry:
            raise ValueError(f"{path} is missing required field '{field}'")
        value = entry[field]
        # Every required field becomes TTS text (or a voice-map key); an empty
        # or non-string value passes a bare presence check but fails much later
        # at render time with an opaque error, so reject it here by name.
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{path}.{field} must be a non-empty string")


def validate_story(story: object) -> None:
    """Reject malformed Story JSON with a clear message (not a deep KeyError).

    Requirements mirror the hard key accesses in ``build_lesson_from_story``
    and the section builders — including ``lines[].translation``, which
    ``build_translated_section`` reads unconditionally. Required fields must be
    non-empty strings: they all end up as TTS phrases (a "" line renders fine
    at import and then breaks audio rendering).
    """
    if not isinstance(story, dict):
        raise ValueError("story must be a JSON object")
    title = story.get("title")
    if title is not None and (not isinstance(title, str) or not title.strip()):
        raise ValueError("title must be a non-empty string when present")
    key_phrases = story.get("key_phrases", [])
    scenes = story.get("scenes", [])
    if not isinstance(key_phrases, list):
        raise ValueError("key_phrases must be a list")
    if not isinstance(scenes, list):
        raise ValueError("scenes must be a list")
    if not key_phrases and not scenes:
        raise ValueError("story needs at least one of 'key_phrases' or 'scenes'")
    for i, kp in enumerate(key_phrases):
        _require(kp, f"key_phrases[{i}]", ("phrase", "translation"))
    for i, scene in enumerate(scenes):
        _require(scene, f"scenes[{i}]", ("label",))
        lines = scene.get("lines", [])
        if not isinstance(lines, list):
            raise ValueError(f"scenes[{i}].lines must be a list")
        for j, line in enumerate(lines):
            _require(line, f"scenes[{i}].lines[{j}]", ("speaker", "text", "translation"))


def speaker_warnings(story: dict, language: Language) -> list[str]:
    """Warn once per speaker missing from the voice map (silent narrator fallback)."""
    known = set(language.tts_voice_map)
    unknown: list[str] = []
    for scene in story.get("scenes", []):
        for line in scene.get("lines", []):
            speaker = line["speaker"].lower()
            if speaker not in known and speaker not in unknown:
                unknown.append(speaker)
    return [
        f"speaker '{s}' is not in the {language.code} voice map; its lines will use the narrator voice" for s in unknown
    ]


def export_lesson(store: ContentStore, lesson_id: str) -> dict:
    """Export a stored lesson as a self-describing editable Story-JSON file."""
    row = store.get_lesson_row(lesson_id)
    if row is None:
        raise KeyError(f"Lesson not found: {lesson_id}")
    lesson = Lesson.from_json(row["data_json"])
    # Prefer the exact persisted source (present on lessons built since
    # 2026-07); reconstruction is the fallback for legacy lessons.
    story = lesson.generation_metadata.get("story") or _reconstruct_story(lesson)
    return {
        "curriculum_id": row["curriculum_id"],
        "day": row["day"],
        "story": story,
    }


def _reconstruct_story(lesson: Lesson) -> dict:
    """Rebuild Story JSON from the expanded Lesson (legacy lessons lack a stored source).

    The NATURAL_SPEED section is the dialogue's canonical form: phrase 0 is the
    section title, each narrator/EN phrase afterwards opens a scene, and every
    L2 phrase is a line whose ``role`` is the original ``speaker``.
    """
    translations = lesson.generation_metadata.get(
        "sentence_translations"
    ) or extract_sentence_translations_from_translated(lesson)

    natural = next(
        (s for s in lesson.sections if s.section_type is SectionType.NATURAL_SPEED),
        None,
    )
    scenes: list[dict] = []
    phrases = natural.phrases[1:] if natural is not None else []
    for phrase in phrases:
        if phrase.role == "narrator" and phrase.language_code == "en":
            scenes.append({"label": phrase.text, "lines": []})
        elif scenes:
            scenes[-1]["lines"].append(
                {
                    "speaker": phrase.role,
                    "text": phrase.text,
                    "translation": translations.get(phrase.text, ""),
                }
            )

    token_glosses = lesson.generation_metadata.get("token_glosses", {})
    return {
        "title": lesson.title,
        "key_phrases": [{"phrase": kp.phrase, "translation": kp.translation} for kp in lesson.key_phrases],
        "scenes": scenes,
        "dialogue_glosses": [{"word": w, "translation": t} for w, t in token_glosses.items()],
        "morphology_focus": lesson.generation_metadata.get("morphology_focus", []),
    }


def import_lesson(store: ContentStore, file: dict, language: Language) -> tuple[str, Lesson]:
    """Rebuild and save a Lesson from a self-describing Story-JSON file.

    Append semantics: every import mints a fresh lesson id; the latest lesson
    per day wins (``get_latest_lesson_by_day``), exactly like regeneration.
    """
    story = file.get("story")
    validate_story(story)
    lesson = build_lesson_from_story(story, language=language)
    lesson_id = mint_id(lesson.title)
    store.save_lesson(lesson_id, file["curriculum_id"], file["day"], lesson)
    sync_curriculum_day_title(store, file["curriculum_id"], file["day"], lesson.title)
    return lesson_id, lesson


def sync_curriculum_day_title(store: ContentStore, curriculum_id: str, day: int, title: str) -> None:
    curriculum = store.get_curriculum(curriculum_id)
    if curriculum is None:
        return
    for d in curriculum.days:
        if d.day == day:
            d.title = title
            break
    store.save_curriculum(curriculum_id, curriculum)
