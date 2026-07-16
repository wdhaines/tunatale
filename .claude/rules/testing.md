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
- **`backend/tests/mock_grandfather.txt`** — exact `file<TAB>target<TAB>count` ledger of pre-existing internal mocks. **Shrink-only ratchet**: counts may only go down; the checker tells you the exact line edit when a count changes. Never add a line. Regenerate with `--write-grandfather` (allowlisted targets are excluded — pinned by a unit test).

Known blind spots (documented in the script): `patch.object(obj, "name")` and 2-arg `monkeypatch.setattr(obj, …)` aren't policed — they're predominantly settings pins. Don't exploit this to smuggle an internal mock past the checker.

**When the checker fails on your new test**: the fix is to test *through* the seam, not to grandfather it. The canonical pattern is `TestSociableSync` (`test_anki_sync_orchestrator.py`): the real `peer_sync` → `main` → `run_full_sync` pipeline runs against a real on-disk `SyntheticCollection` at `settings.tt_collection_path`, with ONLY `_run_driver` replaced by a `fake_driver` fixture that returns canned response dicts and records an op log. Assertions are **outcomes** (rows in the collection file, op-log leg sequence, file bytes), not mock-call shapes.

## Test Tiers

1. **`./test.sh`** (pre-commit, mandatory) — lint + format + mock-boundary check + full pytest incl. `--run-oracle` + frontend + Playwright e2e.
2. **`cd backend && uv run pytest tests/test_anki_peer_sync_selfhost.py --run-peer-sync --no-cov`** — real round-trips against an **auto-started** throwaway `anki.syncserver` (session fixture in `tests/_helpers/sync_server.py`; no manual server; under the flag an unstartable server FAILS, never skips). Run when touching sync/orchestrator/driver/media code.
3. **CI** (every push/PR) — four parallel jobs: backend (unit + coverage + boundary check), frontend, oracle-parity, peer-sync. An oracle or peer-sync failure is a parity/round-trip regression, not a unit bug — debug it as such.

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

