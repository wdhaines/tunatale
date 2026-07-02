"""Shared Pydantic request models for API endpoints."""

from __future__ import annotations

from pydantic import BaseModel

# ── SRS models ──────────────────────────────────────────────────────────────


class ListenRequest(BaseModel):
    lesson_id: str
    word_ratings: dict[str, str] = {}  # lemma → "hard"|"easy"|"again"


class DrillRequest(BaseModel):
    rating: str | None = None
    signal: str | None = None
    time_ms: int = 0


class TranslateRequest(BaseModel):
    text: str
    language_code: str


class CreateItemRequest(BaseModel):
    text: str
    language_code: str
    word_count: int
    translation: str = ""
    source_sentence: str = ""
    source_lesson_id: str | None = None
    source_line_index: int | None = None


class UpdateItemRequest(BaseModel):
    text: str
    translation: str


class BulkDeleteRequest(BaseModel):
    ids: list[int]


class SuspendRequest(BaseModel):
    suspended: bool
    direction: str | None = None


class SetStateRequest(BaseModel):
    state: str  # "new" | "learning" | "known" | "ignored"


class IgnoreLemmaRequest(BaseModel):
    lemma: str
    language_code: str


# ── Generation models ────────────────────────────────────────────────────────


class GenerateStoryRequest(BaseModel):
    curriculum_id: str
    day: int = 1
    strategy: str = "WIDER"


class ImportLessonRequest(BaseModel):
    """Self-describing Story-JSON file (docs/lesson-authoring.md).

    `story` stays a free dict — its schema is validated by
    `lesson_io.validate_story` so errors carry field paths.
    """

    curriculum_id: str
    day: int
    story: dict


# ── Audio models ────────────────────────────────────────────────────────────


class RenderAudioRequest(BaseModel):
    lesson_id: str


# ── Curriculum models ────────────────────────────────────────────────────────


class GenerateCurriculumRequest(BaseModel):
    topic: str
    cefr_level: str = "A2"
    num_days: int = 7


class InflectionClozeRequest(BaseModel):
    surface: str
    lemma: str
    feature: str
    sentence: str
    language_code: str
    # Optional lesson context: resolves the word gloss + sentence translation
    # from the lesson's generation_metadata (mirrors /listen). Omitted by older
    # callers, in which case the cloze carries only its grammar hint.
    lesson_id: str = ""
    translation: str = ""


class CreateBaseCardRequest(BaseModel):
    surface: str
    lemma: str
    sentence: str
    language_code: str
    translation: str = ""
