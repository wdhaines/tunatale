# Stage 3b step 4 — implement `new` mode (handoff)

**Audience**: a fresh chat session implementing the next step of the event-sync migration. Self-contained; you don't need any prior conversation. Read this top to bottom, then start with the tests.

**Branch**: `stage3b-steps-1-3` (continue here; don't branch off). **Working tree is clean as of handoff.**

## What you're building

`event_sync_pull` mode `"new"`: the path where `sync_pull` writes **replay-derived** FSRS state to the authoritative columns instead of the legacy merge result, taking Anki's value only when replay diverges (an Anki `recompute_memory_state` event between syncs). This is step 4 of the staged cadence in `~/.claude/plans/ticklish-questing-fountain.md`.

**This is test-first work.** Write the failing tests first (red), then implement to green. The project enforces strict TDD (`.claude/rules/tdd.md`) and 100% backend coverage.

**Safety invariant — do not violate**: the `event_sync_pull` flag stays defaulted to whatever it currently is (`compare` on the live deck; `legacy` is the code default). `new` mode only activates when someone manually calls `set_event_sync_pull_mode("new")`. **Your change must produce zero production behavior change until that manual flip happens.** Verify this: a `legacy`-default sync_pull must behave byte-identically before and after your change.

## Where things stand (verified at handoff)

- **Steps 1-3 are done** (commit `09028c0`): the flag, incremental replay (`rebuild_from_revlog` with `starting_state`/`since_id`), and `compare` mode (writes replay result to shadow columns `stability_replayed` / `fsrs_difficulty_replayed`, leaves authoritative columns to legacy).
- **Forward-step parity is bit-exact** (Layers 49-58). The empirical measurement that validated this is in `docs/stage-3b-empirical-measurement.md` — 89/89 stability+difficulty strict match on an Anki-only-grading day.
- **The compare-soak is clean**: `stability_diverge = 0`, `difficulty_only_diverge = 104` (the known-benign 05-21 restore floor — see `docs/anki-parity-layers.md` and the 05-28 design note in the empirical-measurement doc). Soak has run clean across 2 syncs so far; the plan wants ≥1 week before the flip.
- **`new` mode does NOT exist yet.** `_pull_merge_direction` and the `sync_pull` loop only branch on `event_mode == "compare"`. There is no `new` write path and no `_record_recompute_divergence`. That's what you're adding.

## The design (already decided — don't redesign)

From the Big Pickle brief in `~/.claude/plans/ticklish-questing-fountain.md`. The `new`-mode FSRS-state path, per direction:

1. Ingest new Anki revlog rows since the pre-ingest boundary (already happens via `_ingest_anki_revlog_for_card`; the boundary is captured the same way compare mode does it — see `sync.py:1862-1869`).
2. Replay forward from the stored state: `rebuild_from_revlog(coll_id, direction, params, col_crt, anki_card_id=..., starting_state=local_dir, since_id=pre_ingest_revlog_id)`. This is exactly what `_write_compare_shadow` (`sync.py:1701`) already computes — reuse that replay call; don't write a new one.
3. Compare `replayed.stability/difficulty` against Anki's candidate (`card_rec`'s stability/difficulty).
   - **Within tolerance** (`abs(Δstability) <= s_tol and abs(Δdifficulty) <= d_tol`; use the same tolerances the measurement script uses — `s_tol = 0.01`, `d_tol = 0.01`, confirm against `app/anki/measure_stage3b_premise.py`): the happy path. Write a state that is **replay-derived for `stability`, `fsrs_difficulty`, `last_review`, `last_review_time_ms`** and **pass-through-from-Anki for `reps`, `lapses`, `state`, `due_at`, `anki_due`, `anki_card_mod`, `left`, `bury_kind`**.
   - **Outside tolerance**: Anki ran `recompute_memory_state` between syncs. Take Anki's value for everything (same as legacy would), and call a NEW `_record_recompute_divergence(...)` — distinct from `_record_conflict`.
4. Suspend (`queue == -1`) and bury (`queue ∈ {-2,-3}`) branches are unchanged from legacy — they fire before the FSRS-state branch regardless of mode.

**Pass-through field list** (canonical, from the plan): `due_at`, `anki_due`, `reps`, `lapses`, `state`, `left`, `anki_card_mod`, `bury_kind`. **Replay-derived**: `stability`, `fsrs_difficulty`, `last_review`, `last_review_time_ms`.

**Why reps/lapses/state are pass-through, not replay-derived**: Anki's `cards.reps` doesn't count revlog rows 1-for-1 (accounting consolidation), and forward-step `schedule()` increments reps by 1 per call. The 2026-05-21 measurement found a consistent reps off-by-one for exactly this reason. Don't try to make replay reproduce reps — take it from Anki.

**Why due_at is pass-through**: Anki re-derives `cards.data.s` between grade and sync but keeps the grade-time `cards.ivl`; the two are internally inconsistent by design, so the grade-time interval can't be reconstructed from forward-step. Plus the load balancer (Layer 53) relocates intervals using a global histogram TT can't reconstruct mid-session. `sync_pull` reads `cards.due` directly — synced cards carry Anki's value verbatim.

## Tests to write first (red)

Add to `backend/tests/test_anki_sync_pull_event_mode.py` (existing file; 5 compare/legacy tests there now). Match its style — `RevlogReader`, `_run_pull`, `_seed_review_direction`, `_shadow`, `_good_revlog_row` helpers are all defined at the top and reusable.

Required cases:

1. **`new` mode, replay matches Anki → writes replay-derived stability/difficulty.** Seed a REVIEW direction, feed one Anki GOOD revlog row whose resulting `card_rec` stability is what forward-step would produce. Set mode `"new"`. Assert the authoritative `stability`/`fsrs_difficulty` equal the replay output (not necessarily Anki's `card_rec` value if they differ — but in the matching case they're within tolerance, so assert against the replay result). Assert `reps`/`lapses`/`state`/`due_at` came from Anki (`card_rec`).

2. **`new` mode, replay diverges → takes Anki's value + records divergence.** Seed a direction, feed a `card_rec` whose stability is far from what replay produces (simulate a recompute_memory_state event — e.g., `card_rec.stability` = replay result × 2). Set mode `"new"`. Assert authoritative columns got **Anki's** value, assert exactly one `_record_recompute_divergence` entry exists, assert `report.conflicts` (the `_record_conflict` channel) is **unchanged** (divergence ≠ conflict).

3. **`new` mode, suspend branch unchanged.** `card_rec.queue == -1` → state SUSPENDED, same as legacy. (Borrow the assertion shape from existing suspend tests in `test_anki_sync_pull.py`.)

4. **`new` mode, bury branch unchanged.** `card_rec.queue == -2` → buried with correct `bury_kind`, same as legacy.

5. **`new` mode, zero new revlog rows → no-op on FSRS state.** With no new Anki grades, replay returns the stored state; authoritative columns unchanged, no divergence recorded.

6. **`legacy` default unchanged (regression guard).** A `legacy`-mode sync_pull over the same fixtures produces byte-identical authoritative columns to pre-change behavior. (This is the safety invariant. The existing `test_compare_mode_keeps_authoritative_identical_to_legacy` is the template.)

7. **`new` mode, dry_run writes nothing.**

## Implementation (green)

- `app/anki/sync.py`:
  - In the `sync_pull` loop (around `sync.py:1862-1953`), compute the replay for `new` mode the same way compare does — refactor the shared replay call out of `_write_compare_shadow` if it helps, but don't duplicate it.
  - Add the `new`-mode branch: when `event_mode == "new"`, the FSRS-state portion of `_pull_merge_direction`'s result is replaced by the replay-derived-or-Anki-on-divergence state. Cleanest shape is likely a new helper `_merge_direction_new_mode(...)` that wraps the replay + tolerance check + pass-through assembly, called instead of `_pull_merge_direction`'s FSRS branches when mode is `new`. Keep suspend/bury going through the existing path.
  - Add `_record_recompute_divergence(...)` parallel to `_record_conflict` (`sync.py:1266`). It needs its own list on `PullReport` (`sync.py:165`) — add `recompute_divergences: list[...] = field(default_factory=list)`. Decide the payload (collocation_id, direction, replay stability/difficulty, Anki stability/difficulty). Surface its count in the sync summary log (`sync.py:2425`) separately from conflicts.
- `app/srs/database.py`: you likely need a `set_direction_from_replay(...)` writer that updates only the replay-derived columns while preserving the pass-through ones, OR assemble the full `DirectionState` and reuse `update_direction`. Prefer assembling the full state and reusing `update_direction` — fewer new methods, and `_direction_differs` already gates the write.
- `app/srs/migrations.py`: probably **no migration needed** for step 4 — the shadow columns from step 3 already exist, and `new` mode writes to authoritative columns that already exist. Confirm; don't add one speculatively.

## Gates (all must pass before you're done)

```bash
cd backend
./test.sh                                                   # or from repo root: ./test.sh
uv run pytest tests/test_parity_*.py --run-oracle --no-cov  # oracle harness
```

- `./test.sh` green: lint + format + 100% backend coverage + frontend + E2E.
- `--run-oracle` green: 24 parity tests.
- Coverage is 100% — every line of the new `new`-mode path and `_record_recompute_divergence` needs a test hitting it. No `# pragma: no cover` to dodge this (see `.claude/rules/testing.md` — pragma discipline; a prior incident `63bfd94` is the cautionary tale).

## Do NOT do in this step

- **Do not flip the live flag to `"new"`.** That's a manual operational step after the soak hits ≥1 week clean. Your code ships with the flag still at its current value.
- **Do not drop `prior_*` columns or touch `_derive_revlog_shape`.** That's Stage 5 — `sync_push` still reads them. See the plan's "Not deleted in Stage 3b" callout.
- **Do not delete the 6 legacy FSRS-state branches** from `_pull_merge_direction`. That's step 5, after the `new`-mode soak proves out. For now `legacy`, `compare`, and `new` all coexist behind the flag.
- **Do not redesign the pass-through vs replay-derived split.** It's empirically settled (see "The design" above and the plan).

## Reference map

| Thing | Location |
|---|---|
| Plan (canonical) | `~/.claude/plans/ticklish-questing-fountain.md` — read "Big Pickle brief" + staged cadence |
| Empirical validation | `docs/stage-3b-empirical-measurement.md` |
| Existing event-mode tests | `backend/tests/test_anki_sync_pull_event_mode.py` |
| Compare-mode replay (reuse this) | `app/anki/sync.py:1701` `_write_compare_shadow` |
| sync_pull loop + compare wiring | `app/anki/sync.py:1764` onward; mode read at `1774`, compare write at `1945` |
| `_record_conflict` (parallel for divergence) | `app/anki/sync.py:1266`; `PullReport` at `165` |
| `rebuild_from_revlog` (replay engine) | `app/srs/database.py:2103` |
| Mode get/set | `app/srs/database.py:1922` / `1934` |
| Shadow-column writer | `app/srs/database.py:427` `set_direction_shadow_replay` |
| Parity rules (read before editing sync.py) | `.claude/rules/anki-queue-parity.md` — especially the Pre-Layer checklist |
| Tolerances | `app/anki/measure_stage3b_premise.py` (s_tol/d_tol used in the measurement) |

## Commit shape

One commit, message prefix `feat(srs): Stage 3b step 4 — new-mode write path + recompute-divergence recording`. Report honest numbers (file LOC via `wc -l`, `git diff --stat`). If coverage forced any awkward test, say so rather than papering it with a pragma.
