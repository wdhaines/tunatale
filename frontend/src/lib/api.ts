/**
 * TunaTale API client — wraps backend endpoints.
 */

import type { components } from "./api-types";

// ── Schema-derived types (kept in sync via check:api / gen:api) ────────
// Alias generated types so every consumer imports from this module.
// Drift between backend and frontend is caught by svelte-check when the
// generated shape changes and a consumer uses a vanished field.
export type ListenPreviewCandidate = components["schemas"]["ListenPreviewCandidate"];
export type ListenPreview = components["schemas"]["ListenPreviewResponse"];
export type CommitPendingResponse = components["schemas"]["CommitPendingResponse"];

// SSR fetches go straight to the backend (the browser uses the Vite proxy via
// relative URLs). Protocol and port must mirror the proxy target in
// vite.config.ts: HTTPS only when start-dev.sh sets VITE_SSL_ENABLED, port from
// API_PORT (E2E runs the backend on 8001).
const SSR_PROTO = import.meta.env.VITE_SSL_ENABLED === "true" ? "https" : "http";
export const BASE_URL =
  typeof window !== "undefined" ? "" : `${SSR_PROTO}://localhost:${process.env.API_PORT ?? 8000}`;

// localStorage key the language selector writes; read here so every request
// carries the active language (the backend resolves the per-language connection
// from this header). SSR / no selection → no header → backend default language.
export const LANGUAGE_STORAGE_KEY = "tt-language";

function activeLanguageHeader(): Record<string, string> {
  if (typeof localStorage === "undefined") return {};
  const code = localStorage.getItem(LANGUAGE_STORAGE_KEY);
  return code ? { "X-TT-Language": code } : {};
}

// ── Session expiry ────────────────────────────────────────────────────
// A 401 from a data endpoint means the cookie is gone or expired mid-session.
// The API client cannot navigate (it must stay importable outside a SvelteKit
// runtime — `api.test.ts` imports it into bare jsdom), so it reports and the
// layout decides. See `stores/auth.svelte.ts` for the handler that is
// registered here in the app.
let unauthorizedHandler: (() => void) | null = null;

/** Register (or clear, with `null`) the callback fired on a session-expiry 401. */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

// `/api/auth/*` is excluded from the handler on purpose: 401 is an ORDINARY
// answer there, not a session expiry. `login` answers 401 for a wrong password
// — treating that as "session expired" would bounce the user off the very
// login page that carries the error message — and `me` answers 401 as its
// plain "no, you are not logged in", which the auth store must interpret
// itself (the backend answers it identically whether the gate is on or off).
const AUTH_PATH_PREFIX = "/api/auth/";

export interface AuthStatus {
  /**
   * Whether this deployment requires a login at all.
   *
   * The frontend cannot infer this from a 401: `GET /api/auth/me` answers 401
   * for an anonymous caller in BOTH flag states, so a dev running with auth
   * off would be redirected to a login page that cannot help them.
   */
  auth_enabled: boolean;
}

export interface AuthUser {
  email: string;
}

export interface LanguageOption {
  code: string;
  name: string;
}

export interface LanguagesResponse {
  languages: LanguageOption[];
  active: string;
  // Whether the optional anki_sync plugin is installed AND settings.sync_enabled
  // is on for this deployment (see backend app/main.py). Absent on older backends;
  // callers should treat a missing value as available (fail open).
  sync_available?: boolean;
}

export interface DayPlan {
  /**
   * Stable key — addresses the day in URLs and API calls. Deleting a day leaves
   * a permanent gap here (1, 2, 3, 4, 6), so never show it to the learner.
   */
  day: number;
  /** 1-based place in the ordered plan. Contiguous whatever has been deleted; this is what the UI labels. */
  position: number;
  title: string;
  focus: string;
  collocations: string[];
  learning_objective: string;
  story_guidance: string;
}

export interface ProposedBatch {
  start_day: number;
  days: DayPlan[];
}

export interface CurriculumSummary {
  id: string;
  topic: string;
  language_code: string;
  cefr_level: string;
  days: DayPlan[];
  proposed: ProposedBatch | null;
  generation_mode?: "auto" | "manual";
  review_pressure?: ReviewPressure;
}

/** POST /generate and /plan return a day COUNT, not day objects. */
export interface CurriculumCreated {
  id: string;
  topic: string;
  language_code: string;
  cefr_level?: string;
  days: number;
}

export interface PlanTurnResponse {
  reply: string;
  proposed: ProposedBatch | null;
}

export interface PromptExport {
  system_prompt: string;
  user_prompt: string;
}

export interface PlanSource {
  id: string;
  topic: string;
  language_code: string;
  cefr_level: string;
  days: DayPlan[];
}

interface SectionSummary {
  type: string;
  phrase_count: number;
}

export interface LessonSummary {
  id: string;
  title: string;
  sections: SectionSummary[];
}

/** REVIEW is themeless: the learner's decaying vocabulary IS the content. */
export type ContentStrategy = "WIDER" | "DEEPER" | "REVIEW";

/** How hard a generated lesson pushes to reuse vocabulary the learner is
 *  forgetting, at the cost of its theme. Wire values; never shown to a user. */
export type ReviewPressure = "NATURAL" | "BALANCED" | "INSISTENT";
export type Direction = "recognition" | "production";

interface PhraseDetail {
  text: string;
  role: string;
  language_code: string;
  voice_id: string;
}

interface SectionDetail {
  type: string;
  phrases: PhraseDetail[];
}

interface KeyPhrase {
  phrase: string;
  translation: string;
}

export interface LessonDetail {
  id: string;
  day: number;
  title: string;
  language_code: string;
  sections: SectionDetail[];
  key_phrases: KeyPhrase[];
  /** What the generating prompt asked the model to reuse. Empty means
   *  unmeasurable (authored import, or generated before the meter existed),
   *  never "none landed". */
  review_requested?: string[];
  review_used?: string[];
}

export interface DayProgress {
  day: number;
  /** @see DayPlan.position */
  position: number;
  lesson_id: string;
}

// Mirrors the backend's rating domain for /listen (app/api/models.py::ListenRequest).
// "good" is the default a listen stages; "skip" means "stage nothing for this item".
export type WordRating = "again" | "hard" | "good" | "easy" | "skip";

export interface WordToken {
  surface: string;
  prefix_punct?: string;
  suffix_punct?: string;
  lemma: string;
  srs_state: string;
  srs_item_id: number | null;
  translation: string | null;
  collocation_span_id: number | null;
  collocation_start: boolean;
  collocation_srs_state: string | null;
  collocation_lemma: string | null;
  collocation_translation: string | null;
  // Optional: present only on collocation-span tokens; null/absent off-span.
  // Optional (not required-with-null like the siblings) to spare ~40 inline test
  // literals — the consumer reads `?? 0`.
  collocation_progress?: number | null;
  // Same optionality rationale: enclosing collocation's active direction is due
  // (gates the phrase popover's grade button — same _is_due rule as word is_due).
  collocation_is_due?: boolean;
  card_type: string | null;
  active_state: string;
  active_direction: string | null;
  is_due: boolean;
  progress: number | null;
  inflectable: boolean;
  inflection_feature: string | null;
  known_marked: boolean;
  // Read-ahead: the recognition direction is on the review ramp (learning/review/
  // relearning), regardless of due date. Reading the word counts as a recognition
  // review even when the SRS wouldn't have surfaced it yet. Optional (not
  // required-with-null) so existing test literals need no update.
  recognition_reviewable?: boolean;
  // Recognition-side state and dueness for mastery-line bucketing.
  // None when the word has no recognition direction (untracked, production-only cloze).
  recognition_state?: string | null;
  recognition_is_due?: boolean;
  // Recognition scheduled past the listen horizon — the cutoff that already
  // stops the listen preview asking about this word. Rendered as "known" and
  // counted in the known bucket. Optional, same rationale as the siblings.
  well_known?: boolean;
}

export interface CreateSRSItemRequest {
  text: string;
  language_code: string;
  word_count: number;
  translation?: string;
  source_sentence?: string;
  source_lesson_id?: string;
  source_line_index?: number;
}

export interface DialogueLine {
  role: string;
  // The backend always sends this (reconstructed from surfaces); optional here so
  // test fixtures need not restate it. Consumers read it defensively (`?? ''`).
  sentence?: string;
  words: WordToken[];
}

export interface TranscriptData {
  lesson_id: string;
  key_phrases: KeyPhrase[];
  dialogue_lines: DialogueLine[];
}

export interface ListenResponse {
  status: string;
  created: number;
  /** Auto-graded rows parked in the pending bucket for "Check your work". */
  staged: number;
  /** Rows the user graded in the preview, applied immediately. */
  applied: number;
  remaining_candidates: number;
  listen_count: number;
}

export interface LessonListenRecord {
  lesson_id: string;
  listen_count: number;
  last_listened_at: string;
}

export interface ListensResponse {
  lessons: LessonListenRecord[];
}

export interface ImportListensResponse {
  imported: string[];
  already_present: string[];
  unknown: string[];
}

export interface SectionAudio {
  audio_id: string;
  section_index: number;
  section_type: string;
  title: string;
  cues?: Cue[] | null;
}

export interface CueRef {
  kind: "line" | "key_phrase" | "narration";
  // Absent on narration refs — the backend emits {"kind": "narration"} with no
  // target (see app/audio/cues.py); only line/key_phrase refs carry an index.
  target_index?: number;
}

export interface Cue {
  index: number;
  start_ms: number;
  end_ms: number;
  section_index: number | null;
  section_type: string | null;
  phrase_index: number;
  role: string;
  language_code: string;
  text: string;
  ref: CueRef | null;
}

export interface LessonAudio {
  audio_id: string;
  lesson_id: string;
  sections: SectionAudio[];
  cues?: Cue[] | null;
}

interface DirectionState {
  state: string;
  due_at: string;
  stability: number;
  difficulty: number;
  reps: number;
  lapses: number;
  last_review: string | null;
  anki_card_id: number | null;
  left?: number;
}

export interface SRSItemDetail {
  id: number;
  text: string;
  translation: string;
  word_count?: number;
  state: "new" | "learning" | "review" | "relearning" | "suspended" | "known";
  due_at: string;
  stability: number;
  difficulty: number;
  reps: number;
  lapses: number;
  last_review: string | null;
  language_code: string;
  guid?: string | null;
  anki_note_id?: number | null;
  directions?: {
    // Either side can be null for single-template Anki notetypes (e.g. Basic
    // phonics, ord=0 only) where the matching card doesn't exist.
    recognition: DirectionState | null;
    production: DirectionState | null;
  };
  card_type?: "vocab" | "cloze";
  source_sentence?: string;
  source_sentence_translation?: string;
  image_url?: string | null;
  audio_url?: string | null;
  word_audio_url?: string | null;
  grammar?: string;
  note?: string;
  // Gender/indefinite article (en/ei/et) — rendered as a display-time prefix on
  // the headword (e.g. "en orden"). Empty for non-nouns / languages without it.
  article?: string;
  // Part of speech, present only when the surface is ambiguous across word
  // classes (e.g. "fange" noun vs verb). Empty otherwise.
  pos?: string;
  // Rich back-of-card fields sourced from the Anki note (IPA, inflections,
  // examples, dictionary entry…). Each carries pre-sanitized HTML and a tier
  // controlling where it renders on the answer side. Absent/empty for cards
  // without any.
  extras?: BackField[];
}

export interface BackField {
  label: string;
  html: string;
  // "summary" → always visible; "details" → collapsed disclosure; "deep" → its
  // own nested disclosure (the verbose dictionary entry).
  tier: "summary" | "details" | "deep";
}

export interface SRSItemsPage {
  items: SRSItemDetail[];
  total: number;
}

export interface SRSListParams {
  search?: string;
  state?: SRSItemDetail["state"];
  sort?:
    | "text"
    | "translation"
    | "state"
    | "due_at"
    | "fsrs_difficulty"
    | "reps"
    | "lapses"
    | "last_review";
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
}

export interface SRSDue {
  due: Array<{ text: string; translation: string }>;
}

export interface SRSNew {
  new: Array<{ text: string; translation: string }>;
}

export interface SRSStats {
  total: number;
  due_today: number;
}

export interface QueueStats {
  new: number;
  learning: number;
  review: number;
  daily_new_cap: number;
  cap_source: string;
  fsrs_source?: string;
}

export interface RateLimitSnapshot {
  age_s: number | null;
  requests_limit: number | null;
  requests_remaining: number | null;
  requests_reset_in_s: number | null;
  tokens_limit: number | null;
  tokens_remaining: number | null;
  tokens_reset_in_s: number | null;
}

export interface RateLimitLast429 {
  ago_s: number | null;
  retry_in_s: number | null;
}

export interface RateLimitStatus {
  provider: string;
  model: string;
  llm_mode: string;
  snapshot: RateLimitSnapshot | null;
  last_429: RateLimitLast429 | null;
  tokens_used_day: number | null;
  tokens_per_day_limit: number | null;
  tokens_day_reset_in_s: number | null;
  requests_used_day: number | null;
  requests_per_day_limit: number | null;
  requests_day_reset_in_s: number | null;
}

export interface LlmHealthLastError {
  status: string | number;
  message: string;
  ago_s: number;
}

export interface LlmHealthStatus {
  healthy: boolean;
  consecutive_failures: number;
  last_error: LlmHealthLastError | null;
  fallback_allowed: boolean;
  llm_mode: string;
}

export interface ReviewQueueItem extends SRSItemDetail {
  direction: "recognition" | "production";
  pending_rating: WordRating | null;
}

// ── Pipeline status ───────────────────────────────────────────────────

export interface PipelineDayState {
  day: number;
  /** @see DayPlan.position */
  position: number;
  state: "queued" | "generating" | "rendering" | "ready" | "failed";
  lesson_id: string | null;
  has_audio: boolean;
  error: string | null;
  retryable: boolean | null;
  detail: string | null;
}

export interface PipelineStatus {
  active: boolean;
  days: PipelineDayState[];
}

export interface PipelineRetryResponse {
  status: "queued" | "ready";
}

export interface PipelineRegenerateRequest {
  day: number;
  strategy: ContentStrategy;
}

// ── LLM activity ──────────────────────────────────────────────────────

export interface LlmCallEvent {
  seq: number;
  timestamp: number;
  kind: "llm_call";
  provider: string;
  model: string;
  latency_ms: number;
  status: string;
  is_fallback: boolean;
  prompt_preview: string;
  response_preview: string;
  rate_limits: Record<string, unknown> | null;
  reasoning_effort: string | null;
}

export interface PipelineEvent {
  seq: number;
  timestamp: number;
  kind: "pipeline";
  curriculum_id: string;
  day: number;
  state: string;
  message: string;
}

export type ActivityEvent = LlmCallEvent | PipelineEvent;

export interface ActivityResponse {
  latest: number;
  events: ActivityEvent[];
}

// ── Story source / import ──────────────────────────────────────────────

export interface StorySourceResponse {
  curriculum_id: string;
  day: number;
  story: Record<string, unknown>;
}

export interface ImportStoryPayload {
  curriculum_id: string;
  day: number;
  story?: Record<string, unknown>;
  raw?: string;
}

export interface ImportStoryResponse {
  id: string;
  title: string;
  sections: Array<{ type: string; phrase_count: number }>;
  warnings: string[];
}

export interface PeerSyncResult {
  auth_success: boolean;
  pull_required: number | null;
  push_required: number | null;
  tt_push_pull_exit: number | null;
  dry_run: boolean;
}

export interface ImageCandidate {
  preview_url: string;
  webformat_url: string;
  tags: string;
  width: number;
  height: number;
  likes: number;
}

export interface ImageCandidatesResponse {
  query: string;
  status: string;
  candidates: ImageCandidate[];
}

export class TunaTaleAPI {
  private baseUrl: string;

  constructor(baseUrl: string = BASE_URL) {
    this.baseUrl = baseUrl;
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const method = init?.method ?? "GET";
    const langHeader = activeLanguageHeader();
    let res: Response;
    if (Object.keys(langHeader).length > 0) {
      res = await fetch(`${this.baseUrl}${path}`, {
        ...init,
        headers: { ...langHeader, ...(init?.headers as Record<string, string> | undefined) },
      });
    } else {
      res = init
        ? await fetch(`${this.baseUrl}${path}`, init)
        : await fetch(`${this.baseUrl}${path}`);
    }
    if (!res.ok) {
      if (res.status === 401 && !path.startsWith(AUTH_PATH_PREFIX)) {
        unauthorizedHandler?.();
      }
      // Surface the server's error detail (FastAPI puts it in body.detail) instead of
      // the bare status line — statusText is empty over HTTP/2, which left sync/other
      // failures showing a useless "METHOD /path:" with no reason.
      let detail = "";
      try {
        const body = await res.json();
        const d = (body as { detail?: unknown }).detail;
        if (typeof d === "string") {
          detail = d;
        } else if (Array.isArray(d)) {
          // FastAPI validation errors (422) put detail as a list of
          // {loc, msg, type} objects — surface "field: message" lines.
          detail = d
            .map((e: { loc?: unknown[]; msg?: string }) => {
              const field = Array.isArray(e.loc) ? String(e.loc[e.loc.length - 1]) : "";
              return field && e.msg ? `${field}: ${e.msg}` : (e.msg ?? "");
            })
            .filter(Boolean)
            .join("; ");
        }
      } catch {
        /* error response body wasn't JSON */
      }
      const err = new Error(
        `${method} ${path}: ${detail || res.statusText || `HTTP ${res.status}`}`,
      );
      const retryAfter = res.headers.get("Retry-After");
      if (retryAfter) (err as Error & { retryAfter?: number }).retryAfter = Number(retryAfter);
      // The status, so a caller can tell a REFUSAL from a FAILURE. Matching on
      // the detail text instead would break the first time the wording changes,
      // and the wording is written for the learner, not for code.
      (err as Error & { status?: number }).status = res.status;
      throw err;
    }
    return res.json();
  }

  async listCurricula(): Promise<Array<{ id: string; topic: string; created_at: string }>> {
    return this.request("/api/curriculum");
  }

  // ── Review sessions ─────────────────────────────────────────────────
  // Dated, not numbered, and reached by their own path: a review session
  // belongs to no curriculum (bd tunatale-9p9d).

  async listReviewSessions(): Promise<
    Array<{
      id: string;
      language_code: string;
      session_date: string;
      title: string;
      // null means NEVER MEASURED and renders as no readout; [] is a measured
      // zero. Collapsing the two would put "reused 0 of 0" on older sessions.
      review_requested: string[] | null;
      review_used: string[] | null;
    }>
  > {
    const body = await this.request<{
      sessions: Array<{
        id: string;
        language_code: string;
        session_date: string;
        title: string;
        review_requested: string[] | null;
        review_used: string[] | null;
      }>;
    }>("/api/review-sessions");
    return body.sessions;
  }

  async getReviewSession(id: string): Promise<{
    id: string;
    session_date: string;
    title: string;
    language_code: string;
    key_phrases: KeyPhrase[];
    // Same shape as a lesson's, so buildScenes works on it unchanged — the
    // response is a lesson read minus `day`.
    sections: SectionDetail[];
    // Arrays here, not null: this response inherits LessonResponse, where empty
    // means UNMEASURABLE — the same convention the lesson page already reads.
    // The LIST endpoint keeps null/[] distinct; a single read cannot, and a
    // generated session is always measured, so nothing turns on it here.
    review_requested: string[];
    review_used: string[];
  }> {
    return this.request(`/api/review-sessions/${id}`);
  }

  async renderReviewSession(id: string): Promise<LessonAudio> {
    return this.request(`/api/review-sessions/${id}/render`, { method: "POST" });
  }

  async createReviewSession(): Promise<{
    id: string;
    session_date: string;
    title: string;
    review_requested: string[];
    review_used: string[];
    warnings: string[];
  }> {
    return this.request("/api/review-sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
  }

  async getCurriculum(id: string): Promise<CurriculumSummary> {
    return this.request(`/api/curriculum/${id}`);
  }

  async getCurriculumProgress(id: string): Promise<DayProgress[]> {
    return this.request(`/api/curriculum/${id}/progress`);
  }

  async startPlan(topic: string, cefrLevel = "A2"): Promise<CurriculumCreated> {
    return this.request("/api/curriculum/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic, cefr_level: cefrLevel }),
    });
  }

  async planTurn(
    id: string,
    message: string,
    batchSize = 5,
    pastedResponse?: string,
  ): Promise<PlanTurnResponse> {
    const body: Record<string, unknown> = { message, batch_size: batchSize };
    if (pastedResponse !== undefined) {
      body.pasted_response = pastedResponse;
    }
    return this.request(`/api/curriculum/${id}/plan/turn`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  async commitPlan(id: string): Promise<{ id: string; days: number }> {
    return this.request(`/api/curriculum/${id}/plan/commit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
  }

  async resetPlanChat(id: string): Promise<{ reply_count_cleared: number }> {
    return this.request(`/api/curriculum/${id}/plan/reset`, { method: "POST" });
  }

  async sendPlanFeedback(
    id: string,
    day: number,
    note: string,
  ): Promise<{ feedback: Array<{ day: number; note: string }> }> {
    return this.request(`/api/curriculum/${id}/plan/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ day, note }),
    });
  }

  async deleteCurriculum(id: string): Promise<{ deleted: string }> {
    return this.request(`/api/curriculum/${id}`, { method: "DELETE" });
  }

  async deleteCurriculumDay(
    id: string,
    day: number,
  ): Promise<{ deleted_day: number; days: number }> {
    return this.request(`/api/curriculum/${id}/days/${day}`, { method: "DELETE" });
  }

  async setGenerationMode(id: string, mode: "auto" | "manual"): Promise<{ mode: string }> {
    return this.request(`/api/curriculum/${id}/generation-mode`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
  }

  async setReviewPressure(id: string, pressure: ReviewPressure): Promise<{ pressure: string }> {
    return this.request(`/api/curriculum/${id}/review-pressure`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pressure }),
    });
  }

  async getPlanTurnPrompt(id: string, message: string, batchSize = 5): Promise<PromptExport> {
    return this.request(`/api/curriculum/${id}/plan/turn/prompt`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, batch_size: batchSize }),
    });
  }

  async getStoryPrompt(
    curriculumId: string,
    day: number,
    strategy: ContentStrategy = "WIDER",
  ): Promise<PromptExport> {
    const params = new URLSearchParams({ day: String(day), strategy });
    return this.request(`/api/story/prompt?curriculum_id=${curriculumId}&${params}`);
  }

  async getPlanSource(id: string): Promise<PlanSource> {
    return this.request(`/api/curriculum/${id}/source`);
  }

  async importPlan(file: Omit<PlanSource, "id"> & { id?: string }): Promise<CurriculumCreated> {
    return this.request("/api/curriculum/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(file),
    });
  }

  async getLessonByDay(curriculumId: string, day: number): Promise<LessonDetail> {
    return this.request(`/api/curriculum/${curriculumId}/days/${day}/lesson`);
  }

  async getLesson(lessonId: string): Promise<LessonDetail> {
    return this.request(`/api/story/${lessonId}`);
  }

  async renderAudio(lessonId: string): Promise<LessonAudio> {
    return this.request("/api/audio/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lesson_id: lessonId }),
    });
  }

  audioUrl(audioId: string): string {
    return `${this.baseUrl}/api/audio/${audioId}`;
  }

  audioZipUrl(lessonId: string): string {
    return `${this.baseUrl}/api/audio/lesson/${lessonId}/zip`;
  }

  async getLessonAudio(lessonId: string): Promise<LessonAudio> {
    return this.request(`/api/audio/lesson/${lessonId}`);
  }

  async getSRSDue(): Promise<SRSDue> {
    return this.request("/api/srs/due");
  }

  async getSRSNew(): Promise<SRSNew> {
    return this.request("/api/srs/new");
  }

  async fetchDue(direction: "recognition" | "production" | "any"): Promise<SRSItemDetail[]> {
    const data = await this.request<{ due: SRSItemDetail[] }>(
      `/api/srs/due?direction=${direction}`,
    );
    return data.due;
  }

  async fetchNew(direction: Direction, limit = 20): Promise<SRSItemDetail[]> {
    const data = await this.request<{ new: SRSItemDetail[] }>(
      `/api/srs/new?direction=${direction}&limit=${limit}`,
    );
    return data.new;
  }

  async submitDrill(
    itemId: number,
    direction: "recognition" | "production",
    rating: WordRating,
    timeMs?: number,
    lessonReview = false,
  ): Promise<{ new_due_at: string; new_state: string; left?: number }> {
    return this.request(`/api/srs/items/${itemId}/direction/${direction}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rating, time_ms: timeMs, lesson_review: lessonReview }),
    });
  }

  async undoGrade(
    itemId: number,
    direction: "recognition" | "production",
  ): Promise<{ status: string; restored_state: string; restored_due_at: string }> {
    return this.request(`/api/srs/items/${itemId}/direction/${direction}/undo`, {
      method: "POST",
    });
  }

  async getSRSStats(): Promise<SRSStats> {
    return this.request("/api/srs/stats");
  }

  async fetchQueueStats(): Promise<QueueStats> {
    return this.request("/api/srs/queue-stats");
  }

  async getLlmHealth(): Promise<LlmHealthStatus> {
    return this.request("/api/llm/health");
  }

  async getRateLimit(): Promise<RateLimitStatus> {
    return this.request("/api/llm/rate-limit");
  }

  async probeRateLimit(): Promise<RateLimitStatus> {
    return this.request("/api/llm/rate-limit/probe", { method: "POST" });
  }

  async getPipeline(curriculumId: string): Promise<PipelineStatus> {
    return this.request(`/api/curriculum/${curriculumId}/pipeline`);
  }

  async retryPipelineDay(curriculumId: string, day: number): Promise<PipelineRetryResponse> {
    return this.request(`/api/curriculum/${curriculumId}/pipeline/retry`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ day }),
    });
  }

  async regenerateDay(
    curriculumId: string,
    day: number,
    strategy: ContentStrategy,
  ): Promise<PipelineRetryResponse> {
    return this.request(`/api/curriculum/${curriculumId}/pipeline/regenerate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ day, strategy }),
    });
  }

  async getStorySource(lessonId: string): Promise<StorySourceResponse> {
    return this.request(`/api/story/${lessonId}/source`);
  }

  async importStory(payload: ImportStoryPayload): Promise<ImportStoryResponse> {
    return this.request("/api/story/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  async getLlmActivity(since?: number): Promise<ActivityResponse> {
    const qs = since != null ? `?since=${since}` : "";
    return this.request(`/api/llm/activity${qs}`);
  }

  async fetchReviewQueue(
    opts: { sessionStart?: boolean } = {},
  ): Promise<{ queue: ReviewQueueItem[] }> {
    const path = opts.sessionStart
      ? "/api/srs/review-queue?session_start=1"
      : "/api/srs/review-queue";
    return this.request(path);
  }

  async markAsListened(
    lessonId: string,
    wordRatings: Record<string, WordRating> = {},
    kpRatings: Record<string, WordRating> = {},
    // Items the user graded by hand in the preview. These are applied
    // immediately; everything else is staged for "Check your work". Separate
    // from the ratings maps because presence there is already overloaded — a
    // well-known row must be listed for the backend to consider it at all.
    confirmedWords: string[] = [],
    confirmedKps: string[] = [],
    // Rows the user opted PAST the daily new-card cap, by grading them in the
    // preview. Same shape as confirmed_* — separate from the ratings maps
    // because presence there is inverted for create rows (absent means the
    // backend's default "good", which creates the card).
    overCapWords: string[] = [],
    overCapKps: string[] = [],
  ): Promise<ListenResponse> {
    const body: Record<string, unknown> = {
      lesson_id: lessonId,
      word_ratings: wordRatings,
      kp_ratings: kpRatings,
      confirmed_words: confirmedWords,
      confirmed_kps: confirmedKps,
    };
    // Empty arrays are omitted rather than always posted: the backend defaults
    // both to [] anyway, and the untouched-list request is otherwise unchanged.
    if (overCapWords.length > 0) body.over_cap_words = overCapWords;
    if (overCapKps.length > 0) body.over_cap_kps = overCapKps;
    return this.request("/api/srs/listen", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  async getListens(): Promise<ListensResponse> {
    return this.request("/api/srs/listens");
  }

  async importListens(lessonIds: string[], languageCode?: string): Promise<ImportListensResponse> {
    return this.request("/api/srs/listens/import", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(languageCode ? { "X-TT-Language": languageCode } : {}),
      },
      body: JSON.stringify({ lesson_ids: lessonIds }),
    });
  }

  async fetchLessonReviewQueue(
    lessonId: string,
  ): Promise<{ queue: ReviewQueueItem[]; has_unreviewed_listen: boolean }> {
    return this.request(`/api/srs/lesson/${lessonId}/review-queue`);
  }

  async markLessonReviewed(lessonId: string): Promise<{ ok: boolean }> {
    return this.request(`/api/srs/lesson/${lessonId}/reviewed`, { method: "POST" });
  }

  async getListenPreview(lessonId: string): Promise<ListenPreview> {
    return this.request(`/api/srs/lesson/${lessonId}/listen-preview`);
  }

  async commitPending(lessonId: string): Promise<CommitPendingResponse> {
    return this.request(`/api/srs/lesson/${lessonId}/commit-pending`, {
      method: "POST",
    });
  }

  async getLessonTranscript(lessonId: string): Promise<TranscriptData> {
    return this.request(`/api/srs/lesson/${lessonId}/transcript`);
  }

  async createSRSItem(payload: CreateSRSItemRequest): Promise<SRSItemDetail> {
    return this.request("/api/srs/items", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  async listSRSItems(params: SRSListParams = {}): Promise<SRSItemsPage> {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined) qs.set(k, String(v));
    }
    const query = qs.toString() ? `?${qs.toString()}` : "";
    return this.request(`/api/srs/items${query}`);
  }

  async updateSRSItem(
    id: number,
    fields: { text: string; translation: string },
  ): Promise<SRSItemDetail> {
    return this.request(`/api/srs/items/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fields),
    });
  }

  async deleteSRSItem(id: number): Promise<{ status: string }> {
    return this.request(`/api/srs/items/${id}`, { method: "DELETE" });
  }

  async bulkDeleteSRSItems(ids: number[]): Promise<{ deleted: number }> {
    return this.request("/api/srs/items/bulk-delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
  }

  async resetSRSItem(id: number): Promise<SRSItemDetail> {
    return this.request(`/api/srs/items/${id}/reset`, { method: "POST" });
  }

  async suspendSRSItem(
    id: number,
    suspended: boolean,
    direction?: Direction,
  ): Promise<SRSItemDetail> {
    return this.request(`/api/srs/items/${id}/suspend`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ suspended, ...(direction ? { direction } : {}) }),
    });
  }

  async restoreKnown(id: number): Promise<SRSItemDetail> {
    return this.request(`/api/srs/items/${id}/restore-known`, { method: "POST" });
  }

  async setSRSItemState(id: number, state: string): Promise<SRSItemDetail> {
    return this.request(`/api/srs/items/${id}/state`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ state }),
    });
  }

  async translateTerm(text: string, language_code: string): Promise<{ translation: string }> {
    return this.request("/api/srs/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, language_code }),
    });
  }

  async untrackSRSItem(
    id: number,
  ): Promise<{ action: "deleted" } | { action: "suspended"; item: SRSItemDetail }> {
    return this.request(`/api/srs/items/${id}/untrack`, { method: "POST" });
  }

  async createInflectionCloze(body: {
    surface: string;
    lemma: string;
    feature: string;
    sentence: string;
    language_code: string;
    lesson_id?: string;
    translation?: string;
  }): Promise<{ id: number; was_created: boolean; item: SRSItemDetail }> {
    return this.request("/api/srs/inflection-clozes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  async createBaseCard(body: {
    surface: string;
    lemma: string;
    sentence: string;
    language_code: string;
    translation?: string;
  }): Promise<{ id: number; was_created: boolean; item: SRSItemDetail }> {
    return this.request("/api/srs/items/base", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  async ignoreLemma(lemma: string, language_code: string): Promise<{ status: string }> {
    return this.request("/api/srs/ignored-lemmas", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lemma, language_code }),
    });
  }

  async unignoreLemma(lemma: string, language_code: string): Promise<{ status: string }> {
    return this.request(
      `/api/srs/ignored-lemmas?lemma=${encodeURIComponent(lemma)}&language_code=${encodeURIComponent(language_code)}`,
      { method: "DELETE" },
    );
  }

  async peerSync(dryRun = false): Promise<PeerSyncResult> {
    return this.request(`/api/anki/peer-sync?dry_run=${dryRun}`, { method: "POST" });
  }

  async getLanguages(): Promise<LanguagesResponse> {
    return this.request("/api/languages");
  }

  async fetchImageCandidates(id: number, query?: string): Promise<ImageCandidatesResponse> {
    const qs = query ? `?q=${encodeURIComponent(query)}` : "";
    return this.request(`/api/srs/items/${id}/image/candidates${qs}`);
  }

  async setItemImageFromUrl(id: number, url: string): Promise<SRSItemDetail> {
    return this.request(`/api/srs/items/${id}/image`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
  }

  async uploadItemImage(id: number, file: File): Promise<SRSItemDetail> {
    const form = new FormData();
    form.append("file", file);
    return this.request(`/api/srs/items/${id}/image/upload`, {
      method: "PUT",
      body: form,
    });
  }

  async removeItemImage(id: number): Promise<SRSItemDetail> {
    return this.request(`/api/srs/items/${id}/image`, { method: "DELETE" });
  }

  /** Whether this deployment requires a login. Unauthenticated by design. */
  async getAuthStatus(): Promise<AuthStatus> {
    return this.request("/api/auth/status");
  }

  /**
   * Exchange credentials for a session cookie.
   *
   * The token is never visible to JS — the backend sets an HttpOnly cookie and
   * the response body carries only the email. There is deliberately nothing to
   * store here.
   */
  async login(email: string, password: string): Promise<AuthUser> {
    return this.request("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
  }

  async logout(): Promise<{ status: string }> {
    return this.request("/api/auth/logout", { method: "POST" });
  }

  /** The current user, or a 401 rejection when there is no live session. */
  async getMe(): Promise<AuthUser> {
    return this.request("/api/auth/me");
  }
}

export const api = new TunaTaleAPI();
