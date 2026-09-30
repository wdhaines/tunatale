---
paths:
  - "frontend/**"
---

# Frontend Coverage Gate (Svelte 5 phantom filter)

*For which tier a frontend test belongs in, see `.claude/rules/test-tiers.md` —
including its note that adding a second vitest project would change what this
gate measures.*

The frontend requires 100% lines/branches/functions/statements per file, enforced
by `frontend/scripts/coverage-gate.ts`. Vitest's built-in `thresholds:` block is
intentionally absent; the custom gate is what enforces. It reads
`coverage/coverage-final.json`, filters the phantom branches the Svelte 5
compiler injects, then asserts 100% on every file.

## What counts as a phantom

`isPhantom(branchType, text, synthetic, duplicateRange)` in `coverage-gate.ts`
classifies each uncovered sub-location:

- **Synthetic or empty source range** → phantom (the compiler emitted a branch at
  a position the user source never reached).
- **cond-expr** (`?:`): phantom if the sub-location text is a JS literal (`null`,
  `undefined`, booleans, numbers, quoted strings), which Svelte 5 folds.
  Identifiers and property access stay real.
- **binary-expr** (`||`, `&&`, `??`): phantom if (a) the text starts with `}` or
  ends with `{` (a Svelte template-interpolation boundary), or (b) the text is a
  bare JS literal (a defensive fallback like `?? ''`), or (c) the uncovered
  operand's source range is identical to another operand's in the same branch.
  Case (c) exists because Svelte 5 compiles a `{expr}` that shares a text run
  with a sibling into `expr ?? ""`, and v8 maps both operands onto the source
  expression, so the text is an identifier or call (`t(`, `showAddPhrase`) that
  (b) cannot see; a real source `a ?? b` always has two distinct operand ranges.
  Object literals that start with `{` and end with `}` stay real.
- **if**: phantom only when the text is empty. Non-empty if-bodies are real.
- Unknown types stay real (conservative).

Every classification is pinned by `frontend/tests/coverage-gate.test.ts` against
real TunaTale cases (e.g., `'} created, {'` → phantom, `'e.message'` → real).
Adding or changing a rule means updating both.

## Maintenance — heuristic drift after Svelte upgrades

The heuristic depends on the shape of Svelte 5's compiled output, and compiler
changes (even patch releases) can alter what v8 reports as branches, silently
breaking the filter. Around any `svelte` / `@sveltejs/kit` /
`@sveltejs/vite-plugin-svelte` / `@vitest/coverage-v8` version bump:

1. **Measure before and after on the same tree.** Before the upgrade, run
   `cd frontend && bun run test:coverage`, note the gate's final line
   (`Coverage gate: dropped N phantom branch(es)`), and copy
   `coverage/dropped-branches.json` aside (it is gitignored, so git cannot diff
   it for you). Repeat after the upgrade. Compare the two runs rather than an old
   recorded figure: the drop count grows with feature code at a roughly constant
   per-file density, so only a same-tree comparison isolates compiler drift.
2. **A change of more than 20% in either direction is a signal.** Fewer drops
   means the compiler emits phantom shapes the filter misses (the gate may fail
   on real-looking phantoms); more drops means the filter wrongly classifies new
   shapes as phantom (real gaps hidden).
3. **Read the diff** of the two `dropped-branches.json` snapshots and look for
   branch shapes that match none of the patterns in `coverage-gate.ts`.
4. **Refine the heuristic, not the threshold.** For a new phantom shape, extend
   `isPhantom` to recognize it and add a self-test case to
   `coverage-gate.test.ts` that pins the classification. Never lower the per-file
   100% target to absorb drift — that is how phantom detection turns into bug
   hiding.
5. **If the filter drops something a test could exercise**, tighten the heuristic,
   then write the test for the real branch.

## Don't bypass the gate

- No `/* c8 ignore */` or `/* istanbul ignore */` comments in source; the gate
  doesn't read them. The right answers are (a) write the test, (b) refactor the
  dead branch out, or (c) extend `isPhantom` with a new pinned classification.
- Don't restructure markup just to dodge a phantom the filter misses — extend
  `isPhantom` instead.
- No `thresholds:` block re-added to `vite.config.ts`; the gate is the single
  source of truth.
