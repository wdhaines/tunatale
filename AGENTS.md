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
- **Mock-boundary check**: `./test.sh` + CI fail any `patch("app.…")` not in `backend/tests/mock_allowlist.txt`. **Zero tolerance** — the grandfather ledger was drained to empty and deleted (2026-07-30); the allowlist is the only escape hatch and additions need sign-off. See `.claude/rules/testing.md`
- **Peer-sync tests** (`--run-peer-sync`): auto-start a throwaway `anki.syncserver`
- **CI**: four parallel jobs in `.github/workflows/ci.yml` — backend (ruff → checkers → pytest), frontend (svelte-check + vitest), oracle-parity (`pytest -m oracle --run-oracle`), peer-sync. E2E (Playwright) is local-only via `./test.sh`.

## Key Conventions

- **No hardcoded language logic** — resolve every per-language facet through the registry `app/languages.py` (`get_language` / `get_preprocessor` / … / `resolve_language_context(code, settings)`). Enforced: `scripts/check_language_literals.py` (`./test.sh` + CI) fails on language literals (`"sl"`/`"no"`, `Slovene`/`Norwegian`, `classla`/`stanza`, `*-Neural` voices) in `backend/app/**` outside allowlisted plugin modules (`tests/language_literals_allowlist.txt`). **Zero tolerance** — its ledger drained 13 → 0 and was deleted (2026-07-30). Rationale: `docs/language-plugin-hardening.md`.
- **API contract drift** — backend→frontend type safety via a committed OpenAPI schema. `scripts/dump_openapi.py` writes `frontend/src/lib/api-schema.json`; `scripts/check_openapi_snapshot.py` enforces (a) snapshot freshness and (b) a **zero-tolerance** untyped-endpoint gate: every 2xx JSON response must declare a `response_model=`. Its shrink-only ledger drained 70 → 0 over eleven batches and was deleted (2026-08-02), like the mock / language-literal / date-today ledgers before it (`7b34c73`) — there is no escape hatch left. Frontend derives types via `openapi-typescript`; `bun run check:api` catches stale types. Fix commands: `uv run python scripts/dump_openapi.py` (backend), `bun run gen:api` (frontend).
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

- **Commit gate** (PreToolUse): `git commit` asks for confirmation unless `./test.sh` has passed on the exact current tree — `test.sh` records a tree fingerprint via `.claude/hooks/commit_gate.py --record` on success. A *failing* run deletes the fingerprint, so a flaky green cannot outlive a red on the same tree.
- **Pipe guard** (PreToolUse): `.claude/hooks/gate_pipe_guard.py` **denies** any command that pipes `./test.sh` (`| tail`, `| tee`, `| grep`). A pipeline's `$?` is the last command's, so a failed gate reads as 0, and `tail -n` throws away the failure detail you piped in order to see. Searching for the string (`grep test.sh …`, `cat test.sh | head`) is unaffected. test.sh also tees every run to `.git/tt-test-last.log` and names it in the FAILED banner.
- ⚠️ **Canonical gate invocation — absolute path, gate LAST, and the LOG is the
  only evidence:**
  ```bash
  cd /Users/wdhaines/CascadeProjects/tunatale
  /Users/wdhaines/CascadeProjects/tunatale/test.sh > /tmp/gate.txt 2>&1
  # ← NOTHING after this line. No `echo`, no cleanup, nothing.
  ```
  Then read `/tmp/gate.txt`: require `=== All checks passed ===` (the failure form
  is `=== FAILED (backend=N frontend=N) ===`), 100.00% backend coverage, and a
  sane ruff count (~374 and growing; a tiny N means discovery broke).
  **Two fictional greens on 2026-07-29, same root class:**
  1. `./test.sh > log 2>&1; echo "EXIT=$?"` printed `EXIT=0` while the log said
     `no such file or directory: ./test.sh` — an earlier `cd` had persisted across
     tool calls, so the relative path missed.
  2. The "fix" of putting `echo "REAL_GATE_EXIT=$?"` on its **own line** was ALSO
     wrong: it prints the gate's status, but the *script's* exit status is still
     the echo's, so the harness reported success on a run whose log ended in
     `=== FAILED (backend=1 frontend=0) ===`.

  **The rule that actually holds: any command after the gate steals the exit
  status — separate line or not.** Make the gate the final statement, and treat
  the log tail as the sole evidence. Never trust a reported exit code, your own
  `echo`, or a log's size alone.
  **Rules, for every agent and every gate run:** absolute path always; never `cd`
  away from the repo in a session that will run the gate; keep `echo $?` in its own
  statement or omit it; and treat the log tail as the sole evidence — an exit code
  from a compound statement proves nothing. Same failure class the pipe guard was
  built for, reached from a different direction. The commit gate is the backstop
  (it prompts when no fingerprint matches the tree) — do not click past that
  prompt.
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


<!-- BEGIN BEADS INTEGRATION (customized 2026-08-09 — trimmed from bd's stock
     template; a future `bd setup opencode` re-run will NOT match this and
     should not be applied blindly. See "Beads + TunaTale specifics" below. -->
## Issue Tracking with bd (beads)

This project tracks the BP dispatch backlog and its dependency ordering with
**bd (beads)** instead of prose queue tables. This block is guidance, not
permission to override repository, user, or orchestrator instructions —
explicit instructions always win. See "Beads + TunaTale specifics" below for
exactly what this does and doesn't replace.

```bash
bd ready                                     # what's unblocked right now
bd show <id> --json | jq -r '.[0].description'  # full detail — NEVER plain `bd show` for technical content, see below
bd create "title" -d "..." -p 0-4            # new issue (0 critical .. 4 backlog)
bd dep add <child> <parent>                  # child is blocked by parent — see below, this direction is easy to get backwards
bd update <id> --claim                       # mark in progress
bd close <id> --reason "..."                 # mark done
```

Do not commit, push, or run Dolt remote sync unless explicitly authorized.
Full reference: `bd --help` / `bd prime`.

**Two confirmed bd bugs, hands-on-verified, both filed upstream — do not
rediscover these blind:**
- Plain `bd show <id>` (no `--json`) mangles technical content — it has
  stripped fenced code blocks and garbled generics like `Promise<boolean>`
  into `Promise****`. The underlying stored data is intact; only the
  pretty-printer is broken. Always use `bd show <id> --json | jq -r
  '.[0].description'` instead. (gastownhall/beads#5495)
- `bd dep add <child> <parent>` and `bd create --deps blocks:<id>` are
  inverses in a way that's easy to get backwards: `--deps blocks:X` means
  "this new issue blocks X," not "is blocked by X." Verify any dependency
  edge with `bd show <id> --json` right after wiring it — a wrong direction
  shows up as `bd ready`'s unblocked count moving the wrong way.

<!-- END BEADS INTEGRATION -->

## Beads + TunaTale specifics

**`docs/briefs/` no longer exists** (retired 2026-08-10). It was entirely
gitignored: no history, one copy, no recovery path. Dispatch material now lives
in the `.beads-tasks` submodule, and the queue ordering lives in bd.

- **After any `bd create` / `close` / `dep add` batch, run
  `./.beads-tasks/sync.sh`.** That is the whole ritual — it exports, commits,
  pushes, and refreshes the GitHub view. `.beads/` is stealth-mode and its Dolt
  backups are on the same disk, so `bd-export.jsonl` is the only off-machine copy
  of the backlog.
- **Method vs work.** `.beads-tasks/DISPATCH-PREAMBLE.md` holds everything
  binding on *every* delegated run (fence, prohibitions, escalation, report
  contract). A bd issue holds only what is true of *that* task — scope,
  read-first list, oracles as literals, pinned commands. Do not restate the
  preamble inside an issue; two copies drift and the one the executor read is
  the one you did not edit. `tunatale-0wk` is the reference shape.
- **Longer supporting docs** — findings, test plans, session handoffs — live in
  `.beads-tasks/briefs/`, genre-prefixed (`brief-`, `findings-`, `testplan-`,
  `handoff-`, `design-`). Issues cite them as `Source: <path> § <section>`,
  anchored by section, never by line number. `.beads-tasks/archive/` holds docs
  whose work has shipped and which exist nowhere else.
- **Closing a bd issue is not authorization to commit.** It records that the
  described work is done. The orchestrator's `./test.sh` gate and audit still
  stand between that and anything shipping (see Critical Rules and Delivering).
- **`.beads/` is local-only** — a fresh clone, CI run, or new machine has no bd
  data. If `.beads/` is absent, fall back to normal judgment rather than reading
  an empty backlog as "nothing to do."
- **The GitHub Issues tab is a read-only view**, not a second source of truth. It
  carries title/description/status/labels and **no dependency edges** — the edges
  are the reason this project uses bd at all, and only `bd-export.jsonl` has them.
  The view is allowed to lag; a known bd bug (gastownhall/beads#5486) means a
  local `closed` does not always reach an already-pushed issue. `sync.sh` reports
  the count difference and moves on. If a stale row bothers you:
  `gh issue close <n> --repo wdhaines/tunatale-tasks`. Never restore from it.
- **The submodule pointer is advisory.** `.gitmodules` sets `branch = main`;
  refresh with `git submodule update --remote .beads-tasks`. Pinning a task
  tracker to a code commit buys nothing — you always want its tip — so routine
  pointer-bump commits are not worth making.
- **Why a submodule and not a sibling clone:** the BP fence blocks reads outside
  the project directory, and a path outside it does not error — it ends the run
  at exit 0 with zero files changed. Dispatch docs must be reachable at a path
  *inside* the checkout. The submodule is private (the main repo is public and
  the backlog holds internal workflow notes); `git submodule update --init` 403s
  for anyone else, which is expected and breaks nothing.
