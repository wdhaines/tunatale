# Bug & Refactor Backlog

Findings from a code sweep on 2026-07-02 (branch `feat/planner-phase6`). Each
entry is scoped so Big Pickle can implement it: prescriptive instruction plus a
guardrail test. Items in the Anki/sync danger zone are marked **[DANGER ZONE —
not for Big Pickle]** per the standing rubric.

Status legend: `OPEN` (ready to pick up), `FIXED` (done in this sweep, kept for
the record).

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

## 4. OPEN — `split_reply_and_json` gives up on the last fence instead of trying earlier ones

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

**Guardrail tests** (`backend/tests/test_json_parsing.py` or wherever
`split_reply_and_json` tests live): valid json fence followed by an invalid
bare `{`-fence → returns the valid dict; reply whose only json-tagged fence is
malformed → still raises ValueError.

## 5. OPEN — Story/section parsing can raise bare `KeyError` → 500

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

## 6. OPEN — Planner turn prompt: empty-conversation section renders headerless blank

**Cosmetic/prompt-quality.** `build_planner_turn_prompt`
(`backend/app/generation/prompts.py`) writes the `## Conversation` header, and
when `chat` is empty appends nothing — unlike every other section which renders
`(none)` / `(none yet)`. After fix #1 the chat always contains at least the
current user message, so this is now nearly unreachable — verify and either add
`(none)` for symmetry or leave with a comment. Lowest priority.

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

## 8. OPEN — Duplicate lesson-serialization dicts

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

## 10. OPEN — `generate_word_gloss` accepts `pos` but never uses it (POS-blind glosses)

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

## 11. OPEN — Vestigial unused parameters (ruff ARG sweep)

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

## 14. OPEN — Re-render deletes the lesson's audio rows before rendering; old files leak

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

## 15. OPEN — Duplicated topic/day resolution + section-title mapping in audio API

**Refactor.** `backend/app/api/audio.py` repeats two blocks verbatim:
- lines ~148–159 and ~195–206: resolve `(topic, day)` for a lesson id via
  lesson_row → curriculum → lesson-title fallback. Extract
  `_resolve_topic_day(store, lesson_id) -> tuple[str, int]`.
- lines ~33–37 and ~110–114: `SectionType(value) → SECTION_TITLES` with
  ValueError fallback. Extract `_section_title(section_type: str) -> str`.
Behavior-preserving; existing audio endpoint tests are the guardrail.

## 16. OPEN — Planner chat: failed turn silently discards the typed message

**UX bug.** `frontend/src/lib/components/PlannerChat.svelte::send()` clears
`draft` before `await onSend(message)`. The parent (`plan/+page.svelte
handleSend`) catches API failures and shows the error banner, but nothing
re-surfaces the message — the user retypes it from scratch (and the backend
deliberately persists nothing for a failed turn).

**Fix.** Make `onSend` return `Promise<boolean>` (parent's `handleSend`
returns `false` on caught error, `true` otherwise); in `send()`, restore
`draft = message` when the result is `false` (only if the user hasn't typed
something new meanwhile: `if (!draft) draft = message`).

**Guardrail test** (`frontend/src/lib/components/PlannerChat.svelte.test.ts`
pattern; TDD per house rules): onSend rejects/returns false → textarea value
is the original message; success → stays empty.

## 17. OPEN — Duplicate collocations in one proposed day crash the keyed each-block

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

## 18. OPEN — Cassette hash ignores `system_prompt` (stale replays after system-prompt edits)

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

## 19. OPEN — Pixabay image extension detection mislabels `.jpeg` and defaults to `.png`

**Nit.** `backend/app/anki/media/pixabay.py:479` —
`ext = "jpg" if "jpg" in img_url.lower() else "png"`. A `.jpeg` URL doesn't
contain the substring `"jpg"`, so it's saved with a `.png` extension (and any
unknown format also defaults to png). Harmless for display (renderers sniff
bytes) but wrong metadata in the Anki media dir. Fix:
`ext = "png" if img_url.lower().split("?")[0].endswith(".png") else "jpg"`
(jpg is Pixabay's dominant format — make it the default). One unit test with a
`.jpeg` URL and one with `.png?query=x`.

## 20. OPEN — Lifespan uses CWD-relative paths (`tests/cassettes/e2e.json`, `output/audio`)

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

## 21. OPEN — Renderer's preprocessor pinned to the default language (latent multi-language bug)

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

## 25. OPEN — `get_lemmatizer()` singleton breaks in multi-language mode (companion to #21)

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
