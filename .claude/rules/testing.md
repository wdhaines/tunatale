---
paths:
  - "backend/tests/**"
  - "test.sh"
---

# Testing Strategy

*The frontend coverage gate lives in `frontend-coverage-gate.md` (scoped to
`frontend/**`).*

## Which language a new test uses

**Norwegian** — it is the active development language. New backend tests use
`language_code="no"` / the `language_no` conftest fixture.

This is not only preference. The `no` plugin registers facets `sl` does not —
`lemma_plausible_fn`, `breakdown_spans_fn`, `alignment`, a syllabifier — so a
Slovene test silently skips those paths and a bug in one of them is unreachable.
For example, the listen preview once labelled a word `snøm`, a lemma fragment no
card is keyed on; it survived because every preview test was Slovene, where
`get_lemma_plausible("sl")` is `None` and the screen is a no-op.

- The `language` fixture is **Slovene** and is the obvious thing to reach for.
  Use `language_no` for new work; `language` stays for the ~100 modules already
  on it.
- Stub the lemmatizer rather than loading stanza:
  `tests/_helpers/lemmatizer.py::StubLemmatizer` plus
  `monkeypatch.setattr(srs_mod, "get_lemmatizer", …)`. Pattern:
  `test_api_base_cards.py::test_truncated_lemma_falls_back_to_surface`.
- Norwegian tests pass in CI: CI's backend jobs set no `TARGET_LANGUAGE`, and
  plugin `discover()` registers every language regardless. That does not cancel
  the local-vs-CI language trap, which concerns tests asserting the configured
  language *set* (`settings.database_urls`) — those still need their settings
  monkeypatched. Naming a language explicitly is fine.
- Don't retro-migrate. About 100 test modules are Slovene and most are
  language-agnostic in substance; churning them buys nothing and risks
  parity-sensitive tests.

## Test Types

- **Unit tests** — pure functions/models, no I/O, no network
- **Integration tests** — database, cassette-backed LLM calls
- **API tests** — FastAPI `ASGITransport` + `AsyncClient`, no real server

## Mocking Strategy

- **LLM calls**: always use `CassetteLLMClient` — never hit the live API in CI
- **Database**: use `sqlite:///:memory:` for SRS tests
- **TTS (Azure, Gemini)**: `respx` at the httpx transport
  (`tests/test_azure_tts.py`, `tests/test_gemini_tts.py`)
- **HTTP**: use `respx` for external HTTP calls in `LLMClient` tests

## Mock Boundaries (enforced)

**Mock only at process/network boundaries** — the anki driver subprocess
(`_run_driver`), the TTS vendors (Azure, Gemini), Pixabay/Forvo, Groq, the macOS
keychain. Never
`patch("app.…")` an internal function so that two halves of a flow are each
tested against a fake of the other: each half goes green and the bug lives in the
gap (the b0a4b8a regression class — seven regressions through a 100%-coverage
gate).

`backend/scripts/check_mock_boundaries.py` enforces this in `./test.sh` (after
ruff) and in the CI backend job. It AST-scans `backend/tests/**` for
`patch("app.…")` / `monkeypatch.setattr("app.…", …)` and fails on anything not
covered by `backend/tests/mock_allowlist.txt` — permanent fnmatch globs for true
boundaries (driver subprocess, network clients, `app.*.settings.*` config pins,
`_MEDIA_DIR`-style path-constant pins). Additions require the user's approval,
because a boundary claim is an architectural claim. There is no grandfather
ledger and no way to record a mock as debt; the allowlist is the only escape
hatch.

Known blind spots (documented in the script): `patch.object(obj, "name")` and the
2-arg `monkeypatch.setattr(obj, …)` aren't policed, since they are mostly settings
pins. Don't use them to smuggle an internal mock past the checker.

**When the checker fails on your new test**, test *through* the seam. The
canonical pattern is `TestSociableSync` (`test_anki_sync_orchestrator.py`): the
real `peer_sync` → `main` → `run_full_sync` pipeline runs against a real on-disk
`SyntheticCollection` at `settings.tt_collection_path`, with only `_run_driver`
replaced by a `fake_driver` fixture that returns canned response dicts and records
an op log. Assertions are outcomes (rows in the collection file, op-log leg
sequence, file bytes), not mock-call shapes.

## What a green gate means

**CI is authoritative, and `./test.sh` is a strict subset of it by construction:**
green locally is necessary but not sufficient, and green in CI is the claim that
counts. The subset direction is what makes that true, and it is maintained by
hand:

- **Adding a check to `./test.sh` obliges you to add it to
  `.github/workflows/ci.yml` in the same commit.** The reverse is not required.
- **CI may hold checks the local gate does not** — the one allowed direction of
  asymmetry.

### The asymmetries, all of them, deliberately

| CI-only | why it is not local |
|---|---|
| `backend-hostile-tz` (one instance, `Etc/GMT-3`) | another full suite run would multiply the local gate for an axis that changes only when a fixture does date arithmetic |
| `backend-hostile-hour` (computed 04:xx zone) | same, and its point is varying with wall-clock time, which a pre-commit gate cannot meaningfully sample |

**Local-only: nothing.** Keep it that way.

### CI installs only the `dev` group

**CI installs fewer packages than your laptop, deliberately.** Every backend job
runs `uv sync --no-default-groups --group dev` with **`UV_NO_SYNC: "1"` set at
job level**, so classla, stanza, torch and transformers are absent there.
`UV_NO_SYNC` is what makes it stick: a bare `uv run` inside the suite would
otherwise re-sync the environment to `[tool.uv] default-groups` mid-run. An
install-step flag on its own is therefore not evidence about the environment a
test ran in.

**What this costs in practice:** a test may import only what the **dev group**
declares. Anything arriving transitively through `slovene`/`norwegian`/
`alignment` — `yaml` via transformers is the usual example — is present locally
and missing in CI, so the suite goes green on your machine and dies at collection
in all four backend jobs with `ModuleNotFoundError`. Declare the package in
`dev`; don't widen CI's groups.

To reproduce CI's environment before pushing:

```bash
UV_PROJECT_ENVIRONMENT=/tmp/ci-venv uv sync --no-default-groups --group dev
UV_PROJECT_ENVIRONMENT=/tmp/ci-venv UV_NO_SYNC=1 uv run pytest …
```

Three smaller divergences are deliberate, not bugs:
- **The gitignored content directories.** `backend/media/` and
  `backend/output/audio/` have no tracked files, so they exist on every
  developer's disk and in no fresh checkout, and `GET /api/health` probes both by
  writing a real file, answering 503 when they are absent. CI provisions them
  explicitly (`mkdir -p`) rather than the app creating them at startup: a mkdir in
  the lifespan would create a plain directory where a volume failed to mount,
  which is the "unmounted volume reads green" bug `app/api/health.py` exists to
  prevent. Deployments must provision them too.
- **Coverage measures different sets.** Locally `--run-oracle` is folded into the
  covered pytest run; CI's `backend` job omits it and the `anki-gates` job runs
  those tests `--no-cov`. Both reach 100%, so CI's is the stricter claim (100%
  without oracle tests contributing).
- **ffmpeg** is installed on `backend`, the hostile jobs and `e2e`, but not on
  `anki-gates`, which never touches the audio pipeline.

## Test Tiers

*This section is about **where tests run**. For **what a test should be about** —
the seam discriminator that decides whether something belongs in Playwright, and
the sabotage-drill criterion for when a test comes down — see
`.claude/rules/test-tiers.md`.*

1. **`./test.sh`** (pre-commit, mandatory) — three parallel groups: backend (lint
   + format + checkers + full pytest incl. `--run-oracle`, with coverage),
   frontend (fmt + lint + svelte-check + vitest + Playwright e2e), and peer-sync.
2. **CI** (every push to `main` and every PR) — six parallel jobs: `backend`
   (unit + coverage + boundary check), `backend-hostile-tz`,
   `backend-hostile-hour`, `frontend`, `e2e`, and `anki-gates` (oracle parity +
   peer-sync). An oracle or peer-sync failure is a parity/round-trip regression,
   not a unit bug — debug it as such. A hostile-tz/hour failure is usually a
   fixture doing date arithmetic across `ANKI_ROLLOVER_HOUR` — suspect the test
   before the product code.

   **A fixture that asserts a day fact must declare its zone**, with
   `tests/_helpers/localtz.py` (`local_timezone`, `timezone_with_local_hour`).
   The col-day boundary is 04:00 *local*, so which timestamps share a col-day
   depends on the reader's zone — an undeclared zone is an assumption, not a
   default. Pin the narrowest scope that fails (one test or one class), never a
   whole module: over-pinning blinds the hostile jobs to the real zone bugs they
   exist for.

   **`backend-hostile-hour` samples one offset per run, not the band.**
   `.github/actions/hostile-hour-tz` derives its zone from the current UTC hour so
   that local time is 04:00 — 16:0x UTC gives `Etc/GMT-12`, 18:43 UTC gives
   `Etc/GMT-10`. A history of green runs is therefore not evidence that a
   zone-dependent fixture is sound: fixtures red at 5 of 27 offsets have stayed
   green in CI until a push happened to land on a failing hour. Sweeping offsets
   in CI was rejected because job count drives CI's tail latency, so the defence
   is the zone rule above. To check a day fixture by hand:
   `for n in $(seq 0 14); do TZ=Etc/GMT-$n uv run pytest <files> -q --no-cov; done`
   (about 25s for two files across 27 zones; the red set moves with the wall
   clock, so re-run rather than trusting recorded zone names).

Peer-sync runs in every `./test.sh` against an auto-started throwaway
`anki.syncserver` (session fixture in `tests/_helpers/sync_server.py`; under
`--run-peer-sync` an unstartable server fails, never skips), so a sync round-trip
regression is caught before pushing rather than after.

**Playwright retries are 0, everywhere, on purpose** — see the comment block in
`frontend/playwright.config.ts`. A spec that passes only on retry is a flake you
have chosen not to see. CI uploads the HTML report and traces on failure; read
those before calling a red run flaky.

A sociable/outcome test earns its keep by the **sabotage drill**: disable the
phase it guards (e.g. comment out `sync_create_new` in `run_full_sync`), watch
the test go red, revert, watch it go green. A test that can't be shown to catch
its target bug is decoration.

## Cassette System

Cassettes live in `backend/tests/cassettes/`. Each cassette is a JSON file of
recorded LLM prompt/response pairs indexed by SHA256 hash.

### Modes
- `mock` (default, CI): replay from cassette; skip if the cassette is missing
- `record`: call the real LLM and save to cassette
- `live`: call the real LLM without saving
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

Target: 100% line coverage (`fail_under = 100` in pyproject.toml). Run with
`uv run pytest`. The CLI generator script `build_function_word_list.py` is
excluded via `coverage.run.omit`.

### Pragma Discipline

`# pragma: no cover` lowers the gate; it doesn't pass it. Before adding one:

1. **Try to write the test first.** Most "uncoverable" branches turn out to be
   testable with `caplog`, a connection-state fixture, or a small refactor that
   eliminates a dead branch.
2. **Acceptable uses:** the `if __name__ == "__main__":` CLI guard, and
   defensive branches that are genuinely unreachable (e.g., re-checking an
   invariant guaranteed upstream — the comment must say *why* it is unreachable,
   not just that it is).
3. **Not acceptable:** "always true in tests," "pass is a no-op," "would require
   complex setup," "TODO test later." If the justification describes the test
   scenario itself ("always X in tests"), the branch is reachable — write the
   assertion.

When reviewing a PR with new pragmas, read each justification skeptically. If
the comment describes a scenario the tests do hit, the pragma is hiding a missing
assertion, not an unreachable branch.
