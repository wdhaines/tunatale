---
paths:
  - "backend/tests/**"
  - "test.sh"
---

# Testing Strategy

*Path-scoped rule: auto-loads when a backend test file is read. The frontend coverage gate lives in `frontend-coverage-gate.md` (scoped to `frontend/**`).*

## Test Types

- **Unit tests** — pure functions/models, no I/O, no network
- **Integration tests** — database, cassette-backed LLM calls
- **API tests** — FastAPI `ASGITransport` + `AsyncClient`, no real server

## Mocking Strategy

- **LLM calls**: Always use `CassetteLLMClient` — never hit live API in CI
- **Database**: Use `sqlite:///:memory:` for SRS tests
- **EdgeTTS**: Mock `edge_tts.Communicate` with pytest-mock
- **HTTP**: Use `respx` for external HTTP calls in `LLMClient` tests

## Mock Boundaries (enforced)

**Mock only at process/network boundaries** — the anki driver subprocess (`_run_driver`), EdgeTTS, Pixabay/Forvo, Groq, the macOS keychain. Never `patch("app.…")` an internal function so that two halves of a flow are each tested against a fake of the other: that's how b0a4b8a shipped 7 regressions through a 100%-coverage gate (each half green, the bug in the gap).

A mechanical checker enforces this. `backend/scripts/check_mock_boundaries.py` runs in `./test.sh` (after ruff) and in the CI backend job; it AST-scans `backend/tests/**` for `patch("app.…")` / `monkeypatch.setattr("app.…", …)` and fails on anything not covered by:

- **`backend/tests/mock_allowlist.txt`** — permanent fnmatch globs for true boundaries (driver subprocess, network clients, `app.*.settings.*` config pins, `_MEDIA_DIR`-style path-constant pins). Additions require user approval — a boundary claim is an architectural claim.

There is **no grandfather ledger** — the allowlist is the only escape hatch. The shrink-only `mock_grandfather.txt` drained 22 → 0 entries and was deleted on 2026-07-30, along with its ratchet and the scope-keyed touch-rule that nagged about its entries. An empty shrink-only ledger and "no additions, period" are the same rule; the second needs no machinery.

Known blind spots (documented in the script): `patch.object(obj, "name")` and 2-arg `monkeypatch.setattr(obj, …)` aren't policed — they're predominantly settings pins. Don't exploit this to smuggle an internal mock past the checker.

**When the checker fails on your new test**: the fix is to test *through* the seam. There is nowhere to record it as debt, deliberately — the allowlist is a claim that the target IS a process/network boundary (an architectural statement needing user approval), not a place to park a mock. The canonical pattern is `TestSociableSync` (`test_anki_sync_orchestrator.py`): the real `peer_sync` → `main` → `run_full_sync` pipeline runs against a real on-disk `SyntheticCollection` at `settings.tt_collection_path`, with ONLY `_run_driver` replaced by a `fake_driver` fixture that returns canned response dicts and records an op log. Assertions are **outcomes** (rows in the collection file, op-log leg sequence, file bytes), not mock-call shapes.

## What a green gate means

**CI is authoritative. `./test.sh` is a strict SUBSET of it, by construction.**
(Decided 2026-08-14, `tunatale-as5`.) In one line:

> Green locally is necessary but not sufficient; green in CI is the claim that counts.

Before this, neither gate contained the other — Playwright was local-only,
peer-sync was CI-only — so "green" had two meanings and a change could satisfy
either while breaking the other. The subset direction is what makes the sentence
above true rather than aspirational, and it is maintained by hand:

- **Adding a check to `./test.sh` obliges you to add it to `.github/workflows/ci.yml` in the same commit.** The reverse is not required.
- **CI may hold checks the local gate does not.** That is the allowed direction of asymmetry, and today it holds exactly one class of them.

### The asymmetries, all of them, deliberately

| CI-only | why it is not local |
|---|---|
| `backend-hostile-tz` (UTC+14, UTC−12) | a second and third full suite run would triple the local gate for an axis that changes only when a fixture does date arithmetic |
| `backend-hostile-hour` (computed 04:xx zone) | same; and its whole trick is varying with wall-clock time, which a pre-commit gate cannot meaningfully sample |

**Local-only: nothing.** Keep it that way.

### The dependency-group split is deliberate, not an oversight

Local installs all groups; CI runs `--no-group slovene --no-group norwegian
--no-group alignment`. So `build_slicers` wires a real Norwegian syllable slicer
locally (`Syllable slicing enabled for: no` in every local run) and an empty dict
in CI, where those paths are exercised only by `find_spec`-monkeypatched unit
tests.

**This is a chosen trade, not a gap nobody noticed** (2026-08-14): installing the
lemmatizer/alignment groups in CI means a ~1.2 GB model download on six jobs per
push, and the paths in question are covered locally on every commit. **Owner: the
local gate.** The consequence to accept is that a break confined to real-slicer
behaviour surfaces on a developer's machine, never on a push — so when touching
`build_slicers` or a syllabifier, the local run is the gate that matters and a
green CI proves nothing about it.

⚠️ Two smaller divergences that are NOT bugs, so nobody re-files them:
- **Coverage measures different sets.** Local folds `--run-oracle` into the covered pytest run; CI's `backend` job omits it and a separate `oracle-parity` job runs those tests `--no-cov`. Both reach 100%, so CI's is the *stricter* claim (100% without oracle tests contributing). Fine under "CI authoritative".
- **ffmpeg** is installed on `backend`, the hostile jobs and `e2e`, but not on `oracle-parity` / `peer-sync` — they never touch the audio pipeline.

## Test Tiers

1. **`./test.sh`** (pre-commit, mandatory) — three parallel groups: backend (lint + format + checkers + full pytest incl. `--run-oracle`, with coverage), frontend (fmt + lint + svelte-check + vitest + Playwright e2e), and peer-sync.
2. **CI** (every push to `main` / every PR) — eight parallel job instances: `backend` (unit + coverage + boundary check), `backend-hostile-tz` (×2), `backend-hostile-hour`, `frontend`, `e2e`, `oracle-parity`, `peer-sync`. An oracle or peer-sync failure is a parity/round-trip regression, not a unit bug — debug it as such. A hostile-tz/hour failure is a fixture doing date arithmetic across `ANKI_ROLLOVER_HOUR` — suspect the test before the product code.

Peer-sync used to be tier 2 and manual. It is tier 1 as of 2026-08-14: it runs in
every `./test.sh`, against an **auto-started** throwaway `anki.syncserver`
(session fixture in `tests/_helpers/sync_server.py`; no manual server; under
`--run-peer-sync` an unstartable server FAILS, never skips). It was promoted
because CI ran it on every push while the pre-commit gate never did, which made a
sync round-trip regression discoverable only *after* pushing.

**Playwright retries are 0, everywhere, on purpose** — see the comment block in
`frontend/playwright.config.ts`. A spec that passes only on retry is a flake you
have chosen not to see, and this suite has a known open one (`tunatale-vnf.3`)
that may be a real upload race. CI uploads the HTML report and traces on failure;
read those before calling a red run flaky.

A sociable/outcome test earns its keep by the **sabotage drill**: disable the phase it guards (e.g. comment out `sync_create_new` in `run_full_sync`), watch it go red, revert, watch it go green. A net that can't be proven to catch its target bug is decoration — see the Phase 7 commit messages (2026-06-10) for the recorded drills.

## Cassette System

Cassettes live in `backend/tests/cassettes/`. Each cassette is a JSON file containing recorded LLM prompt/response pairs indexed by SHA256 hash.

### Modes
- `mock` (default, CI): replay from cassette; skip if cassette missing
- `record`: call real LLM and save to cassette
- `live`: call real LLM without saving
- `patch`: replay known prompts; record new ones

### Running modes
```bash
# Default (CI-safe):
uv run pytest

# Record new cassettes (requires GROQ_API_KEY):
uv run pytest --llm-mode=record

# Update specific cassettes:
uv run pytest --llm-mode=patch
```

## Coverage

Target: 100% line coverage (strict `fail_under = 100` in pyproject.toml). Run with `uv run pytest`. The CLI generator script `build_function_word_list.py` is excluded via `coverage.run.omit`.

### Pragma Discipline

`# pragma: no cover` lowers the gate; it doesn't pass it. Before adding one:

1. **Try to write the test first.** Most "uncoverable" branches turn out to be testable with `caplog`, a connection-state fixture, or a small refactor that eliminates a dead branch.
2. **Acceptable uses:** the `if __name__ == "__main__":` CLI guard, and defensive branches that are genuinely unreachable (e.g., re-checking an invariant guaranteed upstream — and the comment must say *why* it's unreachable, not just that it is).
3. **Not acceptable:** "always true in tests," "pass is a no-op," "would require complex setup," "TODO test later." If the justification describes the test scenario itself ("always X in tests"), the branch is reachable — write the assertion.

When reviewing a PR with new pragmas, read each justification skeptically. If the comment describes a scenario the tests do hit, the pragma is hiding the absence of an assertion, not an unreachable branch.

See commit `63bfd94` for the Stage 2 incident: two pragmas with self-contradictory justifications ("cache always empty in tests" on the path tests do hit) were removed and replaced with real `caplog` + branch-coverage tests.

## Frontend Coverage Gate

Moved to `.claude/rules/frontend-coverage-gate.md` (path-scoped to `frontend/**`): the Svelte 5 phantom-branch filter, upgrade-drift maintenance, and the no-bypass rules.

