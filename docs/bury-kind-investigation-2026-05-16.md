# Bury-kind investigation (2026-05-16)

Read this if you are a fresh Claude session asked to debug TT/Anki queue divergence around buried cards on or after 2026-05-17. The state of the world and the open questions are here so you don't re-derive them.

## The incident

User reported: TunaTale "to review" badge showed 136; Anki badge showed 145; user had not synced TT today. New day, no Anki review session yet.

Diagnostic walked the data and found:
- 140 directions in TT had `state='buried' bury_kind='user'`.
- 0 directions had `bury_kind='sched'`.
- All 140 had Anki queue=2 (109 rows) or queue=0 (31 rows) — i.e. Anki had released them at this morning's 4 AM rollover.
- 100% of the 140 had a sibling whose `last_review` was yesterday (2026-05-15) — they were sibling-buried at the end of yesterday's review session.

The 9 net-collocations of the 140 whose `anki_due` is on or before `today_col_day` (15 cards, 6 of which are siblings of an already-due TT row) explain the badge gap exactly: 136 + 9 = 145.

## Two bugs fixed today

Both landed in the same change. Tests in `backend/tests/test_anki_sync_pull.py` (`TestDirectionDiffersDetectsLastReviewTransition::test_sync_pull_bury_trace_*` and `test_direction_differs_detects_bury_kind_change`).

### Bug 1 — read-path missing column

`backend/app/srs/database.py:_DIR_COLUMNS` did not include `bury_kind`. Every direction loaded via `get_collocation_by_*` came back with `bury_kind=None` regardless of the DB value, because `_load_directions` only `SELECT`s the columns in `_DIR_COLUMNS` and the construction at line 489 has a defensive `if "bury_kind" in row.keys() else None` that masked the absent column.

Net effect: any Python code reading `direction.bury_kind` since Layer 35 (2026-05-13) saw `None`. The `unbury_if_needed` sweep was unaffected (it queries the DB directly via SQL filter `WHERE state='buried' AND bury_kind='sched'`), so the runtime stickiness behavior was preserved by the DB column even though Python couldn't see it.

Fix: added `"bury_kind"` to `_DIR_COLUMNS`.

### Bug 2 — `_direction_differs` blind to `bury_kind`

`backend/app/anki/sync.py:_direction_differs` compared 13 fields between local and candidate but did not include `bury_kind`. When TT had `state='buried' bury_kind='user'` (from the Layer 35 migration's pessimistic backfill) and sync_pull computed a candidate with `bury_kind='sched'` (from Anki queue=-3), the function returned False if all other fields matched — silently no-op'ing the kind correction. Combined with bug 1, every sync since 2026-05-13 left the cohort wrongly tagged as `'user'`.

Fix: added `or local.bury_kind != candidate.bury_kind` to the diff.

## What's still open (the 28 problem)

Of the 140 stuck rows, **28 had their OWN `last_review` set to 2026-05-15** — meaning that direction was actively reviewed yesterday. The transition we expected to see during yesterday's sync was REVIEW → BURIED (sibling reviewed → Anki sibling-buries → queue=-3). That transition DOES change `state`, which would fire `_direction_differs` even pre-fix. So we expected the May 15 sync to write `bury_kind='sched'` for those 28. But all 140 (including the 28) ended up tagged `'user'`.

Per `anki-source-expert` walkthrough of `/tmp/anki-source/rslib/`:

- `bury_and_suspend.rs:132-143` — grade-time `bury_siblings` ONLY calls `BuryOrSuspendMode::BurySched` (queue=-3). Never `BuryUser`.
- `BuryUser` (queue=-2) is only reachable from explicit UI actions (Ctrl-J, browse-bulk-bury, `Op::Bury`).

So if TT received `bury_kind='user'` from sync_pull on those 28, Anki must have shown them as queue=-2 — which the grade path cannot produce. Three candidate explanations remain, undisambiguable from snapshot data:

1. **User bulk-buried in Anki yesterday.** Unlikely for 28+ cards but not impossible (browse-select-then-Ctrl-J).
2. **An Anki add-on rewrites sibling-bury to queue=-2.** Look at `~/Library/Application Support/Anki2/addons21/` if you need to disambiguate.
3. **Pre-fix flow** — the rows were ALREADY `state='buried' bury_kind='user'` from migration backfill (before the May 15 review session), and the May 15 sync didn't actually transition them through REVIEW. Their own `last_review` got stamped from revlog at the same time, but they never left BURIED locally. Possible if a sync ran between the user's review event and the rollover that day, in a window I can't reconstruct.

### How to disambiguate after tomorrow's sync

1. Sync TT once. The pre-existing 140 should release via the state-mismatch path — `BURY_TRACE summary` should show `buried_to_released_writes` ≈ 140 (the cohort) plus any siblings buried this morning.
2. The user reviews in Anki today. Anki sibling-buries cards for today's session → queue=-3.
3. Sync TT again. `BURY_TRACE summary` should show:
   - `anki_queue_minus3_seen` = count of today's sibling-buries.
   - `anki_queue_minus2_seen` = **0** if Anki is behaving per source. **Non-zero is the smoking gun** — means something (user action or add-on) is producing queue=-2.
4. Tomorrow morning (post-rollover, pre-review):
   - Anki rolls over → all queue=-3 cards released. `last_unbury_day` cache in TT bumps too.
   - First `/queue-stats` runs TT's daily sweep — releases `bury_kind='sched'` rows.
   - Run `SELECT bury_kind, state, COUNT(*) FROM collocation_directions GROUP BY 1, 2`. Should be all `state IN ('review', 'new')`, all `bury_kind IS NULL`. Any `'sched'` remaining = sweep didn't fire (check `anki_state_cache['last_unbury_day']`). Any `'user'` remaining = either real manual buries OR the bug re-emerged.

## How to read BURY_TRACE logs

Per-card line, INFO level on logger `app.anki.sync`:

```
BURY_TRACE cid=1775264032182 text='kopalnica' dir=recognition anki_queue=-3 anki_mod=1778862455
  local=(state=review kind=None last_review=2026-05-15T02:31:04+00:00)
  candidate=(state=buried kind=sched last_review=2026-05-15T02:31:04+00:00)
  diff=True write=True
```

Summary line at end of sync_pull:

```
BURY_TRACE summary dry_run=False {
  'anki_queue_minus2_seen': 0,
  'anki_queue_minus3_seen': 12,
  'buried_to_released_writes': 140,
  'released_to_buried_writes': 12,
  'kind_only_flips_written': 0,
  'buried_state_match_no_write': 87
}
```

Interpretation:
- `anki_queue_minus2_seen` > 0: Anki shows manual user-buries. Investigate add-ons if not user-initiated.
- `anki_queue_minus3_seen` > 0: normal sibling-buries from prior grading.
- `buried_to_released_writes` > 0: TT had stale buried rows that Anki has since released. Normal cleanup; high counts post-rollover are expected.
- `released_to_buried_writes` > 0: today's sibling-buries from Anki being mirrored to TT.
- `kind_only_flips_written` > 0: the post-fix code path corrected a wrong kind. Should be near-zero after the initial cleanup. **Non-zero on every sync = something is producing wrong kinds repeatedly.**
- `buried_state_match_no_write` > 0: rows where state matched both sides and nothing else differed — already in sync, no work to do. High counts are fine.

## Files touched 2026-05-16

- `backend/app/anki/sync.py` — `_direction_differs` includes `bury_kind`; sync_pull main loop emits `BURY_TRACE` per direction + summary.
- `backend/app/srs/database.py` — `_DIR_COLUMNS` includes `bury_kind`.
- `backend/tests/test_anki_sync_pull.py` — new tests `test_direction_differs_detects_bury_kind_change`, `test_sync_pull_bury_trace_logs_user_bury`, `test_sync_pull_bury_trace_counters_and_log`.
- `.claude/rules/anki-queue-parity.md` — principle 10 corrected (queue=-2 also released at Anki rollover); divergence playbook updated.
- `docs/bury-kind-investigation-2026-05-16.md` — this file.

## Cross-references

- `.claude/rules/anki-queue-parity.md` — principles 9 (daily unbury sweep) and 10 (bury_kind split, with the 2026-05-16 correction and BURY_TRACE diagnostic).
- `docs/anki-parity-layers.md` — Layer 35 narrative. Backfill of today's findings into a Layer 37 entry is recommended once the next sync confirms the cohort releases cleanly.
- Anki source: `/tmp/anki-source/rslib/src/scheduler/bury_and_suspend.rs` (sibling-bury queue value, unbury_on_day_rollover behavior), `/tmp/anki-source/rslib/src/search/sqlwriter.rs:471-476` (StateKind::Buried filter).
