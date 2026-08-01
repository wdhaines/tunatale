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
    grade_class: Literal["create", "new", "learning", "due", "ahead"]
    rating: Literal["again", "hard", "good", "easy", "skip"]
    translation: str
    progress: float | None
    well_known: bool = False
    # True for every row this listen will actually act on; False for rows past
    # the shared introduction budget, which the modal renders as a read-only
    # "next listen" tail. Two populations can be False: create rows beyond the
    # budget, and `grade_class="new"` rows (a NEW-state card whose introduction
    # this listen cannot afford) — both spend the same allowance, so both
    # overflow into the same tail.
    # The default spares Python callers from restating it, but NOT the frontend:
    # FastAPI emits the serialization schema, where a defaulted field is still
    # `required`, so the generated TS type demands it and every candidate
    # fixture must carry it.
    will_create: bool = True
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


# ── Batch 5: SRS status returns and the lesson transcript ───────────────────
#
# Every model below was derived by reading its handler's return dict, not the
# underlying dataclass — the two differ (WordToken carries `collocation_is_due`,
# which get_lesson_transcript does not emit). `response_model=` FILTERS, so a
# field the model omits is silently deleted from the live payload; each of these
# is pinned by a key-set test written against the UNFILTERED handler output.


class SrsStatsResponse(BaseModel):
    """Response of GET /api/srs/stats."""

    total: int
    due_today: int


class BulkDeleteResponse(BaseModel):
    """Response of POST /api/srs/items/bulk-delete."""

    deleted: int


class BackfillTranslationsResponse(BaseModel):
    """Response of POST /api/srs/backfill-translations."""

    updated: int
    glosses_found: int


class TranslateResponse(BaseModel):
    """Response of POST /api/srs/translate."""

    translation: str


class TranslateMissingResponse(BaseModel):
    """Response of POST /api/srs/translate-missing.

    Two returns — the "nothing untranslated" early return and the batch loop's
    — but both carry exactly these keys, so there is no conditional key-set.
    Both branches are pinned by their own key-set test.
    """

    translated: int
    skipped: int


class ImportListensResponse(BaseModel):
    """Response of POST /api/srs/listens/import.

    Each list holds lesson ids, partitioned by what the import did with them.
    """

    imported: list[str]
    already_present: list[str]
    unknown: list[str]


class UndoGradeResponse(BaseModel):
    """Response of POST /api/srs/items/{item_id}/direction/{direction}/undo.

    ``restored_state`` is ``SRSState.value``; ``restored_due_at`` is an ISO-8601
    timestamp — both already stringified by the handler.
    """

    status: str
    direction: str
    restored_state: str
    restored_due_at: str


class ListenedLesson(BaseModel):
    """One element of ListensResponse.lessons.

    Shape declared at ``db_listens.py::get_listened_lessons``.
    """

    lesson_id: str
    listen_count: int
    last_listened_at: str


class ListensResponse(BaseModel):
    """Response of GET /api/srs/listens."""

    lessons: list[ListenedLesson]


class TranscriptKeyPhrase(BaseModel):
    """One element of LessonTranscriptResponse.key_phrases."""

    phrase: str
    translation: str


class TranscriptWord(BaseModel):
    """One element of TranscriptDialogueLine.words.

    The 25 fields the handler projects out of ``transcript.WordToken``. Note
    that ``WordToken.collocation_is_due`` is deliberately NOT among them — it is
    computed but never serialized, and adding it here would not surface it (the
    model can only filter, never invent).
    """

    surface: str
    prefix_punct: str
    suffix_punct: str
    lemma: str
    srs_state: str
    srs_item_id: int | None
    translation: str | None
    collocation_span_id: int | None
    collocation_start: bool
    collocation_srs_state: str | None
    collocation_lemma: str | None
    collocation_translation: str | None
    collocation_progress: float | None
    card_type: str | None
    active_state: str
    active_direction: str | None
    is_due: bool
    progress: float | None
    inflectable: bool
    inflection_feature: str | None
    known_marked: bool
    recognition_reviewable: bool
    recognition_state: str | None
    recognition_is_due: bool
    well_known: bool


class TranscriptDialogueLine(BaseModel):
    """One element of LessonTranscriptResponse.dialogue_lines."""

    role: str
    sentence: str
    words: list[TranscriptWord]


class LessonTranscriptResponse(BaseModel):
    """Response of GET /api/srs/lesson/{lesson_id}/transcript."""

    lesson_id: str
    key_phrases: list[TranscriptKeyPhrase]
    dialogue_lines: list[TranscriptDialogueLine]


# ── Batch 6a: the shared SRS item shape ─────────────────────────────────────
#
# Four element models + two envelopes, serving the 8 endpoints that serialize
# through ``srs.py::_item_to_dict`` / ``_direction_to_dict`` (create_item,
# list_items, patch_item, reset_item, restore_known_item, set_item_state,
# suspend_item, untrack_item). Every field below is the serializer's OUTPUT
# type (post ``.isoformat()`` / ``.value``), NOT the source dataclass type —
# ``SrsItemResponse.difficulty`` is a float because it rides the direction, not
# ``SyntacticUnit.difficulty`` (an int 1-5), and ``due_at``/``last_review`` are
# nullable on the item (``flat_src`` is None for single-template notes) though
# they are not on the direction.
#
# Every route takes ``response_model_exclude_unset=True`` because
# ``_direction_to_dict`` OMITS ``left`` when it is None; a plain
# ``response_model=`` would ADD ``"left": null`` to every payload that today
# omits the key. Same for ``untrack_item``'s short branch, which omits ``item``.


class DirectionStateResponse(BaseModel):
    """One direction of an SRS item; serves the 8 ``_item_to_dict`` endpoints.

    ``left`` is omitted when None (``srs.py::_direction_to_dict``), so it must
    ride on ``response_model_exclude_unset`` — never a plain ``response_model``.
    """

    state: str  # ds.state.value
    due_at: str  # ds.due_at.isoformat()
    stability: float
    difficulty: float
    reps: int
    lapses: int
    last_review: str | None
    last_review_time_ms: int
    anki_card_id: int | None
    left: int | None = None  # omitted when None


class ItemExtra(BaseModel):
    """One element of SrsItemResponse.extras (a BackField → {label, html, tier})."""

    label: str
    html: str
    tier: str


class ItemDirections(BaseModel):
    """SrsItemResponse.directions; production is None for single-template notes."""

    recognition: DirectionStateResponse | None
    production: DirectionStateResponse | None


class SrsItemResponse(BaseModel):
    """Response of create_item, patch_item, reset_item, restore_known_item,
    set_item_state, and suspend_item; the element of list_items and untrack_item.

    Exactly 25 keys, one per ``_item_to_dict`` output key.
    """

    id: int
    text: str
    translation: str
    word_count: int
    # ── the 7 **flat keys — from flat_src, which is None-able, so due_at and
    #    last_review are nullable here even though they are not on the direction
    state: str
    due_at: str | None
    stability: float
    difficulty: float
    reps: int
    lapses: int
    last_review: str | None
    # ── the rest
    language_code: str
    guid: str | None
    anki_note_id: int | None
    directions: ItemDirections
    card_type: str
    source_sentence: str
    source_sentence_translation: str
    image_url: str | None
    audio_url: str | None
    grammar: str
    note: str
    article: str
    extras: list[ItemExtra]
    pos: str


class ListItemsResponse(BaseModel):
    """Response of GET /api/srs/items."""

    items: list[SrsItemResponse]
    total: int


class UntrackItemResponse(BaseModel):
    """Response of POST /api/srs/items/{item_id}/untrack.

    Two branches — ``{"action": "deleted"}`` and ``{"action": "suspended",
    "item": ...}``. ``item`` is optional and left unset on the deleted branch,
    which is why the route uses ``response_model_exclude_unset``.
    """

    action: str
    item: SrsItemResponse | None = None


# ── Batch 6b: the due/new envelopes ─────────────────────────────────────────
#
# get_due_collocations / get_new_collocations serialize through the same
# ``srs.py::_item_to_dict`` / ``_direction_to_dict`` as 6a, so their element
# model is SrsItemResponse unchanged — only the envelopes are new. Both routes
# take ``response_model_exclude_unset=True`` because the nested
# DirectionStateResponse omits ``left`` when None (same trap as 6a).


class DueCollocationsResponse(BaseModel):
    """Response of GET /api/srs/due."""

    due: list[SrsItemResponse]


class NewCollocationsResponse(BaseModel):
    """Response of GET /api/srs/new."""

    new: list[SrsItemResponse]


# ── Batch 6d: the image-item response ────────────────────────────────────────
#
# The 5-key static literal returned bare by PUT /items/{id}/image, PUT
# /items/{id}/image/upload, and DELETE /items/{id}/image (``srs_images.py::
# _item_response``). No conditional keys — ``image_url`` is always emitted
# (null when the card has no image), so a plain ``response_model=`` suffices.


class ImageItemResponse(BaseModel):
    """Response of the three SRS image endpoints."""

    id: int
    text: str
    translation: str
    card_type: str
    image_url: str | None


# ── Batch 6f: LLM rate-limit status ─────────────────────────────────────────
#
# GET /api/llm/rate-limit and POST /api/llm/rate-limit/probe both end in
# ``llm.py::_status_payload``, so they share one model. Nested objects are
# whole-or-None (built as full dict literals or left None), so a plain
# ``response_model=`` (no exclude_unset) is safe.


class RateLimitSnapshot(BaseModel):
    """Non-null value of RateLimitStatusResponse.snapshot."""

    age_s: float
    requests_limit: int | None
    requests_remaining: int | None
    requests_reset_in_s: float | None
    tokens_limit: int | None
    tokens_remaining: int | None
    tokens_reset_in_s: float | None


class Last429(BaseModel):
    """Non-null value of RateLimitStatusResponse.last_429."""

    ago_s: float
    retry_in_s: float | None


class RateLimitStatusResponse(BaseModel):
    """Response of GET /api/llm/rate-limit and POST /api/llm/rate-limit/probe."""

    provider: str
    model: str | None
    llm_mode: str
    snapshot: RateLimitSnapshot | None
    last_429: Last429 | None
    tokens_used_24h: int | None
    tokens_per_day_limit: int
