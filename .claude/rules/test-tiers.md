---
paths:
  - "frontend/tests/**"
  - "frontend/src/**"
  - "frontend/playwright.config.ts"
  - "backend/tests/**"
---

# Test Tiers — what a test is ABOUT, and when it comes down

This answers **what a test should be about and whether it is forever**.
`testing.md` § "What a green gate means" answers **where tests run and what green
means**. The two are deliberately separate: this rule sets no policy about which
gate runs what.

## The tiers

| tier | what belongs there |
|---|---|
| **Backend** (pytest) | everything expressible against the API or below |
| **Component** (vitest/jsdom) | logic, state, rendering decisions — anything the **app** computes |
| **E2E** (Playwright) | core user journeys end to end, and **seams** |

**Incident regressions are not a tier.** A regression test lands at the
*cheapest tier that can actually catch the bug*, which is usually not E2E. "A bug
happened here once" is never by itself a reason for a spec to be in Playwright.

## Admission to E2E: two doors, and only one uses the discriminator

A spec earns a Playwright slot by one of two routes, and it matters which:

1. **It is a core user journey** — a path a user actually walks, end to end,
   across the real stack. `smoke`, `review-flow` and `planner-chat` live here.
   Their assertions are app-computed (visibility, `textContent`, URL), and that is
   fine: the claim is "this whole path works joined up", which no tier below can
   make. A journey is admitted on the path it walks, not on what it reads.
2. **Everything else in E2E must be a seam**, and clears the bar below.

Don't apply the discriminator to a core journey and conclude it should move down.
That reading empties the tier of the journeys it exists for; the question below is
an admission test for the seam door only.

## The discriminator, for everything that is not a journey

A seam is a place where **two systems must agree and neither can prove the join
alone**. Operationally, one question, checkable against a PR:

> **Is the value you are asserting computed by the BROWSER, or by the APP?**
> Browser-computed → E2E. App-computed → a tier down.

"Browser-computed" is wider than geometry. It is anything the app hands to the
engine and gets a verdict back on: layout and paint (rects, overflow, wrapping,
stacking), the security policy (`cors-lockdown` — preflight and enforcement), the
service worker and HTTP range machinery (`offline-audio`). For the layout case,
which is most of them in practice: *could you assert this without asking the
browser to compute a number?*

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

- **CORS** (`cors-lockdown.spec.ts`) — the backend grants the headers, the browser
  enforces them, and only a real browser can observe the join.
- **Layout** — the app declares *intent* (grid tracks, `rem` sizing,
  `flex-wrap`); the **engine**, plus platform font metrics and the user's root
  font size, decides the actual geometry. The app never computes the answer, so
  nothing below a real browser can check it.

### Why "layout is a seam" is not a loophole

**It is measured.** Against the invariant `listen-preview-layout.spec.ts` checks
(a grid's header cell and row cell sharing one left edge):

```
jsdom 29:      every rect x=0 w=0   | edges "agree": TRUE (0 === 0)
happy-dom 20:  every rect x=0 w=0   | edges "agree": TRUE (0 === 0)
```

Both pass vacuously. Moving a layout test to either does not make it cheaper — it
makes it green for free, the clean-negative trap of `.claude/rules/tdd.md` in its
most convincing disguise. There is no CSS layout in JS without a browser engine;
"happy-dom is lighter than jsdom" is irrelevant to that.

**And the rule still excludes what people want it to include.** "It touches the
DOM" is not a seam; neither is "it renders". A file being mostly legitimate E2E
does not launder its other tests. Some tests in `listen-preview-layout.spec.ts`
are composites — "cancelling the countdown neither swallows the click nor shifts
the rows" is one app-computed claim and one engine-computed claim welded
together. Only the geometry half earns the browser; split such tests when you
next work in the file.

## There is no fourth tier

**Vitest browser mode is rejected as a home for layout specs.** Adoption cost is
low (one dev dependency, and its Playwright provider reuses the installed
Playwright), and that is not the reason.

**The reason is that page-level geometry is not composable from component-level
geometry.** A non-wrapping `rem`-sized nav row once overflowed a 320px viewport;
the spec that caught it is named for the listen-preview modal, because the
offender was the nav behind the overlay, not the modal being measured. Read the
failing field before opening the component under test. The assertion is
`document.documentElement.scrollWidth - clientWidth`, on the **document**. A
component-mounted test of the modal renders no nav, so that number is 0 and the
test goes green while the bug ships. Any tier that mounts a component in
isolation structurally cannot see this class of defect, and this class is most of
what these specs catch.

Platform-dependence is not the argument against it. Layout specs are
platform-dependent — CI (Linux) hits the same 2px overflow at an 18px root where
macOS needs 20px — but Playwright and vitest browser mode both run on whatever
platform invokes them, so that axis does not distinguish them. Composability
does.

**Before reopening this**, if a spec ever appears whose invariant is provably
component-local, first work out whether a second vitest project would be counted
once, twice or not at all by `frontend/scripts/coverage-gate.ts` (it reads
`coverage/coverage-final.json` from the jsdom run). Settle that before moving
anything, or the 100% gate silently starts measuring a different set.

## When a test comes down

Retirement is a proof, not a guess.

**Criterion — the sabotage drill, applied retroactively.** Break the thing the
test names. If it cannot be made to go red, the behavior it guards no longer
exists and the test is decoration. If it goes red, it stays — no further
argument, no appeal to age or cost. (`testing.md` already requires this drill for
new sociable tests; this extends the same discriminator to removal.)

**Trigger, not schedule.** Run the drill when you are already in that file for
another reason, or when the spec blocks a refactor — never as a calendar sweep. A
scheduled audit re-reads tests whose original context is gone, which is exactly
where wrong deletions come from.

**Consolidation is the default action; deletion is the exception.** Nine specs
asserting nine facets of one modal may legitimately be one parameterised spec,
which preserves every assertion; deletion does not.

**Age and last-failure date are not criteria.** A test that never fails is
indistinguishable from one that never *could*, which is the ambiguity the drill
resolves. A regression guard that has never gone red is in its success case, not
its obsolescence case.

**Both coverage gates stay at 100%.** This rule is about what tests are about and
where they live, never how many exist. If a proposed criterion's effect is "fewer
tests, faster runs", it is the wrong criterion. E2E is often CI's long pole;
that is a fact to plan around, not a mandate to thin the suite.

## Retries stay 0, everywhere

A spec that passes only on retry is a flake you have chosen not to see. When a
flaky spec reddens CI, that is the policy working: fix it, or quarantine it by
name — never hide it behind a global `retries` bump. Read the uploaded HTML report
and traces before calling any red run flaky.
