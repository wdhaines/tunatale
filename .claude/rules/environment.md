# Environment & Secrets

## Setup

```bash
cd backend
uv sync --all-groups
cp ../.env.example .env
# Edit .env and set GROQ_API_KEY
```

## Virtual Environment

All commands run via `uv run` (no manual venv activation needed):
```bash
uv run pytest
uv run uvicorn app.main:app --reload
uv run ruff check app tests
```

## Secrets

- `GROQ_API_KEY` — Groq API key (required for record/live/patch LLM modes)
- Never commit `.env` files
- CI uses mock cassettes only (no API key needed)

## Groq Setup

Model: `llama-3.3-70b-versatile` (default)
Endpoint: `https://api.groq.com/openai/v1/chat/completions`

Rate limits: free tier ~30 RPM, 14,400 TPM. The LLMClient handles 429 retry with header-based backoff.
