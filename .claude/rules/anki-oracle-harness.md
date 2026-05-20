# Anki Oracle Test Harness

Required reading before adding a test under `backend/tests/test_parity_*.py` or modifying anything in `backend/tests/anki_oracle/`. The harness was built in Phase 2 of the simplify effort (see `~/.claude/plans/you-ve-written-a-ton-happy-yeti.md` if it still exists, otherwise commit `0c076fe` and Phase 2.2.x commits) to pin TT↔Anki parity end-to-end. It surfaced two findings on first use (Layer 42 — a real lapse-stability bug; Layer 43 — Layer 38's "NULL-R at dr position" was a coincidence of `elapsed≈ivl`).

## What the harness is

A pytest fixture (`synthetic_collection`) that builds a minimal `collection.anki2` in memory, plus a subprocess driver (`oracle.py`) invoked via `uv run --with anki python` to run Anki's actual scheduler against the file and return JSON outputs (queue order, R values, post-grade states, next-state predictions per rating).

```
backend/tests/anki_oracle/
├── synthetic_collection.py   # High-level builder for collection.anki2
├── oracle.py                 # Subprocess: opens collection, runs ops, dumps JSON
└── harness_fixtures.py       # pytest fixtures + run_oracle() helper
```

Tests live alongside other tests as `backend/tests/test_parity_*.py`. They're opt-in via `--run-oracle`: `./test.sh` passes the flag so local pre-commit runs the harness, while CI (`.github/workflows/ci.yml`) runs `uv run pytest` without it so it doesn't depend on Anki being installable in the CI image. If you need to skip the harness locally for speed, run `cd backend && uv run pytest` directly.

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

**Not modeled (extend the builder if you need these).**
- **Multi-template notetypes.** Default Basic has one template (Front/Back). Phase 2.2.4's sibling-bury parity couldn't test the cross-direction case because of this. Add a 2-template notetype only if a future Layer surfaces a cross-direction divergence.
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
- `~/.claude/projects/-Users-wdhaines-CascadeProjects-tunatale/memory/reference_anki_ground_truth_capture.md` — pre-Phase-2 ad-hoc recipe that became this harness.
