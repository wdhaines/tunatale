# TunaTale

AI-powered language learning system that generates personalized audio curricula.

## Quick Start

```bash
cd backend
uv sync --all-groups
cp ../.env.example .env  # set GROQ_API_KEY
uv run pytest            # run test suite
```

From repo root:
```bash
./test.sh        # lint + test
./start-dev.sh   # backend dev server (port 8000)
```

## Architecture

```
backend/
├── app/
│   ├── main.py          # FastAPI app
│   ├── config.py        # Pydantic Settings
│   ├── models/          # Pure domain models (no I/O)
│   ├── llm/             # Groq LLM client + cassette system
│   ├── srs/             # FSRS spaced repetition engine
│   ├── generation/      # Curriculum + story generation
│   ├── audio/           # TTS + audio assembly pipeline
│   └── api/             # FastAPI route modules
└── tests/
    ├── conftest.py
    ├── cassettes/       # Recorded LLM responses
    └── test_*.py
```

## Rules

See `.claude/rules/` for detailed guidance:
- `testing.md` — test types, mocking strategy, cassette system
- `tdd.md` — red-green-refactor workflow, step ordering
- `environment.md` — secrets, venv, Groq setup
- `anki-sync.md` — USN/sync protocol; required reading before touching `backend/app/anki/`
- `anki-queue-parity.md` — TT↔Anki queue/badge parity principles + divergence playbook; read before touching `backend/app/api/srs.py`, `backend/app/srs/fsrs.py`, `backend/app/srs/queue_stats.py`, or `backend/app/anki/sync.py`. Full layer-by-layer history at `docs/anki-parity-layers.md` (reference only, not auto-loaded).

## Key Design Decisions

- No hardcoded language logic — language plugins (TextPreprocessor, voice maps)
- No module-level side effects — config via Pydantic Settings
- Cassette system for all LLM tests — record once, replay in CI
- Replacement dictionary fully dynamic — built from SRS database

## Rules (project-level)

- Whenever making large changes your last step should be to run pytest to make sure the entire test suite passes.
