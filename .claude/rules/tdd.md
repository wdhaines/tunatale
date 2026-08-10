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

## ⚠️ The red commit cannot exist here — do not ask for one

Red-green-refactor is about the *working tree*, not about commits. **A commit
whose tests are deliberately red cannot pass the commit gate**, which requires
`./test.sh` green on the exact tree being committed. So "land the failing test
first, then the implementation" describes a commit this repo structurally
forbids.

This bites whenever a task says to rewrite a locked oracle *before* the change it
guards (`tunatale-om6` → F-14, 2026-08-10, is the worked example). The intent —
prove the new oracle discriminates, rather than writing it to fit code you
already wrote — is right and stays. What cannot happen is a separate red commit
in between.

**What to do instead:** verify red-then-green in the working tree, then commit
the oracle and the change together, and **record the measurement in the commit
message** — how many tests were red before, how many regression guards stayed
green. F-14's `48b0efb` does this: *"Verified red first: 12 discriminating tests
failed pre-change, 5 regression guards stayed green."*

**What this costs, honestly:** the evidence becomes a claim in prose rather than
something a reviewer can re-derive by checking out an intermediate commit. That
is a real reduction in verifiability and the reason to state the numbers
precisely. A reviewer who doubts it can still revert the source half locally and
re-run — but nobody can do it from history alone.
