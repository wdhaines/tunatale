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

Target: ≥95% line coverage. Run with `uv run pytest` (coverage configured in pyproject.toml).
