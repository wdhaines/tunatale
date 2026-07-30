"""Shared Pydantic request models for API endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

# ── SRS models ──────────────────────────────────────────────────────────────


class ListenRequest(BaseModel):
    lesson_id: str
    word_ratings: dict[str, Literal["again", "hard", "good", "easy", "skip"]] = {}  # lemma → rating
    kp_ratings: dict[str, Literal["again", "hard", "good", "easy", "skip"]] = {}  # key-phrase text → same domain
    # Items the user actually graded in the preview, as opposed to ones the
    # listen auto-rated. A confirmed grade is a review the user performed, so it
    # is APPLIED immediately; everything else is staged into the pending bucket
    # for "Check your work". Kept separate from the ratings maps because
    # presence there is already overloaded — a well-known row must be listed for
    # the backend to consider it at all, so "present" cannot also mean
    # "reviewed".
    confirmed_words: list[str] = []  # lemmas the user graded by hand
    confirmed_kps: list[str] = []  # key-phrase texts the user graded by hand


class ImportListensRequest(BaseModel):
    lesson_ids: list[str]


class ListenPreviewCandidate(BaseModel):
    """One row of GET /lesson/{id}/listen-preview's response."""

    kind: Literal["create", "word", "kp"]
    text: str
    item_id: int | None
    grade_class: Literal["create", "learning", "due", "ahead"]
    rating: Literal["again", "hard", "good", "easy", "skip"]
    translation: str
    progress: float | None
    well_known: bool = False
    due_at: str | None = None


class ListenPreviewResponse(BaseModel):
    """Response of GET /lesson/{id}/listen-preview."""

    candidates: list[ListenPreviewCandidate]


class CommitPendingResponse(BaseModel):
    """Response of POST /lesson/{id}/commit-pending."""

    status: str
    applied: int


class DrillRequest(BaseModel):
    rating: str | None = None
    signal: str | None = None
    time_ms: int = 0
    # Set by the lesson "Check your work" review flow. When true, a re-grade of a
    # card already reviewed today (e.g. the listen's auto-Good) updates FSRS state
    # but does not re-charge the daily review budget. See drill_feedback.
    lesson_review: bool = False


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


class GenerationModeRequest(BaseModel):
    mode: Literal["auto", "manual"]


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


# ── Response models (BP ledger drain, bp-ledger-burndown-2026-07 stage 3) ────
# Each mirrors a handler that returns a single fixed key-set — no conditional
# keys, no delegation to a helper whose shape must be traced.


class HealthResponse(BaseModel):
    """Response of GET /api/health."""

    status: str


class StatusResponse(BaseModel):
    """Response of endpoints that return only a fixed ``status`` string:
    DELETE /api/srs/items/{id}, POST /api/srs/ignored-lemmas,
    DELETE /api/srs/ignored-lemmas."""

    status: str


class MarkLessonReviewedResponse(BaseModel):
    """Response of POST /api/srs/lesson/{lesson_id}/reviewed."""

    ok: bool


class DeleteCurriculumResponse(BaseModel):
    """Response of DELETE /api/curriculum/{curriculum_id}."""

    deleted: str


class DeleteDayResponse(BaseModel):
    """Response of DELETE /api/curriculum/{curriculum_id}/days/{day}."""

    deleted_day: int
    days: int


class PlanResetResponse(BaseModel):
    """Response of POST /api/curriculum/{curriculum_id}/plan/reset."""

    reply_count_cleared: int


class SetGenerationModeResponse(BaseModel):
    """Response of POST /api/curriculum/{curriculum_id}/generation-mode."""

    mode: str


class PlanTurnPromptResponse(BaseModel):
    """Response of POST /api/curriculum/{curriculum_id}/plan/turn/prompt."""

    system_prompt: str
    user_prompt: str


class GetLessonAudioResponse(BaseModel):
    """Response of GET /api/audio/lesson/{lesson_id}."""

    audio_id: str
    lesson_id: str
    sections: list
    cues: list | None = None


class ImportCurriculumPlanResponse(BaseModel):
    """Response of POST /api/curriculum/import."""

    id: str
    topic: str
    language_code: str
    days: int


class StartPlanResponse(BaseModel):
    """Response of POST /api/curriculum/plan."""

    id: str
    topic: str
    language_code: str
    cefr_level: str
    days: int


class PlanTurnResponse(BaseModel):
    """Response of POST /api/curriculum/{curriculum_id}/plan/turn."""

    reply: str
    proposed: dict | None = None


class PlanCommitResponse(BaseModel):
    """Response of POST /api/curriculum/{curriculum_id}/plan/commit."""

    id: str
    days: int


class PlanFeedbackResponse(BaseModel):
    """Response of POST /api/curriculum/{curriculum_id}/plan/feedback."""

    feedback: list


class GenerateStoryResponse(BaseModel):
    """Response of POST /api/story/generate."""

    id: str
    title: str
    sections: list


class GetStoryPromptResponse(BaseModel):
    """Response of GET /api/story/prompt."""

    system_prompt: str
    user_prompt: str


class StorySection(BaseModel):
    """One element of ImportStoryResponse.sections / GenerateStoryResponse.sections."""

    type: str
    phrase_count: int


class ImportStoryResponse(BaseModel):
    """Response of POST /api/story/import."""

    id: str
    title: str
    sections: list[StorySection]
    warnings: list[str]


class LanguageItem(BaseModel):
    """One element of LanguagesResponse.languages."""

    code: str
    name: str


class LanguagesResponse(BaseModel):
    """Response of GET /api/languages."""

    languages: list[LanguageItem]
    active: str
    sync_available: bool


class LlmLastError(BaseModel):
    """Non-null value of LlmHealthResponse.last_error."""

    status: int
    message: str
    ago_s: float


class LlmHealthResponse(BaseModel):
    """Response of GET /api/llm/health."""

    healthy: bool
    consecutive_failures: int
    last_error: LlmLastError | None
    fallback_allowed: bool
    llm_mode: str


class LlmActivityResponse(BaseModel):
    """Response of GET /api/llm/activity.

    ``events`` is intentionally bare ``list``: ActivityLog.record_llm_call
    splats a caller-supplied ``info`` dict (``{**info, "seq", "kind"}``) whose
    keys vary by call site (success vs. 429 vs. pipeline events carry different
    fields), so there is no constant element key-set to model.
    """

    latest: int
    events: list
