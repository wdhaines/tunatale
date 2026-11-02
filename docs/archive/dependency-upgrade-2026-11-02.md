# Dependency upgrade — 2026-11-02

Fifth upgrade pass (after `-2026-07`, `-2026-07-25`, `-2026-08-31`, `-2026-10-01`).
This pass took every major on offer except TypeScript 7, including the SvelteKit 3
migration. It also took the CI actions and the local toolchain, which the 08-31 and
10-01 passes deferred. Each batch
is a separate commit, green on `./test.sh` before the next one started.

| batch | commit subject |
|---|---|
| 1 | frontend patch/minor bumps within caret ranges |
| 2 | backend `uv lock --upgrade` (huggingface-hub held at 1.x) |
| 3 | huggingface-hub 1.33 → 2.4 |
| 4 | oxfmt 0.71 → 0.75, `astral-sh/setup-uv` v10.0.1 → v10.3.3 |
| 5 | SvelteKit 2 → 3, adapter-static 3 → 4 |

## Majors and pre-1.0 breaking bumps, and what each touched

| bump | breaking changes upstream | exposure measured here |
|---|---|---|
| **@sveltejs/kit 2.70 → 3.1.1**, adapter-static 3 → 4 | ~60 (see below) | 156 files `$lib` → `#lib`, 9 `$app/stores`, the service worker, both tsconfigs, `svelte.config.js` |
| **huggingface-hub 1.33 → 2.4** (transitive) | HTTP stack → `httpx2`, OS trust store, 1.x deprecations removed | none in `app/`; exercised by hand (below) |
| **FastAPI 0.142 → 0.162** | 11 releases with a "Breaking Changes" section | none: no `Form()`, `root_path`, `openapi_prefix`, OpenTelemetry, or `**extra` on param functions |
| **ruff 0.16 → 0.17** | pragma comments stop counting toward line length; `ruff-lsp` removed | none: check clean, 866 files already formatted |
| **oxfmt 0.71 → 0.75** | new markdown + TOML formatters, `--migrate prettier` reworked | none: our globs are ts/js only; `fmt` rewrote nothing |
| **uv 0.12 → 0.13** (local) | Python 3.15 becomes the default stable | none yet: CI already ran uv 0.13 on 3.14.10 (run of 2026-11-01) |

## SvelteKit 3

`sv migrate sveltekit-3` exists, and it was **run in a scratch worktree and then not
used** for most of the work. Its printer re-indents whole `.svelte` templates when it
rewrites a script block (64 changed lines in `+layout.svelte` for a two-line change), and
it deletes comment lines from `vite.config.ts`, including the A/B/C measurement table
that explains `server.watch.ignored`. What landed instead:

- **`$lib` → `#lib`**, by a small resolver that appends the explicit extension
  subpath imports need (`foo.ts` → `foo.js`, `x.svelte.ts` → `x.svelte.js`, a
  directory → `/index.js`; `.svelte` and assets unchanged). It was validated
  against the tool's output on all 156 files; the only differences were three
  comment strings. `package.json` gains `"imports": {"#lib/*": "./src/lib/*"}`.
  The tool also adds a bare `"#lib": "./src/lib/index.js"`; it was dropped,
  because no `src/lib/index` exists and nothing imports bare `#lib`.
- ⚠️ **Nine tests imported `$lib/../test/factories`.** That form cannot survive the
  move: a subpath import cannot climb out of `src/lib`. svelte-check reported 9
  errors. They are now plain relative imports.
- **`$app/stores` → `$app/state`** in 4 components, and the 5 test mocks that
  faked a store's `subscribe` now expose getters (`get url() {…}`). The mocks read
  their mutable holder lazily, as the old `subscribe` callbacks did.
- **`invalidateAll` → `refreshAll`.** It is deprecated, not removed. They are
  equivalent here because nothing uses shallow routing (`page.state`).
- **`svelte.config.js` is gone.** Kit 3 reads none. `compilerOptions`, `outDir`
  and `adapter` are now arguments of `sveltekit()` in `vite.config.ts`, with
  their comments intact.
- **One tsconfig.** Kit 3 writes its generated config to
  `node_modules/$app/tsconfig.json`, a single file whatever `outDir` is. Its
  `rootDirs` point at the `outDir` of the most recent `svelte-kit sync`. The old
  `tsconfig.json` / `tsconfig.test.json` / `tsconfig.base.json` split existed only
  to extend two different generated trees, so the three files are now one.
  `check` syncs to `.svelte-kit-test` first and still reads its own types.
- **The service worker** moves from `$service-worker` (`build`, `files`,
  `version`) to `$app/manifest` (`immutable`, `assets`) and `$app/env`
  (`version`). Manifest paths are relative to the base path with no leading `/`,
  so each path goes through `resolve()`. Without that, the precache exclusion of
  `/voice-probe.html` would have silently stopped matching.

**svelte-check coverage, by file count rather than wall clock** (the 08-31 lesson):
Kit 2 checked 652 files and Kit 3 checks 647. `tsc --listFilesOnly` on both
projects shows that every one of the 5 is a generated declaration file: three
ambient files Kit 3 now supplies through `$app/types`, and `$types.d.ts` for
routes that import no `$types`. Every source file is still checked.

**The service worker's precache list**, extracted by running each built
`service-worker.js` against a stub `self` and capturing `cache.addAll` (Kit 2 build
vs Kit 3 build, same sources):

| | Kit 2 | Kit 3 |
|---|---|---|
| `_app` files listed / on disk | 69 / 70 | 71 / 72 |
| the one not listed | `_app/version.json` | `_app/version.json` |
| statics | 5 (icons, manifest, robots.txt) | the same 5 |
| voice-probe entries | 0 | 0 |
| leading `/` | all | all |

The only change is chunking (two more chunks).

**Coverage-gate drift** (required for a kit bump): 175 phantom drops before and
after, and the two `dropped-branches.json` snapshots are identical. The same
held for batch 1's svelte 5.57.3 / vite / coverage-v8 bumps.

**One behaviour change needed a fix.** Kit 3's `goto()` rejects a path that
matches no route, where Kit 2 navigated and rendered the 404. `/login` does
`await goto(safeNext(next))` inside the same `try` as the sign-in. A stale
`?next=` therefore turned a successful sign-in into an error on the login form,
leaving the user authenticated but stuck. The fix falls back to `/`. A test pins
it. It was red without the fix and green with it (sabotage drill), and only
after the test file's `goto` mock was made to return a Promise as the real one does.
Every other `goto` target in the app is a literal route.

## huggingface-hub 2

Nothing in CI downloads a model, so the hub path was exercised locally after the
bump. `create_aligner()` loaded `NbAiLab/nb-wav2vec2-300m-bokmaal`. `model_info()`
and `hf_hub_download(config.json)` went over the network through
`httpx2` + `truststore`. stanza `no` ran tokenize/pos/lemma on one sentence.

## Small things worth knowing

- **Playwright 1.64 needs `chromium-1248`.** A gate run before
  `bunx playwright install chromium` fails all 100 E2E tests in about 1 ms each,
  with `Executable doesn't exist`. CI keys its browser cache on the resolved
  playwright-core version and reinstalls by itself.
- **pytest-asyncio 1.4.1.post1** warns 8× per run while
  `asyncio_default_fixture_loop_scope` is unset. It is now `"function"`, which is
  what it resolved to anyway, because every async fixture is function-scoped.
- `setup-uv` is still an exact pin: there is no floating `v10` tag.

## Local toolchain

uv 0.12.8 → **0.13.6**, bun 1.4.0 → **1.4.4**, Node 26.8.1 → **26.13.0**,
ffmpeg 9.0.1 → **9.0.2**, gh 2.98 → **2.103.3**, bd 1.3.1 → **1.3.2**, and the rest of
Homebrew (`brew outdated` now empty). The backend venv was rebuilt on CPython
**3.14.10** (was 3.14.2) to match what CI runs. A gate on the unchanged tree
after the toolchain swap was green before batch 5 started, so batch 5's result is
not confounded with the toolchain.

## Holds

1. **TypeScript `^6.0.3`.** npm `latest` is 7.0.2, and 7.1 is only `beta`. More
   decisively, svelte-check 4.7.8 declares `typescript: ^5 || ^6`, and the
   08-31 measurement stands: `--tsgo` checks about 7% of files. Re-probe by
   file count when 7.1 is stable *and* svelte-check accepts it.
2. **protobuf override.** Unchanged. classla 2.2.3 is still the latest release, and per the 10-01 note it pins protobuf 4.21.2.
