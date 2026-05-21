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

## Cloze Tests (Phase F)

- **Unit**: `tests/test_function_words.py` — `is_function_word`, `make_cloze_text`
- **Integration**: `tests/test_api.py::TestListenClozeIntegration` — `/listen` cloze flag detection
- **Sync**: `tests/test_anki_sync_create_new.py::TestClozeNote`, `TestSyncCreateNewRouting` — `create_cloze_note`, cloze routing
- **E2E**: `tests/test_e2e_listen_to_sync.py` — full listen→sync round-trip
- **In-memory Anki collections**: use `_make_cloze_collection_conn()` or `_make_dual_collection_conn()` from `test_anki_sync_create_new.py`
