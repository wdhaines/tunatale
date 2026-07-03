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

## 2. OPEN — Stale planner proposal survives plan import → duplicate day numbers on commit

**Bug.** `import_plan` (`backend/app/storage/plan_io.py`) deliberately
preserves `existing.metadata` (chat state) when re-importing an existing
curriculum — but that includes `metadata["planner"]["proposed"]`. The proposal
was numbered against the *pre-import* day list (`start_day = old_max + 1`).
If the hand-edited import removed/renumbered days, a subsequent
`POST /{id}/plan/commit` (`backend/app/api/curriculum.py::plan_commit`)
appends days whose numbers collide with or gap the committed days. Nothing
downstream tolerates duplicate day numbers (`get_lesson_by_day`, day sorting,
`plan_turn`'s `start_day = max(...) + 1`).

**Fix (two parts, both):**
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

## 3. OPEN — `batch_size` unvalidated server-side

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

## 7. OPEN — `/api/story/generate`: invalid strategy and LLM failures both surface as raw 500s

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

## 9. OPEN — Fire-and-forget `asyncio.create_task` without a strong reference

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

## 12. OPEN — `serve_media` path guard uses bare `startswith`

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

---

## Danger-zone observations (NOT for Big Pickle — needs owner decision)

- **4 AM rollover constant lives in 3 places** (`app/srs/database.py`
  `_anki_day_bounds_utc`, `app/anki/sync*` `_local_today_4am`, protobuf wire
  helpers). Known from Layer 67; the maintenance strategy explicitly calls for
  single-sourcing mirror logic. A behavior-preserving extraction (one
  `anki_rollover.py` helper, all three call sites) is the highest-value de-dup
  named in `.claude/rules/anki-queue-parity.md`, but it touches parity code —
  do it with the oracle harness green before/after, not as a drive-by.
