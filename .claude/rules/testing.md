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

## Cloze Tests (Phase F)

- **Unit**: `tests/test_function_words.py` — `is_function_word`, `make_cloze_text`
- **Integration**: `tests/test_api.py::TestListenClozeIntegration` — `/listen` cloze flag detection
- **Sync**: `tests/test_anki_sync_create_new.py::TestClozeNote`, `TestSyncCreateNewRouting` — `create_cloze_note`, cloze routing
- **E2E**: `tests/test_e2e_listen_to_sync.py` — full listen→sync round-trip
- **In-memory Anki collections**: use `_make_cloze_collection_conn()` or `_make_dual_collection_conn()` from `test_anki_sync_create_new.py`
