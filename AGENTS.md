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
- **Submodule-pointer auto-stage** (PreToolUse): `.claude/hooks/stage_submodule_pointer.py` stages `.beads-tasks` onto any `git commit` that already carries other content, so the "pointer rides code commits" rule needs no reader. Guards (all fail open — it can never block a commit): initialised submodule, actually drifted, HEAD contained in a remote-tracking branch, not `--dry-run`, and something else already going in. **It is order-independent with the commit gate**: `ignore = all` keeps the gitlink out of `git diff HEAD --name-only`, which is what the gate fingerprints, so staging it cannot invalidate a green `./test.sh` (verified 2026-08-10 — byte-identical fingerprint before and after).
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

## Delegation and cost — BP is the default executor

**Orchestrator tokens are the scarce resource; BP's are free.** Delegating
mechanical work to Big Pickle (the free Sonnet-class executor, via the
`bp-delegate` skill) is the DEFAULT, not an optimization to remember. Doing a
multi-file mechanical edit inline is the exception, and it should have a reason
you could state.

**Delegate:** multi-file mechanical edits, test additions against a pinned
oracle, doc sweeps, ledger burn-downs, renames/refactors with a mechanical rule
— anything whose hard part is typing rather than deciding.

**Do NOT delegate, regardless of cost:**
- Anything touching Anki / SRS / sync semantics (`.claude/rules/anki-safety-core.md`).
- Oracle **design** — deciding what would falsify a claim. Executing a supplied
  oracle is fine; choosing it is not.
- The final `./test.sh` gate, the audit of BP's diff, and the merge decision.
  Those are what the orchestrator is *for*.

**The economics that actually govern this:** the written brief is the expensive
artifact and the executor is swappable — when BP's quota is out, the same brief
runs on haiku or sonnet. So the question is never "is BP available", it is "is
this work brief-able".

**The threshold, stated so it is not re-litigated every session:** if writing
the brief would cost more than doing the work, do the work. A three-line fix you
already have full context on is not worth a brief. A twenty-file sweep is — and
*especially* then, because that is the shape that burns orchestrator context for
no judgement.

**Do not delegate work that is already finished.** The failure mode is not
stupidity, it is timing: a "use BP" instruction arriving mid-task tempts you to
dispatch something you just completed. Check `git status` before briefing.

**Batch, because delegation is not free either.** Every dispatch costs a brief
plus an audit of the returned diff. Three related tasks in one brief cost one
audit; three separate dispatches cost three.

## Committing, Pushing, and Merging

**Committing is standing-authorized; merging into `main` is the checkpoint the
user babysits** (2026-08-13). Commit and push freely under the rules below — the
`./test.sh` gate, not a permission prompt, is what stands between a change and
history.

**Route by weight, and the routing is a judgement call, not a checklist:**

- **Small and self-contained** — docs, a settings field, a one-module fix, a test
  addition — goes straight to `main` and is pushed without asking.
- **Substantial** — anything touching Anki/SRS/sync, anything spanning several
  modules, anything whose blast radius you would have to think about — goes on a
  branch and opens a PR. **Stop at the open PR.** The user approves the merge.
- **When it feels risky for any reason you can name, ask** — even if it is small
  by the rule above. Latitude to route was granted explicitly; latitude to skip
  the gate was not.

**Why the checkpoint is the merge and not the commit:** a local commit is cheap
and revertible, and gating it bought a round-trip per change without buying
safety — the same reasoning `f884e52` applied to beads sync. A merge into `main`
on a **public** repo is the irreversible, world-visible step, so that is where
the human belongs.

⚠️ **This only works if branches exist.** From 2026-08-12 to 2026-08-13 every
commit landed directly on `main` with no branch and no PR, which silently
emptied this checkpoint of meaning — a merge gate cannot fire when nothing
merges. If you notice a run of direct-to-`main` commits on work that was
supposed to be branched, that is the failure mode, not a shortcut that worked.

**Weak-model executors (BP et al.) committing their own work is the
orchestrator's call**, case by case. `.beads-tasks/DISPATCH-PREAMBLE.md` still
says leave it uncommitted, and that remains the safe default: auditing a
working-tree diff is easier than auditing history you are forbidden to amend.
Let an executor commit only on its own branch, only when the work is mechanical
enough that the audit is a formality — and never for Anki/SRS/sync changes.

Unchanged by any of this: `./test.sh` green on the exact tree before every
commit, never amend an audited commit, and beads sync stays standing-authorized.

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

**Beads sync is standing-authorized — just run it, do not ask** (2026-08-10).
After any `bd create` / `close` / `dep add` batch, run `./.beads-tasks/sync.sh`
as a matter of course. It only ever touches the private tasks repo, and the
alternative is a backlog that lives on one disk. An earlier version of this line
lumped sync in with code commits, so every session stopped to ask permission for
what is really just the tail of a bd edit.

Committing and pushing **this** repo's code is also standing-authorized as of
2026-08-13 — the gate moved to merges into `main`. See "Committing, Pushing, and
Merging" above; do not re-derive the policy from this line.
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

**`docs/briefs/` is retired from the workflow** (2026-08-10). It was entirely
gitignored: no history, one copy, no recovery path. Dispatch material now lives
in the `.beads-tasks` submodule, and the queue ordering lives in bd. A gitignored
local safety copy remains, rebuilt from bd + the submodule and kept until the new
setup proves out — see `docs/briefs/README.md`. It holds no unique content; do
not author anything new there.

- **After any `bd create` / `close` / `dep add` batch, run
  `./.beads-tasks/sync.sh`.** That is the whole ritual — it pushes the Dolt store,
  exports, commits, pushes, and refreshes the GitHub view. `.beads/` is
  stealth-mode and its Dolt backups are on the same disk, so nothing leaves this
  machine until that script runs.
- **The backlog syncs git-natively as of 2026-08-10** (`tunatale-pjb`). The store
  travels as the hidden ref `refs/dolt/data` on the **private** tunatale-tasks
  repo — full Dolt history, not a snapshot. `bd-export.jsonl` is now a *secondary*
  human-diffable copy, kept as a fallback; `bd export --help` states outright it
  "is not a full database backup". Restore from the Dolt ref, not the JSONL.
  - **The Dolt remote is deliberately NOT this repo's origin.** `.beads/` sits at
    the parent root whose origin is the PUBLIC repo; bd's Dolt remote is an
    independent URL, pointed at the private one. **A hidden ref is unlisted, not
    private** — anything on a public remote is world-fetchable. `sync.sh` refuses
    to push if the remote ever stops containing `tunatale-tasks`; do not "fix"
    that guard by relaxing it.
  - Ordinary use needs no new commands: `bd dolt push` / `bd dolt pull` are
    wrapped by `sync.sh`. Stealth mode (`no-git-ops: true`) stays on and does not
    block explicit pushes.
- **Agent mail lives in bd** (`./.beads-tasks/mail.sh`), for talking to another
  Claude session working this repo. Borrowed from Gas City, where mail is
  literally beads with `type=message`.
  ```bash
  ./.beads-tasks/mail.sh inbox            # unread addressed to you
  ./.beads-tasks/mail.sh read <id>        # print it, mark read
  MAIL_ID=orch ./.beads-tasks/mail.sh send peer "subject" "body"
  ```
  `unread == open`, `read == closed`, addressing is a `to-*` label. Message beads
  are excluded from `bd list` / `bd ready` / `GRAPH.md`, and `sync.sh` filters
  them out of the GitHub mirror — verified, they cannot be mistaken for backlog.
  A `SessionStart` hook surfaces unread mail automatically; without it a session
  never looks, which is exactly what happened on 2026-08-10 (a peer completed a
  whole stage with two messages waiting).
  ⚠️ **Mail is NOT in `bd-export.jsonl` and NOT in the Dolt push** — it is the one
  thing here with no off-machine copy. Anything that must survive belongs in an
  issue, not a message.
- **Related issues get an epic — do not leave siblings loose at the top level**
  (2026-08-12). When one investigation or theme produces **more than two** issues,
  create an `--type epic` and hang them off it with `--parent`. Retrofit it the
  moment you notice you are creating the third; `bd update <id> --parent <epic>`
  reparents an existing issue, so there is no cost to doing it late and no excuse
  for not doing it at all.

  Why it is a rule and not a preference: `bd ready` sorts by priority across the
  WHOLE backlog, so loose siblings scatter — three P2s from one theme land in
  three different places, separated by unrelated P1s, and the reader has no way
  to see they are one piece of work. The epic is also the only place the
  *through-line* can live; a child issue can state its own scope but not why the
  set exists. `tunatale-vnf` is the reference shape: five children, and the
  paragraph explaining that the suite has grown by accretion and never shrunk is
  in the epic, stated once, rather than copy-pasted into five descriptions where
  it would drift.

  The epic carries the theme, the ordering rationale, and anything explicitly
  OUT of scope (the cheapest place to stop a well-meaning executor from
  "helpfully" widening the work). It does not restate the children.
- **Method vs work.** `.beads-tasks/DISPATCH-PREAMBLE.md` holds everything
  binding on *every* delegated run (fence, prohibitions, escalation, report
  contract). A bd issue holds only what is true of *that* task — scope,
  read-first list, oracles as literals, pinned commands. Do not restate the
  preamble inside an issue; two copies drift and the one the executor read is
  the one you did not edit.
- **Short work inline, long briefs as files.** A screenful goes in the issue
  description. Anything longer, or carrying a big oracle table, goes in
  `.beads-tasks/briefs/` with the issue holding scope + a `Source:` pointer +
  the decisive oracles — **never both**. `tunatale-0wk` is the reference shape.
  Authoring in throwaway `docs/briefs/*.md` scratch is fine; what killed the old
  setup was gitignored *permanence*, not files. Editing a bd description has no
  `git diff`, so a contradiction introduced mid-edit is invisible — that cost is
  what sets the threshold (one shipped in a live brief on 2026-08-10).
- **Longer supporting docs** — findings, test plans, session handoffs — live in
  `.beads-tasks/briefs/`, genre-prefixed (`brief-`, `findings-`, `testplan-`,
  `handoff-`, `design-`). Issues cite them as `Source: <path> § <section>`,
  anchored by section, never by line number. `.beads-tasks/archive/` holds docs
  whose work has shipped and which exist nowhere else.
- **Closing a bd issue is not authorization to commit.** It records that the
  described work is done. The orchestrator's `./test.sh` gate and audit still
  stand between that and anything shipping (see Critical Rules and Delivering).
- **A fresh clone has no bd data — run `./beads-bootstrap.sh` to get it.** One
  command; verified end-to-end on 2026-08-10 (16 open, both dependency edges).
  This is needed because a default `git clone` fetches only `refs/heads/*` and
  `refs/tags/*`, so the hidden data ref never comes along, and bd's own
  auto-detection looks at *this* repo's origin — the wrong repo, on purpose.
  The script supplies the missing pointer and calls `bd bootstrap`.
  - It needs read access to the private tasks repo. Without it the clone step
    fails, which is expected, not a bug.
  - **An empty backlog after bootstrap is a red flag, not "nothing to do."** The
    script exits non-zero on 0 open issues for exactly that reason. If `.beads/`
    is simply absent and you cannot bootstrap, fall back to normal judgment.
- **The GitHub Issues tab is a read-only view**, not a second source of truth. It
  carries title/description/status/labels and **no dependency edges** — the edges
  are the reason this project uses bd at all, and only the Dolt ref and
  `bd-export.jsonl` have them.
  The view is allowed to lag; a known bd bug (gastownhall/beads#5486) means a
  local `closed` does not always reach an already-pushed issue. `sync.sh` reports
  the count difference and moves on. If a stale row bothers you:
  `gh issue close <n> --repo wdhaines/tunatale-tasks`. Never restore from it.
- **The submodule pointer rides code commits — never its own** (revised
  2026-08-10). `.gitmodules` sets `branch = main`; refresh with
  `git submodule update --remote .beads-tasks`.

  **This is automated — there is nothing to remember.** A `PreToolUse` hook
  (`.claude/hooks/stage_submodule_pointer.py`) stages `.beads-tasks` onto any
  `git commit` in this repo that already carries other content. It never
  manufactures a pointer-only commit — a bump that needs its own `./test.sh` run
  costs more than the staleness it fixes — and it declines to stage a submodule
  SHA that is not yet on the remote, which would hand every other clone a pointer
  it cannot fetch. Every guard fails open; the hook cannot block a commit.

  **Why not "the pointer always tracks HEAD":** git cannot express it. A
  submodule is stored as a literal commit SHA (a gitlink) in the parent's tree,
  and a commit must name an immutable tree — `branch = main` is only an input to
  `git submodule update --remote`, never a recorded tracking mode. If it *did*
  track a branch, checking out an old parent commit would hand you today's
  backlog instead of the backlog as of that commit. Auto-staging is the closest
  achievable thing: the pointer equals the submodule's HEAD at commit time.

  The reasoning is asymmetric between the two kinds of bd change, and that
  asymmetry is the point:
  - **A closure means code shipped**, so there is always a commit for it to ride.
  - **An addition has no code event**, so demanding one would be arbitrary. It
    drifts until the next commit, which is fine.

  ⚠️ **Closures land at most ONE commit late, and that is inherent.** A close
  cites the hash of the commit that shipped it (`--reason "Shipped in b8a8a50"`),
  so the commit must exist *before* the close — the close cannot be inside it.
  Closing first would lose the hash; amending afterwards is forbidden. So the
  pointer carrying a closure rides the *next* code commit. Do not try to
  engineer this away.

  `ignore = all` stays set: it keeps the between-commit drift out of
  `git status`, so a `git add -A` cannot sweep a meaningless bump into an
  unrelated commit — only the hook's explicit pathspec stages it. Check drift on
  demand with `git submodule status` — a leading `+` means behind, a space means
  current. ⚠️ `ignore = all` also hides the gitlink from `git diff` and
  `git status` **even when it is staged**; pass `--ignore-submodules=none` to see
  it, or you will conclude the staging silently failed when it did not.

  **Why this replaced "the pointer is advisory, never bump it":** that rule was
  correct about correctness (the backlog was always fully synced) and wrong
  about legibility. Browsing `.beads-tasks` on the public repo showed a stale
  tree, which read as "the sync did not run" — it cost a real round of
  confusion on 2026-08-10 before the sync was confirmed healthy. Staleness that
  is indistinguishable from breakage is not free, even when nothing is broken.
- **Why a submodule and not a sibling clone:** the BP fence blocks reads outside
  the project directory, and a path outside it does not error — it ends the run
  at exit 0 with zero files changed. Dispatch docs must be reachable at a path
  *inside* the checkout. The submodule is private (the main repo is public and
  the backlog holds internal workflow notes); `git submodule update --init` 403s
  for anyone else, which is expected and breaks nothing.
