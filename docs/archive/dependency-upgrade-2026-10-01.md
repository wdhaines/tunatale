# Dependency upgrade — 2026-10-01

Fourth upgrade pass (after `-2026-07`, `-2026-07-25`, `-2026-08-31`). Same method:
every backend (`uv`) and frontend (`bun`) dependency to its most recent stable,
floors raised to match, every deliberate stop short documented.

Three things make this pass worth reading rather than skimming: **the Anki pin moved
with desktop Anki and was proven with a negative control**, **two `uv` overrides
retired because classla stopped pinning what they defeated**, and **a Svelte patch
release exposed ~45 real untested frontend branches that "100% coverage" had been
hiding**.

## Anki 26.8.1 → 26.9.3 (desktop: 26.09.3, build 29bb700b)

The user upgraded desktop Anki to 26.09.3 in this pass, so `Settings.anki_pkg_version`
and the one hardcoded CI site (`ci.yml`, anki-gates `Warm anki subprocess env`) moved
to `26.9.3` together.

Measured with the harness the config comment documents
(`ANKI_PKG_VERSION=<v> uv run pytest tests/test_parity_*.py --run-oracle`):

| ANKI_PKG_VERSION | result |
|---|---|
| 26.8.1 (control: the old pin) | 106 passed |
| **26.9.3** | **106 passed** — no TT-side change needed |
| 26.5 (negative control) | **4 failed** — the SInc(Hard) tests, as recorded on 2026-08-31 |

The 26.5 run is what makes the 26.9.3 green mean something: a clean pass is also what
an ignored override (a cached 26.8.1 env) would look like. 26.5 going red on exactly
the expected four proves the env var reached the oracle. fsrs-rs-python is already
latest (0.9.3), and nothing in 26.9.3 moved the FSRS formula.

## classla 2.2.3 retired two of three overrides

classla 2.2.1 hard-pinned `torch<=2.6` (no cp314 wheel), `protobuf==4.21.2` and
`requests==2.28.0`; `[tool.uv] override-dependencies` forced all three. classla 2.2.3
declares `torch>=2.9; python_version >= "3.14"` and a bare `requests`, so:

- **torch override removed.** torch now resolves naturally: 2.13.0 → **2.14.1**.
  ⚠️ This was one of the "incidental" anti-`--upgrade` protections the 08-31 note
  listed. torch now floats with `uv lock --upgrade`; that is intended.
- **requests override removed.** requests stays **2.34.2** and **PySocks 1.7.1** is
  still in the lock (anki's own `requests[socks]` now reaches it unreplaced).
- **protobuf override kept** — classla 2.2.3 still pins `protobuf==4.21.2`.

⚠️ **NLP engines were import-tested only.** classla 2.2.3, stanza 1.15.0, torch
2.14.1, transformers 5.18.0 and protobuf 7.36.2 all import on Python 3.14.7 and
torch computes. A real pipeline run could not be done from the session that made this
pass: its network policy returns 403 for both huggingface.co (stanza models) and
clarin.si (classla models). Nothing in CI runs them either
(`findings-nlp-removal-control-2026-08.md`). Run one sentence through each locally
before relying on them.

## ⚠️ esrap 2.3 (via svelte 5.57.1) — "100%" was partly an artifact

After the frontend bumps the coverage gate went red on 19 files. Bisected to the
**transitive** `esrap` (Svelte's code printer and sourcemap emitter):

| svelte / esrap | phantom drops | gate |
|---|---|---|
| (baseline lock) esrap 2.2.13 | 221 | pass |
| everything bumped, `overrides: esrap 2.2.13` | 221 | pass |
| everything bumped, `overrides: esrap 2.3.14` | 178 | **fail** |
| everything bumped (esrap 2.4.0) | 178 | **fail** |

Reverting `svelte`, `vite-plugin-svelte` or `vite` alone each still failed, because
`bun add` does not downgrade a transitive. svelte 5.57.1 requires `esrap ^2.3.6`, so
holding esrap means holding svelte. Note that svelte 5.57.0's own range (`^2.2.12`)
also admits 2.4.0, so the old green depended on the lockfile happening to hold 2.2.13.

**What changed:** esrap 2.2 mapped ~45 real source branches to empty ranges, so the
gate's "empty range → phantom" rule dropped them. esrap 2.3+ maps them correctly. They
were real:

- **35** × the `String(e)` arm of `e instanceof Error ? e.message : String(e)` — no test
  rejected with a non-`Error`. Each got a test that rejects with a string and asserts
  the text is shown.
- `login`: the `Try again in N seconds` arm (retry-after ≤ 90).
- `LessonPlayer`: no current cue before the first cue starts (lead-in silence).
- `playbackController.selectTrack`: a section row with no cue manifest.
- lesson page: `review_used ?? []` when only `review_requested` is present.
- `MasteryLine`: the `?? []` lemma fallbacks and the `mastery ? … : []` null arms were
  **dead** — `lessonMastery()` always sets `lemmas`, and both lists are read only
  inside the `{:else if mastery …}` guard. Fixed by making `MasteryResult.lemmas`
  required and building both lists from a non-null result in the template.

One was a genuine new phantom shape: `LanguageSelector`'s `{option.name}` compiles to
`option.name ?? ""`, and esrap 2.3+ maps that fallback onto the single space in
`<option value=…>`. `isPhantom` now treats whitespace-only ranges like empty ones
(pinned in `tests/coverage-gate.test.ts`; rule doc updated).

**Drift accounting** (same tree, before vs after, 221 → 172): 52 entries left the drop
set — the ~45 above now tested or removed, plus a few the new tests incidentally cover
(LessonPlayer `?? ''` captions, `section.cues ?? null`) — and 2 entered (MasteryLine
template-fragment phantoms at shifted line numbers). 172 is the new baseline.

⚠️ The gate prints at most 5 `uncovered:` samples per file. The first count of "43"
undercounted: the plan page read `54/61` but listed 5. Read the `N/M` line.

## vitest 4 → 5

Adopted. One test broke: `clientLog.test.ts` did `document = document` (a no-op
self-assignment); vitest 5's jsdom global exposes `document` getter-only, so it threw.
Line removed. **The phantom-drop set under vitest 5 is identical to vitest 4's**
(172 entries, equal element for element). The nested duplicate `vite@8.2.2` that
vitest 4 pulled in is gone from the lock.

## oxfmt 0.65 → 0.71 is NOT a no-op

Unlike 0.49→0.65, 0.71 reformats one existing file
(`LessonPlayer.test.ts`: how a `function (this: …)` callback argument wraps).
Applied as the formatter emits it.

## Upgraded

**Backend** floors: fastapi →**0.142.2**, uvicorn →**0.54.0**,
python-dotenv →**1.2.4**, numpy →**2.5.3**, google-auth →**2.59.1**,
anyio →**4.15.1**, ruff →**0.16.9**, watchfiles →**1.3.0**,
transformers →**5.18.0**, classla ==**2.2.3**, stanza **>=1.15.0** (was unpinned).
Notable transitives: starlette →1.7.0, torch →2.14.1, protobuf →7.36.2,
filelock 3.32 →**4.0.8** (major, via torch/huggingface-hub), huggingface-hub →1.33.0.
fastapi 0.142 adds `opentelemetry-api` as a dependency.

**ruff 0.16.9** — `ruff check` clean, no reformat.

**Frontend** caret floors: @sveltejs/vite-plugin-svelte →**7.3.1**,
@types/node →**26.6.3**, eslint →**10.11.0**, eslint-plugin-oxlint + oxlint →**1.86.0**,
globals →**17.13.0**, jsdom →**30.1.1**, svelte →**5.57.1**,
typescript-eslint →**8.71.0**, vite →**8.3.2**, oxfmt →**0.71.0**,
vitest + @vitest/coverage-v8 + @vitest/ui →**5.0.3**.

After this, `bun outdated` lists only TypeScript.

## Holds

1. **TypeScript `^6.0.0`** — unchanged. npm `latest` is 7.0.2; 7.1 is still nightly
   (`next` = `7.1.0-dev.20261001.1`). The 08-31 reason stands: svelte-check's `--tsgo`
   path checks ~7% of files. Re-probe on TS 7.1 stable, by file count.
2. **protobuf override** — see above; classla 2.2.3 still pins 4.21.2.

## Not in this pass

Toolchain and CI actions (uv, bun, `setup-uv`, `actions/*`), as in 08-31. Locally,
uv 0.8.17 could not install a stable CPython 3.14 (it offered 3.14.0rc2); the pass ran
on uv 0.12.21 / Python 3.14.7.
