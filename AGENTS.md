# AGENTS.md — TunaTale

AI-powered language learning system (Slovene) that generates personalized audio curricula from Anki decks.

## Developer Commands

```bash
# Full suite (root): lint + format + pytest + svelte-check + vitest + playwright
./test.sh

# Backend only (from repo root):
cd backend && uv run ruff check app tests      # lint
cd backend && uv run ruff format app tests     # format
cd backend && uv run pytest                     # test + coverage (target: 100%)

# Frontend only:
cd frontend && npm run check                    # svelte-check
cd frontend && npm run test:coverage            # vitest
cd frontend && npm run test:e2e                 # playwright

# Dev servers (backend :8000, frontend :5173):
./start-dev.sh
```

## Architecture

Two main packages:

- **`backend/`** — FastAPI app (`app/main.py`), Python 3.13, `uv` for deps
  - `app/anki/` — Anki collection reading & USN sync (use `safety.safe_open`, never raw sqlite3)
  - `app/api/` — FastAPI route modules
  - `app/audio/` — EdgeTTS + audio assembly pipeline
  - `app/generation/` — Curriculum + story generation
  - `app/llm/` — Groq LLM client + cassette system
  - `app/models/` — Pure domain models (no I/O)
  - `app/srs/` — FSRS spaced repetition engine
  - `app/storage/` — File/DB storage layer

- **`frontend/`** — SvelteKit + TypeScript, Vite, Vitest, Playwright

- **`tests/`** (root) — shared prompts and test data (not a test package)

- **`micro-demo-*/`** — separate git repos, ignored by main repo

## Backend Setup

```bash
cd backend
uv sync --all-groups
cp ../.env.example .env   # set GROQ_API_KEY, LLM_MODE=mock for CI-safe
```

All commands use `uv run` (no manual venv activation).

## Testing Quirks

- **Cassette system** (`backend/tests/cassettes/`): records LLM responses as JSON indexed by prompt hash.
  - `--llm-mode=mock` (default): replay cassettes, skip if missing
  - `--llm-mode=record`: call real Groq API, save cassettes
  - `--llm-mode=patch`: replay known, record new
  - `--llm-mode=live`: call real API, don't save
- **Coverage fails at <100%** (`pyproject.toml: fail_under = 100`)
- **SRS tests**: use `sqlite:///:memory:` via `srs_db` fixture
- **Anki tests**: use `fake_anki_db`, `fake_anki_db_modern`, `fake_anki_db_slovene_pairs` fixtures from `conftest.py` — never use real `collection.anki2`
- **CI only runs backend** (no frontend checks in `.github/workflows/ci.yml`)
- CI requires `ffmpeg` as system dependency

## Key Conventions

- **No hardcoded language logic** — use language plugins (`TextPreprocessor`, voice maps)
- **No module-level side effects** — config via Pydantic Settings in `app/config.py`
- **LLM tests must use cassettes** — never hit live API in tests
- **Anki writes**: always use `app.anki.safety.safe_open()` — handles lock probe, SHA256 backup, integrity check
- **Anki mutations**: always set `usn=-1`, `mod=now_ts` on touched rows, and update `col` — see `.claude/rules/anki-sync.md`

## CI Order

```
ruff check → ruff format --check → pytest
```

Frontend checks are local-only (`npm run check`, `test:coverage`, `test:e2e`).

## Instruction Files

- `.claude/rules/testing.md` — cassette system, mocking strategy, test types
- `.claude/rules/tdd.md` — red-green-refactor workflow, step ordering
- `.claude/rules/environment.md` — secrets, venv, Groq setup, rate limits
- `.claude/rules/anki-sync.md` — USN protocol, safety envelope, migration rules
