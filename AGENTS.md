# AGENTS.md — TunaTale

AI-generated audio language curricula — Pimsleur-style listening with content adapted to the learner's vocabulary. Slovene and Norwegian are wired end-to-end (Slovene most completely); the architecture is language-plugin based. Integrates bidirectionally with the user's Anki deck rather than replacing it. See `README.md` for the product pitch and `docs/walkthrough.md` for the system tour.

## Developer Commands

**⚠️ Must run `./test.sh` before every commit — the full suite must pass, or you DO NOT commit.** (Enforced by a commit-gate hook — see Hooks below.)

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
- **Peer-sync tests** (`--run-peer-sync`): auto-start a throwaway `anki.syncserver`. Tier 1 as of 2026-08-14 — a third parallel group in `./test.sh`, not a manual step.
- **CI is authoritative; `./test.sh` is a strict SUBSET of it** (`tunatale-as5`, 2026-08-14). Green locally is necessary but not sufficient. **Adding a check to `test.sh` obliges you to add it to `ci.yml` in the same commit**; the reverse is not required. Six parallel job instances in `.github/workflows/ci.yml` — backend (ruff → checkers → pytest), `backend-hostile-tz` (×1, `Etc/GMT-3`), `backend-hostile-hour`, frontend, `e2e` (Playwright), and `anki-gates` (oracle-parity + peer-sync, merged 2026-09-01 as `tunatale-ej8.2` — they were byte-identical apart from their final step, and job COUNT is what drives the tail). **It was eight until 2026-08-31**: `backend-hostile-tz` ran a two-zone matrix at the extremes (UTC+14 / UTC-12), and both instances were measured blind to the only offset bug this repo has ever found, which reproduces at UTC+2..+4 — so the pair was replaced by one instance inside that band (`tunatale-vnf.9`). Job COUNT, not job speed, drives the CI tail. The measurement and the redundancy argument are in the comment above the job. The hostile-timezone jobs are the only CI-only checks; there are no local-only ones. **There is no dependency-group split** — CI's `--no-group` flags were measured to be cosmetic (`uv run` re-syncs to `[tool.uv] default-groups`, which lists all three) and were deleted. Full rationale: `.claude/rules/testing.md` § "What a green gate means".

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
- `testing.md` — mock boundaries (enforced), cassettes, where tests run, pragma discipline → `backend/tests/**`
- `test-tiers.md` — what a test is ABOUT: the seam discriminator (is the value engine-computed or app-computed?), no fourth tier, and the sabotage-drill retirement criterion → `frontend/tests/**`, `frontend/src/**`, `backend/tests/**`
- `frontend-coverage-gate.md` — Svelte 5 phantom-branch filter → `frontend/**`
- `anki-sync.md` — USN protocol, safety envelope, graves, migrations, card-adding-UI contract → `backend/app/plugins/anki_sync/**`, `backend/app/api/anki.py`, Anki tests
- `anki-queue-parity.md` — REQUIRED before changing SRS/queue/sync behavior or debugging any TT↔Anki divergence → `backend/app/srs/**`, `backend/app/api/srs.py`, `backend/app/plugins/anki_sync/**`, SRS/parity tests
- `anki-oracle-harness.md` — parity harness guide → `backend/tests/test_parity_*.py`, `backend/tests/anki_oracle/**`

## Hooks (`.claude/settings.json`)

- **Commit gate** (PreToolUse): `git commit` asks for confirmation unless `./test.sh` has passed on the exact current tree — `test.sh` records a tree fingerprint via `.claude/hooks/commit_gate.py --record` on success. A *failing* run deletes the fingerprint, so a flaky green cannot outlive a red on the same tree. It matches `git commit` only in **command position**, ignoring quoted arguments — `opencode run ... "Run: git commit"` commits nothing and is not gated (fixed 2026-08-31; a shell `-c` argument is exempt from that narrowing, since there the quoted string is a command line).
- **Pipe guard** (PreToolUse): `.claude/hooks/gate_pipe_guard.py` **denies** any command that pipes `./test.sh` (`| tail`, `| tee`, `| grep`). A pipeline's `$?` is the last command's, so a failed gate reads as 0, and `tail -n` throws away the failure detail you piped in order to see. Searching for the string (`grep test.sh …`, `cat test.sh | head`) is unaffected. test.sh also tees every run to `.git/tt-test-last.log` and names it in the FAILED banner.
- ⚠️ **Canonical gate invocation — absolute path, gate LAST, and the LOG is the
  only evidence:**
  ```bash
  cd /Users/wdhaines/CascadeProjects/tunatale
  /Users/wdhaines/CascadeProjects/tunatale/test.sh > /tmp/gate.txt 2>&1
  # ← NOTHING after this line. No `echo`, no cleanup, nothing.
  ```
  Then read `/tmp/gate.txt`: require `=== All checks passed ===` (the failure form
  is `=== FAILED (backend=N frontend=N peer_sync=N) ===`), 100.00% backend
  coverage, and a sane ruff count (~446 and growing; a tiny N means discovery
  broke).
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

<!-- BEGIN BEADS INTEGRATION (hand-trimmed 2026-08-18 from bd's stock template;
     a future `bd setup opencode` will NOT match this and must not be applied
     blindly). -->
## Issue Tracking with bd (beads)

The BP dispatch backlog and its dependency ordering live in **bd (beads)**, not
in prose queue tables. This block is guidance, not permission to override
repository, user, or orchestrator instructions.

```bash
bd ready --exclude-type=epic                  # unblocked work (epics are containers, not work)
bd ready --parent <epic> --exclude-type=epic  # ...scoped to one theme
bd show <id> --json | jq -r '.[0].description'    # ALWAYS --json; see bugs below
bd create "title" -d "..." -p 0-4             # 0 critical .. 4 backlog
bd dep add <child> <parent>                   # child is blocked by parent
bd update <id> --claim  /  bd close <id> --reason "..."
bd graph --all --html   /  --compact          # browse the backlog, edges included
```

Full reference: `bd --help`, and **`bd prime` — run it by hand** whenever you
want the current command and flag reference. It is authoritative and
self-updating in a way this hand-written section can never be, and it is only
~100 lines.

**Do not wire it into a SessionStart hook.** The objection is mechanism, not
content: auto-injected, its "Core Rules" block arrives as authoritative context
every session, and the predicted drift is an agent that quietly stops writing
briefs and stops committing. ⚠️ **That drift is a prediction and has never been
measured.** The ban stands on asymmetry — running it by hand costs nothing, and
the failure it guards against would be silent.

⚠️ **Audited against the real output 2026-08-18. This used to claim four
contradictions; only two are real, and on one of them beads is right.**
- **"Create beads issue BEFORE writing code"** — a real conflict, and ours is
  the sloppier side: most commits here have no bead.
- **"Do NOT use MEMORY.md"** — a real conflict where we are right for this
  situation. Its stated reason is that they "fragment across accounts"; this is
  one user on one machine, and MEMORY.md is the harness's own system, not a
  choice bd can override.
- "No markdown files for task tracking" — **not a conflict.** Tracking lives in
  bd; `.beads-tasks/briefs/` holds documents, not a task list.
- "Git workflow: stealth mode (no git ops)" — **not a conflict, and reading it
  as one was backwards.** That line is bd echoing OUR OWN config (`no-git-ops`),
  which is a privacy guard — see the stealth-mode note below.

⚠️ **bd's JSON is not one shape, and a wrong field name returns a clean negative
rather than an error.** Verify any field against a record whose answer you
already know before believing a count. All three of these produced confident
wrong conclusions on 2026-08-18:
- the field is **`parent`**, not `parent_id`;
- **`dependency_count` counts only `blocks` edges** while the `dependencies`
  array also carries `parent-child` and `discovered-from` — they disagree on 47
  of 58 open issues, which looks exactly like a bug and is not one;
- `bd show --json` uses a different shape again from `bd list` / `bd export`.

⚠️ **A `parent-child` edge BLOCKS the child when the parent is a `task`, but not
when it is an `epic`** (verified 2026-08-24). A task parented to another open
task drops out of `bd ready` and appears in `bd blocked` as *"Blocked by 1 open
dependencies: [<parent>]"*. Epic children are unaffected — which is why
`bd ready --exclude-type=epic` works at all, and why this goes unnoticed.
Parenting a task to an open task **while also** adding `bd dep add <parent>
<child>` creates a mutual block and the child is silently invisible; the symptom
is a bead you just filed never appearing in `bd ready`. Use an epic as the
container, or express the ordering with a `blocks` edge alone and leave the
parent unset.

⚠️ **`bd comment` succeeds SILENTLY — no output is not failure.** Retrying on the
apparent silence posts it twice (done, 2026-08-24). Confirm with the
`comment_count` field; `.comments` comes back empty and is not the field, which
makes the wrong check look like a confirmed failure. Same clean-negative class as
the JSON-shape traps above.

⚠️ **Two upstream bugs, hands-on verified — do not rediscover them blind:**
- plain `bd show <id>` mangles technical content (strips code fences, garbles
  `Promise<boolean>` into `Promise****`). The stored data is fine; only the
  pretty-printer is broken. Always `--json | jq`. (gastownhall/beads#5495)
- `bd dep add <child> <parent>` and `bd create --deps blocks:<id>` are inverses:
  `--deps blocks:X` means "this new issue blocks X," not "is blocked by X."
  Verify every edge right after wiring it.
<!-- END BEADS INTEGRATION -->

## Beads + TunaTale specifics

- **Sync is standing-authorized — after any `bd create` / `close` / `dep add`
  batch run `./.beads-tasks/sync.sh`, do not ask.** `.beads/` is stealth-mode
  and its Dolt backups sit on the same disk, so nothing leaves this machine until
  that script runs. It pushes the Dolt store, exports, renders `GRAPH.md`,
  commits and pushes.
- **Stealth mode (`no-git-ops`) is a privacy guard, not a preference.** `.beads/`
  must sit at the PARENT repo root (bd stops walking up at a git root, so from
  inside `.beads-tasks/` it never finds `../.beads`) — and that root's origin is
  the PUBLIC repo. With bd's automatic git operations on, they would write
  `refs/dolt/data` to the public remote, and **a hidden ref is unlisted, not
  private**. It costs nothing: stealth suppresses only *automatic* git ops, so
  `bd dolt push` via `sync.sh` still works. Full reasoning:
  `.beads-tasks/briefs/design-beads-github-sync-2026-08.md` § Appendix B.
- **The backlog is private, and the Dolt remote is the whole mechanism.** The
  store travels as the hidden ref `refs/dolt/data` on the **private**
  tunatale-tasks repo — deliberately NOT this repo's origin, which is public.
  **A hidden ref is unlisted, not private**; anything on a public remote is
  world-fetchable. `sync.sh` refuses to push if the remote ever stops containing
  `tunatale-tasks` — never relax that guard. `bd-export.jsonl` is a secondary
  human-diffable copy; restore from the Dolt ref, not the JSONL.
- **Browse with `npx beads-ui start`** (mantoni/beads-ui; serves
  `http://127.0.0.1:3000`, talks to the `bd` CLI so it reads the live store, and
  never leaves localhost — verified working 2026-08-18). `bd graph <epic>
  --compact` is the terminal equivalent. ⚠️ **`bd graph --all --html`
  degenerates on this backlog and should not be the default suggestion.** Every
  node is colored by status and nearly everything here is `open`; 39 of 52 open
  issues sit at layer 0, and `forceX(150 + layer*220)` pulls all 39 identical
  blue 130×40 rects into one column with `forceCollide(50)` — a solid blue slab,
  not a graph. Two further defects, both measured 2026-08-18: its help calls the
  HTML "self-contained" and that is FALSE (it pulls d3 from `https://d3js.org`,
  so it needs the network and cannot be published as an Artifact without inlining
  d3), and `bd graph <id> --html` does NOT scope — it still emitted all 52 nodes
  for a 22-issue epic, though it did recompute the layers. `--compact` scopes
  correctly.
  ⚠️ **How this got into a doc is the lesson:** the adoption control counted
  nodes and edges (52/57, agreeing with `--dot` and `bd list`) and never looked
  at the rendered picture. The data was right and the view was unusable —
  a control has to test the property you are actually claiming.
  The GitHub Issues mirror was retired 2026-08-18 (`tunatale-93s`; rationale in
  `cc7ddea`) and the tab is disabled.
- **A fresh clone has no bd data — run `./beads-bootstrap.sh`.** A default clone
  fetches only `refs/heads/*` and `refs/tags/*`, and bd's auto-detection looks at
  *this* repo's origin — the wrong repo, on purpose. Needs read access to the
  private tasks repo; without it the clone step fails, which is expected.
  **An empty backlog after bootstrap is a red flag, not "nothing to do"** — the
  script exits non-zero on 0 open issues for exactly that reason.
- **Agent mail lives in bd** — `./.beads-tasks/mail.sh inbox` / `read <id>` /
  `MAIL_ID=orch ./.beads-tasks/mail.sh send peer "subject" "body"` — for talking
  to another Claude session sharing this tree. `unread == open`, addressing is a
  `to-*` label, and message beads are excluded from `bd list` / `bd ready` /
  `GRAPH.md`. A `SessionStart` hook surfaces unread mail; without it a session
  never looks. ⚠️ **Mail is in neither `bd-export.jsonl` nor the Dolt push** — it
  is the one thing here with no off-machine copy. Anything that must survive
  belongs in an issue.
- **Related issues get an epic, once a theme produces more than two.** `bd ready`
  sorts by priority across the WHOLE backlog, so loose siblings scatter across
  unrelated work and the reader cannot see they are one piece. Retrofit the
  moment you notice you are creating the third (`bd update <id> --parent <epic>`
  reparents, so there is no cost to doing it late). The epic carries the
  through-line, the ordering rationale, and anything explicitly OUT of scope —
  the cheapest place to stop an executor widening the work. It does not restate
  its children. `tunatale-vnf` is the reference shape.
- **Method vs work.** `.beads-tasks/DISPATCH-PREAMBLE.md` holds what binds
  *every* delegated run (fence, prohibitions, escalation, report contract). An
  issue holds only what is true of *that* task. Never restate the preamble inside
  an issue — two copies drift, and the one the executor read is the one you did
  not edit.
- **Short work inline, long briefs as files.** A screenful goes in the issue
  description. Anything longer, or carrying a big oracle table, goes in
  `.beads-tasks/briefs/` — genre-prefixed (`brief-`, `findings-`, `testplan-`,
  `handoff-`, `design-`) — with the issue holding scope, `Source: <path> §
  <section>`, and the decisive oracles. Never both. `.beads-tasks/archive/` holds
  docs whose work shipped and which exist nowhere else. Prefer a file for length
  and citability, NOT because bd edits are unreviewable — they are versioned in
  `bd-export.jsonl`, and the prose diff is one command:
  ```bash
  desc () { git show "$1:bd-export.jsonl" | jq -r --arg id "$2" 'select(.id==$id).description'; }
  diff <(desc HEAD~1 tunatale-xyz) <(desc HEAD tunatale-xyz)
  ```
  `docs/briefs/` is retired: gitignored *permanence* is what killed it, not
  files. Do not author anything new there.
- **Closing a bd issue is not authorization to commit.** It records that the
  described work is done; the `./test.sh` gate and the diff audit still stand
  between that and anything shipping.
- **The `.beads-tasks` pointer is auto-staged — there is nothing to remember.**
  `.claude/hooks/stage_submodule_pointer.py` stages it onto any commit already
  carrying other content; every guard fails open, and it never manufactures a
  pointer-only commit. Order-independent with the commit gate, because
  `ignore = all` keeps the gitlink out of what the gate fingerprints. Check drift
  with `git submodule status` (leading `+` = behind, space = current).
  ⚠️ `ignore = all` also hides the gitlink from `git diff` / `git status` **even
  when it is staged** — pass `--ignore-submodules=none`, or you will conclude the
  hook silently no-opped when it did not. Closures land at most one commit late
  by construction: a close cites the hash of the commit that shipped it, so it
  cannot be inside that commit. Do not try to engineer that away.
- **Why a submodule and not a sibling clone:** the BP fence blocks reads outside
  the project directory, and an outside path does not error — it ends the run at
  exit 0 with zero files changed. Dispatch docs must be reachable *inside* the
  checkout, and the backlog must stay private. `git submodule update --init` 403s
  for anyone else; expected, breaks nothing.
