# Curriculum Planning — Chat-based planner (design)

Status: **implemented** (2026-07-02) — the chat planner is the only creation flow the UI exposes. The one-shot `/api/curriculum/generate` endpoint has been removed.

## Motivation

One-shot curriculum generation (`POST /api/curriculum/generate`) had two problems:

1. **No iteration.** If the generated days weren't quite right — wrong collocations, off-topic focus, wrong difficulty — the user's only options were "re-roll" (same topic, different random seed) or hand-editing after the fact. No conversation, no refinement.
2. **No learner context.** The generator knew the topic and CEFR level but not the learner's existing SRS deck, so it couldn't build on known vocabulary or avoid already-mastered material.

The chat planner solves both: the user and the LLM collaborate over one or more "turns," and the planner has access to a **learner snapshot** (known lemmas, FSRS states, recent review history) to personalize each proposal.

## Core principle: Plan JSON is source; the chat is scaffolding

There are two representations of a curriculum in the codebase:

| Representation | Where it lives | Shape | Role |
|---|---|---|---|
| **Plan JSON** | `curricula.data_json` (`tunatale_\<lang>.db`), exportable via `GET /{id}/source` | Full `Curriculum` with all day objects | The source of truth you can hand-edit and re-import |
| **Chat transcript** | `curricula.metadata.planner.chat` in the same DB row | Array of `{role, content}` messages | Ephemeral scaffolding that produces plan JSON |

The chat transcript is not the source. It's the conversation that *produced* the plan. If you lose it, the plan is still fully intact as `CurriculumDay[]` objects. You can always start a new chat from the committed plan.

## Endpoint table

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/curriculum/plan` | **LLM-free**: mint an id, save an empty curriculum with empty planner state |
| `POST` | `/api/curriculum/{id}/plan/turn` | One planner chat turn: learner snapshot → LLM → append chat, set/replace proposal |
| `POST` | `/api/curriculum/{id}/plan/commit` | Append the proposed batch to committed days, clear proposal |
| `POST` | `/api/curriculum/{id}/plan/reset` | Clear planner chat + pending proposal (recover from a language-contaminated conversation); committed days untouched |
| `POST` | `/api/curriculum/{id}/plan/feedback` | Record listening feedback for a committed day (enters next turn's prompt) |
| `GET` | `/api/curriculum/{id}` | Full day-object list + `cefr_level` + pending `proposed` batch |
| `GET` | `/api/curriculum/{id}/source` | Export plan as self-describing JSON (the full Curriculum model) |
| `POST` | `/api/curriculum/import` | Import a plan JSON (self-describing: `topic`, `language_code`, `cefr_level`, `days`) |
| `POST` | `/api/curriculum/generate` | **(deleted)** — one-shot generation, replaced by the chat planner |
| `POST` | `/api/curriculum/{id}/plan/turn/prompt` | Export the exact prompts for a planner turn (no LLM call, no persistence) |
| `POST` | `/api/curriculum/{id}/generation-mode` | Set generation mode: `auto` (default, Groq pipeline) or `manual` (copy/paste with Claude chat) |

## Chat flow

```
Start plan → Turn (user msg) → LLM proposes days → Commit batch → Feedback → Turn (next)
```

### 1. Start plan

`POST /api/curriculum/plan` with `{topic, cefr_level}`. This is **LLM-free**: it just mints an id (`{slug}-{uuid}`) and saves an empty `Curriculum` with `days=[]` and `metadata.planner = {chat: [], proposed: null, feedback: []}`. The response carries the id; the frontend navigates to `/c/{id}/plan`.

### 2. Turn (propose)

`POST /api/curriculum/{id}/plan/turn` with `{message, batch_size}`. This builds the full prompt from:

1. **Committed plan** — the last 14 full day objects, older days as title-only lines
2. **Learner snapshot** — from `build_learner_snapshot()` (`app/srs/planner_snapshot.py`): known lemmas, FSRS states, recent review log
3. **Feedback** — per-day notes from prior `/plan/feedback` calls
4. **Conversation** — the last 12 chat messages; older messages elided with a marker

The LLM receives the `PLANNER_SYSTEM_PROMPT` (a role instruction that asks for conversational replies with optional JSON blocks) and the assembled user prompt from `build_planner_turn_prompt()` (`app/generation/prompts.py`).

The planner's `turn()` method (`app/generation/planner.py`) extracts any ` ```json ` block from the LLM reply as the proposed days. The reply (with the JSON block stripped) becomes the chat message; the parsed days become the `proposed` state.

### 3. Commit

`POST /api/curriculum/{id}/plan/commit` appends `proposed.days` to `curriculum.days` and clears the proposal. An event message (e.g., "Committed days 3-4.") is appended to the chat.

### 4. Feedback

`POST /api/curriculum/{id}/plan/feedback` with `{day, note}` stores a feedback entry. On the next turn, all feedback entries are included in the prompt under `## Feedback`.

### 5. Next turn

The user can continue the conversation — refining existing proposals, asking for adjustments, or requesting the next batch.

## Export/import hand-edit round-trip

Plan JSON is the source. You can:

1. `GET /api/curriculum/{id}/source` → download the full plan as JSON
2. Edit it by hand (change titles, collocations, reorder days, etc.)
3. `POST /api/curriculum/import` with the edited JSON → a new curriculum with the edited days
4. Generate story lessons for those days via `POST /api/story/generate`

The imported days must be **byte-identical** to what the generator would have produced for the same prompt (see "Cassette determinism" below), otherwise the story cassette won't match and the story generation will 500 in mock mode.

## Cassette-determinism constraints

The LLM response cassette system (`app/llm/cassette.py`) keys prompts by hash. For the planner prompt:

- **All mutable state is in the user prompt.** The prompt built by `build_planner_turn_prompt()` includes:
  - The committed days (sorted by `day`, always ascending)
  - The learner snapshot (deterministic from the SRS state)
  - Feedback entries (sorted by `day`)
  - The last 12 chat messages (in order)

- **No timestamps.** The planner prompt contains no `datetime.now()`, `uuid.uuid4()`, or other non-deterministic values. Neither does the learner snapshot (`build_learner_snapshot` uses only deterministic FSRS fields and lemma lists).

- **Deterministic ordering.** Days are sorted by `day`; feedback by `day`; chat messages are in insertion order (always the same sequence for the same conversation). `dict` iteration is stable in modern Python (3.7+). The prompt string is fully determined by the topic/CEFR/chat history/learner state.

- **`build_planner_turn_prompt`** (`app/generation/prompts.py:308`) is a pure function — same inputs → same string output every time.

- **`build_learner_snapshot`** (`app/srs/planner_snapshot.py`) computes known lemmas, FSRS parameters, and recent review history from the SRS database. For a given SRS state, the snapshot is deterministic.

## Frontend

The chat UI lives at `/c/{curriculumId}/plan` and consists of:

- **`PlannerChat.svelte`** — role-styled message list (user/planner/event), Enter-to-send textarea, clamped days-per-batch input, "Plan the next N days" quick action
- **`ProposedBatch.svelte`** — day cards (title/focus/collocation chips/objective/story guidance) with Commit/Revise buttons
- **`lib/planner.ts`** — pure helpers (`appendTurn`, `batchRange`, `commitEvent`, `clampBatchSize`)

The library page (`+page.svelte`) exposes "+ New curriculum" which opens an inline topic+CEFR form → `startPlan()` → `/c/{id}/plan`.

## Settled decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Source of truth | Plan JSON (`CurriculumDay[]`), not the chat transcript |
| 2 | Start plan is LLM-free | `POST /api/curriculum/plan` only mints an id and saves empty state; the first LLM call happens on the first turn |
| 3 | Latest-proposal-wins | Each proposing turn replaces any prior uncommitted proposal; only committed days accumulate |
| 4 | Learner snapshot | Included in every turn prompt via `build_learner_snapshot()`; updates every turn from the current SRS state |
| 5 | No endpoint exposes the full chat | `GET /{id}` returns days + proposed + cefr, but not the chat transcript (which is session-local) |

## Manual mode (Claude chat)

Free Groq models produce noticeably worse curricula and stories than a Claude chat subscription. Manual mode lets the user author both **curriculum days** (planner turns) and **story/day JSON** by pasting TunaTale's exact prompts into Claude chat and pasting Claude's reply back — no paid API calls, no API key, pure copy/paste.

### Mode toggle

`curriculum.metadata["generation_mode"]` stores `"auto"` (default, byte-for-byte unchanged) or `"manual"`. Absent key = `"auto"`. The mode is exposed via `GET /api/curriculum/{id}` (top-level `generation_mode` field) and set via `POST /api/curriculum/{id}/generation-mode`.

### Copy/paste planner loop

1. User types a message (same textarea as auto mode) → clicks **"Copy prompt"**
2. `POST /api/curriculum/{id}/plan/turn/prompt` returns `{system_prompt, user_prompt}` — the exact prompts the Groq path would send for the same inputs (byte-identical, enforced by a drift-guard test)
3. The prompts are copied to the clipboard as `system_prompt + "\n\n" + user_prompt`
4. User pastes into Claude chat, gets a reply
5. User pastes Claude's final reply into the "Paste Claude's reply" textarea → clicks **"Submit reply"**
6. `POST /api/curriculum/{id}/plan/turn` with `pasted_response` — skips the LLM call, parses the paste directly through `parse_turn()`. All validation (renumbering, batch_size, validate_plan_days) applies identically to pasted responses.
7. Proposal/commit UI unchanged.

**Important:** Only Claude's FINAL reply gets pasted back. Side conversation in Claude chat is not persisted.

### Pipeline gating

When mode == `"manual"`:
- `plan_commit` does **NOT** enqueue `"generate"` pipeline jobs (days still commit to the curriculum)
- `reconcile` does **NOT** enqueue `"generate"` for lesson-less days, but **STILL** enqueues `"render"` for lessons missing audio (rendering is EdgeTTS/ffmpeg, no LLM — a pasted-in day must still get its audio automatically)
- Explicit per-day endpoints (`pipeline/retry`, `regenerate`, `POST /api/story/generate`) keep working unchanged — deliberate user actions may still use Groq

### Code references

- Prompt export: `planner.py::build_turn_prompt`, `story.py::build_story_prompts`
- Paste parsing: `json_parsing.py::parse_json_object`, `split_reply_and_json` (single parse path invariant)
- Mode endpoint: `curriculum.py::set_generation_mode`
- Pipeline gate: `curriculum.py::plan_commit`, `pipeline.py::reconcile`
