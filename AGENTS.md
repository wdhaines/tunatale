# AGENTS.md — TunaTale

AI-generated audio language curricula — Pimsleur-style listening with content adapted to the learner's vocabulary. Slovene and Norwegian are wired end-to-end (Slovene most completely); the architecture is language-plugin based. Integrates bidirectionally with the user's Anki deck rather than replacing it. See `README.md` for the product pitch and `docs/walkthrough.md` for the system tour.

## Developer Commands

**⚠️ Must run `./test.sh` before every commit — the full suite must pass, or you DO NOT commit.** (Enforced by a commit-gate hook — see Hooks below.)

```bash
# Full suite (root): lint + format + checkers + pytest + svelte-check + vitest + playwright
./test.sh

# Backend only (from repo root):
cd backend && uv run ruff check app tests      # lint
cd backend && uv run ruff format app tests     # format
cd backend && uv run pytest                     # test + coverage (target: 100%)

# Frontend only:
cd frontend && bun run check                    # svelte-check
cd frontend && bun run test:coverage            # vitest
cd frontend && bun run test:e2e                 # playwright

# Dev servers (backend :8000, frontend :5173):
./start-dev.sh
```

## Architecture

Two main packages:

- **`backend/`** — FastAPI app (`app/main.py`), Python 3.14, `uv` for deps
  - `app/languages.py` — per-language plugin registry (`LanguageConfig`/`LanguageContext`)
  - `app/cards/` — vocab-card notetypes (`vocab_notetype`, `field_map`) + media-fetch pipeline (Forvo/Pixabay/EdgeTTS); no `anki` runtime dep
  - `app/plugins/anki_sync/` — optional Anki collection reading & USN sync (use `safety.safe_open`, never raw sqlite3); gated on `sync_enabled` + package presence
  - `app/plugins/languages/` — language plugins (each subfolder is self-contained: registration, preprocessor, syllabifier, audio breakdown, vocab notetype); core never imports these directly
  - `app/api/` — FastAPI route modules
  - `app/common/` — cross-cutting helpers (guid generation)
  - `app/audio/` — EdgeTTS + audio assembly pipeline
  - `app/generation/` — Curriculum + story generation
  - `app/llm/` — Groq LLM client + cassette system
  - `app/media/` — in-app media import (Anki media → TT cache)
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
cp .env.example .env      # set GROQ_API_KEY, LLM_MODE=mock for CI-safe
```

All commands use `uv run` (no manual venv activation). Never commit `.env`. Groq model: `openai/gpt-oss-120b` (free tier ~30 RPM; `LLMClient` handles 429 backoff). CI needs no API key (mock cassettes) but the backend job requires `ffmpeg`.

## Testing Quirks

- **Cassette system** (`backend/tests/cassettes/`): LLM responses recorded as JSON by prompt hash. `--llm-mode=` `mock` (default: replay, skip if missing) / `record` (call Groq, save) / `patch` (replay known, record new) / `live`. LLM tests must use cassettes — never hit the live API in tests.
- **Coverage fails at <100%** (`pyproject.toml: fail_under = 100`)
- **SRS tests**: `sqlite:///:memory:` via `srs_db` fixture
- **Anki tests**: use the `fake_anki_db*` fixtures from `conftest.py` — never a real `collection.anki2`
- **Mock-boundary check**: `./test.sh` + CI fail any `patch("app.…")` not in `backend/tests/mock_allowlist.txt` or the shrink-only `mock_grandfather.txt` — see `.claude/rules/testing.md`
- **Peer-sync tests** (`--run-peer-sync`): auto-start a throwaway `anki.syncserver`
- **CI**: four parallel jobs in `.github/workflows/ci.yml` — backend (ruff → checkers → pytest), frontend (svelte-check + vitest), oracle-parity (`pytest -m oracle --run-oracle`), peer-sync. E2E (Playwright) is local-only via `./test.sh`.

## Key Conventions

- **No hardcoded language logic** — resolve every per-language facet through the registry `app/languages.py` (`get_language` / `get_preprocessor` / … / `resolve_language_context(code, settings)`). Enforced: `scripts/check_language_literals.py` (`./test.sh` + CI) fails on language literals (`"sl"`/`"no"`, `Slovene`/`Norwegian`, `classla`/`stanza`, `*-Neural` voices) in `backend/app/**` outside allowlisted plugin modules (`tests/language_literals_allowlist.txt`; shrink-only ledger `tests/language_literals_grandfather.txt`). Rationale: `docs/language-plugin-hardening.md`.
- **No module-level side effects** — config via Pydantic Settings in `app/config.py`
- **Anki safety**: hard invariants in `.claude/rules/anki-safety-core.md` (always loaded for Claude Code; other agents read it before any Anki/SRS work); full protocol in `.claude/rules/anki-sync.md`
- **Cloze items**: set `card_type="cloze"` on the `SyntacticUnit`; PRODUCTION direction only; sync via `OfflineWriter.create_cloze_note()` against Anki's built-in Cloze notetype
- **Doc citations**: cite code as `module.py::symbol` (symbol-anchored), not bare `file:line` — line numbers rot in weeks; symbols survive refactors.

## Instruction Files (path-scoped, lazy-loaded)

Most `.claude/rules/*.md` carry `paths:` frontmatter — Claude Code auto-loads a rule when reading files it covers, keeping session startup lean (~20k tokens). A rule not appearing at session start is by design; don't "fix" it by removing the frontmatter. Non-Claude agents: read the relevant rule before working in its domain.

- `anki-safety-core.md`, `tdd.md` — always loaded (no `paths`)
- `testing.md` — mock boundaries (enforced), cassettes, test tiers, pragma discipline → `backend/tests/**`
- `frontend-coverage-gate.md` — Svelte 5 phantom-branch filter → `frontend/**`
- `anki-sync.md` — USN protocol, safety envelope, graves, migrations, card-adding-UI contract → `backend/app/plugins/anki_sync/**`, `backend/app/api/anki.py`, Anki tests
- `anki-queue-parity.md` — REQUIRED before changing SRS/queue/sync behavior or debugging any TT↔Anki divergence → `backend/app/srs/**`, `backend/app/api/srs.py`, `backend/app/plugins/anki_sync/**`, SRS/parity tests
- `anki-oracle-harness.md` — parity harness guide → `backend/tests/test_parity_*.py`, `backend/tests/anki_oracle/**`

## Hooks (`.claude/settings.json`)

- **Commit gate** (PreToolUse): `git commit` asks for confirmation unless `./test.sh` has passed on the exact current tree — `test.sh` records a tree fingerprint via `.claude/hooks/commit_gate.py --record` on success.
- **Coverage-artifact cleanup** (SessionEnd): deletes `backend/**/*.py,cover` and `backend/coverage.json` (pytest `--cov` leftovers; also gitignored).

## Critical Rules

1. **Strict TDD**: red-green-refactor (`.claude/rules/tdd.md`). Run `./test.sh` BEFORE declaring victory — never commit with failing tests or coverage failures.
2. **Ask for help when stuck**: 3+ failed attempts on the same problem → stop spinning, report what you tried, and ask the user for guidance (or escalation to a stronger model / more thinking).

## Delivering

When completing a phase or fix, the definition of done includes pasting the verification output into the report:

1. **`./test.sh` output tail** — the actual log lines showing backend/frontend/E2E all pass.
2. **CI Actions run URL** — after push, confirm all parallel jobs are green and provide the link.
3. **Commit message** — states what was verified (and how), plus any non-obvious mechanism or diagnostic signature that would help the next person debugging this class of bug.

This convention exists because "Done" with no output was the gap in both Phase 3 and Phase 5 — the fix was correct, but the acceptance evidence was missing.
