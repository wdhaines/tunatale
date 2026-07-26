# Dependency upgrade — 2026-07-25

Second upgrade pass (the first is `dependency-upgrade-2026-07.md`, ~2 weeks earlier).
Same goal: every backend (`uv`) and frontend (`bun`) dependency plus the **toolchain
itself** at its most recent stable, floors raised to match, and every deliberate stop
short of latest documented.

Because the July pass had already taken most floors to their ceiling, the package
churn here is small. The substantive content is the toolchain bumps, one hold
**resolved** (classla's stale `requests` pin), one hold re-verified empirically
(TypeScript 7), and a pre-existing time-bomb test found by the gate.

## Toolchain

| tool | before | after | notes |
|---|---|---|---|
| **uv** | 0.9.27 | **0.11.32** | two minor series; `uv self update`. Lock format unchanged (`version = 1`, `revision = 3`) — no lockfile migration. |
| **ruff** | 0.15.21 | **0.16.0** | **Clean no-op.** `ruff check` passed unchanged; `ruff format --check` → "349 files already formatted". No new rule fired, no reformat. |
| **bun** | 1.3.14 | 1.3.14 | already latest (`bun-v1.3.14` is the current release). |
| **oxfmt** | 0.59.0 | **0.60.0** | reformat was a no-op on existing source, same as the 0.49→0.59 jump. Note `^0.59.0` does **not** admit 0.60 (caret on 0.x is `>=0.59 <0.60`), so the floor must be bumped by hand each minor. |
| **Node** | v24.9.0 | unchanged | not pinned by the repo; nothing required it. |

**GitHub Actions** (`.github/workflows/ci.yml`) were two majors behind:
`actions/checkout` **v5 → v7**, `astral-sh/setup-uv` **v7 → v9.0.0**.
`oven-sh/setup-bun@v2` left as-is — `v2` is the floating major tag and already
resolves to the current v2.2.0. These are only verifiable in CI, not locally.

**Trap (cost one red CI run):** `astral-sh/setup-uv` **stopped publishing floating
major tags after v7.** The repo has `v7`, `v7.6`, … but there is no `v8` or `v9`
alias — only exact `v9.0.0`. Writing `@v9` by analogy with `@v7` is unresolvable
and fails at the **"Set up job"** step, before a single line of the job runs. All
three uv-using jobs died identically while the frontend job (same new
`checkout@v7`, no uv) went green — that asymmetry is the diagnostic: a failure in
"Set up job" is action *resolution*, not anything about your code, and the job that
doesn't use the suspect action passing is what isolates it. `actions/checkout` does
still publish a floating `v7`. Verify with
`gh api repos/<owner>/<repo>/git/ref/tags/v9` before assuming a major alias exists.

## Upgraded (routine)

**Backend** — only three direct deps had moved since the July pass; everything else
was already at its ceiling. Floors raised to the resolved version:
fastapi 0.139.0→**0.140.0**, pre-commit 4.6.0→**4.6.1**, ruff 0.15.21→**0.16.0**.
Transitives refreshed by `uv lock --upgrade`: aiohttp 3.14.3, annotated-types 0.8.0,
certifi 2026.7.22, coverage 7.15.2, cuda-pathfinder 1.6.0, filelock 3.32.0,
hf-xet 1.5.2, huggingface-hub 1.24.0, platformdirs 4.11.0, python-discovery 1.5.0,
regex 2026.7.19, **stanza 1.13.0→1.14.0**, tqdm 4.69.1, virtualenv 21.7.0,
yarl 1.24.5.

**Frontend** — @playwright/test →**1.62.0**, @sveltejs/kit →**2.70.1**,
eslint →**10.8.0**, eslint-plugin-oxlint →**1.75.0**, eslint-plugin-svelte →**3.22.0**,
oxfmt →**0.60.0**, oxlint →**1.75.0**, svelte 5.56.5→**5.56.8**,
svelte-check →**4.7.3**, typescript-eslint →**8.65.0**, vite →**8.1.5**.
After this, `bun outdated` lists **only** the deliberately-held `typescript`.

## Resolved this pass: classla's `requests==2.28.0` pin

The July pass documented classla's `torch<=2.6` / `protobuf==4.21.2` pins as the
reason for `[tool.uv] override-dependencies`. A third pin was missed: classla 2.2.1
also hard-pins **`requests==2.28.0`** (a 2022 release), which stranded the whole
lock at **urllib3 1.26.20** and **charset-normalizer 2.0.12**. `uv lock --upgrade`
cannot move them — the pin is exact, so nothing looks "outdated" at the top level
while three transitives sit years behind.

Unlike the torch/protobuf overrides (which exist out of *necessity* — no cp314
wheel), this one is a staleness fix, so it was justified empirically rather than by
assertion:

- **Nothing in `backend/app/**` imports `requests`.** It arrives only via
  classla, stanza, and the anki extra.
- **classla touches `requests` in exactly two places**: `requests.get(url,
  stream=True)` + `.iter_content()` for model downloads
  (`classla/resources/common.py`), and the CoreNLP server client
  (`classla/server/client.py`) that TunaTale never uses. That surface is unchanged
  between 2.28 and 2.34.
- **Smoke-tested against the real thing, not asserted**: streamed classla's actual
  `resources_2.2.json` from its real host over requests 2.34.2 / urllib3 2.7.0
  (HTTP 200, 15860 bytes), then ran a full Slovene `tokenize,pos,lemma` pipeline —
  `Dober dan, kako se imate?` → `dober/dan/kako/se/imeti`, correct.

Result: `requests` **2.28.0 → 2.34.2**, `urllib3` **1.26.20 → 2.7.0**,
`charset-normalizer` **2.0.12 → 3.4.9**.

**Gotcha worth remembering:** a uv override *replaces* the requirement it matches,
including its extras. Writing it as a bare `requests>=2.34.2` silently dropped the
PySocks that anki's own `requests[socks]` declares. The override is therefore
spelled **`requests[socks]>=2.34.2`**. (The sync driver runs as an isolated
`uv run --with anki` subprocess that resolves independently, so real sync was never
at risk either way — but the lock should still say what anki declares.)

## stanza 1.14 invalidates the Norwegian model cache

stanza caches models under a **version-scoped** directory
(`~/Library/Caches/stanza/<version>/resources/`), so 1.13→1.14 orphans the existing
models and the pipeline dies with `ResourcesFileNotFoundError` until they are
re-fetched. Models were re-downloaded and Norwegian re-verified:
`God dag, hvordan har du det?` → `god/dag/hvordan/ha/du/det`, correct.

**Reclaimable disk:** the old version-scoped caches are now dead weight —
`~/Library/Caches/stanza/1.11.0` (239M) and `1.13.0` (680M) can be deleted.
Expect this cost on every future stanza minor bump.

## Holds

### 1. TypeScript — still `^6.0.0`, TS 7 still blocked (re-verified empirically)
The July note said "revisit when TS 7.1 ships and svelte-check + typescript-eslint
cut TS 7-compatible releases". Both tools *did* cut new releases this pass
(svelte-check 4.7.3, typescript-eslint 8.65.0) and TS 7.0.2 is out — so the hold was
re-probed rather than assumed. Installing typescript@7.0.2 reproduces the **identical
failure signature**:

```
TypeError: Cannot read properties of undefined (reading 'useCaseSensitiveFileNames')
    at new ConfigLoader (node_modules/svelte-check/dist/src/index.js)
```

typescript-eslint fails the same way. Root cause is unchanged and is not a TunaTale
limitation: TS 7 ships no stable programmatic API until **7.1 (~Oct 2026)**, and both
tools embed the TS API (svelte-check via Volar). TunaTale gets **no upside** from TS 7
anyway — nothing here invokes `tsc` directly (type-check is svelte-check, build is
vite). **Re-probe at TS 7.1.**

### 2. fsrs-rs-python — still `>=0.8.2,<0.9`
Unchanged from July: the hold is keyed to desktop Anki's FSRS formula, and **desktop
Anki is still 26.05** — the exact version the July `answer_card` HARD probe was run
against. Same binary ⇒ same verdict, so no re-probe was warranted this pass. Bump only
when desktop Anki adopts the 0.9.x non-decreasing `SInc(Hard)` formula, and re-run
`test_parity_*` when it does.

### 3. anki — `==26.5`, now *at* the ceiling
26.5 is both the user's desktop version **and** the newest release on PyPI, so this is
no longer a hold behind latest — it's matched and current. Keep bumping
`anki_pkg_version` in lockstep with desktop Anki.

### 4. classla — `==2.2.1` (already latest); stanza unpinned (1.14.0, latest)
Unchanged. classla's torch/protobuf overrides remain necessary (no cp314 wheels).

## Fixed en route: a wall-clock time-bomb in the frontend suite

Three `ListenPreviewModal` dueness-tag tests failed **before any frontend change** —
they seeded `due_at` with absolute literals (`2026-07-25T04:00:00+00:00` = "today").
`formatDueAt()` buckets by **UTC** midnight, so the seeds went red the instant UTC
rolled past them: at 2026-07-26T00:34Z it was still 2026-07-25 locally (EDT), and
"today" had become "-1d". This is the failure class in the July note about
`due_at` seeds — a green gate earlier the same day proves nothing about the evening.

Fixed by seeding relative to the current UTC day via a `dueInDays(n)` helper
(04:00 UTC, matching the backend convention) rather than by hardcoding dates.
The implementation was correct and was not touched.

## Verification
- **Backend**: `uv lock --check` clean. Full `./test.sh` backend leg green — ruff
  (349 files) + mock-boundary + language-literal + plugin-import + OpenAPI-snapshot
  checkers + pytest with `--run-oracle` at 100% coverage.
- **Peer-sync** (tier 2, not in `./test.sh`): `--run-peer-sync` **7 passed** — run
  because `requests` moved underneath the Anki sync path.
- **Language plugins**: Slovene (classla 2.2.1 / requests 2.34.2) and Norwegian
  (stanza 1.14.0) pipelines both lemmatize correctly against real models.
- **Frontend**: fmt-check clean, oxlint + eslint clean, `check:api` clean,
  svelte-check **534 files, 0 errors, 0 warnings**, vitest **1496 passed**,
  coverage gate **100% on 53 files**.
- **Coverage-gate drift check** (required by `.claude/rules/frontend-coverage-gate.md`
  after any svelte/vite/coverage-v8 bump): phantom drops were **183 on 53 files both
  before and after**, and `coverage/dropped-branches.json` was **byte-identical**
  across the upgrade — zero heuristic drift. (The rule's recorded baseline of 131/47
  from 2026-07-10 is stale feature growth, not drift; updated to 183/53.)
- **E2E**: 21 passed. Playwright 1.62 ships a new browser build — `bunx playwright
  install chromium` is required after the bump or all 18 browser-backed specs fail
  with "Executable doesn't exist".
- Full `./test.sh` → `=== All checks passed ===` before commit.
