# TDD Workflow

## Red-Green-Refactor

1. **Red**: Write a failing test that describes the desired behavior
2. **Green**: Write the minimum code to make the test pass
3. **Refactor**: Clean up without breaking the test

## Rules

- Never write implementation before the test file exists
- Each plan step: write ALL tests for that step first, then implement
- Tests must fail before implementation (verify with `pytest -x`)
- After each step: `./test.sh` must pass (lint + all tests + coverage)
- **Never declare victory with `./test.sh` failing** — fix all errors before moving on
- **Never commit with failing tests or coverage failures**

## Plan Step Ordering

Multi-step plans are ordered by dependency. Never implement step N+1 until step N's tests are green.

## Run a control before you write the finding

When a probe disagrees with a design, treat that as evidence about the probe
until a control says otherwise. Before reporting "X is broken", run the same
command against something whose answer you already know — the same probe at
`HEAD~1`, or a known-good record beside the suspect one. If the control also
fails, the probe is broken and the finding is noise. Plausible commands in this
repo have produced clean-looking wrong answers this way: a Playwright spec that
is not idempotent under `--repeat-each`, `bd show` and `bd list` using different
JSON shapes, and the wrong bd command for restoring a remote.

The tell is a clean negative. Zero results, `null`, and an empty list are what
both "genuinely absent" and "wrong query" look like; a control distinguishes
them, and re-reading your own command does not.

## Red-green happens in the working tree, not in commits

The commit gate requires `./test.sh` green on the exact tree being committed, so
a commit whose tests are deliberately red cannot exist here. When a task asks
for a failing oracle before the change it guards, keep the intent — prove the
new oracle discriminates rather than writing it to fit the code — by verifying
red-then-green in the working tree, then committing the oracle and the change
together. Record the measurement in the commit message, because it replaces the
intermediate commit a reviewer can no longer check out: how many tests were red
before and how many regression guards stayed green (e.g. *"Verified red first:
12 discriminating tests failed pre-change, 5 regression guards stayed green."*).
