---
paths:
  - "backend/tests/test_parity_*.py"
  - "backend/tests/anki_oracle/**"
---

# Anki Oracle Test Harness

*Path-scoped rule: auto-loads when a file matching the `paths:` frontmatter is read.*

Required reading before adding a test under `backend/tests/test_parity_*.py` or modifying anything in `backend/tests/anki_oracle/`. The harness was built in Phase 2 of the simplify effort (commit `0c076fe` and the Phase 2.2.x commits) to pin TT↔Anki parity end-to-end. It surfaced two findings on first use (Layer 42 — a real lapse-stability bug; Layer 43 — Layer 38's "NULL-R at dr position" was a coincidence of `elapsed≈ivl`).

## What the harness is

A pytest fixture (`synthetic_collection`) that builds a minimal `collection.anki2` in memory, plus a subprocess driver (`oracle.py`) invoked via `uv run --with anki python` to run Anki's actual scheduler against the file and return JSON outputs (queue order, R values, post-grade states, next-state predictions per rating).

```
backend/tests/anki_oracle/
├── synthetic_collection.py   # High-level builder for collection.anki2
├── oracle.py                 # Subprocess: opens collection, runs ops, dumps JSON
└── harness_fixtures.py       # pytest fixtures + run_oracle() helper
```

Tests live alongside other tests as `backend/tests/test_parity_*.py`. They're opt-in via `--run-oracle`: `./test.sh` passes the flag so local pre-commit runs the harness, and CI runs them in the **`anki-gates` job** (`.github/workflows/ci.yml`: warm the isolated anki env, then `pytest -m oracle --run-oracle -n auto --no-cov`) so an oracle failure is never conflated with a unit failure. Every oracle-gated test must carry `@pytest.mark.oracle` or the CI job won't select it. If you need to skip the harness locally for speed, run `cd backend && uv run pytest` directly.

⚠️ **`anki-gates` runs at Anki's 04:00 rollover, not at the workflow's `TZ: UTC`** (2026-09-03). It resolves a zone whose LOCAL clock is inside `[04:00, 05:00)` via `.github/actions/hostile-hour-tz`, so every run exercises the day boundary instead of reaching it by luck. Why: between the oracle gate landing in CI (2026-06-10) and 2026-09-03, exactly **1 of 598 runs** ever started in that hour, and it went red the first time it did. `backend-hostile-hour` had sat in the band on every run for months but does not pass `--run-oracle`, so it had never run a single parity test — the hole was at the intersection of two correctly-configured jobs.

**Consequence for triage, and it is the opposite of `backend-hostile-tz`:** a boundary-only failure there is usually a fixture encoding a wall-clock assumption. Here, suspect PRODUCT code first — these tests compare against the real backend, so a failure at the boundary is TT and Anki genuinely disagreeing about what day it is. Only this job has an oracle to tell the two readings apart.

**A test asserting an absolute day index must pin its zone** (`tests/_helpers/localtz.py`: `local_timezone`, `timezone_with_local_hour`). The quantity is a local calendar day, so it is genuinely zone-dependent; declaring the zone is the opposite of assuming one. `local_timezone` sets `TZ` in the environment, which the oracle subprocess inherits — both sides must agree about what day it is.

### Ops the driver understands

`get_queue` (cards + post-limit `counts` + per-rating `states`), `scheduling_states` (intervals only), `answer_card` (grades for real, returns post-grade `stability`/`difficulty`), `get_card`, `get_revlog`, `note_ords`, `set_config`, `add_review_cards`, `check_database`, plus:

- `get_today` — `col.sched.today` **and** `day_cutoff`, Anki's `next_day_at`. The answering path measures elapsed as a duration back from `day_cutoff`, so it is what a grade-elapsed question must be checked against.
- `deck_today` — a deck's `newToday` / `revToday` as `[last_day_studied, count]`, alongside `today` and the post-limit `new_count`. Anki charges the daily limit only when the stamp equals its own `today`, so the pair says both what TT wrote and whether Anki accepted it.

## Subprocess boundary — never violate

**Backend production code (`backend/app/**`) must never `import anki`.** That's a runtime dependency on Anki being installed in the production environment, which breaks the "Anki = reference, not runtime dependency" principle (queue-parity rule 1).

The harness imports anki in a **separate Python process** spawned via `uv run --with anki python oracle.py`. Backend tests never import anki either — they call `run_oracle(collection_path, operations)` which builds the subprocess command.

If you find yourself wanting to call Anki from a test fixture or backend module, stop. The right shape is: build a synthetic collection at the SQLite level, dump it to disk, hand the path to the subprocess.

## When to write a harness test vs a unit test

**Write a harness test (`test_parity_*.py`)** when the question is:
- "Does TT produce the same output as Anki for this input?"
- "Does TT's queue order match Anki's for this card configuration?"
- "Does TT's FSRS computation match Anki's per-rating next-state?"

**Write a unit test (`test_*.py`)** when the question is:
- "Does TT crash on this malformed input?" (harness uses well-formed inputs only)
- "Does TT's `/api/srs/feedback` endpoint return the right JSON shape?" (TT API contract, not parity)
- "Does TT's `session_main_queue` cache invalidate at the right moments?" (TT-internal invariant)
- "Does TT's `count_reviews_completed_today` exclude buried directions?" (TT SQL behavior)

Heuristic: if a failure mode is "TT and Anki produce different outputs for this input" → harness. If it's "TT does something forbidden on the way to the right answer" or "TT crashes" → unit test.

## Synthetic collection — what's modeled, what's not

`SyntheticCollection` writes a modern-format `collection.anki2` (schema v18). The builder methods are deliberately small — extend the builder, don't ad-hoc raw-SQL the collection in your test.

**Modeled.** `col` table, `notes`, `cards` (with `data.s` / `data.d` / `data.lrt` / `data.dr`), `revlog`, `decks` + `deck_config` (modern protobuf), `notetypes` + `fields` + `templates`, `config` table (modern). FSRS-5 weights + desired_retention + new/reviews_per_day + learn_steps + relearn_steps + review_order. The V3 scheduler is enabled automatically by `oracle.py` after open (`col.set_v3_scheduler(True)`).

**Multi-template notetypes: modeled as of 2026-08-17** (`tunatale-qf6.2`). `add_notetype(..., templates=[(name, qfmt, afmt), …])` writes real `templates.config` blobs via production's own `build_template_config`. The older `template_count=N` form still writes anonymous `Card N` templates with an **empty** config, which stays the default because the scheduling tests never render a card.

**Use `templates=` whenever the subject is card generation.** Anki decides whether to create a card for an ord by asking whether that template's front renders non-empty (`cardgen.rs::new_cards_required_normal`), so an empty config makes every front empty and the generator a silent no-op — a test that passes vacuously. Phase 2.2.4's sibling-bury parity was blocked on this gap; a cross-direction queue test is now buildable.

**Not modeled (extend the builder if you need these).**
- **Time-travel.** `col.crt` is fixed at 2024-01-01 UTC and the subprocess's `now` is real wall-clock time. Day-rollover unbury timing (Layer 27/35) can't be tested cleanly.
- **Revlog history beyond what `add_revlog` writes.** No automatic computation of `cards.data` from revlog; you write both directly.

## Gotchas (each one cost real debugging time during Phase 2)

These are documented in the test docstrings too, but listed here for fast recall.

1. **`cards.data` needs `s` AND `d` AND `dr` AND `lrt` for the FSRS path.** Missing `lrt` → Anki sees `days_elapsed=0` → routes through `stability_short_term` instead of `stability_after_success`. Missing `dr` → Anki's queue-sort SQL function falls back to SM2 → all FSRS cards tie at the same near-zero value and queue order goes pseudo-random. `add_card(stability=..., difficulty=..., last_review_secs=..., desired_retention=...)` writes all four.

2. **`schedVer=2` and `fsrs=true` must be in the `config` table, not just `col.conf`.** Modern Anki's `ConfigManager` reads through the Rust backend from the `config` table; `col.conf` JSON is legacy and ignored. `SyntheticCollection.enable_fsrs()` writes both.

3. **`review_order` defaults to `RETRIEVABILITY_ASCENDING` (proto value 7), not Anki's app-default `DAY` (0).** Without this, parity tests against TT's R-asc queue assembly compare different orderings on the two sides and look like divergence. `_make_deck_config_blob` writes field 33 = 7 by default.

4. **`learn_steps` / `relearn_steps` are `repeated float` (packed LEN-delimited f32), not VARINT.** Earlier code wrote them as VARINTs and Anki silently fell back to defaults `[1.0, 10.0]` / `[10.0]`. Use `_packed_float_field` (already wired into `_make_deck_config_blob`).

5. **`QueuedCard.card` is a protobuf message with different field names from the Python `anki.cards.Card` class.** `ctype` not `type`, `interval` not `ivl`, `remaining_steps` not `left`. `_serialize_card` in `oracle.py` normalizes back to the Python-class names for tests to consume.

6. **`Card.memory_state` is a property in current anki, not a method.** Don't call it.

7. **`col.sched.counts()` returns `tuple[int, int, int]`** (new, learning, review), not an object with named attributes.

8. **`due > 365_000` triggers a different `days_elapsed` formula** inside Anki's `extract_fsrs_relative_retrievability`. The cutoff is a sentinel for "(re)learning cards encoded as Unix timestamps." Stay below it for review-card tests.

9. **`due=0, ivl=10` for a NULL-R card lands at the queue tail.** SM2 fallback `-(elapsed/ivl)` evaluates to `-0.0001` because of saturating-`u32` wraparound on `review_day = due - interval = -10`. Use `due=today_col_day, ivl=N → elapsed=N` to land NULL-R near the dr position (Layer 43).

10. **Never compute Anki's `today` Python-side.** Naive UTC day division (`(now - col.crt) // 86400`) ignores the local-TZ 4AM rollover. Between local midnight and 4AM, the naive UTC day has advanced but Anki's `today` has NOT — so `due=naive_today` lands one day in Anki's **future**, the card isn't due yet, and it's absent from the queue entirely (a past-due card would still be gathered). Passes in EDT afternoons, fails in UTC CI (and on any machine in the midnight–4AM window). Use the `get_today` oracle op (`col.sched.today`) — Anki's authoritative day index. Found on the first Linux/UTC CI run (2026-06-10), LAYER_38.

    ⚠️ **This rule was right and was scoped to the wrong half of the codebase for three months.** It said "in tests"; the identical arithmetic was sitting in PRODUCTION as `compute_anki_day_index`, and on 2026-09-03 it broke `anki-gates` from there (Anki=127 vs TT=126). In production the answer is `anki_today_col_day`, not `compute_anki_day_index` — see that function's docstring for the two-domain split, and never introduce a third. When a harness gotcha describes a *formula* rather than a fixture, ask whether the shipped code has the same one before filing it under "testing".

    Seeding a `due` from `anki_today_col_day` (rather than a prior `get_today` call) is now the cheaper move for a card that must be due today, because it needs no extra oracle round-trip — but see gotcha 13 for the ordering constraint that makes the two-call form fail silently.

11. **`col.fix_integrity()` (Check Database) reports failure by RETURN VALUE, not by raising** — and an aborted check generates no cards, which is indistinguishable from Anki *deciding* not to generate. The synthetic collection's schema is minimal, so a whole-collection operation walks into tables the scheduler tests never touch: dbcheck opens with `select tag from tags where collapsed = false`, the fixture's `tags` table was missing `collapsed`, and dbcheck returned `(DbError{…no such column…}, False)` with the notes loop never running. The `check_database` op returns `ok` separately for exactly this reason — **assert on it**. If you extend the fixture for another whole-collection operation, expect the same class of gap, and take the DDL from the real collection (`SELECT sql FROM sqlite_master WHERE name='…'`) rather than from what looks plausible. This is `.claude/rules/tdd.md`'s clean-negative trap in its harness costume: the probe disagreed with a measurement against a real anki-built collection, and the *probe* was wrong.

12. **Seed the collection BEFORE the first `run_oracle` call.** Opening the file with Anki rewrites it; a `SyntheticCollection.save()` afterwards writes the builder's rows into a file Anki has already restructured, and they do not take. The symptom is not an error — `get_queue` returns `counts: {new: 0, learning: 0, review: 0}` and an empty card list, which reads exactly like "the scheduler declined to gather them". If you need Anki's `today` in order to compute a `due`, use `anki_today_col_day(col_crt, now)` rather than a first `get_today` round-trip, and assert it equals the `today` the same run reports.

13. **`answer_card` fails with `not at top of queue` if a `get_queue` op ran before it in the same batch.** The earlier op builds and caches the queue; the grade then targets a card that is no longer the head. Put `answer_card` first, or in its own `run_oracle` call. Nothing about the message points at op ordering.

14. **`states.current.elapsed_days` is the RETRIEVABILITY path's number, not the answering path's.** For a card with no `lrt` they disagree: `current.elapsed_days` reports the day-level fallback (`today - (due - ivl)`) while the grade Anki actually applies uses the revlog (or `stability_short_term` when there is no revlog at all). Judge an elapsed question by the post-grade **stability**, never by the reported `elapsed_days` — that field agreeing with your expectation is not evidence the grade will. Measured 2026-09-03; both branches pinned in `test_parity_no_lrt_elapsed.py`.

15. **A fixture builder with no caller is not a working builder.** `add_revlog` bound 8 values into the 9-column `revlog` table (`factor` missing) and raised on every call from the day it was written until it got its first caller (2026-09-03). Coverage does not catch this: the function was never executed. Before relying on an unused fixture helper, call it once and look.

## Both gates per commit

Every commit that touches the harness or `test_parity_*.py` must pass:

```bash
./test.sh                                                      # lint + format + 100% coverage + frontend + e2e
cd backend && uv run pytest tests/test_parity_*.py --run-oracle --no-cov   # harness goldens
```

If `./test.sh` is green but `--run-oracle` is red, the production code drifted from Anki — open a Layer-N+1 finding (don't fix inline in a test commit). If `--run-oracle` is green but `./test.sh` is red, something in TT-internal correctness broke — usually a refactor that touched both production and tests.

## Adding a new harness test

Pattern:

```python
@pytest.mark.oracle
def test_parity_X(synthetic_collection: SyntheticCollection) -> None:
    """Pin <behavior> against Anki's V3Scheduler.

    What this covers:
    - ...

    What this does NOT cover (deferred or owned elsewhere):
    - ...
    """
    synthetic_collection.enable_fsrs(weights=DEFAULT_FSRS5_PARAMS.weights, retention=0.9)
    # ... setup cards via synthetic_collection.add_note / add_card ...
    synthetic_collection.save()

    result = run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    )
    anki_output = result.raw()["get_queue_0"]

    # Compute TT's equivalent
    tt_output = <call TT function on the same input>

    assert tt_output == anki_output, f"divergence: TT={tt_output} Anki={anki_output}"
```

If TT diverges, **don't fix TT in the test commit.** Mark the test `xfail(strict=True)` with a clear `reason` and surface the finding as a Layer-N+1 entry in `docs/anki-parity-layers.md`. The harness's job is to detect; the fix is its own commit. See Layer 42 (`077d6a5`) for the pattern.

## When the oracle binary disagrees with the Anki source

You're reading `/tmp/anki-source/` and the source predicts behavior X, but the PyPI anki binary produces behavior Y. Don't pick a side immediately — see if you can reproduce Y with a more specific input configuration. Layer 43's investigation pattern: vary the inputs Anki touches (here it was `due, ivl, days_elapsed`), dump the actual SQL function's output per card, find the input where the source's prediction and the binary's observation reconcile. Often the "contradiction" is just two different input regimes producing different behavior under the same code path.

## Cross-references

- `.claude/rules/anki-queue-parity.md` — load-bearing helpers (see the "Pre-Layer checklist") and the full divergence playbook.
- `docs/anki-parity-layers.md` — every Layer's history, especially Layers 42 (real bug, surfaced by harness) and 43 (Layer 38 demystified).
