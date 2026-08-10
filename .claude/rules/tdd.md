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

## ⚠️ Run the control before you write the finding

**When a probe disagrees with a design, that is evidence about the probe until a
control says otherwise.** Before reporting "X is broken", run the same command
against something whose answer you already know. If the control also fails, the
probe is broken and the finding is noise.

This is cheap and it is not optional. **Five times on 2026-08-10 a plausible
command produced a clean-looking wrong answer**, and four were nearly written up
as findings about someone else's work:

| probe | what it "showed" | the actual cause |
|---|---|---|
| `playwright --repeat-each=6` | a 50%-broken test on `main` | the spec is non-idempotent; repeats start from a dirty shared DB |
| `.dependencies[].depends_on_id` on `bd show --json` | a malformed `discovered-from` edge | `bd show` uses a different JSON shape than `bd list`/`bd export` |
| `bd init` + `bd dolt pull` | the backup restores **zero** issues | wrong command — `bd bootstrap` is the one that clones a remote |
| `bd bootstrap` in a public-repo clone | recovery is impossible | our `refs/dolt/data` is deliberately on the *private* remote |
| `bd list --type message` on a restored store | mail survives the Dolt push | `types.custom` is not restored, so the filter matches nothing |

The controls that caught them: running the flake probe at `HEAD~1` too (same
rate ⇒ measuring the harness, not the change), querying a known-good bead
beside the suspect one, and — the decisive one — noticing that a `bd show`
lookup failed for a bead that *definitely* existed, which exposed an unrelated
`beads.role` misconfiguration rather than missing data.

**The tell is a clean negative.** Zero results, `null`, an empty list — these
are what both "genuinely absent" and "wrong query" look like. A control
distinguishes them; re-reading your own command does not.

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
