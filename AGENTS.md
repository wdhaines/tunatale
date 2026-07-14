# AGENTS.md — TunaTale

AI-generated audio language curricula — Pimsleur-style listening with content adapted to the learner's vocabulary. Slovene and Norwegian are wired end-to-end (Slovene most completely); the architecture is language-plugin based. Integrates bidirectionally with the user's Anki deck rather than replacing it. See `README.md` for the product pitch and `docs/walkthrough.md` for the system tour.

## Developer Commands

**⚠️ Must run `./test.sh` before every commit — the full suite must pass, or you DO NOT commit.**

```bash
# Full suite (root): lint + format + pytest + svelte-check + vitest + playwright
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
  - Cloze tests use `_make_cloze_collection_conn()` or `_make_dual_collection_conn()` helpers from `test_anki_sync_create_new.py`
- **E2E tests**: `test_e2e_listen_to_sync.py` combines `/listen` API calls with offline Anki sync
- **Mock-boundary check**: `./test.sh` and CI fail any `patch("app.…")` not in `backend/tests/mock_allowlist.txt` (true process/network boundaries) or the shrink-only `mock_grandfather.txt` — see `.claude/rules/testing.md` "Mock Boundaries"
- **Peer-sync tests** (`--run-peer-sync`): auto-start a throwaway `anki.syncserver` (no manual server, zero skips); also a dedicated CI job
- **CI runs four parallel jobs** in `.github/workflows/ci.yml`; frontend job runs svelte-check + vitest; E2E (Playwright) is local-only
- CI requires `ffmpeg` as system dependency (backend job only)

## Key Conventions

- **No hardcoded language logic** — resolve every per-language facet through the registry `app/languages.py` (`get_language` / `get_preprocessor` / `get_deck_name` / `get_tts_voice` / `get_lemmatizer_type` / `get_vocab_notetype`, or the bundled `resolve_language_context(code, settings) → LanguageContext`). **Enforced**: `scripts/check_language_literals.py` (in `./test.sh` + the CI backend job) fails on a hardcoded language literal (`"sl"`/`"no"`, `Slovene`/`Norwegian`, `classla`/`stanza`, `*-Neural` voices) in `backend/app/**` outside the allowlisted plugin modules (`tests/language_literals_allowlist.txt`). Adding one means routing it through the registry, or recording it in the shrink-only ledger `tests/language_literals_grandfather.txt`. Rationale + remaining seams: `docs/language-plugin-hardening.md`.
- **No module-level side effects** — config via Pydantic Settings in `app/config.py`
- **LLM tests must use cassettes** — never hit live API in tests
- **Anki writes**: always use `app.plugins.anki_sync.safety.safe_open()` — handles lock probe, SHA256 backup, integrity check
- **Anki mutations**: always set `usn=-1`, `mod=now_ts` on touched rows, and update `col` — see `.claude/rules/anki-sync.md`
- **Cloze items** (Phase F): set `card_type="cloze"` on the `SyntacticUnit`; only produce PRODUCTION direction; sync uses `OfflineWriter.create_cloze_note()` targeting Anki's built-in Cloze notetype

## CI Order

**Backend job** (parallel): `ruff check → ruff format --check → mock-boundary check → pytest`

**Frontend job** (parallel): `bun install → svelte-check → vitest`

**Oracle-parity job** (parallel): `setup-uv → uv sync → warm anki env → pytest -m oracle --run-oracle -n auto --no-cov`

**Peer-sync job** (parallel): `setup-uv → uv sync → warm anki env → pytest tests/test_anki_peer_sync_selfhost.py --run-peer-sync --no-cov -v`

All four run in parallel on push/PR. E2E (Playwright) is local-only via `./test.sh`.

## Instruction Files

- `.claude/rules/testing.md` — cassette system, mock boundaries (enforced), test tiers
- `.claude/rules/tdd.md` — red-green-refactor workflow, step ordering
- `.claude/rules/environment.md` — secrets, venv, Groq setup, rate limits
- `.claude/rules/anki-sync.md` — USN protocol, safety envelope, migration rules

## Anki Deck Setup (Phase C)

**Requires one-time user action**: Open target deck's options → **Display Order → New card gather order** → set to **"Descending position"**.

Without this, Anki's default "Ascending position" surfaces oldest-first, defeating the Anki-side recency ordering. TT-side recency works regardless (newest cards appear first in `/review`), but sync will not reflect it. Recoverable any time by flipping the setting.

## Critical Rules

1. **Strict TDD**: Always follow red-green-refactor. Run `./test.sh` BEFORE declaring victory — never commit with failing tests or coverage failures. Verify all changes with `./test.sh` before committing.

2. **Ask for help when stuck**: If you're going in circles or stuck, proactively ask for advice from a stronger model (Claude Opus 4.7 with max thinking) rather than spinning endlessly.

## Delivering

When completing a phase or fix, the definition of done includes pasting the verification output into the report:

1. **`./test.sh` output tail** — the actual log lines showing backend/frontend/E2E all pass.
2. **CI Actions run URL** — after push, confirm all parallel jobs are green and provide the link.
3. **Commit message** — states what was verified (and how), plus any non-obvious mechanism or diagnostic signature that would help the next person debugging this class of bug.

This convention exists because "Done" with no output was the gap in both Phase 3 and Phase 5 — the fix was correct, but the acceptance evidence was missing.

## Rules (from ~/.claude/CLAUDE.md)

- Always run tests using the virtual environment; don't just theorize
- Add tests to the tests directory, not other random directories.

## Housekeeping

- **Clean coverage artifacts after running pytest with --cov.** The `--cov` flag generates `*.py,cover` files and `coverage.json`. Run `find backend -name '*.py,cover' -delete && rm -f backend/coverage.json` after each session, or use `--cov-report=term` (no file output). These are now in `.gitignore` but pre-existing files won't disappear.
