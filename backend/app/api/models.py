"""Shared Pydantic request models for API endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

# ── SRS models ──────────────────────────────────────────────────────────────


class ListenRequest(BaseModel):
    lesson_id: str
    word_ratings: dict[str, str] = {}  # lemma → "hard"|"easy"|"again"


class ImportListensRequest(BaseModel):
    lesson_ids: list[str]


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
    strategy: Literal["WIDER", "DEEPER"] = "WIDER"


class ImportLessonRequest(BaseModel):
    """Self-describing Story-JSON file (docs/lesson-authoring.md).

    Provide exactly one of ``story`` (pre-parsed dict) or ``raw`` (pasted text
    containing prose + fenced JSON, cleaned via ``parse_json_object``).
    """

    curriculum_id: str
    day: int
    story: dict | None = None
    raw: str | None = None

    @model_validator(mode="after")
    def _exactly_one_story_or_raw(self):
        if (self.story is None) == (self.raw is None):
            msg = "Exactly one of 'story' or 'raw' must be provided"
            raise ValueError(msg)
        return self


# ── Audio models ────────────────────────────────────────────────────────────


class RenderAudioRequest(BaseModel):
    lesson_id: str


# ── Curriculum models ────────────────────────────────────────────────────────


class ImportPlanRequest(BaseModel):
    """Self-describing plan file for curriculum authoring.

    ``days`` stays a free list — its schema is validated by
    ``plan_io.validate_plan_days`` so errors carry field paths.
    """

    id: str | None = None
    topic: str
    language_code: str
    cefr_level: str
    days: list


class StartPlanRequest(BaseModel):
    topic: str
    cefr_level: str = "A2"


class PlanTurnRequest(BaseModel):
    message: str
    # Mirrors the frontend clamp (clampBatchSize, 1..14) — 0 days is meaningless
    # and large values ask the LLM for more days than the token budget can hold.
    batch_size: int = Field(5, ge=1, le=14)
    pasted_response: str | None = None


class PlanFeedbackRequest(BaseModel):
    day: int
    note: str


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


class PipelineRetryRequest(BaseModel):
    day: int


class PipelineRegenerateRequest(BaseModel):
    day: int
    strategy: Literal["WIDER", "DEEPER"] = "WIDER"


class CreateBaseCardRequest(BaseModel):
    surface: str
    lemma: str
    sentence: str
    language_code: str
    translation: str = ""
