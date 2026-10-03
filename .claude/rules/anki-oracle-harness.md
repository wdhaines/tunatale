---
paths:
  - "backend/tests/test_parity_*.py"
  - "backend/tests/anki_oracle/**"
---

# Anki Oracle Test Harness

Read this before adding a test under `backend/tests/test_parity_*.py` or changing
anything in `backend/tests/anki_oracle/`. The harness pins TT↔Anki parity end to
end by running Anki's real scheduler against the same input TT sees; on first use
it found a real lapse-stability bug (Layer 42) and showed that an earlier finding
was a coincidence of `elapsed≈ivl` (Layer 43).

## What the harness is

A pytest fixture (`synthetic_collection`) that builds a minimal `collection.anki2`
in memory, plus a subprocess driver (`oracle.py`) invoked via
`uv run --with anki python` that runs Anki's actual scheduler against the file and
returns JSON (queue order, R values, post-grade states, next-state predictions per
rating).

```
backend/tests/anki_oracle/
├── synthetic_collection.py   # High-level builder for collection.anki2
├── oracle.py                 # Subprocess: opens collection, runs ops, dumps JSON
└── harness_fixtures.py       # pytest fixtures + run_oracle() helper
```

Tests live beside the others as `backend/tests/test_parity_*.py` and are opt-in
via `--run-oracle`. `./test.sh` passes the flag, so local pre-commit runs the
harness; CI runs it in the **`anki-gates` job** (`.github/workflows/ci.yml`: warm
the isolated anki env, then `pytest -m oracle --run-oracle -n auto --no-cov`), so
an oracle failure is never confused with a unit failure. Every oracle-gated test
must carry `@pytest.mark.oracle`, or the CI job won't select it. To skip the
harness locally for speed, run `cd backend && uv run pytest` directly.

**`anki-gates` runs at Anki's 04:00 rollover, not at the workflow's `TZ: UTC`.**
It resolves a zone whose local clock is inside `[04:00, 05:00)` via
`.github/actions/hostile-hour-tz`, so every run exercises the day boundary. Left
at UTC, a parity run would land in that hour about once in 600 runs — and it went
red the first time it did. `backend-hostile-hour` sits in the band on every run
but does not pass `--run-oracle`, so it runs no parity tests.

**It then runs the oracle suite again at local 01:xx** (`hour: "1"`, exported as
`PRE_ROLLOVER_TZ`). Hour 4 is the hour *after* the rollover, where the two day
domains agree; the band before it, `[local midnight, 04:00)`, is where a day count
from the wrong domain runs a day ahead, and nothing sampled it until one reached
main (Layer 91). A test that asserts a day fact in that band should still pin it
with `timezone_with_local_hour(1)`, so it fails on every run and not only on CI's
pass.

**Triage is the opposite of `backend-hostile-tz`.** There, a boundary-only failure
is usually a fixture encoding a wall-clock assumption. Here, suspect product code
first: these tests compare against the real Anki backend, so a failure at the
boundary means TT and Anki genuinely disagree about what day it is. Only this job
has an oracle that can tell the two readings apart.

**A test asserting an absolute day index must pin its zone**
(`tests/_helpers/localtz.py`: `local_timezone`, `timezone_with_local_hour`). The
quantity is a local calendar day, so it is genuinely zone-dependent, and declaring
the zone is the opposite of assuming one. `local_timezone` sets `TZ` in the
environment, which the oracle subprocess inherits, so both sides agree about what
day it is.

### Ops the driver understands

`get_queue` (cards + post-limit `counts` + per-rating `states`),
`scheduling_states` (intervals only), `answer_card` (grades for real, returns
post-grade `stability`/`difficulty`), `get_card`, `get_revlog`, `note_ords`,
`set_config`, `add_review_cards`, `check_database`, plus:

- `get_today` — `col.sched.today` **and** `day_cutoff`, Anki's `next_day_at`. The
  answering path measures elapsed as a duration back from `day_cutoff`, so a
  grade-elapsed question must be checked against it.
- `deck_today` — a deck's `newToday` / `revToday` as `[last_day_studied, count]`,
  alongside `today` and the post-limit `new_count`. Anki charges the daily limit
  only when the stamp equals its own `today`, so the pair shows both what TT wrote
  and whether Anki accepted it.
- `set_due_date` / `forget_cards` — Anki's own Set Due Date and Forget on
  `card_ids`. Follow with `get_revlog` to read the manual (`type = 4`) row each
  leaves; Forget's is the `factor = 0` one (Layer 88). `forget_cards` takes the
  dialog's two checkboxes, `reset_counts` and `restore_position`, both off by
  default as in the Python API.
- `get_card_row` — the card's stored columns (`type`, `queue`, `due`, `ivl`,
  `factor`, `reps`, `lapses`, `left`, `odue`, `odid`, `data`). `get_card` is the
  scheduler's view; this is the row another program would have to write.

## Subprocess boundary — never violate

**Backend production code (`backend/app/**`) must never `import anki`.** That
would make Anki a runtime dependency of production, breaking the "Anki is a
reference, not a runtime dependency" principle (queue-parity rule 1).

The harness imports anki in a **separate Python process** spawned via
`uv run --with anki python oracle.py`. Backend tests never import anki either;
they call `run_oracle(collection_path, operations)`, which builds the subprocess
command.

If you want to call Anki from a test fixture or backend module, stop. The right
shape is: build a synthetic collection at the SQLite level, write it to disk, and
hand the path to the subprocess.

## When to write a harness test vs a unit test

**Write a harness test (`test_parity_*.py`)** when the question is:
- "Does TT produce the same output as Anki for this input?"
- "Does TT's queue order match Anki's for this card configuration?"
- "Does TT's FSRS computation match Anki's per-rating next state?"

**Write a unit test (`test_*.py`)** when the question is:
- "Does TT crash on this malformed input?" (the harness uses well-formed inputs
  only)
- "Does `/api/srs/feedback` return the right JSON shape?" (a TT API contract, not
  parity)
- "Does `session_main_queue` invalidate at the right moments?" (a TT-internal
  invariant)
- "Does `count_reviews_completed_today` exclude buried directions?" (TT SQL
  behavior)

Heuristic: if the failure mode is "TT and Anki produce different outputs for this
input", use the harness. If it is "TT does something forbidden on the way to the
right answer" or "TT crashes", write a unit test.

## Synthetic collection — what's modeled, what's not

`SyntheticCollection` writes a modern-format `collection.anki2` (schema v18). The
builder methods are deliberately small — extend the builder rather than raw-SQL
the collection in your test.

**Modeled:** the `col` table, `notes`, `cards` (with `data.s` / `data.d` /
`data.lrt` / `data.dr`), `revlog`, `decks` + `deck_config` (modern protobuf),
`notetypes` + `fields` + `templates`, and the modern `config` table. FSRS-5
weights, desired_retention, new/reviews_per_day, learn_steps, relearn_steps and
review_order. `oracle.py` enables the V3 scheduler after opening
(`col.set_v3_scheduler(True)`).

**Multi-template notetypes:** `add_notetype(..., templates=[(name, qfmt, afmt), …])`
writes real `templates.config` blobs via production's own
`build_template_config`. The `template_count=N` form still writes anonymous
`Card N` templates with an **empty** config; it stays the default because the
scheduling tests never render a card.

**Use `templates=` whenever the subject is card generation.** Anki decides whether
to create a card for an ord by asking whether that template's front renders
non-empty (`cardgen.rs::new_cards_required_normal`), so an empty config makes
every front empty and the generator a silent no-op — the test passes vacuously.

**Not modeled (extend the builder if you need these):**
- **Time travel.** `col.crt` is fixed at 2024-01-01 UTC and the subprocess's `now`
  is real wall-clock time, so day-rollover unbury timing (Layers 27/35) can't be
  tested cleanly.
- **Revlog-derived state.** Nothing computes `cards.data` from revlog; you write
  both directly.

## Gotchas

These are also in the test docstrings; they are listed here for fast recall.

1. **`cards.data` needs `s` AND `d` AND `dr` AND `lrt` for the FSRS path.** Missing
   `lrt` → Anki sees `days_elapsed=0` and routes through `stability_short_term`
   instead of `stability_after_success`. Missing `dr` → Anki's queue-sort SQL
   function falls back to SM2, all FSRS cards tie near zero, and queue order goes
   pseudo-random. `add_card(stability=..., difficulty=..., last_review_secs=...,
   desired_retention=...)` writes all four.

2. **`schedVer=2` and `fsrs=true` must be in the `config` table, not just
   `col.conf`.** Modern Anki's `ConfigManager` reads through the Rust backend from
   the `config` table; `col.conf` JSON is legacy and ignored.
   `SyntheticCollection.enable_fsrs()` writes both.

3. **`review_order` defaults to `RETRIEVABILITY_ASCENDING` (proto value 7), not
   Anki's app default `DAY` (0).** Otherwise parity tests against TT's R-ascending
   queue assembly compare different orderings and look like divergence.
   `_make_deck_config_blob` writes field 33 = 7 by default.

4. **`learn_steps` / `relearn_steps` are `repeated float` (packed LEN-delimited
   f32), not VARINT.** Written as VARINTs, Anki silently falls back to the
   defaults `[1.0, 10.0]` / `[10.0]`. Use `_packed_float_field` (already wired into
   `_make_deck_config_blob`).

5. **`QueuedCard.card` is a protobuf message whose field names differ from the
   Python `anki.cards.Card` class:** `ctype` not `type`, `interval` not `ivl`,
   `remaining_steps` not `left`. `_serialize_card` in `oracle.py` normalizes back
   to the Python-class names.

6. **`Card.memory_state` is a property in current anki, not a method.** Don't call
   it.

7. **`col.sched.counts()` returns `tuple[int, int, int]`** (new, learning,
   review), not an object with named attributes.

8. **`due > 365_000` triggers a different `days_elapsed` formula** inside Anki's
   `extract_fsrs_relative_retrievability` — the cutoff is a sentinel for
   "(re)learning cards encoded as Unix timestamps". Stay below it for review-card
   tests.

9. **`due=0, ivl=10` for a NULL-R card lands at the queue tail.** The SM2 fallback
   `-(elapsed/ivl)` evaluates to `-0.0001` because of saturating-`u32` wraparound
   on `review_day = due - interval = -10`. Use `due=today_col_day, ivl=N →
   elapsed=N` to land NULL-R near the dr position (Layer 43).

10. **Never compute Anki's `today` with naive arithmetic.** UTC day division
    (`(now - col.crt) // 86400`) ignores the local 04:00 rollover: between local
    midnight and 04:00 the naive day has advanced but Anki's `today` has not, so
    `due=naive_today` lands one day in Anki's future, the card isn't due, and it is
    absent from the queue (a past-due card would still be gathered). It passes in
    US-evening runs and fails in UTC CI and in any machine's midnight–04:00 window.
    In tests, take Anki's day from the `get_today` op (`col.sched.today`) or from
    `anki_today_col_day`. The same applies to production code: the answer there is
    `anki_today_col_day`, not `compute_anki_day_index` — see that function's
    docstring for the two-domain split, and never introduce a third. When a
    harness gotcha describes a *formula* rather than a fixture, check whether
    shipped code uses the same formula.

    Seeding a `due` from `anki_today_col_day` (rather than a prior `get_today` call)
    is the cheaper way to make a card due today, since it needs no extra oracle
    round-trip — but see gotcha 12 for the ordering constraint that makes the
    two-call form fail silently.

11. **`col.fix_integrity()` (Check Database) reports failure by return value, not
    by raising** — and an aborted check generates no cards, which is
    indistinguishable from Anki deciding not to generate. The synthetic
    collection's schema is minimal, so whole-collection operations walk into tables
    the scheduler tests never touch: dbcheck opens with `select tag from tags where
    collapsed = false`, and a `tags` table without `collapsed` makes it return
    `(DbError{…no such column…}, False)` with the notes loop never run. The
    `check_database` op returns `ok` separately for exactly this reason — **assert
    on it**. When you extend the fixture for another whole-collection operation,
    expect the same class of gap, and take the DDL from a real collection
    (`SELECT sql FROM sqlite_master WHERE name='…'`) rather than from what looks
    plausible.

12. **Seed the collection before the first `run_oracle` call.** Opening the file
    with Anki rewrites it; a `SyntheticCollection.save()` afterwards writes the
    builder's rows into a file Anki has already restructured, and they don't take.
    There is no error — `get_queue` returns `counts: {new: 0, learning: 0,
    review: 0}` and an empty card list, which reads exactly like "the scheduler
    declined to gather them". If you need Anki's `today` to compute a `due`, use
    `anki_today_col_day(col_crt, now)` rather than a first `get_today` round-trip,
    and assert it equals the `today` the same run reports.

13. **`answer_card` fails with `not at top of queue` if a `get_queue` op ran before
    it in the same batch.** The earlier op builds and caches the queue, and the
    grade then targets a card that is no longer the head. Put `answer_card` first,
    or in its own `run_oracle` call; nothing in the message points at op ordering.

14. **`states.current.elapsed_days` is the retrievability path's number, not the
    answering path's.** For a card with no `lrt` they disagree:
    `current.elapsed_days` reports the day-level fallback (`today - (due - ivl)`),
    while the grade Anki actually applies uses the revlog (or
    `stability_short_term` when there is no revlog at all). Judge an elapsed
    question by the post-grade **stability**, never by the reported
    `elapsed_days` — that field matching your expectation is not evidence the grade
    will. Both branches are pinned in `test_parity_no_lrt_elapsed.py`.

15. **A fixture builder with no caller is not a working builder.** Coverage does
    not catch an unexecuted helper — `add_revlog` once bound 8 values into the
    9-column `revlog` table and raised on every call until its first caller. Before
    relying on an unused fixture helper, call it once and look.

## Both gates per commit

Every commit that touches the harness or `test_parity_*.py` must pass:

```bash
./test.sh                                                      # lint + format + 100% coverage + frontend + e2e
cd backend && uv run pytest tests/test_parity_*.py --run-oracle --no-cov   # harness goldens
```

If `./test.sh` is green but `--run-oracle` is red, production code drifted from
Anki — open a Layer-N+1 finding rather than fixing it inline in a test commit. If
`--run-oracle` is green but `./test.sh` is red, TT-internal correctness broke,
usually through a refactor that touched both production and tests.

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

If TT diverges, **don't fix TT in the test commit.** Mark the test
`xfail(strict=True)` with a clear `reason` and record the finding as a Layer-N+1
entry in `docs/anki-parity-layers.md`. The harness's job is to detect; the fix is
its own commit (Layer 42 in that doc is the pattern).

## When the oracle binary disagrees with the Anki source

If the source (e.g. a checkout of ankitects/anki) predicts behavior X but the PyPI
anki binary produces Y, don't pick a side immediately — try to reproduce Y with a
more specific input. Vary the inputs Anki touches (for Layer 43 it was
`due, ivl, days_elapsed`), dump the actual SQL function's output per card, and
find the input where the source's prediction and the binary's observation
reconcile. The "contradiction" is often two input regimes producing different
behavior under the same code path.

## Cross-references

- `.claude/rules/anki-queue-parity.md` — load-bearing helpers (see the "Pre-Layer
  checklist") and the full divergence playbook.
- `docs/anki-parity-layers.md` — every Layer's history, especially Layers 42 (a
  real bug the harness surfaced) and 43 (an earlier finding demystified).
