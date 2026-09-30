# AGENTS.md — TunaTale

AI-generated audio language curricula — Pimsleur-style listening with content adapted to the learner's vocabulary. The architecture is language-plugin based: Slovene and Norwegian are wired end-to-end (Slovene most completely), and Tagalog and Cebuano have plugins under `backend/app/plugins/languages/`. Integrates bidirectionally with the user's Anki deck rather than replacing it. See `README.md` for the product pitch and `docs/walkthrough.md` for the system tour.

## Developer Commands

Run `./test.sh` before every commit; the full suite must pass or you do not commit. A commit-gate hook enforces this.

```bash
# Full suite (root): lint + format + checkers + pytest + svelte-check + vitest + playwright + peer-sync
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

**How to run the gate.** Use the absolute path, make the gate the last statement of the command, and treat the log as the only evidence:

```bash
/Users/wdhaines/CascadeProjects/tunatale/test.sh > /tmp/gate.txt 2>&1
```

Anything after the gate — an `echo $?`, even on its own line — replaces its exit status, and a relative path silently misses when an earlier `cd` persisted; both have produced fictional greens here. In the log, require `=== All checks passed ===` (the failure form is `=== FAILED (backend=N frontend=N peer_sync=N) ===`), 100.00% backend coverage, and a ruff `N files already formatted` count no lower than in the previous run's log (a drop means discovery broke; `.git/tt-test-history.log` records exit codes and timings, not this count). Never pipe `./test.sh` (a hook denies it), and never `cd` away from the repo in a session that will run the gate.

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
- **Mock-boundary check**: `./test.sh` + CI fail any `patch("app.…")` not in `backend/tests/mock_allowlist.txt`. There is no grandfather ledger; the allowlist is the only escape hatch and additions need the user's sign-off. See `.claude/rules/testing.md`.
- **Peer-sync tests** (`--run-peer-sync`) auto-start a throwaway `anki.syncserver` and run as a third parallel group in `./test.sh`.
- **CI is authoritative; `./test.sh` is a strict subset of it.** Green locally is necessary, not sufficient. If you add a check to `test.sh`, add it to `.github/workflows/ci.yml` in the same commit (the reverse is not required). The clock/offset jobs — `backend-hostile-tz`, `backend-hostile-hour`, and `anki-gates` at the 04:00 rollover — are the only CI-only checks. If `anki-gates` is red at the boundary while `backend` is green, suspect product code first.
- **CI installs lean** (`uv sync --no-default-groups --group dev`), so a test may import only what the `dev` group declares. Anything that arrives transitively through the language groups (e.g. `yaml` via transformers) passes locally and fails all four backend jobs with `ModuleNotFoundError`. Declare it in `dev`; never widen CI's groups.

CI job layout and its rationale: `.claude/rules/gate-and-ci.md`. The local/CI asymmetries: `.claude/rules/testing.md` § "What a green gate means".

## Paid vendors — price a render before running it

- The Azure Speech resource is **F0 (free tier)**, read from `sku.name`. Standard and Multilingual Neural characters draw on a 500K/month allowance; exceeding it throttles rather than bills.
- **Azure Neural HD (Dragon) voices are ruled out** (the user's call). HD bills from the first character even on F0. Enforced by `test_languages.py::test_no_voice_map_names_a_paid_hd_voice`.
- **Before any TTS render, price it in billable characters** with `backend/scripts/report_render_cost.py` and put the number in your report. Quote the incremental figure for "what will this run cost" and say which figure you quote; cold and incremental have differed ~7x. There is no usable Azure-side character meter, so this local estimate is the only instrument.
- Never print a key; compare keys by hash.

Measurements, commands, and the wrong claims these replaced: `.claude/rules/paid-vendors.md`.

## Key Conventions

- **No hardcoded language logic** — resolve every per-language facet through the registry `app/languages.py` (`get_language` / `get_preprocessor` / … / `resolve_language_context(code, settings)`). `scripts/check_language_literals.py` (`./test.sh` + CI) fails on language literals (`"sl"`/`"no"`, `Slovene`/`Norwegian`, `classla`/`stanza`, `*-Neural` voices) in `backend/app/**` outside the plugin modules allowlisted in `tests/language_literals_allowlist.txt`. There is no grandfather ledger. Rationale: `docs/language-plugin-hardening.md`.
- **API contract drift** — backend→frontend type safety via a committed OpenAPI schema. `scripts/dump_openapi.py` writes `frontend/src/lib/api-schema.json`; `scripts/check_openapi_snapshot.py` enforces snapshot freshness and that every 2xx JSON response declares a `response_model=` (no escape hatch). The frontend derives types via `openapi-typescript`; `bun run check:api` catches stale types. Fix commands: `uv run python scripts/dump_openapi.py` (backend), `bun run gen:api` (frontend).
- **No module-level side effects** — config via Pydantic Settings in `app/config.py`
- **Anki safety**: hard invariants in `.claude/rules/anki-safety-core.md` (always loaded for Claude Code; other agents read it before any Anki/SRS work); full protocol in `.claude/rules/anki-sync.md`
- **Cloze items**: set `card_type="cloze"` on the `SyntacticUnit`; PRODUCTION direction only; sync via `OfflineWriter.create_cloze_note()` against Anki's built-in Cloze notetype
- **Doc citations**: cite code as `module.py::symbol` (symbol-anchored), not bare `file:line` — line numbers rot in weeks; symbols survive refactors.

## Instruction Files (path-scoped, lazy-loaded)

Most `.claude/rules/*.md` carry `paths:` frontmatter, so Claude Code loads a rule only when it reads a file the rule covers. A rule missing at session start is by design; don't "fix" it by removing the frontmatter. Non-Claude agents: read the relevant rule before working in its domain.

- `anki-safety-core.md`, `tdd.md` — always loaded (no `paths`)
- `testing.md` — mock boundaries (enforced), cassettes, where tests run, pragma discipline → `backend/tests/**`, `test.sh`
- `test-tiers.md` — what a test is ABOUT: the seam discriminator (is the value engine-computed or app-computed?), no fourth tier, and the sabotage-drill retirement criterion → `frontend/tests/**`, `frontend/src/**`, `backend/tests/**`
- `frontend-coverage-gate.md` — Svelte 5 phantom-branch filter → `frontend/**`
- `gate-and-ci.md` — CI job layout, hook mechanics, and the incidents behind the gate rules → `test.sh`, `.github/**`, `.claude/hooks/**`
- `paid-vendors.md` — Azure Speech tier, HD-voice ban, render pricing → TTS/audio code and render scripts
- `anki-sync.md` — USN protocol, safety envelope, graves, migrations, card-adding-UI contract → `backend/app/plugins/anki_sync/**`, `backend/app/api/anki.py`, Anki tests
- `anki-queue-parity.md` — REQUIRED before changing SRS/queue/sync behavior or debugging any TT↔Anki divergence → `backend/app/srs/**`, `backend/app/api/srs.py`, `backend/app/plugins/anki_sync/**`, SRS/parity tests
- `anki-oracle-harness.md` — parity harness guide → `backend/tests/test_parity_*.py`, `backend/tests/anki_oracle/**`
- Skill `beads` (`.claude/skills/beads/`) — bd conventions and traps; load it before bd work (see below)

## Hooks (`.claude/settings.json`)

- **Commit gate** (PreToolUse): `git commit` asks for confirmation unless `./test.sh` passed on the exact current tree; a failing run deletes the recorded fingerprint. Do not click past that prompt.
- **Pipe guard** (PreToolUse): denies piping `./test.sh`. Every run is teed to `.git/tt-test-last.log`, and each step appends a line to `.git/tt-test-history.log` (TSV; its tree-id column separates a flake from a fix).
- **Submodule-pointer auto-stage** (PreToolUse): stages the `.beads-tasks` pointer onto any commit that already carries other content. Nothing to remember; it never blocks and does not change the gate fingerprint.
- **Agent mail and claims** (SessionStart): lists the newest unread mail and the beads in progress with their claimants. **Coverage-artifact cleanup** (SessionEnd).

Mechanics and history: `.claude/rules/gate-and-ci.md`.

## Critical Rules

1. **Strict TDD**: red-green-refactor (`.claude/rules/tdd.md`). Run `./test.sh` BEFORE declaring victory — never commit with failing tests or coverage failures.
2. **Ask for help when stuck**: 3+ failed attempts on the same problem → stop spinning, report what you tried, and ask the user for guidance (or escalation to a stronger model / more thinking).

## Delivering

When completing a phase or fix, the definition of done includes pasting the verification output into the report:

1. **`./test.sh` output tail** — the actual log lines showing backend/frontend/E2E all pass.
2. **CI Actions run URL** — after push, confirm all parallel jobs are green and provide the link.
3. **Commit message** — states what was verified (and how), plus any non-obvious mechanism or diagnostic signature that would help the next person debugging this class of bug.

## Delegation — BP is the default executor

Orchestrator tokens are the scarce resource; Big Pickle's (the free executor, via the `bp-delegate` skill) are not. Delegating mechanical work is the default, and doing a multi-file mechanical edit inline should have a reason you could state.

- **Delegate** work whose hard part is typing rather than deciding: multi-file mechanical edits, test additions against a pinned oracle, doc sweeps, ledger burn-downs, renames and refactors with a mechanical rule. New artifacts qualify too when they have ≥2 in-repo siblings to pattern-match and the oracle is a measured literal table you produced (reference shape: a `scripts/check_*.py` plus its test and its `test.sh`/`ci.yml` wiring, `tunatale-7330`). Widening the work tightens the audit: surviving defects cluster between the cases a brief enumerates.
- **Never delegate:** anything touching Anki/SRS/sync semantics; oracle design (deciding what would falsify a claim — executing a supplied oracle is fine); the final `./test.sh` gate, the audit of the returned diff, and the merge decision.
- **Threshold:** if writing the brief costs more than doing the work, do the work. The brief is the expensive artifact and the executor is swappable (haiku, sonnet, or SWE via `swe-delegate` when BP's quota is out), so the question is whether the work is brief-able, not whether BP is available.
- Check `git status` before briefing, so you never delegate finished work. Batch related tasks into one brief, since every dispatch costs a brief plus an audit.

## Committing, Pushing, and Merging

Committing and pushing are standing-authorized; merging into `main` is the checkpoint the user watches. The repo is public, so the merge is the irreversible, world-visible step.

- **Small and self-contained** (docs, a settings field, a one-module fix, a test addition): commit to `main` and push without asking.
- **Substantial** (anything touching Anki/SRS/sync, spanning several modules, or whose blast radius you would have to think about): branch and open a PR, then stop. The user approves the merge. A run of direct-to-`main` commits on work that should have been branched means this checkpoint has silently stopped existing.
- **If it feels risky for any reason you can name, ask**, even when it is small.
- **Don't stack a PR on another open PR's branch.** Merging the parent deletes the base branch, and GitHub closes the child PR for good (#103 → #104). Base the second PR on `main` and state the merge order in its body, or keep going on the same branch.
- Executors (BP et al.) leave their work uncommitted by default (`.beads-tasks/DISPATCH-PREAMBLE.md`). The orchestrator may let one commit on its own branch for mechanical work — never for Anki/SRS/sync.
- Always: `./test.sh` green on the exact tree before every commit, never amend an audited commit, and beads sync stays standing-authorized.

## Issue Tracking — bd (beads)

The backlog and its dependency ordering live in bd; `bd ready --exclude-type=epic` lists unblocked work. **Load the `beads` skill before any bd write, sync, mail, or brief** — it holds this repo's conventions and these traps in full:

- Plain `bd show` mangles code; always `bd show <id> --json | jq -r '.[0].description'`.
- `bd dep add <child> <parent>` and `bd create --deps blocks:<id>` point in opposite directions. Verify every edge right after wiring it.
- A wrong JSON field name returns a clean negative, not an error (the field is `parent`, not `parent_id`).
- **Claim a bead when you start it, as your location:** `bd update <id> --claim --actor "$(basename "$PWD")@$(git branch --show-current)"`. Every session shares one git identity, so a bare `--claim` can't tell sessions apart; the `worktree@branch` actor says who holds the bead and where the work lives. A claim held by another actor is refused, and that refusal is the coordination. Check `bd list --status in_progress` before starting work, and close what you claimed when it merges.
- After a `bd create` / `close` / `dep add` batch, run `./.beads-tasks/sync.sh` without asking — but not while another agent holds uncommitted work, because it commits and pushes.
- Closing a bead is not authorization to commit.
