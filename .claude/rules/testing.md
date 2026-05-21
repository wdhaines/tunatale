# Testing Strategy

## Test Types

- **Unit tests** — pure functions/models, no I/O, no network
- **Integration tests** — database, cassette-backed LLM calls
- **API tests** — FastAPI `ASGITransport` + `AsyncClient`, no real server

## Mocking Strategy

- **LLM calls**: Always use `CassetteLLMClient` — never hit live API in CI
- **Database**: Use `sqlite:///:memory:` for SRS tests
- **EdgeTTS**: Mock `edge_tts.Communicate` with pytest-mock
- **HTTP**: Use `respx` for external HTTP calls in `LLMClient` tests

## Cassette System

Cassettes live in `backend/tests/cassettes/`. Each cassette is a JSON file containing recorded LLM prompt/response pairs indexed by SHA256 hash.

### Modes
- `mock` (default, CI): replay from cassette; skip if cassette missing
- `record`: call real LLM and save to cassette
- `live`: call real LLM without saving
- `patch`: replay known prompts; record new ones

### Running modes
```bash
# Default (CI-safe):
uv run pytest

# Record new cassettes (requires GROQ_API_KEY):
uv run pytest --llm-mode=record

# Update specific cassettes:
uv run pytest --llm-mode=patch
```

## Coverage

Target: 100% line coverage (strict `fail_under = 100` in pyproject.toml). Run with `uv run pytest`. The CLI generator script `build_function_word_list.py` is excluded via `coverage.run.omit`.

### Pragma Discipline

`# pragma: no cover` lowers the gate; it doesn't pass it. Before adding one:

1. **Try to write the test first.** Most "uncoverable" branches turn out to be testable with `caplog`, a connection-state fixture, or a small refactor that eliminates a dead branch.
2. **Acceptable uses:** the `if __name__ == "__main__":` CLI guard, and defensive branches that are genuinely unreachable (e.g., re-checking an invariant guaranteed upstream — and the comment must say *why* it's unreachable, not just that it is).
3. **Not acceptable:** "always true in tests," "pass is a no-op," "would require complex setup," "TODO test later." If the justification describes the test scenario itself ("always X in tests"), the branch is reachable — write the assertion.

When reviewing a PR with new pragmas, read each justification skeptically. If the comment describes a scenario the tests do hit, the pragma is hiding the absence of an assertion, not an unreachable branch.

See commit `63bfd94` for the Stage 2 incident: two pragmas with self-contradictory justifications ("cache always empty in tests" on the path tests do hit) were removed and replaced with real `caplog` + branch-coverage tests.

## Frontend Coverage Gate (Svelte 5 phantom filter)

Frontend runs 100% lines/branches/functions/statements per file via `frontend/scripts/coverage-gate.ts`. Vitest's built-in `thresholds:` block is intentionally absent — the custom gate is what enforces. The gate reads `coverage/coverage-final.json`, filters Svelte 5 compiler-injected phantom branches, then asserts 100% on every file.

### What counts as a phantom

`isPhantom(branchType, text, synthetic)` in `coverage-gate.ts` classifies each uncovered sub-location:

- **Synthetic or empty source range** → phantom (compiler emitted a branch at a position the user source never reached).
- **cond-expr** (`?:`): phantom if the sub-location text is a JS literal (`null`, `undefined`, booleans, numbers, quoted strings). Svelte 5 folds these. Identifier/property-access stays real.
- **binary-expr** (`||`, `&&`, `??`): phantom if (a) text starts with `}` or ends with `{` (Svelte template-interpolation boundary) OR (b) text is a bare JS literal (defensive fallback like `?? ''`). Object literals starting with `{` and ending with `}` stay real.
- **if**: phantom only when text is empty. Non-empty if-bodies are real.
- Unknown types stay real (conservative).

All classifications are pinned by `frontend/tests/coverage-gate.test.ts` against empirical TunaTale cases (e.g., `'} created, {'` → phantom, `'e.message'` → real). Adding or changing a rule means updating both.

### Maintenance — heuristic drift after Svelte upgrades

The gate's heuristic depends on the shape of Svelte 5's compiled output. Compiler changes (even patch releases) can alter what v8 reports as branches, which can silently break the filter.

After any `svelte` / `@sveltejs/kit` / `@sveltejs/vite-plugin-svelte` / `@vitest/coverage-v8` version bump:

1. **Eyeball the drop count.** Run `cd frontend && bun run test:coverage` and read the gate's final line: `Coverage gate: dropped N phantom branch(es)`. The baseline as of 2026-05-21 is **46 drops on 21 files**.
2. **A >20% delta in either direction is a signal** — either the compiler emits new phantom shapes the filter doesn't catch (fewer drops, gate may fail on real-looking phantoms) or new shapes the filter wrongly classifies as phantom (more drops, real bugs hidden).
3. **Read the diff.** `git diff coverage/dropped-branches.json` (note: this file is gitignored on purpose, so the diff comes from a manual snapshot — copy it to `/tmp/dropped-before.json` before the upgrade, then diff against post-upgrade). Look for new branch shapes in the drop list that don't match the existing patterns documented in `coverage-gate.ts`.
4. **Refine the heuristic, not the threshold.** If you find a new phantom shape, extend `isPhantom` to recognize it AND add a self-test case to `coverage-gate.test.ts` that pins the classification. Never lower the per-file 100% target to absorb drift — that's how phantom-detection turns into bug-hiding.
5. **If you find a false-positive drop** (the filter dropped something a test could exercise): tighten the heuristic, then write the test for the real branch.

### Don't bypass the gate

- No `/* c8 ignore */` or `/* istanbul ignore */` comments in source. The gate doesn't read them. If you find yourself wanting one, the right answers are (a) write the test, (b) refactor the dead branch out (see `DrillCard.svelte` cloze-helper removal and `[lessonId]/+page.svelte:75` non-null-assertion changes in Phase 3 for the canonical pattern), or (c) extend the `isPhantom` heuristic with a new pinned classification.
- No `thresholds:` block re-added to `vite.config.ts`. The gate is the single source of truth.

## Cloze Tests (Phase F)

- **Unit**: `tests/test_function_words.py` — `is_function_word`, `make_cloze_text`
- **Integration**: `tests/test_api.py::TestListenClozeIntegration` — `/listen` cloze flag detection
- **Sync**: `tests/test_anki_sync_create_new.py::TestClozeNote`, `TestSyncCreateNewRouting` — `create_cloze_note`, cloze routing
- **E2E**: `tests/test_e2e_listen_to_sync.py` — full listen→sync round-trip
- **In-memory Anki collections**: use `_make_cloze_collection_conn()` or `_make_dual_collection_conn()` from `test_anki_sync_create_new.py`
