---
paths:
  - "frontend/**"
---

# Frontend Coverage Gate (Svelte 5 phantom filter)

*Path-scoped rule: auto-loads when a frontend file is read. Split out of `testing.md` so backend sessions don't carry it.*

Frontend runs 100% lines/branches/functions/statements per file via `frontend/scripts/coverage-gate.ts`. Vitest's built-in `thresholds:` block is intentionally absent — the custom gate is what enforces. The gate reads `coverage/coverage-final.json`, filters Svelte 5 compiler-injected phantom branches, then asserts 100% on every file.

## What counts as a phantom

`isPhantom(branchType, text, synthetic)` in `coverage-gate.ts` classifies each uncovered sub-location:

- **Synthetic or empty source range** → phantom (compiler emitted a branch at a position the user source never reached).
- **cond-expr** (`?:`): phantom if the sub-location text is a JS literal (`null`, `undefined`, booleans, numbers, quoted strings). Svelte 5 folds these. Identifier/property-access stays real.
- **binary-expr** (`||`, `&&`, `??`): phantom if (a) text starts with `}` or ends with `{` (Svelte template-interpolation boundary) OR (b) text is a bare JS literal (defensive fallback like `?? ''`). Object literals starting with `{` and ending with `}` stay real.
- **if**: phantom only when text is empty. Non-empty if-bodies are real.
- Unknown types stay real (conservative).

All classifications are pinned by `frontend/tests/coverage-gate.test.ts` against empirical TunaTale cases (e.g., `'} created, {'` → phantom, `'e.message'` → real). Adding or changing a rule means updating both.

## Maintenance — heuristic drift after Svelte upgrades

The gate's heuristic depends on the shape of Svelte 5's compiled output. Compiler changes (even patch releases) can alter what v8 reports as branches, which can silently break the filter.

After any `svelte` / `@sveltejs/kit` / `@sveltejs/vite-plugin-svelte` / `@vitest/coverage-v8` version bump:

1. **Eyeball the drop count.** Run `cd frontend && bun run test:coverage` and read the gate's final line: `Coverage gate: dropped N phantom branch(es)`. The baseline as of 2026-07-10 is **131 drops on 47 files** (grown from 46/21 on 2026-05-21 purely by feature-code growth, not compiler drift — the per-file phantom density is roughly constant).
2. **A >20% delta in either direction is a signal** — either the compiler emits new phantom shapes the filter doesn't catch (fewer drops, gate may fail on real-looking phantoms) or new shapes the filter wrongly classifies as phantom (more drops, real bugs hidden).
3. **Read the diff.** `git diff coverage/dropped-branches.json` (note: this file is gitignored on purpose, so the diff comes from a manual snapshot — copy it to `/tmp/dropped-before.json` before the upgrade, then diff against post-upgrade). Look for new branch shapes in the drop list that don't match the existing patterns documented in `coverage-gate.ts`.
4. **Refine the heuristic, not the threshold.** If you find a new phantom shape, extend `isPhantom` to recognize it AND add a self-test case to `coverage-gate.test.ts` that pins the classification. Never lower the per-file 100% target to absorb drift — that's how phantom-detection turns into bug-hiding.
5. **If you find a false-positive drop** (the filter dropped something a test could exercise): tighten the heuristic, then write the test for the real branch.

## Don't bypass the gate

- No `/* c8 ignore */` or `/* istanbul ignore */` comments in source. The gate doesn't read them. If you find yourself wanting one, the right answers are (a) write the test, (b) refactor the dead branch out (see `DrillCard.svelte` cloze-helper removal and `[lessonId]/+page.svelte:75` non-null-assertion changes in Phase 3 for the canonical pattern), or (c) extend the `isPhantom` heuristic with a new pinned classification.
- No `thresholds:` block re-added to `vite.config.ts`. The gate is the single source of truth.
