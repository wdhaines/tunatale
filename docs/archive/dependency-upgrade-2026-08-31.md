# Dependency upgrade — 2026-08-31

Third upgrade pass (`dependency-upgrade-2026-07.md`, then `-2026-07-25.md`, ~5 weeks
earlier). Same method: every backend (`uv`) and frontend (`bun`) dependency to its
most recent stable, floors raised to match, every deliberate stop short documented.

Two things make this pass worth reading rather than skimming: **the TypeScript hold's
premise went stale and its replacement is a trap**, and **the risk this pass was
expected to carry does not exist**.

## The risk that isn't there

The standing worry about `uv lock --upgrade` was classla/stanza — the NLP engines,
which nothing in CI exercises (see `.beads-tasks/briefs/findings-nlp-removal-control-2026-08.md`).
Measured: **`uv lock --upgrade` cannot move any of them**, by three independent
mechanisms:

| package | why it cannot move |
|---|---|
| classla | hard-pinned `==2.2.1` (also already latest) |
| stanza | unpinned, but 1.14.0 *is* latest |
| torch | `[tool.uv] override-dependencies = ["torch==2.13.0", …]` — exact |
| fsrs-rs-python | capped `>=0.8.2,<0.9`, deliberately, to stop exactly this |

Only the last is documented as anti-`--upgrade` protection; the other three are
incidental. Worth knowing, because the protection is load-bearing and three quarters
of it is accidental.

The 38 movers were classified against the repo (direct vs transitive, and whether
anything in the tree reasons about the version). Only four carry a repo opinion:
`anki`, `protobuf`, `charset-normalizer`, `ruff`. Everything else is a floor or a
transitive nobody argues about.

## The lockfile's `anki` is dead weight

`uv lock --upgrade` moved `anki 26.5 -> 26.8.1`, which looks alarming and is inert.
Nothing reaches it:

- `app/config.py::Settings.anki_pkg_version` is `"26.5"` and is the single source.
- `sync_orchestrator.py::_anki_with_spec` renders `anki==26.5`; the sync driver and
  peer-sync server spawn `uv run --isolated --no-project --with anki==26.5`, which
  resolves independently of the project lock.
- `tests/anki_oracle/harness_fixtures.py` is single-sourced from the same setting.
- The `anki` extra is an *extra*, not a group, so neither `uv sync` nor
  `--all-groups` installs it, and `test_anki_extra_isolation.py` pins that `app/`
  never imports it.

**The user's desktop Anki is untouched by this pass.** The version that matters is
`anki_pkg_version`, and bumping it is a separate, collection-affecting decision that
stays with the user.

## Upgraded

**Backend** — floors raised to the resolved lock version:
fastapi 0.140.0→**0.141.1**, uvicorn 0.51.0→**0.52.4**, pydantic 2.13.4→**2.13.5**,
pydantic-settings 2.14.2→**2.15.0**, python-dotenv 1.2.2→**1.2.3**,
numpy 2.5.1→**2.5.2**, ruff 0.16.0→**0.16.5**, transformers 4.57→**5.16.1**.
Two floors were already stale *before* this pass and are now corrected:
argon2-cffi 23.1.0→**25.1.0**, watchfiles 1.1.0→**1.2.0**.

Transitives refreshed by `uv lock --upgrade` (28 more), notably
starlette 1.3.1→**1.6.0**, protobuf 7.35.1→**7.36.0**, tokenizers 0.22.2→**0.23.1**,
huggingface-hub 1.24.0→**1.29.0**, setuptools 83.0.0→**84.0.0**.

**ruff 0.16.5 is a clean no-op**, like 0.15.21→0.16.0 before it: `ruff check` passed
unchanged and `ruff format --check` reported **518 files already formatted**. This is
verified per-bump rather than assumed — ruff is the gate tool, so a reformat would be
a gate change.

**Frontend** — caret floors raised: @playwright/test →**1.62.1**,
@sveltejs/kit →**2.70.3**, @sveltejs/vite-plugin-svelte →**7.3.0**,
@types/node →**26.4.0**, @vitest/coverage-v8 + @vitest/ui + vitest →**4.1.11**,
eslint →**10.9.1**, eslint-plugin-oxlint →**1.80.0**, eslint-plugin-svelte →**3.23.0**,
globals →**17.11.0**, oxlint →**1.80.0**, svelte →**5.57.0**, svelte-check →**4.7.6**,
typescript-eslint →**8.68.0**, vite →**8.2.2**, **oxfmt 0.60.0→0.65.0**.

`oxfmt` still needs a hand-written floor each minor (caret on 0.x is `>=0.65 <0.66`).
The 0.65 reformat was a **no-op on existing source**, as with 0.49→0.59 and 0.59→0.60.
⚠️ The July note says oxfmt is "pinned in both `frontend/package.json` and the root
`package.json`". **There is no root `package.json` any more** — oxfmt is declared once,
at `frontend/package.json`.

After this, `bun outdated` lists only the two deliberate holds.

## ⚠️ TypeScript: the hold's premise is stale, and the new path is a trap

The July and July-25 notes both hold `typescript` at `^6.0.0` on the grounds that TS 7
"ships no stable programmatic API until 7.1 (~Oct 2026)", with svelte-check dying at
`TypeError: Cannot read properties of undefined (reading 'useCaseSensitiveFileNames')`.

**That signature is gone.** svelte-check **4.7.6** — installed by this very pass — now
detects TS 7 and prints a supported recipe instead:

```
Error: TypeScript 7 support currently requires both TypeScript 7 and TypeScript 6
installed in your project, and requires using the --tsgo or --tsgo-experimental-api
flag.
  npm install --save-dev typescript@~6 @typescript/native@npm:typescript@7
```

So the hold's stated blocker ("no release fixes this before 7.1") is no longer true,
and the stated trigger has still not fired: npm has **no stable 7.1** — `latest` is
7.0.2, and the whole 7.1 line is nightly `7.1.0-dev.*` builds.

**The side-by-side path was probed, and it must not be adopted.** Installed
`typescript@~6` plus `@typescript/native@npm:typescript@7.0.2` and ran the real
`check` script both ways:

| command | files checked | wall |
|---|---|---|
| `svelte-check --tsconfig ./tsconfig.test.json` | **547** | 4.87 s |
| `svelte-check --tsconfig ./tsconfig.test.json --tsgo` | **37** | 2.38 s |

`--tsgo` reports `0 ERRORS 0 WARNINGS` while checking **37 of 547 files** — it skips
the Svelte components, which is the entire reason svelte-check exists. It is a
2× speedup that buys a type-check gate covering 6.7% of the project, and it is green,
so nothing announces the loss.

⚠️ **The first measurement of this was wrong in the safe direction and nearly recorded
as a win.** A bare `bunx svelte-check --tsgo` also reported 37 files, which was
initially blamed on the missing `svelte-kit sync`. Running the *identical* command
without `--tsgo` returned 547 — that control is what separated "my probe was
misconfigured" from "`--tsgo` checks almost nothing". The file count, not the wall
clock, is the number that matters here.

**Hold stands, with a corrected reason.** Not "no path exists" but "the path drops
93% of the type-check". Re-probe when TS 7.1 ships stable *and* svelte-check supports
Svelte files under `--tsgo` — and verify by **file count**, not by the timing.

## Holds

1. **TypeScript `^6.0.0`** — see above. Reason updated; trigger unchanged (TS 7.1 stable).
2. **jsdom `^29.1.1`** — 30.0.1 is a major bump, deliberately deferred out of this pass
   to keep it bisectable. Not yet evaluated.
3. **fsrs-rs-python `>=0.8.2,<0.9`** — unchanged. Keyed to desktop Anki's FSRS formula;
   desktop Anki is still 26.05, the binary the July `answer_card` HARD probe ran
   against, so no re-probe was warranted.
4. **anki `==26.5`** (`anki_pkg_version`) — matched to desktop Anki. PyPI has moved to
   26.8.1; bumping is the user's call because it migrates their collection.
5. **classla `==2.2.1` / stanza 1.14.0** — both already latest. classla's torch/protobuf
   overrides remain necessary (no cp314 wheels).

## Deferred out of this pass (filed separately)

Toolchain and CI actions, because they are verifiable only in CI and would make one
un-bisectable PR — and the 07-25 pass records that the actions bump alone cost a red
run: **uv 0.11.32→0.12.7**, **bun 1.3.14→1.4.0**, `astral-sh/setup-uv` v9.0.0→v10.0.1,
`actions/cache` v5→v6.1.0, `actions/upload-artifact` v5→v7.0.1, plus jsdom 29→30.
`actions/checkout@v7` and `oven-sh/setup-bun@v2` are floating majors and already track.

## Verification

- `uv lock --check` clean. Holds re-read out of the lock after re-locking:
  fsrs-rs-python **0.8.2**, classla **2.2.1**, stanza **1.14.0**, torch **2.13.0**.
- `ruff check` clean; `ruff format --check` **518 files already formatted**.
- **Frontend coverage-gate drift check** (required by
  `.claude/rules/frontend-coverage-gate.md` after any svelte / vite / coverage-v8
  bump): **195 phantom drops on 56 files before and after**, and
  `coverage/dropped-branches.json` differs by exactly **one line** —
  `"text": "}>{"` became `"text": "value={"`. Both end in `{`, so both take the same
  binary-expr rule (a); the classification path is unchanged, not merely the count.
  (The rule's recorded baseline of 183/53 from 2026-07-25 is stale feature growth.)
- Full `./test.sh` → **`=== All checks passed ===`**: backend coverage **100.00%**,
  frontend **1711 passed** / gate 100% on 56 files, E2E **52 passed**,
  peer-sync **7 passed** (run because `anki` and `requests`-adjacent transitives moved
  under the sync path).
