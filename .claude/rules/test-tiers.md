---
paths:
  - "frontend/tests/**"
  - "frontend/src/**"
  - "frontend/playwright.config.ts"
  - "backend/tests/**"
---

# Test Tiers — what a test is ABOUT, and when it comes down

*Path-scoped rule: auto-loads wherever a test gets written. Decided 2026-08-14
(`tunatale-vnf.1`).*

This answers **what a test should be about and whether it is forever**.
`testing.md` § "What a green gate means" answers **where tests run and what green
means** (`tunatale-as5`). Adjacent questions, deliberately separate — as5 moved
no test between tiers, and this rule sets no policy about which gate runs what.

## The tiers

| tier | what belongs there |
|---|---|
| **Backend** (pytest) | everything expressible against the API or below |
| **Component** (vitest/jsdom) | logic, state, rendering decisions — anything the **app** computes |
| **E2E** (Playwright) | core user journeys end to end, and **seams** |

**Incident regressions are not a tier.** A regression test lands at the
*cheapest tier that can actually catch the bug*, which is usually not E2E. "A bug
happened here once" is never by itself a reason for a spec to be in Playwright.

## The discriminator: what makes something a seam

A seam is a place where **two systems must agree and neither can prove the join
alone**. Operationally that reduces to one question, checkable against a PR
without opening an argument:

> **Could you assert this without asking the browser to compute a number?**
> If yes, it is not E2E.

```
PASSES — engine computed it, stays E2E:
    doc.scrollWidth - doc.clientWidth        overflow
    el.getBoundingClientRect().left          geometry
    btn.scrollWidth > btn.clientWidth        clipping
    which element is painted on top          stacking

FAILS — the app computed it, belongs a tier down:
    el.classList.contains('blurred')
    el.textContent === 'Grade All'
    store.value / aria-expanded / disabled
```

The two seams this repo has today:

- **CORS** (`cors-lockdown.spec.ts`) — the backend grants the headers, the
  browser enforces them, and only a real browser can observe the join.
- **Layout** — the app declares *intent* (grid tracks, `rem` sizing,
  `flex-wrap`); the **engine**, plus platform font metrics and the user's root
  font size, decides the actual geometry. The app never computes the answer, so
  nothing below a real browser can check it.

### Why "layout is a seam" is not a loophole

**It is measured, not asserted.** Probed 2026-08-12 against the exact invariant
`listen-preview-layout.spec.ts` checks (a grid's header cell and row cell sharing
one left edge):

```
jsdom 29:      every rect x=0 w=0   | edges "agree": TRUE (0 === 0)
happy-dom 20:  every rect x=0 w=0   | edges "agree": TRUE (0 === 0)
```

Both pass **vacuously**. Moving a layout test to either does not make it cheaper
— it makes it green for free, which is the clean-negative trap of
`.claude/rules/tdd.md` wearing its most convincing disguise. There is no CSS
layout in JS without a browser engine, and "happy-dom is lighter than jsdom" is
irrelevant to that.

**And the rule still excludes what people want it to include.** "It touches the
DOM" is not a seam. Neither is "it renders". A file being *mostly* legitimate
E2E does not launder its other tests: several tests inside
`listen-preview-layout.spec.ts` are **composites** — e.g. "cancelling the
countdown neither swallows the click nor shifts the rows" is one app-computed
claim and one engine-computed claim welded together. Only the geometry half earns
the browser. Splitting those is the job of the E2E audit (`tunatale-kct`), not of
this rule.

## There is no fourth tier (evaluated and rejected)

**Vitest browser mode was a real candidate and is rejected as a home for layout
specs.** Adoption cost was genuinely low and that is not why:
`@vitest/browser@4.1.10` is an exact peer match to the installed vitest, its
Playwright provider uses the already-installed `@playwright/test@1.62.0`, no new
browser download — net one dev dependency.

**The reason is that page-level geometry is not composable from component-level
geometry.** The worked example is one commit old. `0fe42e6` fixed a non-wrapping
`rem`-sized nav row that overflowed a 320px viewport; the spec that caught it is
named for the *listen-preview modal*, and its own commit message records the
diagnosis:

> the offender is the nav BEHIND the overlay, not the modal being measured — so
> read the failing field before opening the component under test.

The assertion is `document.documentElement.scrollWidth - clientWidth`, on the
**document**. A component-mounted test of the modal renders no nav at all, so
that number is 0 and the test goes green while the bug ships. Any tier that
mounts a component in isolation structurally cannot see this class of defect, and
this class is most of what these specs actually catch.

⚠️ **Platform-dependence is NOT the argument against it, and was nearly written
up as if it were.** Layout specs are platform-dependent — CI (Linux) hits the
identical 2px overflow at an 18px root where macOS needs 20px — but Playwright
and vitest browser mode *both* run on whatever platform invokes them, and both
would run macOS locally and Linux in CI. That axis does not distinguish them.
Composability does. (Recorded because getting this backwards is easy and the
wrong version is persuasive.)

**Before reopening this**, if a spec ever appears whose invariant is provably
component-local: answer first whether a second vitest project is counted once,
twice, or not at all by `frontend/scripts/coverage-gate.ts` — it reads
`coverage/coverage-final.json` from the jsdom run against a 183-drop/53-file
baseline. Work that out *before* moving anything, or the 100% gate silently
starts measuring a different set.

## When a test comes down

Nothing in this repo had ever been retired before this rule existed. There is now
a retirement story, and it is a **proof, not a guess**.

**Criterion — the sabotage drill, applied retroactively.** Break the thing the
test names. If it cannot be made to go red, the behavior it guards no longer
exists and the test is decoration. If it goes red, it stays — no further
argument, no appeal to age or cost. (`testing.md` already requires this drill for
*new* sociable tests; this extends the same discriminator to removal.)

**Trigger, not schedule.** Run the drill when you are already in that file for
another reason, or when the spec is blocking a refactor. **Never as a calendar
sweep** — a scheduled audit re-reads tests whose original context is gone, and
that is exactly where wrong deletions come from.

**Consolidation is the default action; deletion is the exception.** Nine specs
asserting nine facets of one modal may legitimately be one parameterised spec.
That preserves every assertion, which deletion does not.

**Rejected as criteria: age, and last-failure date.** A test that never fails is
indistinguishable from a test that never *could* — which is precisely the
ambiguity the drill resolves. Use the discriminator, not the proxy. And a
regression guard that has never gone red is in its **success** case, not its
obsolescence case.

⚠️ **Both coverage gates stay at 100%.** This rule is about what tests are about
and where they live, never how many exist. **If a proposed criterion's effect is
"fewer tests, faster runs", it is the wrong criterion.** E2E is currently the CI
long pole (202s of a 203s wall clock) and that is a fact to plan around, not a
mandate to thin the suite. A faster run is a side effect you may enjoy; it is
never the reason.

## Retries stay 0, everywhere, forever

Settled here so it is not reopened by whoever is annoyed first. A spec that
passes only on retry is a flake you have chosen not to see. When the known open
flake (`tunatale-vnf.3`, the card-image thumbnail, possibly a real upload race)
reddens CI, **that is the policy working** — it gets fixed or quarantined by
name, never hidden behind a global `retries` bump. Read the uploaded HTML report
and traces before calling any red run flaky.
