# Dependency upgrade — 2026-07

Goal: bring every backend (`uv`) and frontend (`bun`) dependency to its most recent
stable release, raising the `>=`/caret floors to match, and **document every case
where we deliberately stop short** of latest.

The tree was already modern (Python 3.14, recent packages), so this was mostly a
lock refresh + floor bump. The interesting content is the five holds below — three
are "already at the ceiling," two are deliberate holds *behind* PyPI-latest because
latest is ahead of the reference implementation we mirror.

## Upgraded (routine, to latest stable)

**Backend** (`backend/pyproject.toml` floors raised to the resolved lock version):
fastapi 0.104→**0.139.0**, uvicorn 0.24→**0.51.0**, pydantic 2.5→**2.13.4**,
pydantic-settings 2.0→**2.14.2**, httpx 0.25→**0.28.1**, python-dotenv →**1.2.2**,
python-multipart →**0.0.32**, edge-tts →**7.2.8**, soundfile →**0.14.0**,
aiofiles →**25.1.0**, numpy 2.0→**2.5.1**; dev: pre-commit **4.6.0**, pytest
8→**9.1.1**, pytest-asyncio →**1.4.0**, pytest-cov →**7.1.0**, pytest-xdist
**3.8.0**, respx **0.23.1**, ruff 0.9→**0.15.21**, anyio →**4.14.2**. Override:
torch **2.12.0 → 2.13.0** (protobuf override `>=5.29` unchanged, resolves 7.35.1).

**Frontend** (`frontend/package.json` caret floors raised): @playwright/test
→**1.61.1**, @sveltejs/adapter-auto **7.0.1**, @sveltejs/kit →**2.69.3**,
vite-plugin-svelte →**7.2.0**, @testing-library/svelte **5.4.2**, @vitest/*
→**4.1.10**, eslint **10.7.0**, eslint-plugin-oxlint **1.73.0**,
eslint-plugin-svelte **3.20.0**, globals **17.7.0**, jsdom **29.1.1**, oxlint
**1.74.0**, svelte →**5.56.5**, svelte-check →**4.7.2**, typescript-eslint
**8.64.0**, vite →**8.1.4**, vitest →**4.1.10**. **oxfmt 0.49→0.59.0** in *both*
`frontend/package.json` and the root `package.json` (pinned in both); the 0.59
reformat was a no-op on existing source. **@types/node 25→26.1.1.**

## Holds (as far as we can go — documented decisions)

### 1. classla — held at `==2.2.1` (already latest)
2.2.1 is the newest classla. It's the reason the `[tool.uv] override-dependencies`
exist: classla 2.2.1 pins `torch<=2.6` and `protobuf==4.21.2`, neither of which has
a cp314 wheel, so the overrides force `torch==2.13.0` / `protobuf>=5.29` (3.14-capable).
Nothing to upgrade; the overrides are intentional, not debt. See the comment block in
`pyproject.toml` and `docs/walkthrough.md` §22.2.

### 2. stanza — already latest (1.13.0)
Unpinned by design (kept as the latest the resolver picks). No floor to raise.

### 3. Anki — pinned `anki==26.5`, matched to desktop
`backend/app/config.py:anki_pkg_version` is pinned to match the user's **desktop** Anki
(originally 25.09.5 → `25.9.5`; bumped to **26.05 → `26.5`** when the user upgraded the
desktop). The sync subprocess must speak the same sync protocol and mirror the same
scheduler the parity code is tuned to (`.claude/rules/anki-queue-parity.md`, "trust the
binary"). This one setting drives the sync driver, the peer-sync server (via
`_anki_with_spec`), and — single-sourced this pass — the oracle harness
(`tests/anki_oracle/harness_fixtures.py`) and the two CI warm-env steps, so parity is
validated against the exact version we sync with. The 26.x wheel is `abi3`
(cp310-abi3, requires_python>=3.10) so it imports fine on Python 3.14. **Bump this one
setting in lockstep with desktop Anki, and re-run oracle + peer-sync.** Verified against
26.5: oracle 34 passed, peer-sync 7 passed.

### 4. fsrs-rs-python — held at `>=0.8.2,<0.9` (behind 0.9.x)
fsrs-rs-python is the **bit-exact precision oracle** for TT's FSRS math
(`tests/test_parity_fsrs_f32.py`, `test_parity_same_day_review.py`, …). 0.9.2 changed
the **same-day HARD short-term stability formula** ("Non-decreasing SInc(Hard)") — it
returns `132.667` where 0.8.2 and TT give `99.4537` (3 `test_parity_same_day_review`
cases fail on 0.9.2). **Empirically probed against the real Anki binary: 26.05 still
produces `99.453697` for this scenario** (throwaway `answer_card` HARD grade via the
oracle harness) — i.e. **even Anki 26.05 has NOT adopted the non-decreasing formula**,
so TT (which mirrors it, soak-verified) stays correct and fsrs-rs-python 0.9.x would put
the precision oracle *ahead* of the Anki we mirror. The `<0.9` cap stops `uv lock
--upgrade` from silently re-pulling it (which it did this pass). **Bump — and re-run the
`test_parity_*` suite — only when a future desktop Anki adopts the 0.9.x SInc(Hard)
formula** (re-probe with the same `answer_card` scenario to check).

### 5. TypeScript — held at `^6.0.0` (6.0.3), TS 7 deferred until 7.1
**Not a TunaTale limitation — an officially-acknowledged ecosystem gap.** TypeScript
7.0 (GA 2026-07-08) is the Go-native compiler rewrite (~10× faster) but **ships no
stable programmatic API**; the TS team defers that to **7.1 (~Oct 2026)**. Every tool
that *embeds* the TS API is therefore pinned to the TS 6.0 API surface:
- **svelte-check 4.7.2** type-checks templates via **Volar**, which embeds the TS API.
  Under TS 7 it dies at `TypeError: Cannot read properties of undefined (reading
  'useCaseSensitiveFileNames')` — Volar's FileMap reading an API TS 7 doesn't expose.
  4.7.2 is already the latest svelte-check; no release fixes this before 7.1.
- **@typescript-eslint 8.64** fails the same way (typescript-estree can't load its
  create-program helpers). Microsoft shipped `@typescript/typescript6` (a `tsc6` binary
  re-exporting the 6.0 API) precisely so these tools keep working while `tsc` runs 7.0.

Headline of record: *"TypeScript 7 Now Stable: 10× Faster Builds, But Not for Vue or
Svelte Yet."* TunaTale gets **no upside** from TS 7 regardless: our scripts never invoke
`tsc` directly — type-checking is `svelte-check` (Volar), building is `vite` — so the
"faster tsc" benefit doesn't apply, and the `@typescript/typescript6` side-by-side path
would be complexity for zero gain. **Revisit when TS 7.1 ships and svelte-check +
typescript-eslint cut TS 7-compatible releases**; then bump and re-run `check` + `lint`.

## Verification
- Backend: `uv lock --check` clean; oracle-parity 34 passed, peer-sync 7 passed (both
  against anki 25.9.5); FSRS precision-oracle suite 38 passed at fsrs-rs-python 0.8.2.
- Frontend: fmt-check clean, lint clean, svelte-check 0 errors/0 warnings (503 files),
  vitest 1193 passed, coverage 100% on 48 files (137 phantom drops — within the
  131/47 baseline's tolerance per `.claude/rules/testing.md`).
- Full `./test.sh` green before commit; CI (4 parallel jobs) confirmed on push.
