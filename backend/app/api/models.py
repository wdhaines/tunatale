"""Shared Pydantic request models for API endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    # Lemmas / key-phrase texts the user deliberately opted past the daily
    # new-card cap in the preview. Kept separate from the ratings maps for the
    # same reason `confirmed_words` is — presence there is overloaded. For a
    # create row the polarity is INVERTED: absent from `word_ratings` means the
    # backend's default "good", which CREATES the card, so presence cannot
    # distinguish "opted in past the cap" from an ordinary live row.
    over_cap_words: list[str] = []  # lemmas the user opted past the daily new-card cap
    over_cap_kps: list[str] = []  # key-phrase texts, same


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
    # Why a listen defers this row instead of staging it by default: "known"
    # (scheduled past the listen horizon) or "learning" (mid-acquisition — the
    # step exists to test recall at a specific interval and a listen is not
    # that test). Both render as a collapsed group, rated `skip` by default and
    # stageable only by an explicit per-row grade.
    #
    # ONE field rather than a boolean per population on purpose. These rows
    # invert the polarity of every other row — absent from `word_ratings` means
    # *skip* here and *good* everywhere else — and a second ad-hoc copy of an
    # inverted rule is how the third one gets written wrong. The next deferred
    # category costs a value, not a code path.
    deferred_reason: Literal["known", "learning"] | None = None
    # DERIVED from `deferred_reason`, kept because existing clients read it.
    # Never maintain the two in parallel: two fields that can disagree is the
    # failure the single-field shape exists to prevent.
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
    strategy: Literal["WIDER", "DEEPER", "REVIEW"] = "WIDER"
    # None = inherit the curriculum's setting; a value overrides it.
    review_pressure: Literal["NATURAL", "BALANCED", "INSISTENT"] | None = None


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


class ReviewPressureRequest(BaseModel):
    pressure: Literal["NATURAL", "BALANCED", "INSISTENT"]


class SetReviewPressureResponse(BaseModel):
    pressure: str


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
    strategy: Literal["WIDER", "DEEPER", "REVIEW"] = "WIDER"


class CreateBaseCardRequest(BaseModel):
    surface: str
    lemma: str
    sentence: str
    language_code: str
    translation: str = ""


# ── Auth models ─────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    """Request body for POST /api/auth/login."""

    email: str
    password: str


class LoginResponse(BaseModel):
    """Response of POST /api/auth/login.

    Deliberately does NOT carry the session token — it lives in ``Set-Cookie``
    only.  ``response_model_exclude_unset`` is not needed: every field is
    always set.
    """

    email: str


class MeResponse(BaseModel):
    """Response of GET /api/auth/me.

    Mirrors ``User`` without ``password_hash`` — that is a credential the
    store round-trips, not something to publish.
    """

    email: str


class AuthStatusResponse(BaseModel):
    """Response of GET /api/auth/status.

    One boolean, and it must stay one boolean.  The endpoint is unauthenticated
    by necessity — the SPA asks it before it has a session — so anything added
    here is published to anyone who can reach the port.  "Does this deployment
    require a login?" is safe; "how many users exist" or "has anyone been
    bootstrapped" is reconnaissance.  Pinned by
    ``test_status_leaks_nothing_beyond_the_flag``.
    """

    auth_enabled: bool


class LogoutResponse(BaseModel):
    """Response of POST /api/auth/logout.

    200 with a small model rather than 204: the repo is known-green on the
    200-plus-model path, and the locked test accepts either.
    """

    status: str


# ── Response models (BP ledger drain, bp-ledger-burndown-2026-07 stage 3) ────
# Each mirrors a handler that returns a single fixed key-set — no conditional
# keys, no delegation to a helper whose shape must be traced.


class HealthResponse(BaseModel):
    """Response of GET /api/health.

    ``checks`` maps a fixed dependency name to ``"ok"``/``"fail"``. Status only —
    the route is unauthenticated, so no paths, versions, counts or language names
    go in here. See ``app/api/health.py`` for why the names are aggregated.
    """

    status: str
    checks: dict[str, str]


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
    warnings: list[str]


class CreateReviewSessionRequest(BaseModel):
    """Body of POST /api/story/review-session — deliberately empty.

    ⚠️ ``extra="forbid"`` is the interface carrying the decision. A review
    session is drawn from the WHOLE language deck, so there is no curriculum to
    name and no day to pick; silently ignoring a ``curriculum_id`` would let a
    caller believe it had scoped the session to one plan. Rejecting it says so.
    """

    model_config = ConfigDict(extra="forbid")


class CreateReviewSessionResponse(BaseModel):
    """Response of POST /api/story/review-session.

    Dated, not numbered — a session has no position in a sequence to report.
    """

    id: str
    session_date: str
    title: str
    review_requested: list[str]
    review_used: list[str]
    warnings: list[str]


class ReviewSessionSummary(BaseModel):
    """One row of the dated list on the Lessons index.

    ⚠️ ``review_requested``/``review_used`` are OPTIONAL, and the ``None`` is
    load-bearing: it means "never measured" and renders as no readout at all,
    while ``[]`` is a measured zero. Defaulting them to ``[]`` here would put a
    permanent "reused 0 of 0" on any row that predates the meter — a grade where
    an observation belongs.
    """

    id: str
    language_code: str
    session_date: str
    title: str
    review_requested: list[str] | None = None
    review_used: list[str] | None = None


class ListReviewSessionsResponse(BaseModel):
    """Response of GET /api/review-sessions."""

    sessions: list[ReviewSessionSummary]


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


# ── Batch 6c: the two queue routes ───────────────────────────────────────────
#
# ``get_review_queue`` and ``get_lesson_review_queue`` both serialize through
# ``srs.py::_queue_item_to_dict``, which is ``_item_to_dict``'s output plus
# ``direction`` and ``word_audio_url`` — hence one item model composed from
# SrsItemResponse rather than two near-duplicates. The lesson route then stamps
# ``pending_rating`` onto each entry after the serializer returns, which is the
# ONLY difference between the two element shapes.
#
# Both routes need ``response_model_exclude_unset=True``: the nested
# DirectionStateResponse omits ``left`` when None (same trap as 6a/6b), and a
# plain ``response_model=`` would ADD ``"left": null`` back. Pinned by the
# nested-direction key-set assertions in ``test_api_srs.py::
# TestReviewQueueResponseShape`` and ``test_api_lesson_review_queue.py``.


class QueueItemResponse(SrsItemResponse):
    """One served card: the 25 ``_item_to_dict`` keys + the 2 queue-only keys.

    The 7 flat per-direction keys inherited from SrsItemResponse are overwritten
    by ``_queue_item_to_dict`` with the *queued* direction's values, so their
    types are unchanged — only their provenance is.
    """

    direction: str  # Direction.value of the card actually being served
    word_audio_url: str | None  # cloze only; None for vocab (whose word audio is audio_url)


class LessonQueueItemResponse(QueueItemResponse):
    """A lesson "Check your work" item: a queue item plus the staged rating."""

    pending_rating: str


class ReviewQueueResponse(BaseModel):
    """Response of GET /api/srs/review-queue."""

    queue: list[QueueItemResponse]


class LessonReviewQueueResponse(BaseModel):
    """Response of GET /api/srs/lesson/{lesson_id}/review-queue."""

    queue: list[LessonQueueItemResponse]
    has_unreviewed_listen: bool


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
    tokens_used_day: int | None
    tokens_per_day_limit: int
    tokens_day_reset_in_s: float | None
    requests_used_day: int | None
    requests_per_day_limit: int
    requests_day_reset_in_s: float | None


# ── Batch 7: curriculum listing/detail/progress/source + pipeline + image
#    candidates (BP ledger drain) ─────────────────────────────────────────
#
# Every field below is a fixed key — none of these handlers ever omit a key
# conditionally, so a plain ``response_model=`` suffices (no exclude_unset).
# Each is pinned by a key-set test written against the UNFILTERED handler
# output, per the drain recipe.


class CurriculumSummary(BaseModel):
    """One element of the response of GET /api/curriculum (``store.list_curricula``)."""

    id: str
    topic: str
    created_at: str


class CurriculumDayWithPosition(BaseModel):
    """One element of GetCurriculumResponse.days / .proposed.days.

    Mirrors ``CurriculumDay`` (``app/models/curriculum.py``) plus ``position``,
    the handler-computed contiguous display ordinal (``Curriculum.day_positions()``)
    — as opposed to ``day``, a stable but possibly-gappy key.
    """

    day: int
    title: str
    focus: str
    collocations: list[str]
    learning_objective: str
    story_guidance: str
    position: int


class ProposedBatch(BaseModel):
    """Non-null value of GetCurriculumResponse.proposed: an uncommitted planner batch."""

    start_day: int
    days: list[CurriculumDayWithPosition]


class GetCurriculumResponse(BaseModel):
    """Response of GET /api/curriculum/{curriculum_id}."""

    id: str
    topic: str
    language_code: str
    cefr_level: str
    days: list[CurriculumDayWithPosition]
    proposed: ProposedBatch | None
    generation_mode: str
    review_pressure: str


class CurriculumProgressEntry(BaseModel):
    """One element of the response of GET /api/curriculum/{curriculum_id}/progress."""

    day: int
    lesson_id: str
    position: int


class CurriculumSourceDay(BaseModel):
    """One element of CurriculumSourceResponse.days.

    The raw ``CurriculumDay`` fields with no ``position`` — unlike
    ``CurriculumDayWithPosition``, ``export_plan`` is the editable plan file,
    not the UI-facing view.
    """

    day: int
    title: str
    focus: str
    collocations: list[str]
    learning_objective: str
    story_guidance: str


class CurriculumSourceResponse(BaseModel):
    """Response of GET /api/curriculum/{curriculum_id}/source (``plan_io.export_plan``)."""

    id: str
    topic: str
    language_code: str
    cefr_level: str
    days: list[CurriculumSourceDay]


class PipelineDayStatus(BaseModel):
    """One element of PipelineStatusResponse.days (``LessonPipeline.status_for``)."""

    day: int
    position: int
    state: str
    lesson_id: str | None
    has_audio: bool
    error: str | None
    retryable: bool | None
    detail: str | None


class PipelineStatusResponse(BaseModel):
    """Response of GET /api/curriculum/{curriculum_id}/pipeline."""

    active: bool
    days: list[PipelineDayStatus]


class ImageCandidate(BaseModel):
    """One element of ImageCandidatesResponse.candidates (a Pixabay hit)."""

    preview_url: str
    webformat_url: str
    tags: str
    width: int
    height: int
    likes: int


class ImageCandidatesResponse(BaseModel):
    """Response of GET /api/srs/items/{item_id}/image/candidates."""

    query: str
    status: str
    candidates: list[ImageCandidate]


# ── Batch 8: story/audio/card response models (BP ledger drain) ──────────────
#
# get_lesson and get_lesson_by_day serialize through the same
# ``_serializers.serialize_lesson``, so they share ONE model — the brief
# explicitly forbids two near-duplicates. ``day`` is conditionally present
# (only get_lesson passes it), so both routes need
# ``response_model_exclude_unset``. render_audio's cues nest ``ref`` whose
# ``target_index`` is omitted on narration cues — same exclude_unset reason.
# create_base_card and create_inflection_cloze share the ``_persist_new_card``
# tail, which nests the batch-6a SrsItemResponse (already exclude_unset-bound
# for the DirectionStateResponse ``left`` key).


class LessonKeyPhrase(BaseModel):
    """One element of LessonResponse.key_phrases."""

    phrase: str
    translation: str


class LessonPhrase(BaseModel):
    """One element of LessonSection.phrases."""

    text: str
    role: str
    language_code: str
    voice_id: str


class LessonSection(BaseModel):
    """One element of LessonResponse.sections."""

    type: str
    phrases: list[LessonPhrase]


class LessonResponse(BaseModel):
    """Response of GET /api/story/{lesson_id} and
    GET /api/curriculum/{curriculum_id}/days/{day}/lesson
    (``_serializers.serialize_lesson``). ``day`` is omitted by the by-day
    route (serialized without one), so it rides on ``response_model_exclude_unset``.
    """

    id: str
    title: str
    language_code: str
    key_phrases: list[LessonKeyPhrase]
    sections: list[LessonSection]
    # What the generating prompt asked the model to reuse, and what the story
    # actually used. Empty is "unmeasurable", not "none landed".
    review_requested: list[str] = []
    review_used: list[str] = []
    day: int | None = None  # omitted when unset


class ReviewSessionResponse(LessonResponse):
    """Response of GET /api/review-sessions/{session_id}.

    A lesson read plus a date, MINUS the ``day`` it has no right to.
    ``serialize_lesson`` leaves that key out for a session, but the inherited
    ``day: int | None = None`` would still serialize as ``null`` — so the ROUTE
    must set ``response_model_exclude_unset=True``. That is a route setting, not
    something this model can enforce, which is why the route carries the warning.
    """

    session_date: str


class LessonSourceResponse(BaseModel):
    """Response of GET /api/story/{lesson_id}/source (``lesson_io.export_lesson``).

    ``story`` stays a bare dict on purpose: it is the raw editable Story-JSON
    file — heterogeneous, versioned, and already treated as opaque by both the
    import request model (``ImportLessonRequest.story``) and the frontend
    (``StorySourceResponse.story: Record<string, unknown>``).
    """

    curriculum_id: str
    day: int
    story: dict


class RenderCueRef(BaseModel):
    """Non-null value of RenderSectionCue.ref.

    ``target_index`` is omitted on narration cues (built as ``{"kind":
    "narration"}`` in ``cues.py``), so it rides on
    ``response_model_exclude_unset``.
    """

    kind: Literal["line", "key_phrase", "narration"]
    target_index: int | None = None  # omitted when unset


class RenderSectionCue(BaseModel):
    """One element of RenderAudioResponse.cues (a full-manifest ``Cue``)."""

    index: int
    start_ms: int
    end_ms: int
    section_index: int | None
    section_type: str | None
    phrase_index: int
    role: str
    language_code: str
    text: str
    ref: RenderCueRef | None = None


class RenderAudioSection(BaseModel):
    """One element of RenderAudioResponse.sections."""

    audio_id: str
    section_index: int
    section_type: str
    title: str


class RenderAudioResponse(BaseModel):
    """Response of POST /api/audio/render (``render_service.render_lesson_audio``)."""

    audio_id: str
    lesson_id: str
    sections: list[RenderAudioSection]
    cues: list[RenderSectionCue]


class CreateCardResponse(BaseModel):
    """Response of POST /api/srs/items/base and POST /api/srs/inflection-clozes
    (``srs.py::_persist_new_card``); ``item`` is the batch-6a SrsItemResponse."""

    id: int
    was_created: bool
    item: SrsItemResponse


# ── Batch 9: badge / sync surfaces + the conditional-key grade response ──────
# Mechanically simple (single-return static literals) but load-bearing: these
# sit on the review badge, the pending bucket and the Anki envelope, so a
# dropped key is a silently wrong number rather than a 500.


class QueueStatsResponse(BaseModel):
    """Response of GET /api/srs/queue-stats — the three badges plus the caps
    and their provenance.

    ``new``/``review`` are already MIN'd against the daily caps by the handler
    (see ``.claude/rules/anki-queue-parity.md`` rule 12); the ``*_source``
    fields report whether each cap came from the Anki cache, config, or the
    hardcoded default.
    """

    new: int
    learning: int
    review: int
    daily_new_cap: int
    cap_source: str
    daily_review_cap: int
    review_cap_source: str
    fsrs_source: str


class ListenResponse(BaseModel):
    """Response of POST /api/srs/listen.

    ``staged`` counts grades parked in the pending bucket for "Check your
    work"; ``applied`` counts the ones the user confirmed by hand, which are
    graded immediately. They are disjoint.
    """

    status: str
    staged: int
    applied: int
    created: int
    remaining_candidates: int
    listen_count: int


class RefreshMediaResponse(BaseModel):
    """Response of POST /api/admin/refresh-media (counts from ``import_seed``).

    ``errors`` is fed by ``skipped_guid_collisions`` ALONE — a skipped
    non-vocab note is normal, not a failure, and must not inflate this.
    """

    updated: int
    unchanged: int
    new: int
    errors: int


class TtsCacheStatsResponse(BaseModel):
    """Response of GET /api/admin/tts-cache.

    ⚠️ No path field, ever: counts are fine on this authenticated route, but
    the cache location itself would leak the filesystem layout — the same
    reason ``app/api/health.py``'s body carries status only.
    """

    present: bool
    file_count: int
    total_bytes: int


class PeerSyncResponse(BaseModel):
    """Response of POST /api/anki/peer-sync (``PeerSyncReport``).

    ⚠️ ``pull_required``/``push_required`` are NOT booleans despite the names —
    they carry AnkiWeb's proto ``SyncCollectionResponse.ChangesRequired`` code
    (``0 NO_CHANGES``, ``1 NORMAL_SYNC``, ``2 FULL_SYNC``, …; see
    ``sync_orchestrator._full_sync_required``), and they are ``None`` on a leg
    that never ran — which is exactly what a dry run produces. Typing them
    ``bool`` made the dry-run path raise ResponseValidationError, i.e. a 500 on
    a working endpoint; caught by ``test_forwards_dry_run`` during this flip.
    ``tt_push_pull_exit`` is likewise ``None`` until the push leg runs.
    """

    auth_success: bool
    pull_required: int | None
    push_required: int | None
    tt_push_pull_exit: int | None
    dry_run: bool


class DrillFeedbackResponse(BaseModel):
    """Response of POST /api/srs/items/{id}/direction/{direction}/feedback.

    ``left`` is CONDITIONAL: ``drill_feedback`` appends it only when the new
    direction has a learning-step counter, so a REVIEW-state result omits the
    key entirely. The route therefore carries
    ``response_model_exclude_unset=True`` — without it FastAPI would put
    ``"left": null`` back into the omitting branch, rewriting the payload in
    the ADD direction. Both branches are pinned in
    ``test_api_srs_directions.py::test_feedback_response_keys_match_model_both_branches``.
    """

    status: str
    direction: str
    new_due_at: str
    new_state: str
    left: int | None = None


class ClientLogRequest(BaseModel):
    """Body for POST /api/client-log — a batch of already-formatted lines.

    Deliberately opaque strings rather than a typed event schema. The endpoint's
    job is to make browser-side evidence DURABLE, not to understand it; a schema
    here would have to change every time a new thing needs tracing, and the
    whole point is that the next mobile-only bug is one nobody predicted.
    """

    lines: list[str]


class ClientLogResponse(BaseModel):
    """Response of POST /api/client-log.

    ``accepted`` is how many lines were actually written, which can be fewer
    than were sent — the endpoint caps a batch rather than rejecting it, so a
    client that oversends still gets its earliest (most relevant) entries
    persisted. Reported so a caller can tell truncation from success.
    """

    accepted: int
