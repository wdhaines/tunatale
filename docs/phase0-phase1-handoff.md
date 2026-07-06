# Phase 0 + Phase 1 — LLM activity ring buffer + greedy background pipeline (handoff)

**Audience**: a fresh chat session continuing development. Self-contained; you don't need prior conversation. Read this top to bottom, then start with the tests.

**Branch**: working tree has uncommitted changes (12 modified + 7 new files). `./test.sh` passes fully: backend (3404 passed + 14 skipped, 100% coverage), frontend (svelte-check + vitest + coverage gate), and E2E (15/15 Playwright).

## What was built

### Phase 0: LLM activity ring buffer (`docs/walkthrough.md` item 3b)

A synchronous `ActivityLog` (`app/llm/activity.py`) — bounded deque holding up to 300 events with monotonic sequence numbers. Records two event kinds:
- `llm_call` — fired by the `on_call` callback in `LLMClient` (wired in `lifespan()`)
- `pipeline` — fired by `LessonPipeline` on state transitions

`GET /api/llm/activity?since={seq}` returns events newer than a given sequence number, enabling polling-based monitoring. The `on_call` wiring is verified in `test_main_lifespan.py`.

### Phase 1: Greedy background pipeline (`docs/walkthrough.md` item 5 — "background")

`LessonPipeline` (`app/generation/pipeline.py`): single-asyncio-task worker that consumes a queue of `(language_code, curriculum_id, day)` keys with `kind ∈ {"generate", "render"}`.

**What it does:**
- `enqueue()` — idempotent; no-ops if a job for the same key is already active (queued/generating/rendering), unless `force=True` (used by regenerate)
- `reconcile()` — scans all curriculum days and enqueues missing generate/render jobs; **failure-stickiness** — never re-enqueues previously-failed jobs
- `status_for()` — returns per-day status (`queued`, `generating`, `rendering`, `ready`, `failed`), lesson_id, has_audio, error info
- `retry()` — checks curriculum validity, rejects active jobs (409), short-circuits if audio already exists
- `regenerate()` — same guard but forces re-enqueue with `WIDER` strategy
- `start()` / `shutdown()` — create/cancel the background `asyncio.Task`
- Rate-limit backoff: detects `StoryGenerationError` from 429s or Ollama failures, waits `max(retry_after_s, tokens_reset_remaining, 15s)` capped at `_max_wait_s` (default 90s)
- Pre-warms the SRS analysis cache off the request path after generation via `_prewarm_lesson`

**Extraction:** `render_lesson_audio()` was extracted from `POST /api/audio/render` into `app/audio/render_service.py` so both the endpoint and the pipeline call the same function.

**Wiring:**
- `lifespan()` creates the pipeline, stores on `app.state.pipeline`, calls `pipeline.start()` unless `settings.pipeline_autostart=False`
- `PIPELINE_AUTOSTART='false'` in `frontend/playwright.config.ts` (E2E doesn't need background workers)
- Enqueue hooks in `plan_commit` (generate for new days), `generate_story` (render), and `import_story` (render)
- `app.api.pipeline` router: `GET /{id}/pipeline`, `POST /{id}/pipeline/retry`, `POST /{id}/pipeline/regenerate`

## Where things stand (verified at handoff)

- Backend: 3,404 tests pass, 14 skipped, 100% coverage (all modules)
- Frontend: svelte-check clean, vitest 947/947, coverage gate 100%
- E2E: 15/15 Playwright tests pass
- 3 modules at 100%: `pipeline.py` (216 stmts), `render_service.py` (28 stmts), `activity.py` (16 stmts)
- `app/api/pipeline.py`: 48 stmts, 100%
- `tests/test_pipeline.py`: 1,284 lines, 42 tests across 13 test classes
- `tests/test_api_pipeline.py`: 252 lines, 11 endpoint tests
- `tests/test_llm_activity.py`: 138 lines, 11 unit + integration tests
- `test_main_lifespan.py`: pipeline assertion + `pipeline_autostart=False` test

## Key architectural decisions

- **No `patch("app.…")`** — all pipeline tests use constructor DI with fakes (`FakeStoryGenerator`, `FakeRenderer`, real `ContentStore` with `:memory:`, etc.)
- `FakeStoryGenerator.fail_count` — single-shot decrementing counter (not `raise_on_call` bool), makes retry-loop testing straightforward
- **Dead code removed**: `_generate()` had an unreachable exhausted-retries block (the while-loop body always `return`s before falling through)
- `# pragma: no cover` on `while attempt < self._max_attempts:` — loop body always `return`s or `continue`s, exit genuinely unreachable
- **`RecorderSleep`** used in tests records calls but does not actually sleep; `asyncio.sleep` for tests needing real delay
- `PRAGMA busy_timeout=5000` added to `ContentStore.__init__` (store.py) — needed because pipeline generates lesson and immediately enqueues render, which hits a different DB connection
- Pipeline worker catches `asyncio.CancelledError` inside queue-get and `_process_job`, breaks loop cleanly
- `settings.pipeline_autostart` (default `True`) controls whether `pipeline.start()` is called in lifespan
- `app.state.pipeline` is `None` until lifespan runs — API guards with `if pipeline is None` → 404

## Relevant files

| File | Role |
|---|---|
| `backend/app/generation/pipeline.py` | LessonPipeline (216 stmts, 100%) |
| `backend/app/audio/render_service.py` | render_lesson_audio (28 stmts, 100%) |
| `backend/app/llm/activity.py` | ActivityLog ring buffer (16 stmts, 100%) |
| `backend/app/api/pipeline.py` | pipeline status/retry/regenerate API (48 stmts, 100%) |
| `backend/app/api/llm.py` | GET /api/llm/activity endpoint |
| `backend/app/api/curriculum.py` | plan_commit enqueue hook (line 137-138) |
| `backend/app/api/generation.py` | generate/import enqueue hooks (lines 107, 142) |
| `backend/app/api/audio.py` | POST /api/audio/render now calls render_service |
| `backend/app/api/models.py` | PipelineRetryRequest, PipelineRegenerateRequest |
| `backend/app/config.py` | pipeline_autostart setting (line 93) |
| `backend/app/main.py` | lifespan wiring + router registration |
| `backend/app/storage/store.py` | PRAGMA busy_timeout=5000 |
| `backend/tests/test_pipeline.py` | 42 unit tests |
| `backend/tests/test_api_pipeline.py` | 11 API endpoint tests |
| `backend/tests/test_llm_activity.py` | 11 unit + integration tests |
| `backend/tests/test_main_lifespan.py` | pipeline assertion + autostart=False |
| `frontend/playwright.config.ts` | PIPELINE_AUTOSTART='false' |

## Post-review fixes (Fable review, 2026-07-06)

- `retry()` on a day with a lesson but no audio now enqueues **render**, not generate
  (re-generating burned ~7k Groq tokens to recover from a failed render).
- `_render` stamps `record["lesson_id"]` when it resolves the lesson itself, so
  `status_for` reports `lesson_id`/`has_audio` for ready render-only jobs.
- `regenerate(strategy=…)` is now threaded through the job record into
  `ContentStrategy[record["strategy"]]` — previously accepted but silently ignored
  (always WIDER). The unused `force` plumbing into `_generate` went away with it.
- Failure-stickiness sabotage drill run: guard disabled →
  `test_reconcile_does_not_re_enqueue_failed` red → reverted → green.

## Known test quirks

- Pipeline tests that exercise the full generate→render chain need `tmp_path` for audio file creation
- Rate-limit backoff tests use `FakeStoryGenerator.fail_count=2` with `RecorderSleep` that records calls without sleeping
- The "worker missing record" branch (line 235-236 in pipeline.py) is covered by enqueueing a job that replaces `_process_job` with a coroutine deleting the record before raising
- `asyncio.CancelledError` coverage uses `pipeline.shutdown()` while a job blocks on a slow queue item (not directly processable, so the CancelledError hits the inner `try`)
- `ConcurrentEnqueueGuard` tests verify simultaneous enqueue doesn't create duplicate keys

## Phase 2 — Pipeline UI + LLM status relocation (landed 2026-07-06)

Frontend-only. New: `stores/pipeline.svelte.ts` (2s active / 10s idle poll, refreshes
rateLimit + activity stores while active, generation-token guarded start/stop),
`stores/llmActivity.svelte.ts` (since-cursor, 100-event cap, seq-dedupe + backend-restart
reset), `PipelineCard.svelte`, `LlmActivityLog.svelte` (mock-mode empty state),
`tests/pipeline-card.spec.ts` (2 Playwright specs, route-mocked pipeline/activity).
Modified: `api.ts` (+4 methods/types), layout (RateLimitWidget + 30s poll removed),
plan page (hosts widget, refreshes on mount + after each planner turn, commit starts
pipeline, PipelineCard with onRefresh→restart), curriculum page (hosts widget + log,
starts pipeline on mount, day click: ready→goto / failed→retry+restart / in-flight→
fall through to cached lesson), DayPicker (`pipelineStates` prop → pulse/danger).

**Post-review fixes (Fable, 2026-07-06):**
- `pipeline.svelte.ts` `start()` called bare `stop()` which resolved to `window.stop()`
  — restart leaked the old timer (two polling loops). Now closure `stop` + a generation
  token discards in-flight responses/timers after stop/restart. Regression tests +
  sabotage drill (stop-on-start disabled → leak test red → reverted).
- `llmActivityStore.refresh()` appended re-sent seqs → duplicate keys crashed the keyed
  `{#each}` (real trigger: backend restart resets the ring-buffer seq space). Dedupe +
  reset-on-seq-regression.
- Curriculum page day click hard no-op'd in-flight days, locking users out of a
  readable lesson whose audio was still rendering (caught by lesson-navigation e2e
  against the real reconciling backend + PIPELINE_AUTOSTART=false). In-flight now falls
  through to the cached-lesson lookup.
- Retry paths restart the poll (a failed pipeline idles at 10s cadence — the retried
  state showed up to 10s late).
- `PHANTOM_STATEMENT_LINES` exemption map in coverage-gate.ts REVERTED — the three
  "phantom" lines were lazy Svelte prop thunks (`currentLine`, `curriculumId`) whose
  reading branches no test exercised. Covered with real tests instead; the gate has
  zero per-file exemptions.
- RateLimitWidget relocation left it unfed (it never self-refreshes; the layout's
  mount/focus/30s refreshes were deleted): hosting pages now refresh on mount and the
  plan page after every planner turn.

## Phase 3 — Lesson source panel + render badge (landed 2026-07-06)

Frontend-only. New: `lessonSource.ts` (formatSource / buildClaudePrompt pure helpers),
`LessonSourcePanel.svelte` (lazy source fetch on first open, Copy JSON / Copy prompt via
navigator.clipboard, paste + JSON.parse guard, import with inline 422 detail and
warnings), `tests/lesson-source.spec.ts`. Modified: lesson page (hosts the panel below
the regenerate details; pipeline badge next to the Render button while this day's job is
in flight, via the pipelineStore singleton started/stopped with the page), `api.ts`
(getStorySource / importStory), `tests/helpers.ts` (CANNED_STORY — mirrors backend
`test_api.py::_story()` — and seedWithCannedStory: curriculum + lesson via
POST /api/story/import, no LLM/cassette dependency).

**Post-review fixes (Fable, 2026-07-06):**
- Import-with-warnings used to render the warnings AND call onImported in the same
  breath — the page navigated away before anyone could read them. Navigation is now
  deferred behind a "Continue to imported lesson →" button when warnings exist; editing
  the pasted text resets back to Import. (BP's own test fixtures asserted both
  "warnings shown" and "navigated" — dead UI pinned green.)
- The Playwright spec route-mocked the ENTIRE round-trip (source GET, import POST, the
  new lesson's load data) — the one integration the phase exists to deliver was faked,
  so a frontend/backend schema mismatch would stay green. Rewritten against the real
  backend: real source GET, a real warned import (unknown speaker `alien-9` →
  deterministic speaker_warnings hit, deferred navigation asserted), then a real clean
  import asserting navigation to the freshly minted lesson. Only the pipeline endpoint
  stays mocked (real imports enqueue render jobs that never run under
  PIPELINE_AUTOSTART=false).

## What's next

All planned phases (0–3) of the greedy-pipeline effort are done. Remaining ideas live
in `docs/ui-review-backlog.md` and the plan's deferred items.

## Gates before commit

```bash
./test.sh   # root — backend + frontend + E2E
```