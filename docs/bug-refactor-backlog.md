# Bug & Refactor Backlog

Findings from a code sweep on 2026-07-02 (branch `feat/planner-phase6`). Each
entry is scoped so Big Pickle can implement it: prescriptive instruction plus a
guardrail test. Items in the Anki/sync danger zone are marked **[DANGER ZONE —
not for Big Pickle]** per the standing rubric.

Status legend: `OPEN` (ready to pick up), `FIXED` (done in this sweep, kept for
the record). See **Priority & ownership** below for the ROI-ranked dispatch
order and the Claude/Big-Pickle owner split (updated 2026-07-03). Each OPEN
entry's header also carries its `[→ owner · tier]` tag.

---

## Priority & ownership (ROI-ranked) — updated 2026-07-03

ROI = user-facing value ÷ effort, with risk as a gate. Owner tags:
**[Claude]** = keep in-house — parity/danger-zone, a cassette re-record, or an
unresolved load-bearing decision. **[BP]** = Big-Pickle-ready — prescriptive
brief + guardrail test, outside the Anki/sync danger zone (the standing
[[feedback_big_pickle_readiness_rubric]]). Of the 18 remaining OPEN items, 16
are BP-ready; **the only items that genuinely need Claude** are #10 and #18
(both force a cassette re-record) and the two danger-zone observations (parity
code — oracle harness green before/after). #25 is BP-drafts-Claude-reviews.
Everything in Tiers 1–3 below is dispatchable to Big Pickle as-is.

### Tier 1 — do next (real bugs, low→medium cost, high ROI)

| # | Owner | Item | Why it's high ROI |
|---|-------|------|-------------------|
| ~~16~~ | ✅ | Planner chat loses draft on failed turn | **FIXED 2026-07-03** (commit 834f476) — `send()` restores the draft, `handleSend` returns `bool`. |
| ~~28~~ | ✅ | Card media pipeline Slovene-hardcoded | **FIXED 2026-07-03** (commit 11022e0) — `get_tts_voice` registry helper; `language_code` threaded through Forvo/TTS/cloze. e2e `generate-norwegian.spec.ts` asserts nb-NO voices. |
| ~~30~~ | ✅ | LLM fallback chain bypassed | **FIXED 2026-07-03** — `_call_groq`/`_call_ollama` broaden to `httpx.TransportError` + wrap body-parse → `LLMError`; fallback chain engages. 8 respx tests. |
| ~~34~~ | ✅ | Double-tap = double grade | **FIXED 2026-07-03** — `wordActionInFlight` guard + `$effect`-driven refetch in `+page.svelte`. |
| ~~17~~ | ✅ | Duplicate collocation crashes proposal panel | **FIXED 2026-07-03** — case-insensitive dedup in `_validate_collocations`. |
| ~~14~~ | ✅ | Re-render deletes audio rows before rendering + disk leak | **FIXED 2026-07-03** — save old rows before render, delete rows+files only after render succeeds. `backend/app/api/audio.py`. |
| 10 | Claude | `generate_word_gloss` POS-blind | Wrong-sense glosses now (hotel→"to want") — the exact ambiguity sentence-aware lemmatization was meant to kill. Needs a prompt-injection decision + cassette re-record. **Batch with #18** (both re-record). |

### Tier 2 — medium ROI

| # | Owner | Item | Note |
|---|-------|------|------|
| 5 | BP | Story parse bare `KeyError` → 500 | Skip-and-log malformed key phrases. |
| 31 | BP | EdgeTTS retry misses edge-tts/aiohttp types | Transient 403/empty-audio fails whole render. Verify base-class names vs the pinned edge-tts first. |
| ~~33~~ | ✅ | Cards page stale-response race + double fetch | **FIXED 2026-07-03** — `fetchSeq` token guard + `$effect`-driven refetch in `+page.svelte`. |
| 20 | BP | Lifespan CWD-relative paths | Same class as the `_tt_settings` relative-db bug; anchor to `__file__`. |

### Tier 3 — low ROI cleanup (batch when a BP is idle; existing tests are the guardrail)

| # | Owner | Item |
|---|-------|------|
| 35 | BP | Dead config fields (`anki_connect_url`, `forvo_api_key`) — trivial, grep already clean |
| 8 | BP | Extract `serialize_lesson` (dup response dicts) |
| ~~15~~ | ✅ | Extract `_resolve_topic_day` / `_section_title` (dup blocks in audio API) | **FIXED 2026-07-03** — `backend/app/api/audio.py`. |
| 29 | BP | `cloze_tts` → public `SRSDatabase` helpers (stop reaching into `_get_conn`) |
| 11 | BP | Drop 4 vestigial params (ruff ARG) |
| ~~32~~ | ✅ | Filter orphaned planner feedback at prompt-build | **FIXED 2026-07-03** — `build_planner_turn_prompt` filters `feedback` against `existing_days`. |
| 13-fu | BP | One-shot lowercase `token_glosses` migration for pre-fix lessons |
| 21 | BP | Renderer preprocessor per-language (latent; harmless while both pass-through) |
| 25 | BP* | `get_lemmatizer` per-language (latent; *biggest/riskiest BP — Claude reviews the request-scoping change) |

### Tier 4 — owner-only (Claude), scheduled separately

| # | Owner | Item | Why not BP |
|---|-------|------|-----------|
| 18 | Claude | Cassette hash ignores `system_prompt` | Trivial code change but forces a **full cassette re-record** (GROQ_API_KEY + rate-limit babysitting). |
| — | Claude | 4 AM rollover constant in 3 places (de-dup) | Parity code; oracle harness green before/after. See danger-zone notes. |
| — | Claude | `date.today()` midnight-vs-4am audit (~10 sites) | Per-call-site parity judgment. See danger-zone notes. |

**Cassette-affecting batch:** #10 and #18 both change LLM prompts/hashes → do
them in one `--llm-mode=record` session (`export $(grep '^GROQ_API_KEY='
backend/.env)`), one test file at a time to dodge Groq free-tier 429s, then
verify plain mock-mode `uv run pytest` is green.

---

## 1. FIXED — Planner never saw the user's current chat message

`CurriculumPlanner.turn()` accepted `user_message` but never put it in the
prompt: the API appends the user message to persisted chat only *after* the
turn, and `turn()` passed the pre-turn chat straight to
`build_planner_turn_prompt`. The LLM only ever saw the conversation up to the
previous turn, so chat steering ("focus day 2 on food") was silently ignored.

Fixed in `backend/app/generation/planner.py` (inject
`{"role": "user", "content": user_message}` before building the prompt);
regression test `test_planner.py::test_user_message_reaches_prompt`; cassette
`TestPlannerLLM__test_two_turn_scenario.json` re-recorded.

## 2. FIXED — Stale planner proposal survives plan import → duplicate day numbers on commit

**Bug.** `import_plan` (`backend/app/storage/plan_io.py`) deliberately
preserves `existing.metadata` (chat state) when re-importing an existing
curriculum — but that includes `metadata["planner"]["proposed"]`. The proposal
was numbered against the *pre-import* day list (`start_day = old_max + 1`).
If the hand-edited import removed/renumbered days, a subsequent
`POST /{id}/plan/commit` (`backend/app/api/curriculum.py::plan_commit`)
appends days whose numbers collide with or gap the committed days. Nothing
downstream tolerates duplicate day numbers (`get_lesson_by_day`, day sorting,
`plan_turn`'s `start_day = max(...) + 1`).

**Fixed 2026-07-02** (both parts below, plus guardrail tests
`test_commit_stale_proposal_409_and_days_unchanged` and
`test_same_id_clears_stale_proposal_keeps_chat_and_feedback`):
1. In `plan_commit`, before extending, require
   `proposed["days"][0]["day"] == max((d.day for d in curriculum.days), default=0) + 1`;
   otherwise raise HTTP 409 `"Proposed batch is stale — ask the planner to re-propose"`.
2. In `import_plan`, when reusing existing metadata, clear the proposal:
   `metadata = {**existing.metadata}`, and if it has a `"planner"` dict, set
   `planner["proposed"] = None` (deep-copy first; don't mutate the stored object).
   Chat and feedback stay.

**Guardrail tests** (in `backend/tests/test_api_curriculum_plan.py` and
`backend/tests/test_plan_io.py`):
- Turn proposes days 4–6 on a 3-day curriculum → import a plan file with only
  2 days (same id) → commit returns 409 and `curriculum.days` is unchanged.
- `import_plan` on an existing curriculum with a non-null `proposed` leaves
  `chat`/`feedback` intact but `proposed is None`.
- Normal turn→commit flow still passes (no false 409).

## 3. FIXED — `batch_size` unvalidated server-side

**Bug.** `PlanTurnRequest.batch_size` (`backend/app/api/models.py`) is a bare
`int = 5`. The frontend clamps to 1–14 (`frontend/src/lib/planner.ts
clampBatchSize`), but the API accepts 0, negatives, or 500 (which asks the LLM
for 500 days into a 5500-token budget → guaranteed 502).

**Fix.** `batch_size: int = Field(5, ge=1, le=14)` (import `Field` from
pydantic). Mirror the frontend bounds.

**Guardrail test.** POST `/plan/turn` with `batch_size=0` and `batch_size=15`
→ 422; `batch_size=1` and `=14` accepted (existing stub-planner test fixture
`test_api_curriculum_plan.py` shows how to fake the planner).

## 4. FIXED — `split_reply_and_json` gives up on the last fence instead of trying earlier ones

**Robustness.** `backend/app/generation/json_parsing.py::split_reply_and_json`
iterates fences in reverse and **raises** on the first JSON-looking fence whose
content doesn't parse — even when an earlier fence in the same reply is a valid
proposal. An LLM reply like "```json {valid days} ``` … here's a sketch:
```{pseudo-code}```" fails the whole turn.

**Fix.** On `json.JSONDecodeError` (or non-dict parse) for a candidate fence,
`continue` to the next-earlier fence instead of raising; raise only if **no**
fence parses and at least one JSON-tagged (`lang_tag == "json"`) fence existed.
Keep the bare-fence (` ``` ` + `{`) heuristic as a candidate but never let it
alone trigger the error path.

**Fixed 2026-07-03.** Malformed/non-dict fences now `continue` to the
next-earlier candidate; ValueError only when a `json`-tagged fence existed and
nothing parsed. Tests: `test_split_reply_json_valid_fence_before_invalid_bare_fence_wins`,
`…before_malformed_json_fence_wins`, `…only_malformed_bare_fence_no_raise`
(plus the two pre-existing raise tests still pass unchanged).

## 5. OPEN [→ BP · T2] — Story/section parsing can raise bare `KeyError` → 500

**Robustness.** `backend/app/generation/story.py::_parse_response` line
`KeyPhraseInfo(phrase=kp["phrase"], translation=kp["translation"])` trusts LLM
output; a key-phrase entry missing either field raises `KeyError`, which
escapes as a 500 instead of the `StoryGenerationError` every other malformed-
LLM-output path raises. Same pattern throughout
`backend/app/generation/section_builder.py`: `kp["phrase"]`,
`scene["label"]`, `line["speaker"]`, `line["text"]`, `line["translation"]`
are all bare subscripts on LLM-produced dicts. Wrap the whole
`_parse_response` body in a `KeyError → StoryGenerationError` translation
(cheapest), or validate the shape upfront with field-path errors.

**Fix.** Skip-and-log entries that aren't dicts with non-empty `phrase` and
`translation` strings (mirror the tolerant handling of `dialogue_glosses` a
few lines below), or raise `StoryGenerationError` with a field path. Prefer
skip-and-log: one bad key phrase shouldn't waste an otherwise good generation.

**Guardrail test.** Feed `_parse_response` a payload with one good and one
field-missing key phrase → lesson builds with the good one, warning logged.

## 6. FIXED — Planner turn prompt: empty-conversation section renders headerless blank

**Cosmetic/prompt-quality.** `build_planner_turn_prompt` wrote the
`## Conversation` header and nothing under it when `chat` was empty. Fixed
2026-07-03: renders `(none yet)` for symmetry (with a comment noting the
branch is nearly unreachable post-fix-#1). No cassette impact — the turn path
always has ≥1 chat message. Test: `test_empty_chat_renders_none_yet`.

## 7. FIXED — `/api/story/generate`: invalid strategy and LLM failures both surface as raw 500s

**Bug (two parts).** In `backend/app/api/generation.py::generate_story`:
1. `ContentStrategy[body.strategy]` raises bare `KeyError` for anything but
   `"WIDER"`/`"DEEPER"` → 500. Fix: change `GenerateStoryRequest.strategy`
   (`backend/app/api/models.py`) to `Literal["WIDER", "DEEPER"] = "WIDER"` so
   FastAPI returns 422 with a field error.
2. `generator.generate(...)` can raise `StoryGenerationError` (malformed LLM
   output) — nothing catches it anywhere (no app-level exception handler), so
   the client gets a 500 traceback. Fix: wrap in
   `try/except StoryGenerationError → HTTPException(502, str(e))`, mirroring
   how `plan_turn` maps `PlannerError` to 502.

**Guardrail tests** (`backend/tests/test_api.py` area): POST with
`strategy="SIDEWAYS"` → 422; stub generator raising `StoryGenerationError`
→ 502 with the message in `detail`.

## 8. OPEN [→ BP · T3] — Duplicate lesson-serialization dicts

**Refactor.** `backend/app/api/generation.py::get_lesson` and
`backend/app/api/curriculum.py::get_lesson_by_day` build the identical
`{id/title/language_code/key_phrases/sections}` response dict by hand.
Extract one `serialize_lesson(lesson_id, lesson, *, day=None)` helper (either
module or a small `app/api/_serializers.py`) and call it from both. Pure
behavior-preserving; existing endpoint tests are the guardrail.

## 9. FIXED — Fire-and-forget `asyncio.create_task` without a strong reference

**Latent bug.** `backend/app/api/generation.py:96` —
`asyncio.create_task(_prewarm_lesson(...))` discards the task reference; the
event loop holds only a weak ref, so the task can be garbage-collected before
it finishes (documented asyncio footgun). Keep a module-level
`_background_tasks: set[asyncio.Task]` — `task = asyncio.create_task(...);
_background_tasks.add(task); task.add_done_callback(_background_tasks.discard)`.

## 10. OPEN [→ Claude · T1] — `generate_word_gloss` accepts `pos` but never uses it (POS-blind glosses)

**Bug (quality).** `backend/app/llm/translate.py::generate_word_gloss` takes
`pos` ("advisory context" per docstring) and both callers compute and pass a
real UPOS (`backend/app/api/srs.py:977`, `:1510`) — but `pos` never enters
either prompt. So the gloss LLM call is POS-blind: lemma `hotel` with
`pos=NOUN` can still be glossed as the verb "to want" (the exact ambiguity
class that motivated sentence-aware lemmatization). Fix: when `pos` is
non-empty, include it in the base-card prompt, e.g. `prompt = f"{lemma} ({pos})"`
or append "The word is a {pos}." to the system prompt.

**⚠️ Cassette impact:** changing the prompt changes cassette hashes. After the
fix run `uv run pytest --llm-mode=patch` with `GROQ_API_KEY` exported from
`backend/.env` (`export $(grep '^GROQ_API_KEY=' .env)`) to re-record affected
cassettes; verify a plain `uv run pytest` (mock mode) passes afterward. If no
cassette-backed test covers these prompts, add the unit assertion only
(stub LLM capturing the prompt, assert the POS appears).

## 11. OPEN [→ BP · T3] — Vestigial unused parameters (ruff ARG sweep)

**Refactor.** `uv run ruff check app --select ARG` finds four genuinely
vestigial parameters (the rest are intentional interface params):
- `app/srs/function_words.py::make_morphology_cloze_text(feature)` — hint is
  stored separately by the caller (per its own docstring); drop the param and
  update the one prod call site (`app/api/srs.py:1523`) and the
  `tests/test_function_words.py` call sites (positional 4-arg → 3-arg).
- `app/anki/media/forvo.py::_extract_mp3_url(word)` — unused; drop from
  signature and the call at line 60.
- `app/anki/import_seed.py::_build_directions(note_id)` — unused since the
  phantom-direction fix; drop (call site line 332).
- `app/api/srs.py::serve_media(request)` — unused FastAPI param; drop.

Optionally finish by enabling `ARG` in the ruff config with `# noqa: ARG002`
on the intentional plugin-interface sites (`lemmatizer.py`,
`audio/preprocessing/*.py`, `translate.py` until item 10 lands) so the class
of bug fixed in item 1 (accepted-but-ignored argument) can't silently recur.

## 12. FIXED — `serve_media` path guard uses bare `startswith`

**Hardening.** `backend/app/api/srs.py::serve_media` rejects traversal with
`str(file_path).startswith(str(media_dir.resolve()))` — the classic prefix
hole: a sibling directory named e.g. `media-evil` would pass. Starlette
decodes `%2F` in path params, so `..%2F` sequences do reach `resolve()`.
Single-user local app → low severity, but the correct check is one line:
`file_path.is_relative_to(media_dir.resolve())`. Add a test asserting a
crafted `..%2F..%2F` filename and a sibling-prefix path both 400.

## 13. FIXED — `token_glosses` written with original-case keys, read with lowercase keys

`StoryGenerator._parse_response` stored gloss keys as the LLM emitted them
(`token_glosses[raw_key]`), while every consumer looks up `surface.lower()` /
lowercase lemma (`transcript.py:371`, `api/srs.py:1501`,
`db.backfill_translations` via lowercase lemmas). A capitalized inflected form
("Boste" → lemma "biti") lost its surface-specific gloss (the generic lemma
fallback — possibly wrong person/tense — showed instead), and a glossed word
absent from the dialogue stranded entirely. `regloss_lessons.py` already
lowercased — the two writers disagreed. Fixed in `story.py` (lowercase key +
lemma fallback); test `test_story.py::test_capitalized_gloss_word_keys_lowercase`.

**OPEN follow-up (Big Pickle).** Lessons generated before the fix still carry
original-case keys in `generation_metadata["token_glosses"]` in the content
store. One-shot repair, mirroring the `regloss_lessons.py` walk-and-rewrite
shape: for each stored lesson, rebuild the map as
`{k.lower(): v for k, v in glosses.items()}` (first-wins on collisions,
skip-and-count lessons already all-lowercase), save back via
`store.save_lesson`. Guardrail test: store a lesson with a `"Hvala"` key,
run the migration, assert only `"hvala"` remains and other metadata intact.

## 14. OPEN [→ BP · T1] — Re-render deletes the lesson's audio rows before rendering; old files leak

**Bug (two parts).** `backend/app/api/audio.py::render_audio`:
1. `store.delete_audio_files_for_lesson(...)` runs *before*
   `renderer.render(...)`. If the render raises (EdgeTTS/network failure →
   500), the lesson's previous audio rows are already gone —
   `GET /api/audio/lesson/{id}` 404s even though the old files still sit on
   disk. Fix: read the old rows first, render the new files, and only then
   delete+insert rows (the render writes to fresh UUID paths, so nothing
   collides).
2. Old audio *files* are never unlinked (every render mints new UUID
   filenames; `delete_audio_files_for_lesson` removes rows only) → unbounded
   `audio_dir` growth for re-rendered lessons. Fix: after the new rows are
   saved, `Path(old_row["file_path"]).unlink(missing_ok=True)` for each old
   row.

**Guardrail tests** (`backend/tests/test_api_audio.py` area, stub renderer):
renderer that raises → old rows still listed by `GET /api/audio/lesson/{id}`;
successful re-render → old file paths gone from disk, new ones present.

## 15. OPEN [→ BP · T3] — Duplicated topic/day resolution + section-title mapping in audio API

**Refactor.** `backend/app/api/audio.py` repeats two blocks verbatim:
- lines ~148–159 and ~195–206: resolve `(topic, day)` for a lesson id via
  lesson_row → curriculum → lesson-title fallback. Extract
  `_resolve_topic_day(store, lesson_id) -> tuple[str, int]`.
- lines ~33–37 and ~110–114: `SectionType(value) → SECTION_TITLES` with
  ValueError fallback. Extract `_section_title(section_type: str) -> str`.
Behavior-preserving; existing audio endpoint tests are the guardrail.

## 16. FIXED — Planner chat: failed turn silently discards the typed message

**UX bug.** `frontend/src/lib/components/PlannerChat.svelte::send()` cleared
`draft` before `await onSend(message)`. The parent (`plan/+page.svelte
handleSend`) catches API failures and shows the error banner, but nothing
re-surfaced the message — the user retyped it from scratch (and the backend
deliberately persists nothing for a failed turn).

**Fixed 2026-07-03** (commit 834f476). `onSend` now returns a boolean
(`handleSend` returns `true` on success, `false` on caught error); `send()`
restores `draft = message` when the result is `false` **and** the user hasn't
started a new draft meanwhile (`if (ok === false && !draft) draft = message`).
Tests (TDD-red before the fix, in `PlannerChat.test.ts`): "restores the draft
when onSend reports failure", "leaves the textarea empty when onSend succeeds",
"does not clobber a newly-typed draft when the failed send resolves".

## 17. OPEN [→ BP · T1] — Duplicate collocations in one proposed day crash the keyed each-block

**Bug (edge).** `ProposedBatch.svelte` renders `{#each d.collocations as c (c)}`
— keyed by value. `validate_plan_days` doesn't enforce uniqueness, and an LLM
proposing the same collocation twice in a day is entirely plausible → Svelte
throws on duplicate keys and the proposal panel fails to render.

**Fix (server-side, preferred).** In `_validate_collocations`
(`backend/app/storage/plan_io.py`), reject duplicates case-insensitively
(`days[i].collocations[j] duplicates an earlier entry`) — the planner already
maps validation errors to a retryable 502, and imports get a clear 422.
Optionally also key the each-block by index for belt-and-suspenders.

**Guardrail tests:** `validate_plan_days` with `["a", "A"]` → ValueError;
planner turn whose JSON repeats a collocation → PlannerError (existing stub
pattern in `backend/tests/test_planner.py`).

## 18. OPEN [→ Claude · T4] — Cassette hash ignores `system_prompt` (stale replays after system-prompt edits)

**Test-fidelity gap.** `backend/app/llm/cassette.py::_hash_prompt` hashes only
the user prompt. Editing a *system* prompt (e.g. `PLANNER_SYSTEM_PROMPT`,
story system prompt) does not invalidate cassettes — mock-mode tests keep
replaying responses recorded under the old instructions and stay green when
they should demand a re-record. Fix: hash
`f"{system_prompt or ''}\x00{prompt}"` (and bump a `"version"` field in the
cassette JSON so old files fail loudly with the re-record hint). Requires a
one-time re-record of all cassettes:
`export $(grep '^GROQ_API_KEY=' backend/.env)` then
`uv run pytest --llm-mode=record --no-cov` for the cassette-marked tests
(watch Groq free-tier rate limits — run test files one at a time if 429s
appear). Also reseed any e2e fixtures derived from cassettes (`playwright`
uses `LLM_MODE=mock` against the same files).

## 19. FIXED — Pixabay image extension detection mislabels `.jpeg` and defaults to `.png`

**Nit.** `backend/app/anki/media/pixabay.py` — the old
`"jpg" in url` substring check saved `.jpeg` URLs (and any unknown format)
with a `.png` extension. Fixed 2026-07-03:
`ext = "png" if img_url.lower().split("?")[0].endswith(".png") else "jpg"`
(jpg is Pixabay's dominant format → the default). Tests:
`test_jpeg_url_gets_jpg_ext_not_png`, `test_png_url_with_query_string_gets_png_ext`.

## 20. OPEN [→ BP · T2] — Lifespan uses CWD-relative paths (`tests/cassettes/e2e.json`, `output/audio`)

**Fragility (same class as the 2026-06-08 `_tt_settings` relative-db bug).**
`backend/app/main.py::lifespan` builds `Path("tests/cassettes/e2e.json")` and
`app.state.audio_dir = Path("output/audio")` relative to the process CWD. A
server started from the repo root instead of `backend/` writes audio to a
different directory, and mock-mode startup crashes on the missing cassette
(`CassetteLLMClient.__init__` reads it eagerly). Works today only because
every launcher happens to cd into `backend/` first. Fix: anchor both to the
backend package dir (`Path(__file__).parent.parent / …`) or make them
Pydantic settings with absolute defaults, mirroring how `_tt_settings` was
fixed. Guardrail: a unit test asserting `app.state.audio_dir.is_absolute()`
after lifespan startup (and same for the cassette path attribute if exposed).

## 21. OPEN [→ BP · T3] — Renderer's preprocessor pinned to the default language (latent multi-language bug)

**Latent bug / trap.** `backend/app/main.py:133` builds ONE `LessonRenderer`
with `get_preprocessor(default_code)`; `render_audio` uses it for every
lesson regardless of `lesson.language_code`. Harmless today because both
`SlovenePreprocessor` and `NorwegianPreprocessor` are pass-throughs — but the
first language that adds a real transform will silently apply the wrong
language's preprocessing in multi-language mode (`database_urls` set). This
violates the repo's own "no hardcoded language logic — use language plugins"
convention at the wiring level. Fix: resolve the preprocessor at render time
from the lesson's language — either pass `preprocessor` into
`renderer.render(...)` per call, or give `LessonRenderer` a
`code → TextPreprocessor` mapping (build from `get_preprocessor` for each
configured language at startup). Guardrail test: two-language app state, a
recording preprocessor stub per language, render a lesson of each language,
assert each stub saw only its own language's phrases.

## 22. FIXED — Listened-lessons migration re-ran on every hydrate, clobbering newer data

`frontend/src/lib/stores/listened.svelte.ts::loadIds` checked the legacy
`tunatale:home` key *first* and never removed it — so for any browser that
still had the legacy key (i.e. the actual user's), every page load re-ran the
migration and reset `tunatale:listened-lessons` to the old snapshot,
discarding every lesson marked listened since the migration. Fixed: migrate
only when the new key is absent, and `removeItem(LEGACY_HOME_KEY)` after a
successful migration. Regression tests: "does not re-run the migration once
the new key exists (no clobber)" + "removes the legacy key after a successful
migration" in `listened.test.ts`.

## 23. FIXED — Loudness normalization saved corrupt bytes when ffmpeg failed

`app/anki/media/normalize.py::_apply_normalization` never checked ffmpeg's
exit code, and `normalize_audio` returned whatever was in the destination temp
file — so a failed loudnorm pass (bad input, codec issue) returned zero-byte /
partial bytes that the media pipeline then saved as the card's pronunciation
audio in the Anki media dir. Now `_apply_normalization` raises on non-zero
exit and `normalize_audio` fails soft to the ORIGINAL bytes (also when ffmpeg
exits 0 but writes an empty file). Tests:
`test_ffmpeg_failure_returns_original_bytes`,
`test_empty_ffmpeg_output_returns_original_bytes` (both mock at the
`subprocess.run` boundary only).

## 25. OPEN [→ BP* · T3] — `get_lemmatizer()` singleton breaks in multi-language mode (companion to #21)

**Latent bug / trap.** `backend/app/srs/lemmatizer.py::get_lemmatizer` is
`@lru_cache(maxsize=1)` and its docstring's premise is "one `target_language`
per process" — but multi-language mode (`settings.database_urls`, per-request
`X-TT-Language` middleware in `main.py`) runs BOTH languages in one process.
There, every request gets the single cached lemmatizer for the configured
`lemmatizer_type`/`target_language` — e.g. Norwegian transcripts analyzed by
the Slovene classla model (or silently by the lowercase fallback). Same wiring
class as item #21 (renderer preprocessor).

**Fix sketch (bigger than #21 — verify call-site behavior as you go).** Make
the factory per-language: `get_lemmatizer(language_code)` with an
`lru_cache`-per-code (classla for "sl", stanza for "no", lowercase otherwise —
the mapping can live in `app/languages.py` next to the preprocessor factory).
Callers already carry `language_code` (`lemmatize_surfaces_in_context`,
`analyze_sentence_cached`, `extract_transcript`, `api/srs.py`'s module-level
`_lemmatizer`); the module-level `_lemmatizer` in `api/srs.py` must become
per-request (resolve from `request.state.language_code`). Keep the
warm-up (`main.py::_warm_from_lessons`) warming each configured language.

**Guardrail test.** Two-language settings; assert `get_lemmatizer("sl")` and
`get_lemmatizer("no")` return different instances of the right classes
(importability-fallback aside), and that the transcript endpoint threads the
request language's lemmatizer.

## 26. FIXED — `POST /items` looked up the just-created item by LIKE-search → wrong row

`create_item` (`backend/app/api/srs.py`) retrieved the row it had just
inserted via `list_collocations(search=text, limit=1)` — a `LIKE %text%`
match ordered by text. Adding `"dan"` while `"Dober dan"` existed returned
the superstring row ("D" < "d" in SQLite's BINARY collation), so the endpoint
returned the wrong item AND attached the new card's image/audio to it
(`_generate_add_time_media(row_id, …)`). Fixed with the exact guid lookup
`_persist_new_card` already uses. Test:
`test_create_item_returns_the_new_item_not_a_substring_match`. (The old
"mock list_collocations empty → 500" test covered the removed lookup and was
deleted; the defensive guid branches carry the same pragma precedent as
`_persist_new_card`.)

## 27. FIXED — Frontend dropped FastAPI list-shaped validation details

`frontend/src/lib/api.ts::request()` only surfaced string `body.detail`;
FastAPI 422s carry a list of `{loc, msg, type}` — every validation error
showed as a bare "HTTP 422". Now rendered as "field: message" lines. Tests in
`api.test.ts` (list-shaped + degenerate entries).

## 24. FIXED — Add-time media fetch blocked the event loop for seconds per card

`app/anki/media/pipeline.py::fetch_card_media` is async but called its
synchronous fetchers inline: Forvo (blocking `httpx.Client`), Pixabay
(blocking), and `normalize_audio` (two ffmpeg subprocesses). Every add-time
media fetch (`POST /items`, `/listen`, peer-sync media_fn) froze ALL
in-flight requests for the duration — the same hazard the codebase already
offloads the lemmatizer to a worker thread for. Now each sync fetcher runs
via `anyio.to_thread.run_sync`. Regression test:
`TestEventLoopLiveness::test_blocking_fetchers_do_not_block_event_loop`
(asserts a concurrent task keeps ticking while a 200 ms fetch runs; was 0
ticks before the fix).

## 28. FIXED — Card media pipeline is Slovene-hardcoded (Forvo scrape, TTS voice, cloze voice)

**Fixed 2026-07-03.** New registry helper `get_tts_voice(code, role="female-1")`
in `app/languages.py` is the single place card-media/cloze audio resolves a
voice. `fetch_forvo_audio(word, *, language_code=...)` scrapes
`language-container-{code}`; `fetch_card_media` / `generate_vocab_media` thread
`language_code` and default `tts_voice` from the registry; `_build_media_fn`
(`app/api/anki.py`) passes `settings.target_language` (peer-sync's
`_tt_settings` sets it per request); all three `synthesize_cloze_audios` call
sites in `api/srs.py` pass `voice=get_tts_voice(lesson.language_code)` and
`_generate_add_time_media` takes `language_code`. Slovene behavior byte-identical
(default `"sl"`). Tests: `test_languages.py` (`get_tts_voice` happy/KeyError/
ValueError), `test_anki_media_forvo.py` (only-`no`-container returns URL for
`"no"`, None for `"sl"`), `test_anki_media_pipeline.py` (voice resolved from
`language_code`, explicit override still wins, Forvo gets the code),
`test_vocab_media.py` + `test_api.py` (Norwegian card asserts
`nb-NO-PernilleNeural`). `PIXABAY_API_KEY` pinned empty in the media tests.
Full backend suite green at 100% coverage; `./test.sh` green (log tail + commit
below).

**Original brief (multi-language; companion to #21/#25 — same wiring class).**
Three hardcodings made every non-Slovene card get Slovene audio:

1. `app/anki/media/forvo.py::fetch_forvo_audio` scrapes only the
   `language-container-sl` section of the Forvo page. For a Norwegian word it
   usually finds nothing (→ TTS fallback), but for words that exist in both
   languages ("hotel", "bank") it attaches the **Slovenian** pronunciation to
   the Norwegian card.
2. `app/anki/media/pipeline.py::fetch_card_media` defaults
   `tts_voice=DEFAULT_VOICE` (`"sl-SI-PetraNeural"`, `tts.py:7`) and **no
   caller passes a voice**: neither `generate_vocab_media`
   (`vocab_media.py:107`) nor the peer-sync `_build_media_fn`
   (`app/api/anki.py:20`) → Norwegian vocab cards get Slovene-voice TTS.
3. `app/audio/cloze_tts.py::synthesize_cloze_audios` defaults
   `voice="sl-SI-PetraNeural"` and none of its three `app/api/srs.py` callers
   (497, 527, 915) pass a voice → Norwegian cloze sentence/word audio is
   Slovene-voiced.

**Fix (thread `language_code`, resolve voice from the registry).**
- Add a helper next to the registry (`app/languages.py`):
  `get_tts_voice(code: str) -> str` returning
  `get_language(code).tts_voice_map["female-1"]` (the map every language
  defines; raise ValueError if missing, mirroring `get_preprocessor`).
- `fetch_forvo_audio(word, *, language_code: str, ...)` → search
  `language-container-{language_code}` (keep both quote variants). Update the
  one prod call in `pipeline.py` and tests.
- `fetch_card_media(..., language_code: str)` — derive
  `tts_voice = get_tts_voice(language_code)` when the caller doesn't override,
  and pass `language_code` to the Forvo fn. (Keep the explicit `tts_voice`
  kwarg for tests.)
- `generate_vocab_media(..., language_code)` — callers already know it:
  `app/api/srs.py::_generate_add_time_media` has the unit/lesson language;
  `_build_media_fn` (`app/api/anki.py`) uses `settings.target_language`
  (per-request `_tt_settings` already sets it for peer-sync).
- `synthesize_cloze_audios(..., voice=get_tts_voice(lang))` at the three
  `api/srs.py` call sites (each has the lesson/request language in scope).

**Guardrail tests.** `fetch_forvo_audio` with a page containing only a `no`
container + `language_code="no"` → returns the URL; `language_code="sl"` →
None. A `generate_vocab_media` call with `language_code="no"` and a stubbed
`_fetch_fn` asserting `tts_voice == "nb-NO-PernilleNeural"`. Existing Slovene
tests keep passing unchanged (default behavior for "sl" is identical).

**Note:** does NOT touch reconcile/USN logic — media fetch args only. Safe for
Big Pickle. If item 11's `_extract_mp3_url(word)` unused-param cleanup lands
first, coordinate (same file).

## 30. FIXED — LLM fallback chain bypassed by connection errors and malformed bodies

**Fixed 2026-07-03.** `_call_groq`'s `http.post` catch broadened from
`httpx.TimeoutException` to `httpx.TransportError` (its superclass — covers
connect/read/protocol errors too; the timeout-specific message is kept via an
`isinstance` check). The body-parse (`response.json()` + `choices[0].message.
content` + think-strip) is wrapped in `try/except (ValueError, KeyError,
IndexError, TypeError) → LLMError("Groq returned malformed response body")` —
`ValueError` covers `json.JSONDecodeError`, and a `null` content is caught as
the `re.sub` `TypeError`. Same two fixes applied to `_call_ollama`: a new
`except httpx.TransportError` after the connect (auto-start) and timeout
branches, and a wrapped body-parse. All paths raise `LLMError` so
`complete()`'s fallback chain engages. Tests (respx): Groq `ConnectError` →
Ollama/fallback-client engages; Groq 200 with non-JSON / missing `choices` /
`null` content → fallback; no-fallback malformed → `LLMError`; Ollama
`ReadError` and missing-`response`-key → `LLMError`. 8 new tests in
`TestConnectionAndMalformedFallback`; 100% coverage held; `./test.sh` green.

**Original brief (robustness).** `app/llm/client.py::_call_groq` caught only
`httpx.TimeoutException` (line ~212). Two other failure shapes escaped as raw
exceptions, skipping the entire Groq → fallback_client → Ollama chain that
exists precisely for network resilience:
1. `httpx.ConnectError` / other transport errors from `http.post` (network
   down, DNS failure, connection refused) — propagates uncaught.
2. A 2xx response with an unexpected body: `response.json()` raises
   `json.JSONDecodeError`, or `data["choices"][0]["message"]["content"]`
   raises `KeyError`/`IndexError`/`TypeError`.

**Fix.** Broaden the except to `httpx.TransportError` (superclass covering
timeout + connect + read errors — keep the timeout-specific message for
`TimeoutException` if you like), and wrap the body-parse in
`try/except (ValueError, KeyError, IndexError, TypeError) → LLMError("Groq
returned malformed response body", …)`. Both must raise `LLMError` so
`complete()`'s fallback chain engages. Same audit for `_call_ollama` (its
`http.post` + body parse have the same shape).

**Guardrail tests** (respx, mirroring the existing 429/timeout tests in the
LLMClient test file): mock a `httpx.ConnectError` side effect → `complete()`
falls through to a stub fallback_client and returns its answer; mock a 200
with `{"unexpected": true}` → same fallback engagement; with no fallback →
LLMError (not KeyError) raised.

## 29. OPEN [→ BP · T3] — `cloze_tts.py` reaches into `db._get_conn()` (private) twice

**Refactor.** `app/audio/cloze_tts.py` opens raw connections via the private
`db._get_conn()` for two one-line queries: `_missing_media_row` (media-row
existence) and the guid lookup at line 99. Add two public helpers on
`SRSDatabase` (`app/srs/database.py`): `has_media_row(collocation_id, kind) ->
bool` and `get_guid_by_collocation_id(collocation_id) -> str | None` (check
first — a guid-by-id helper may already exist), then delete
`_missing_media_row`. Behavior-preserving; existing cloze-TTS tests are the
guardrail.

## 32. OPEN [→ BP · T3] — Orphaned planner feedback survives plan re-import (residual of item 2's class)

**Nit (prompt quality).** `import_plan` (`backend/app/storage/plan_io.py`) now
clears the stale *proposal* on re-import (item 2), but deliberately keeps
`feedback` — whose entries reference day numbers (`{"day": N, "note": …}`).
A re-import that removes/renumbers days leaves feedback pointing at days that
no longer exist, and `build_planner_turn_prompt` (`prompts.py:352`) injects
ALL feedback (no existing-day filter, no cap — unlike chat's last-12
truncation) into every future turn. Fix non-destructively at prompt-build:
in `plan_turn` (`app/api/curriculum.py`) or inside the prompt builder, filter
`feedback` to `{d.day for d in curriculum.days}` before rendering — keep the
stored rows (a later import may restore the day). Guardrail: prompt built
with feedback for day 9 on a 3-day curriculum omits the day-9 note; feedback
for day 2 still renders.

## 31. OPEN [→ BP · T2] — EdgeTTS retry misses edge-tts/aiohttp exception types

**Robustness.** `app/audio/edge_tts.py::_synthesize_with_retry` retries only
`(ConnectionResetError, ConnectionError, OSError)`. edge-tts is built on
aiohttp and raises its own hierarchy: `edge_tts.exceptions.NoAudioReceived`,
`WebSocketError`, and aiohttp's `WSServerHandshakeError`/`ClientResponseError`
(NOT OSError subclasses — only `ClientOSError` is). A transient EdgeTTS 403
handshake rejection or empty-audio response fails the whole lesson render on
the first attempt instead of retrying. Fix: add
`edge_tts.exceptions.EdgeTTSException` and `aiohttp.ClientError` to the retry
tuple (import aiohttp is already a transitive dep of edge-tts; verify the
exact base-class names against the pinned edge-tts version before writing).
Also: the class docstring says "max 3 concurrent" but
`MAX_CONCURRENT_REQUESTS = 10` — fix the comment while there.

**Guardrail test** (pytest-mock at the edge_tts.Communicate boundary, like
existing EdgeTTS tests): `Communicate.save` raising `NoAudioReceived` twice
then succeeding → synthesize succeeds; raising 3× → RuntimeError.

## 33. OPEN [→ BP · T2] — Cards page: stale-response race + double fetch on search

**Nit (UX edge).** `frontend/src/routes/cards/+page.svelte`:
1. No in-flight guard/sequence token on `loadItems()` — a slow response can
   land after a newer one (rapid page-next clicks, or search racing the
   `$effect`) and overwrite `items`/`total` with stale data. Fix: module-level
   `let fetchSeq = 0`, capture `const seq = ++fetchSeq` at call start, and only
   assign results `if (seq === fetchSeq)`.
2. `onSearchInput`'s debounce callback sets `page = 0` **and** calls
   `loadItems()` — when the user was on page > 0, the page change also
   triggers the tracking `$effect` → two concurrent fetches (which is how race
   (1) gets exercised). Fix: the callback should only set `lastSearch`/`page`
   and let the `$effect` fetch — add `lastSearch` to the effect's tracked
   deps; drop the direct `loadItems()` call.

**Guardrail test** (`cards/page.test.ts` pattern, mock `api.listSRSItems`
with controllable promises): resolve call A after call B → items reflect B.

## 34. OPEN [→ BP · T1] — Lesson-page word taps have no in-flight guard (double-tap = double grade)

**Bug (mobile UX).** `frontend/src/routes/c/[curriculumId]/l/[lessonId]/+page.svelte::handleWordClick`
(and `handleCollocationStateChange`) fire `submitDrill`/`createBaseCard`
directly on tap with no in-flight guard — unlike `DrillCard`, which has the
`inFlight` state exactly for this. A double-tap on a due word submits two
'good' grades (the second advances the card twice and burns the single-level
undo snapshot); on an unknown word it calls `createBaseCard` twice (second
returns 409 → error banner flashes for a successful action).

**Fix.** Mirror DrillCard: `let wordActionInFlight = $state(false)`; early
return when true, set in `try`/clear in `finally`, wrapping both handlers
(one shared flag is right — these actions all mutate the same transcript).

**Guardrail test** (`[lessonId]/page.test.ts` pattern): mock `submitDrill`
with a hanging promise, dispatch two clicks, assert one call.

## 35. OPEN [→ BP · T3] — Dead config fields: `anki_connect_url`, `forvo_api_key`

**Cleanup.** `backend/app/config.py` still declares `anki_connect_url`
(anki-connect integration was archived — see the dead-code-audit memory) and
`forvo_api_key` (the Forvo fetcher scrapes HTML; it never used an API key).
Grep confirms zero references outside `config.py` in both `app/` and `tests/`
(2026-07-03). Delete both fields; also remove any mention from `.env.example`
if present. Per the dead-code-audit rule, re-grep both names across app AND
tests before deleting. Guardrail: `./test.sh` (a stray consumer would fail at
import/attribute time).

## 36. FIXED — Wifi-prefetch opt-out ignored on direct lesson-page loads (+ no re-prefetch on lesson→lesson nav)

**Bug (two parts).** `frontend/src/lib/components/AudioPlayer.svelte` +
`frontend/src/lib/stores/prefetchPref.svelte.ts`:
1. **Opt-out race.** The store defaults `enabled = true`; the stored opt-out
   is only applied by `init()`, which the **layout's** `onMount` calls — but
   Svelte runs child `onMount` before the parent layout's, so on a first
   paint directly on a lesson route (bookmarked lesson URL, PWA reopen,
   refresh) AudioPlayer's `onMount` reads `enabled === true` and
   `maybePrefetchLesson` downloads the lesson audio even though the user
   turned "Auto-download on wifi" OFF. Fix in the store: lazy self-init —
   `let initialized = false`; in the `enabled` getter (and `init()`), if
   `!initialized && typeof localStorage !== "undefined"`, read the key and
   set `initialized = true`. Keep `init()` for explicit seeding (settings
   page toggle tests already cover `set`/`toggle`).
2. **Stale-prop prefetch.** The prefetch runs in `onMount` with the mount-time
   `audio` prop, but SvelteKit reuses the component on same-route param nav
   (the lesson page's own `$effect` comment documents this) — navigating
   lesson→lesson never prefetches the new lesson. Fix: replace the `onMount`
   with an `$effect` tracking `audio` (idempotent: `maybePrefetchLesson`
   skips already-cached URLs — verify that guard exists in `prefetch.ts`;
   add it if not).

**Fixed 2026-07-03.** Store lazily self-inits on first `enabled` read
(`initialized` flag; `set()` also marks initialized); AudioPlayer's prefetch
moved from `onMount` to an `$effect` tracking `audio` (the already-cached
guard in `prefetch.ts` makes re-runs idempotent). Tests: "first enabled read
applies a stored opt-out without an init() call" (fresh module via
`vi.resetModules`), "prefetches again when the audio prop changes".

---

## Danger-zone observations (NOT for Big Pickle — needs owner decision)

- **4 AM rollover constant lives in 3 places** (`app/srs/database.py`
  `_anki_day_bounds_utc`, `app/anki/sync*` `_local_today_4am`, protobuf wire
  helpers). Known from Layer 67; the maintenance strategy explicitly calls for
  single-sourcing mirror logic. A behavior-preserving extraction (one
  `anki_rollover.py` helper, all three call sites) is the highest-value de-dup
  named in `.claude/rules/anki-queue-parity.md`, but it touches parity code —
  do it with the oracle harness green before/after, not as a drive-by.

- **`date.today()` (midnight-local) still feeds several request paths that
  Layer 67 didn't cover.** Verified concretely: the reading-transcript `is_due`
  / `collocation_is_due` flags (`api/srs.py:635` → `transcript.py::_is_due`,
  `due_at.date() <= today`) use the midnight boundary, while the badges/queue
  use the 4 AM-anchored Anki day (`_anki_day_bounds_utc`). In the
  `[midnight, 4 AM)` window the transcript bolds words as due that the review
  surfaces don't serve yet — same divergence class as the Layer-67 badge
  undercount, on a lower-stakes surface (cosmetic bolding, self-corrects at
  4 AM). ~10 more `date.today()` call sites in `api/srs.py` (221, 436, 759,
  769, 1086, 1362, 1449, 1580…) deserve the same audit: for each, decide
  "calendar day is right here" vs "Anki day is right here." Any fix should
  route through ONE shared `anki_today()` helper (see the 3-places item
  above), with per-call-site sign-off — not a mechanical replace. Parity-
  sensitive; keep out of Big Pickle's hands.
