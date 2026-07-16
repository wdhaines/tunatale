# TunaTale frontend

SvelteKit (Svelte 5) + TypeScript single-page UI for TunaTale — AI-generated audio
language lessons with Anki-integrated spaced repetition. All data lives in the FastAPI
backend (`../backend`); this app talks to it through a `/api` dev-server proxy
(`vite.config.ts`), so there is no frontend-only mode.

See the repo-root `README.md` for the product itself and `AGENTS.md` for the
development workflow and commit gate.

## Prerequisites

- [bun](https://bun.sh) (package manager + script runner — not npm)
- The backend set up per `backend/README`/`AGENTS.md` (`uv sync --all-groups`)

```sh
bun install
```

## Developing

The usual path is the repo-root script, which starts backend (:8000) and frontend
(:5173) together, generates mkcert TLS certs, and binds all interfaces so a
Tailscale-connected phone can use the app:

```sh
../start-dev.sh          # from this directory; or ./start-dev.sh from repo root
```

Standalone (backend already running on :8000, plain HTTP):

```sh
bun run dev
```

HTTPS is opt-in via `VITE_SSL_ENABLED=true` (set by `start-dev.sh`); `API_PORT`
overrides the proxy target. The service worker (offline audio caching, `src/lib/sw/`)
only activates against a production build — HMR and service workers conflict — so
phone-facing offline mode is `start-dev.sh --prod`, which builds and serves via
`vite preview` (`bun run preview:robust`).

## Commands

```sh
bun run check            # svelte-check (type-checks .svelte + .ts)
bun run test             # vitest, single run
bun run test:coverage    # vitest + coverage gate (this is what test.sh/CI run)
bun run test:e2e         # Playwright — boots its OWN backend on :8001 with a throwaway DB
bun run lint             # oxlint (fast, .ts) + eslint (svelte plugin)
bun run fmt              # oxfmt over src/**/*.ts — separate from eslint, run both
bun run build            # production build
```

The full pre-commit gate is `./test.sh` at the repo root (backend + frontend + E2E);
per `AGENTS.md` it must pass before every commit.

## Testing rules that surprise people

- **Coverage is 100% per file** — lines, branches, functions, statements — enforced by
  `scripts/coverage-gate.ts`, not by a vitest `thresholds:` block (deliberately absent;
  don't re-add one). The gate filters Svelte 5 compiler-injected "phantom" branches
  first; the heuristic and its maintenance protocol live in
  `.claude/rules/frontend-coverage-gate.md`. No `c8`/`istanbul` ignore comments — the
  gate doesn't read them; write the test or refactor the branch out.
- **Playwright is self-contained**: `playwright.config.ts` starts an isolated backend
  (port 8001, fresh `tunatale-test.db` each run) plus the frontend — never your dev
  servers. E2E runs locally via `./test.sh`; CI runs only svelte-check + vitest.
- Component tests use `@testing-library/svelte` under jsdom; specs sit next to their
  source (`foo.ts` / `foo.test.ts`, `+page.svelte` / `page.test.ts`).

## Layout

```
src/
  routes/
    +page.svelte                     # home: curricula + lesson progress
    c/[curriculumId]/                # curriculum day picker
    c/[curriculumId]/plan/           # planner chat (propose/commit curriculum days)
    c/[curriculumId]/l/[lessonId]/   # lesson page: listen + read modes, transcript
    review/                          # SRS drill (Anki-parity queue)
    cards/                           # card browser/admin (search, suspend, images)
    settings/
  lib/
    api.ts                # typed client for every backend endpoint
    stores/               # Svelte 5 rune stores (language, listened, …)
    components/           # shared UI (DayPicker, Tooltip, drill cards, …)
    playback/             # audio playback controller
    sw/                   # service-worker audio cache + prefetch
    mastery.ts            # word/lesson mastery display helpers
scripts/coverage-gate.ts  # the 100%-per-file coverage enforcer
tests/                    # Playwright E2E specs
```
