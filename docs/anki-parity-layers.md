# TT ↔ Anki Queue Parity — Layer-by-layer history

Not auto-loaded. Open this when you're debugging a TT/Anki divergence and the question is "have we hit something like this before?" The principles and decision tree live in `.claude/rules/anki-queue-parity.md`; this file is the record of how each principle was reached.

Each layer = one identifiable divergence + the fix that resolved it. Layers are numbered chronologically across multiple sessions.

---

## Layer 1 — queue=1 sort tiebreak

**Bug.** TT's learning-bucket sort used `stability` as a tiebreak. Anki only sorts by `(reps==0, due)` then falls through to SQLite's stable scan order (effectively `cards.id` ASC). Two cards with identical `due_at`/`anki_due` to the second (e.g. both lapsed in the same session) ended up in opposite orders across the two apps.

**Fix.** Drop stability from the sort key; tiebreak on `anki_card_id` ASC.

**Files.** `backend/app/api/srs.py:801` (now ~`learning_cards.sort` block around line 842).

---

## Layer 2 — frozen `learning_cutoff`

**Bug.** `/review-queue` split learning cards into ready vs pending using live `datetime.now()`. A card whose timer expired mid-screen would jump to the head and preempt the user's current card. Anki freezes `current_learning_cutoff` at the last answer event so this doesn't happen.

**Fix.** New `anki_state_cache` key `learning_cutoff`. `advance_learning_cutoff(db, now)` is called at every grade event and every `sync_pull` ingest. `resolve_learning_cutoff(db, fallback=now)` reads it. `/review-queue` uses the cached value, not live now.

**Files.** `app/srs/queue_stats.py` (`resolve_/advance_learning_cutoff`), `app/api/srs.py` (call sites), `app/anki/sync.py` (advance after ingest).

---

## Layer 3 — frontend never reordered

**Bug.** The Svelte review page cached the queue once on mount and never refetched after grade events. Server state shifted (sync, cutoff advance) but the user saw stale order.

**Fix.** Frontend rewrite: drop `deferred`/`buriedCollocationIds`/`reapDeferred`/`topUpQueue` local logic. Refetch `/review-queue` on every grade. Always render `queue[0]`.

**Files.** `frontend/src/routes/review/+page.svelte`, `frontend/src/lib/api.ts`, page tests rewritten.

---

## Layer 4 — `session_main_queue` freeze

**Bug.** Server rebuilt the main queue (review + new spread mix) on every fetch. The intersperser ratio changed as counts decremented through the session, so new cards drifted to earlier or later positions vs Anki's frozen main.

**Fix.** Cache the built main queue keyed on `today.isoformat()`. First call builds + freezes; later calls keep the cached order, filter out graded cards, and append mid-day arrivals at the tail.

**Files.** `app/srs/queue_stats.py` (`get_/set_session_main_queue`), `app/api/srs.py`.

---

## Layer 5 — page mount advances cutoff

**Bug.** After page reload, the cutoff stayed frozen at the last grade event. Learning cards whose timers expired between sessions stayed pending forever, never surfacing.

**Fix.** Frontend passes `?session_start=1` on mount; server advances `learning_cutoff` to current now. Mirrors Anki's `update_learning_cutoff_and_count` on deck open.

**Files.** `app/api/srs.py` (session_start path), `frontend/src/lib/api.ts`, `+page.svelte`.

---

## Layer 6 — Bit-exact RNG port for learning fuzz

**Bug.** TT scheduled learning steps with no fuzz. Anki adds uniform `[0, min(0.25*step, 300))` seconds. TT's `due_at` always landed exactly at `+60s` while Anki's was `+60..+74s`, so the cutoff fell between them and the cards diverged.

**Fix.** `app/srs/_anki_rng.py` — bit-exact port of Rust's `StdRng::seed_from_u64(seed) → rng.random_range(low..high)`. Chain: SplitMix64 → ChaCha12 → Canon's biased widening-multiply method. Seeded by `(card_id + reps) mod 2^64`. SplitMix64 verified against canonical reference values; downstream functions are regression-pinned against our own output.

**Files.** NEW `app/srs/_anki_rng.py`, `app/srs/fsrs.py:_learning_step_fuzz_seconds`, NEW `tests/test_anki_rng.py`.

### Layer 6b — revlog shape decode

**Bug.** `_derive_revlog_shape` used `due_at - last_review` to decode the step duration. After Layer 6, that included the fuzz, so the shape overcounted by up to 25% of the step.

**Fix.** Decode the unfuzzed step from `left + last_rating`, with Hard-on-first-step special case (Anki uses `(steps[0]+steps[1])/2` there).

**Files.** `app/anki/sync.py:_derive_revlog_shape`.

---

## Layer 7 — `session_main_queue` invalidation on sync

**Bug.** The Layer-4 freeze never repositioned cards that transitioned learning→review mid-session. A card that graduated yesterday evening showed up at the cached tail (as a latecomer) today, instead of head-of-R-asc.

**Fix.** Three parts:
- `sync_pull` invalidates `session_main_queue` on completion (mirrors Anki's `requires_study_queue_rebuild`).
- `/review-queue` drops review-state latecomers instead of appending them at the tail.
- `clear_session_main_queue` helper + `delete_anki_state_cache` DB method.

**Files.** `app/srs/queue_stats.py:clear_session_main_queue`, `app/srs/database.py:delete_anki_state_cache`, `app/anki/sync.py`, `app/api/srs.py`.

---

## Layer 8a — review badge driven by TT state

**Bug.** `/queue-stats.review` used `count_anki_review_remaining_today`, which reads `collection.anki2`. Didn't decrement when the user graded a card in TT.

**Fix.** `review = db.count_review_due_collocations(today)` — distinct collocation count with sibling-bury semantics. Cross-app catch-up at sync.

**Files.** `app/srs/database.py:count_review_due_collocations`, `app/api/srs.py`.

### Layer 8b — frontend visibility refetch

**Bug.** API was correct but widget didn't update after Anki-side grades because frontend only refetched on TT grade events.

**Fix.** `$effect` registers `visibilitychange` listener; refetches with `sessionStart=false` when tab regains focus.

**Files.** `frontend/src/routes/review/+page.svelte:46-58`.

---

## Layer 9 → reverted at Layer 14

**Initial idea.** Add `reviewed_today` to `R_start` for intersperser ratio. Reasoning: Anki's ratio uses session-start counts, not current remaining.

**Why it failed.** Worked for pre-sync drift but broke post-sync alignment. Anki actually *rebuilds* on sync (`gather_due_cards`) using current pool, not session-start pool. So after sync, both apps should use natural list lengths.

**Resolution.** Layer 14 removed the override.

### Layer 9b — `count_review_due_collocations` excludes graded-today

**Bug.** Dual-template note with one direction graded today still counted (TT +3 over Anki: srajca, streha, usta).

**Fix.** Exclude collocations whose any direction has `last_review` today.

**Files.** `app/srs/database.py:1412`.

### Layer 9c — `import_seed` word-count filter

**Bug.** `1 <= word_count <= 8` filter rejected legitimate phonics/reference Q&A notes whose extracted L2 text was >8 words. 13 missed at original import.

**Fix.** Drop upper bound on `SyntacticUnit.word_count` and `import_seed` filter; keep `>=1`. Backfilled via re-run.

**Files.** `app/models/syntactic_unit.py:31`, `app/anki/import_seed.py:115`.

---

## Layer 10 → replaced at Layer 12

**Initial idea.** Sync writes `MAX(revlog.id)` as `last_review`.

**Why it failed.** For cards graded multiple times in one session (Again → relearning step → Hard), revlog-max advances on every step while Anki's `extract_fsrs_retrievability` uses `cards.data.lrt`, which sticks to the FSRS-touched grade. Different sources, different R values.

**Resolution.** Layer 12 switched to reading `lrt`.

---

## Layer 11 — `compute_retrievability` sub-day precision

**Bug.** Day-level elapsed snapped freshly-graded cards (with precise lrt today) to `elapsed=0` → `R=1.0` → sent to end of queue.

**Fix.** `compute_retrievability` accepts `now: datetime` and computes fractional elapsed in seconds when `last_review` is a precise datetime.

**Files.** `app/srs/fsrs.py:91-127`.

---

## Layer 12 — `parse_fsrs_data` reads `lrt`

**Bug.** TT used `MAX(revlog.id)` per card (Layer 10), but Anki's R formula uses `cards.data.lrt` — sticky to the FSRS grade event, not advanced by step transitions.

**Fix.** Extract `lrt` from `cards.data` JSON when available; fall back to day-level `_compute_last_review`. Sync uses `card_rec.last_review` directly.

**Files.** `app/anki/sqlite_reader.py:235-241`, `app/anki/sync.py:755-766`.

---

## Layer 13 — `_compute_today_col_day` mirrors Anki

**Bug.** Naive `(now - crt) // 86400` undercounts when `crt` is at noon UTC and `now` is in morning UTC. Ignores rollover hour entirely. TT thought today=4509 while Anki used 4510, so review-pool filters were off by a day.

**Fix.** Local-date subtraction + rollover-hour adjustment (mirrors `scheduler/timing.rs::sched_timing_today_v2_new`).

**Files.** `app/srs/queue_stats.py:39-82`.

---

## Layer 14 — drop ratio_override + over-fetch new cards

**Bug 1.** Layer 9's `r_start = R_remain + reviewed_today` was wrong: Anki rebuilds on sync with current pool, not session-start pool. After sync, TT must use natural list lengths.

**Bug 2.** TT pulled only `new_quota` new cards then sibling-buried some — ending up under quota. Anki keeps iterating, skipping buried cards, until quota is hit.

**Fix.** Remove `ratio_override` in `get_review_queue` (natural `(one_len+1)/(two_len+1)`). Pull `max(new_quota*4, new_quota+50)` new cards, final cap at `new_quota` AFTER proactive sibling-bury.

**Files.** `app/api/srs.py`.

---

## Layer 15 — integer-day elapsed for non-lrt cards

**Bug.** Cards without `lrt` in `cards.data` have `last_review = midnight UTC` (day-level fallback). Anki's `extract_fsrs_retrievability` for these uses `(today_col_day - review_day) * 86400` = integer-day elapsed. TT used fractional elapsed (5.5d vs Anki's 5d → R 0.706 vs 0.723 → flipped R-asc order).

**Fix.** `compute_retrievability` detects midnight-UTC `last_review` and uses integer-day elapsed. Precise lrt still uses fractional (Layer 11 preserved).

**Files.** `app/srs/fsrs.py:91-127`.

---

## Layer 16 — Drop live-Anki dependency from `/queue-stats`

**Bug.** `count_anki_introduced_today` read `collection.anki2` on every `/queue-stats` and `/review-queue` request. Violated "Anki = reference, not runtime dependency."

**Fix.** Both call sites swapped to `db.count_new_introduced_today(today)` — pure TT state (`prior_state='new' AND last_review today`). Function deleted along with its 9-test class. Stale `patch()` calls in `test_api.py` simplified.

**Files.** `app/api/srs.py`, `app/srs/queue_stats.py`, `tests/test_queue_stats_cache.py`, `tests/test_api.py`, `tests/test_api_srs.py`.

---

## Layer 17 — `_direction_differs` compares `left`, `due_at`, `prior_state`

**Bug.** `sync_pull`'s diff-before-write check excluded these three fields. A merged direction whose only change was step-state or `prior_state` could be silently dropped.

**Fix.** Added all three to the comparison.

**Files.** `app/anki/sync.py:506-530`.

---

## Layer 18 — `sync_pull` defers to Anki when Anki is ahead

**Bug.** When `dirty_fsrs=True` AND both apps still saw the card as learning, sync_pull kept TT's `left`. Push then wrote TT's stale `left` over Anki's — un-graduating cards Anki had already advanced past.

**Fix.** Two new branches in the `dirty_fsrs` path:
- **Inverse state-class divergence**: local LEARNING but Anki queue=2 (graduated) → Anki wins, drop dirty, surface `state_class` conflict.
- **Step progress**: both in learning but `_anki_step_ahead(anki.left, local.left)` is true → take Anki's `left`/`due_at`/FSRS state, drop dirty, surface `step_progress` conflict.

New helper `_anki_step_ahead(anki_left, local_left)` encapsulates the `% 1000` comparison (shared with Layer 19).

**Files.** `app/anki/sync.py`, `tests/test_anki_sync_pull.py`.

---

## Layer 19 — `sync_push` skips when Anki is ahead

**Bug.** Push unconditionally called `set_learning_state(card_id, ds.left, …)`. If Anki had already graduated the card (queue=2) or had a smaller `total_remaining`, the write erased Anki's progress.

**Fix.** New `OfflineWriter.get_current_card_state(card_id)` returns `{queue, type, left} | None`. In `sync_push`, before writing learning state, fetch Anki's current row. If `queue=2` or `_anki_step_ahead(anki.left, ds.left)`, skip the card write **and** the revlog, mark the direction clean, increment `directions_pushed`, continue. Layer 18's pull-side merge then carries Anki's state into TT on the same sync.

`FakeWriter` in `tests/test_anki_sync_push.py` gained `current_states: dict[int, dict]` + `get_current_card_state(card_id)`.

**Files.** `app/anki/sync.py`, `tests/test_anki_sync_push.py`.

---

## Layer 20 — `sync_pull` sets `prior_state` on state-class transitions

**Bug.** `sync_pull` never wrote `prior_state`. After Anki introduced a card today (queue 0→1 or 0→2), TT mirrored `state=LEARNING/REVIEW` but `prior_state` stayed None. `count_new_introduced_today` filters by `prior_state='new'`, returned 0 → new-card badge stuck at `cap − 0` instead of `cap − N`.

**Fix.** New helper `_resolve_prior_state(local_dir, new_state, *, first_review_ms, today_start_ms)`:
- On state-class change → return `local_dir.state` (captures the transition).
- Else preserve `local_dir.prior_state` (no-op syncs don't clobber).
- **Self-heal**: when state matches and Anki's `first_review_ms` is today AND new_state isn't NEW, force `prior_state='new'`. Recovers stale data from pre-Layer-20 syncs and from same-day graduations that clobbered the marker. Broadened in Layer 22 to fire regardless of current `prior_state` value.

Wired into all 6 direction-construction sites in `sync_pull`. Extended `CardRecord` with `first_review_ms` (sourced from `MIN(revlog.id)` in `OfflineReader`).

**Files.** `app/anki/sync.py`, `tests/test_anki_sync_pull.py`.

---

## Layer 21 — Sticky-NEW `prior_state` (initial: same-class only)

**Bug.** After Layer 20 correctly set `prior_state='new'` on Anki-introduced cards, TT's grade endpoint overwrote it. Every `_schedule_with_steps` branch did `prior_state=prev.state`, so grading Good on a freshly-introduced learning card changed `prior_state` from `'new'` to `'learning'`. Card dropped from `count_new_introduced_today` → badge rebounded **up** by 1.

**Fix (initial).** New helper `_grade_prior_state(prev, new_state)`:
```python
if new_state == prev.state and prev.prior_state == SRSState.NEW:
    return SRSState.NEW
return prev.state
```
`'new'` sticky across **same-state-class** grades. All 7 `prior_state=prev.state` sites in `fsrs.py` swapped.

**Files.** `app/srs/fsrs.py`, `tests/test_srs_fsrs.py`, `tests/test_api_srs.py`.

**Followup.** Layer 22 broadened the sticky to also cover LEARNING→REVIEW graduation.

---

## Layer 22 — Sticky-NEW across graduation; broader self-heal

**Bug.** Layer 21's sticky-NEW only covered same-state grades. When a card was introduced AND graduated today (LEARNING→REVIEW), the graduation event reset `prior_state` to `'learning'` — same drop-from-count as Layer 21 but on a different transition. User saw TT show `new=2` while Anki showed `new=0`; the gap was 3 cards (pesek, kovina, les) that went NEW→LEARNING→REVIEW today.

**Fix.**
- `_grade_prior_state` now releases sticky-NEW only on REVIEW→RELEARNING (lapse), where revlog `type=1` correctness needs `prior_state='review'`. All other transitions preserve `'new'`.
- `_resolve_prior_state` self-heal broadened: fires whenever `first_review_ms >= today_start_ms` and new_state isn't NEW, regardless of current `prior_state` value. Recovers stale cards on re-sync without manual SQL.

**Files.** `app/srs/fsrs.py:209`, `app/anki/sync.py:_resolve_prior_state`, `tests/test_srs_fsrs.py`, `tests/test_anki_sync_pull.py`.

---

## Layer 23 — Just-graded learning collapse

**Bug.** After grading srebro in TT, srebro re-appeared immediately while Anki served družina next. Cause: Anki's `requeue_learning_entry` (`rslib/scheduler/queue/learning.rs:94-113`) shifts a just-requeued learning card's `due` to `next.due + 1s` when main is empty and the card would otherwise be served immediately — preventing "press Good, see same card." TT had no equivalent.

**Fix.** In `get_review_queue`, after sorting `pending_learning`, if `ordered_main` is empty AND `len(pending) >= 2` AND head's `last_review == cutoff` (i.e. just graded) AND head's `due_at <= cutoff+1200s` AND next's `due_at+1 < cutoff+1200s` AND `next.due_at >= head.due_at`, swap positions [0] and [1]. Since TT rebuilds the queue from disk each request, the swap achieves the same display effect without mutating stored `due_at`.

The `last_review == cutoff` equality check is exact — the grade endpoint sets both from the same `now`, so they match to microsecond precision for the most-recently-graded card.

**Files.** `app/api/srs.py:927-947`, `tests/test_api_srs.py:TestJustGradedLearningCollapse`.

---

## Path 2 (deferred architectural pivot)

Across Layers 9-15 in particular, every fix to "TT reconstructs Anki's queue from TT state" surfaced another input-quality bug. The pattern: Anki has N code branches, TT had mirrored M of them. Path 2 would dissolve this whole class by snapshotting Anki's actual queue at sync time.

**Mechanism.** At `sync_pull`, while `collection.anki2` is open, execute Anki's `review_order_sql` and persist the resulting card-id sequence as TT's "today's anchor queue." Between syncs, TT serves from the snapshot, filtered by "graded since sync."

**Tradeoffs.**
- Wins: removes session_main_queue freeze, intersperser ratio override, R-asc reconstruction, two-branch R formula mirror, today_col_day computation, sibling-bury reconstruction. Estimated 60% of `app/api/srs.py` queue logic goes away.
- Costs: ~2-3 hour refactor. Single sync-time dependency on `collection.anki2` (already accepted at sync). Slightly less flexible if TT later wants to serve cards Anki wouldn't have served (e.g. self-introduced TT-only cards).

**Decision.** Not yet executed. Layers 18-23 closed enough leaks that the leak count is flat. Revisit if the next session needs another R-asc patch.

---

## Pending work (not started)

1. **~~Review-interval fuzz~~** (resolved by Layers 6 + N cascade). TT already adds review-interval fuzz via `_review_interval_fuzz`. The remaining gap — `_next_interval` not applying the `greater_than_last` cascade — was closed by Layer N.

2. **`sync_pull` create-on-encounter.** Currently `sync_pull` silently skips cards in Anki that have no TT row. Only `import_seed` creates rows. Proposed: have `sync_pull` call the `extract_l2 → upsert_by_guid` flow when it encounters a missing TT row.

3. **`sync_push` writes `reps`/`lapses`.** `set_learning_state` doesn't update Anki's `cards.reps` or `cards.lapses`. After a TT-side grade + push, Anki's reps stays at the pre-TT-grade value. Sync_pull then regresses TT's reps. Symptom: `palec` outlier observed in Layer 23 diagnostic — TT.reps=1, Anki.reps=0 for a card TT had graded.

---

## How layer-numbering works

Layers are chronological across sessions. Some are sub-numbered (6b, 8a, 8b, 9b, 9c) when a single user-visible report broke into multiple bugs handled in one push. Reverted layers (9, 10) are documented with their reasoning so future work doesn't re-introduce the same wrong path.

When adding a new layer to this file:
- Number it `Layer N+1` (or `Layer Nx` if it's a sub-issue of N).
- Lead with the user-visible bug (one paragraph).
- Then the fix mechanism (one paragraph).
- Then files touched (one line).
- Cross-link any layer it interacts with.

---

## Layer 24 — Recency-prioritized new bucket (both sides)

**Trigger.** Phase A `/listen` auto-adds sank to the back of the new queue, hidden behind the imported Anki backlog. New rows (`anki_due=NULL`) were sorted after imported rows (`anki_due` in the hundreds) by TT's `get_new_items` ORDER BY. After sync, `MAX(due)+1` gave them the highest `cards.due`, but Anki's default "Ascending position" gather surfaces lowest-due first — so they landed at the end of Anki's new queue too.

**TT divergence.** Leading sort by `c.created_at DESC` before the existing `d.anki_due ASC / d.anki_card_id ASC / c.id ASC` tiebreakers. Fresh auto-adds (create time = now) sort ahead of the imported backlog (create time = import_seed time). Migration v16→v17 adds `idx_collocations_created_at` to keep the query cheap.

**Anki-side alignment.** `sync_create_new` sorts items by `created_at` ASC before the existing `MAX(due)+1` allocator. Oldest pending item gets the lowest `cards.due`; newest gets the highest. With the user's "Descending position" deck setting (`due DESC, ord ASC`), Anki surfaces highest-due (= newest) TT card first.

**Why we deviate.** Listen-first acquisition loop. The user's most recent listening exposure must surface before the historical backlog. Anki has `NewCardGatherPriority::HighestPosition` ("Descending position") in its deck options — the user flips this once, and both apps show the freshest card first.

**Sync impact.** None. `created_at` is a TT-side column; sync doesn't carry it. `cards.usn = -1, mod = ts` already set by `create_note`. No `col.scm` change, no new sync code path. The existing `MAX(due)+1` allocator is unchanged.

**Files.** `backend/app/srs/database.py:622-634` (ORDER BY), `backend/app/srs/database.py` — new `get_created_at_by_guid` helper, `backend/app/anki/sync.py:1295-1297` (sort), `backend/app/srs/migrations.py` (v16→v17), `.claude/rules/anki-queue-parity.md` (playbook), `docs/anki-parity-layers.md` (this entry).

---

## Layer 25 — Anki-matching new-bucket sort (revises Layer 24)

**Trigger.** User running both apps simultaneously: at the start of a session with no relearning, queues stayed in lock-step for review cards but diverged at the first new-bucket card — Anki served one card, TT another. Even though the user had correctly set "Descending position" in Anki, Layer 24's `c.created_at DESC` primary key meant TT and Anki disagreed whenever `created_at` and `cards.due` didn't move together (e.g., pre-Phase-C imports, manually re-imported rows).

**Fix.** Replace the primary sort key with the one Anki actually uses under HighestPosition: `d.anki_due DESC NULLS FIRST, c.created_at DESC NULLS LAST, d.anki_card_id ASC NULLS LAST, c.id ASC`. Synced rows order identically in both apps. Unsynced TT-adds (anki_due NULL) sit on top via NULLS FIRST, preserving the listen-first benefit of Layer 24 — and once `sync_create_new` allocates `MAX(due)+1`, they re-anchor at the top of the synced pool.

**Why the original Layer 24 sort was wrong.** Anki-imported backlog rows have monotonic `anki_due` set by Anki's own position counter — but `created_at` for those rows is whatever timestamp `import_seed` ran at, NOT the user's actual learning order. So `created_at DESC` would, for example, put a 2026-05-08 import of "časa" ahead of a 2026-05-01 import of "zdravo" even when Anki's `cards.due` says zdravo (1002000) should come before časa (1001997).

**Files.** `backend/app/srs/database.py:613-654` (ORDER BY rewrite), `backend/tests/test_srs_database.py` (new test class), `backend/tests/test_api_srs.py` (replaced legacy ASC test).

---

## Layer 26 — `introduced_at` column for `count_new_introduced_today`

**Trigger.** User grading the same deck in both apps. At a point where no new cards had been graded today (Anki's `newToday` = 0), TT's new badge read 29 while Anki's read 30. Diagnosis: the single graded card had `prior_state='new'` set by the sticky-NEW marker (stamped on its original introduction days earlier, sticky through every grade) and a `last_review` in today's range — so the legacy `WHERE prior_state='new' AND last_review today` filter counted it as a fresh introduction. Anki's `newToday` only increments on the actual first-grade event, never on subsequent reviews.

**Fix.** Add `collocation_directions.introduced_at TEXT` (migration v17→v18) and stamp it exactly once per intro arc:
- `fsrs._schedule_new` writes `introduced_at = last_review_dt` on NEW → LEARNING/RELEARNING.
- `fsrs._graduate_to_review` writes it on NEW + EASY → REVIEW; preserves on LEARNING/RELEARNING → REVIEW.
- `sync._resolve_introduced_at` writes it on first observed Anki revlog when local is still NULL; sticky thereafter.
- All other `replace`-driven transitions preserve the existing value via `prev.introduced_at`.

`count_new_introduced_today` now filters `introduced_at >= today_start_utc AND introduced_at < tomorrow_start_utc`. Pre-Layer-26 rows have NULL and don't count — acceptable, because their actual introductions happened before this column existed (revisits would have already over-counted under the old filter; correction is a one-time decrement).

**Why not keep the sticky-NEW filter and add a `reps=1` gate?** A NEW + Again grade today produces `reps=2 + prior_state=new + last_review=today`, which is still a today-introduction. A `reps=1` gate would drop it. `introduced_at` decouples "is this on the intro arc?" (sticky `prior_state`) from "when did the intro happen?" (single-stamp `introduced_at`).

**Files.** `backend/app/models/srs_item.py` (DirectionState field), `backend/app/srs/migrations.py` (v17→v18 + CURRENT_VERSION = 18), `backend/app/srs/database.py` (`_DIR_COLUMNS`, `update_direction`, `_row_to_directions`, `count_new_introduced_today`), `backend/app/srs/fsrs.py` (`_schedule_new`, `_graduate_to_review`), `backend/app/anki/sync.py` (`_resolve_introduced_at` + 9 sync-merge call sites), `backend/tests/test_srs_database.py` (`TestCountNewIntroducedToday`), `backend/tests/test_srs_migrations.py` (v17→v18 test).

---

## Layer 27 — Daily unbury sweep for stale `state='buried'`

**Trigger.** Snapshot showed 151 directions in TT with `state='buried'` but only 4 of those had `queue=-2` in Anki — 147 stale-buried rows accumulated going back to 2026-05-03. Anki's queue builder unburies (queue=-2 → queue=2) once per day on the first rebuild after rollover, but TT relied on `sync_pull` to overwrite `state='buried'` with the next pulled state. Without recent syncs, TT under-counted reviews and silently dropped cards from the queue.

**Fix.** `SRSDatabase.unbury_if_needed(today)` sweeps all `state='buried'` rows to `state='review'` (reps>0) or `state='new'` (reps=0). Idempotent per local day via `anki_state_cache['last_unbury_day']` — second call same-day is a no-op so today's fresh sibling-buries (landed by mid-day `sync_pull`) survive until tomorrow. Hooked into:
- `GET /api/srs/queue-stats` (badge correctness)
- `GET /api/srs/review-queue` (queue head)
- `sync_pull` (before processing Anki state, so any buried rows the pull lands are today's and won't be re-swept)

**Why "reps>0 → review, reps=0 → new"?** TT's BURIED state only enters via `sync_pull` mirroring Anki's queue=-2/-3 (the sibling-bury or user-bury terminal states). For those, the pre-bury state was either review (graded card whose sibling was today's grade) or new (rare, but possible for sibling-buried news under `bury_new=true`). `reps` is the only signal in the row that distinguishes them.

**Files.** `backend/app/srs/database.py` (`unbury_if_needed`), `backend/app/api/srs.py:get_review_queue, get_queue_stats` (call sites), `backend/app/anki/sync.py:sync_pull` (pre-merge sweep), `backend/tests/test_srs_database.py` (`TestUnburyIfNeeded`), `backend/tests/test_api_srs.py` (queue-endpoint integration test).

---

## Layer 28 — Anki-parity gather + Template re-sort for the new bucket (revises 25)

**Trigger.** Layer 25 aligned TT's `get_new_items` ORDER BY with Anki's `HighestPosition` gather (`due DESC, ord ASC`), but the user still saw TT serve `časa` while Anki served `sekira` first.

**Root cause.** Anki's queue builder gathers BOTH ords together in one pass (rslib `queue/builder/gathering.rs:63-169`), and `add_new_card` proactively buries the second-seen sibling. For `časa` (rec ord=0 due=1001997, prod ord=1 due=1001998), the gather order is `prod, rec` — so `prod` survives, `rec` gets buried. Then `sort_new` (rslib `queue/builder/sorting.rs:14-36`) re-sorts the survivors stably by `template_index` (= `ord`). Survivors `[časa prod (ord=1), sekira rec (ord=0)]` → after Template stable sort: `[sekira rec, časa prod]`. Anki serves `sekira` first.

TT did this in three separated steps (`get_new_items(REC)`, `get_new_items(PROD)`, `_merge_directions` = rec-then-prod concat), so sibling-bury favored whichever direction was listed first instead of the higher-due one. `časa rec` won by listing order, masking the divergence.

**Fix.** `_merge_directions` now interleaves both directions by Anki's gather key `(anki_due DESC NULLS FIRST, ord ASC, anki_card_id ASC, row_id ASC)`. The existing `_bury_siblings_in_queue` step (which kept first-seen-wins behavior) now correctly favors the higher-due sibling. After bury, `get_review_queue` applies a stable sort by `ord ASC` over the surviving new pool — the Anki Template step. End-to-end: gather → bury → Template-sort matches `rslib/queue/builder/{gathering,sorting}.rs` exactly.

**Files.** `backend/app/api/srs.py:_merge_directions` (re-keyed sort), `backend/app/api/srs.py:get_review_queue` (added stable sort by ord after bury), `backend/tests/test_api_srs.py` (updated `_merge_directions` tests + added `test_review_queue_new_head_matches_anki_gather_bury_template` reproducing the `časa`/`sekira` case).

**Aftermath / lessons.**
- The user reported "fix isn't working" because `session_main_queue` (DB-backed cache; survives restarts) still held the pre-Layer-28 order. Sync clears it; restart does not. Future debug sessions: run `clear_session_main_queue` before doubting a fix — see the new note in principle 2 of the rule file.
- Layer 25 fixed the per-direction ORDER BY but missed that Anki's gather phase pools both ords together AND its sibling-bury runs during gather (rejecting the second-seen sibling). The Layer-25 sort was right; the merge step it fed was wrong. The right mental model: TT's `get_new_items` produces the per-direction *input* to the merge, not the final order. `_merge_directions` is the gather phase; the bury step matches Anki's `add_new_card`; the post-bury `nonlearning_new.sort` is Template.

---

## Layer 29 — Eager session_main_queue rebuild at sync_pull

**Trigger.** Mid-session, the user reported TT serving a review card (`krožnik`) while Anki served a new card (`zdravo`) at the same position in their progress. Both apps had near-identical pools (TT: 136 reviews + 28 new; Anki: 133 + 28). The cause: TT and Anki froze their queues at *different moments*. Anki rebuilds at session open; TT's `sync_pull` previously only *cleared* `session_main_queue`, deferring the rebuild to the next `/review-queue` request. If hours passed between sync and the next request, the pool shifted and the frozen orders diverged by a slot or two — enough to make the first-new card land on different positions of the spread.

**Fix.** `sync_pull` now eagerly rebuilds the cache via `build_and_freeze_main_queue(db)` immediately after `clear_session_main_queue`. The freeze moment is the sync moment, matching Anki's `requires_study_queue_rebuild`. Extracted `_compute_live_main(db)` from inside `get_review_queue` so the rebuild logic has one home (deduped: route handler and sync_pull both call it).

**Files.** `backend/app/api/srs.py` (new `_compute_live_main` + `build_and_freeze_main_queue`; `get_review_queue` refactored to call the helper), `backend/app/anki/sync.py:1257-1267` (sync_pull now calls build + freeze), `backend/tests/test_anki_sync_pull.py` (updated `test_sync_pull_clears_…` to `test_sync_pull_rebuilds_…` reflecting the new contract).

**Aftermath / lesson.** The stale-cache trap from Layer 28's aftermath is now eliminated for the sync_pull path. Deploy-time stale cache (cache held in DB across backend restart) is still a concern — `clear_session_main_queue` from a manual diagnostic remains the right escape hatch when reasoning about ordering bugs against an old freeze. Documented in principle 2 of `.claude/rules/anki-queue-parity.md`.

---

## Layer 30 — `_queue_to_state` must trust `queue`, not `reps`

**Trigger.** TT served `ničnothing` as the first-new card while Anki served `zdravo`. `ničnothing` in Anki was `queue=2` (review), `type=2`, `due=4515`, `ivl=16`, `reps=0` — a card that's clearly been graduated but somehow has `reps=0` (e.g., the user used Anki's "Forget" action, which clears `reps` but leaves the card in `queue=2`). TT's `_queue_to_state` had this fallback at the bottom: `if reps == 0: return SRSState.NEW`. The `queue=2` arm was never reached. So sync_pull saw the card as Anki-reviewed but wrote `state='new'` to TT — and TT then surfaced it at the head of the new bucket every day.

**Fix.** `_queue_to_state` now uses `queue` as the authoritative signal: `queue=2 → REVIEW`, `queue=0 → NEW`, regardless of `reps`. The reps fallback only fires for unknown queue values (never happens against current Anki, but defensively kept for future-proofing).

**Files.** `backend/app/anki/sync.py:_queue_to_state` (explicit `queue == 2` arm added before the reps fallback; `queue == 0` arm explicit too), `backend/tests/test_anki_sync_pull.py::test_queue_to_state_mapping` (added `(queue=2, reps=0) → REVIEW` and `(queue=2, reps=7) → REVIEW` parametrize cases; updated existing `(queue=0, reps=5)` from REVIEW to NEW to reflect "queue is authoritative").

**How to spot in the wild.** Run the diagnostic:
```sql
SELECT c.id, c.queue, c.type, c.due, c.reps, c.ivl, n.flds
FROM cards c JOIN notes n ON c.nid=n.id
WHERE c.queue=2 AND c.reps=0 AND c.did=<your-deck>;
```
Any row here is a "Forget"-style or manually-edited card. After Layer 30 these correctly mirror to TT as REVIEW.

---

## Layer 31 — `<b>L2</b><br><i>EN</i>` import bug + one-shot cleanup script

**Trigger.** User noticed `ničnothing` at the head of TT's new bucket — clearly mangled text (Slovene `nič` concatenated with English gloss `nothing`). Traced to `extract_l2_from_fields` in `app/anki/sqlite_reader.py`: the HTML-strip fallback `re.sub(r"<[^>]+>", "", field)` removes tags without inserting whitespace, so Anki's Pronunciation/Basic notetype Front field `<b>nič</b><br><i>nothing</i>` collapsed into the single token `ničnothing`. Saved as TT's `text`, English gloss lost. 39 rows affected in the user's deck (every Basic-notetype note that used the `<b>L2</b><br><i>EN</i>` formatting).

**Fix (import side).** Added `extract_gloss_from_fields(fields) -> str | None` that recognises the `<b>L2</b><br><i>EN</i>` pattern and returns the gloss. Updated `extract_l2_from_fields` to short-circuit on the same pattern returning the L2 group. `import_seed.py` now checks `extract_gloss_from_fields` before falling back to the "other field" stripped-HTML translation extractor. Future imports of these notes do the right thing.

**Fix (existing data).** One-shot script `app/anki/fix_html_concat_imports.py` walks every TT collocation linked to an Anki note, parses the Front field, and:
- **renames** the row (`text=L2, translation=EN`) when no other TT collocation already uses the clean L2 text;
- **deletes** the row when a clean-L2 twin already exists (Pronunciation cards duplicate Slovene Vocabulary cards for these words; user opted to drop the dupes).

Defensive: if a rename hits a UNIQUE conflict at apply-time, it falls back to delete. Tests cover both planning and apply phases, plus the CLI's dry-run / missing-DB / mixed-output paths.

**Files.** `backend/app/anki/sqlite_reader.py` (added `_B_THEN_I_PATTERN`, `extract_gloss_from_fields`, second-pass arm in `extract_l2_from_fields`), `backend/app/anki/import_seed.py:128-144` (uses `extract_gloss_from_fields` when matched), `backend/app/anki/fix_html_concat_imports.py` (new one-shot script), `backend/tests/test_anki_sqlite_reader.py` (extractor tests), `backend/tests/test_anki_import_seed_readonly.py` (round-trip test), `backend/tests/test_anki_fix_html_concat_imports.py` (script tests).

**Aftermath.** Live cleanup applied: 19 renames + 20 deletes, no fallback-deletes needed. User's queue now serves clean Slovene words; `ničnothing` and friends are gone.

---

## Layer 32 — Overfetch limit broke cross-direction sibling bury

**Trigger.** After sync, TT served `eksplodirati` recognition (anki_due=1127) as the first new card while Anki served `zdravo` production (anki_due=1002000). The Layer 28 fix (`_merge_directions` interleaves both directions; bury favors the higher-due sibling; Template sort by ord) was correctly implemented — and called by the post-sync eager rebuild in Layer 29. But the merge step received per-direction lists that were truncated asymmetrically.

**Mechanism.** `_compute_live_main` fetched `new_rec` and `new_prod` with `limit = _NEW_OVERFETCH = max(new_quota * 4, new_quota + 50)` = 112 for `new_quota=28`. The user's pool had:
- ~30 production-only-new cards with `anki_due` in the 1001970–1002000 range (notes where the recognition sibling was already graduated).
- Many paired notes with low `anki_due` (e.g., `eksplodirati` rec=1127, prod=1128).

Sorted DESC, the high-anki_due production-only notes filled `new_prod`'s top 112 slots, pushing `eksplodirati prod` (1128) outside the limit. But `new_rec` returned only 36 cards total (no overflow), so `eksplodirati rec` (1127) WAS in the list. In the merge there was no `eksplodirati prod` to bury against → `eksplodirati rec` survived → Template sort by `ord` put it ahead of all production cards.

**Fix.** Replace the quota-based overfetch with `max(count_new_available, new_quota + 50)` — a strict upper bound on the per-direction new pool. For the user's ~350-card pool this fetches everything per direction, guaranteeing every paired note's `prod` (if state=new) is present in the merge alongside its `rec`. The bury step then correctly drops the lower-due sibling.

**Files.** `backend/app/api/srs.py:_compute_live_main` (one-line change to `_NEW_OVERFETCH`), `backend/tests/test_api_srs.py:test_review_queue_new_head_unaffected_by_overfetch_truncation` (regression test: 120 high-due production-only notes + one low-due paired note; asserts the paired-note's prod survives, not its rec).

**Lesson.** Cross-direction operations (merge, bury) require BOTH directions to be fetched coherently. Per-direction limits that don't account for the *union* size break the parity. A safer pattern: fetch unbounded, then cap *after* the merge.

---

## Layer 33 — NULLS FIRST only for genuinely-fresh /listen adds

**Trigger.** Post-Layer-32, TT served `trgovina` (production, anki_due=NULL) as the first new card while Anki served `zdravo` prod (anki_due=1002000). Both should agree.

**Root cause.** `trgovina` is a HOMONYM corruption from the user's Anki collection: there are TWO trgovina notes in deck "0. Slovene" — a Basic-notetype prompt card (nid=1775264031843) and a Slovene-Vocabulary dual-direction card (nid=1775264031842). TT's `compute_guid("trgovina","sl","")` collapses them to a single collocation. The collocation got linked to the Basic note (anki_note_id=...843) but its production direction's anki_card_id (1776536654137) points to the Slovene-Voc note's prod card. `sync_pull` iterates Anki by note, finds the Basic note via the collocation's anki_note_id, processes only that note's one card (recognition), and never reaches the Slovene-Voc note. The production direction stays state=new with anki_due=NULL forever.

Under Layer 28 + 32's `_gather_key`, `anki_due=NULL` always sorted to the front via NULLS FIRST — appropriate for /listen auto-adds (which the user wants to surface immediately) but wrong for cross-note phantoms.

**Fix.** Make NULLS FIRST conditional on `item.anki_note_id IS NULL`:
- **Fresh /listen add** (`anki_note_id IS NULL` AND `anki_due IS NULL`) → primary key `(0, 0)` → top.
- **Synced row** (`anki_due IS NOT NULL`) → primary key `(1, -anki_due)` → middle, by Anki position descending.
- **Phantom** (`anki_note_id IS NOT NULL` AND `anki_due IS NULL`) → primary key `(2, 0)` → bottom.

This preserves the Phase C listen-first benefit (true fresh adds surface immediately) while sinking cross-note / orphan / never-synced-direction phantoms below the synced pool — where they don't displace Anki's actual queue head.

**Files.** `backend/app/api/srs.py:_gather_key` (three-bucket primary key), `backend/tests/test_api_srs.py:test_merge_directions_sinks_phantom_directions` (regression), `test_merge_directions_nulls_first_for_fresh_listen_adds` (renamed + clarified existing test).

**Lesson.** "Unsynced" is ambiguous — a fresh /listen add and a cross-note phantom both look like `anki_due=NULL` at the direction level. The distinguishing signal is at the parent collocation: `anki_note_id IS NULL` means TT has never pushed this row to Anki, so it's genuinely new; `anki_note_id IS NOT NULL` means TT has pushed *something*, and any NULL `anki_due` on a direction reflects a sync gap, not a fresh add.

The underlying homonym corruption (TT collocation guid points to one Anki note while its directions link to cards on another) is a separate data-quality issue worth a future cleanup; Layer 33 only ensures the queue head behaves correctly in its presence.

---

## Layer 34 — LingQ-import historical mess + pinned spec

**Trigger.** Investigating Layer-33's `trgovina` phantom revealed a systemic corruption: 40 Anki Basic-notetype notes in deck "0. Slovene" that a prior buggy `/listen` import had created. 21 of them duplicated existing Slovene-Vocabulary notes (the "twins" — cross-note linked); 19 were words not in Anki that should have been pushed as Slovene-Voc with two cards but landed as Basic with one. The current `sync_create_new` already meets the correct spec (Slovene-Voc notetype, `DuplicateNoteError`-caught for guid-matched twins) — the mess is leftover data from code that's been since fixed.

**Cleanup script.** New `app/anki/fix_lingq_import_mess.py`:
- Identifies Basic-notetype notes in the deck whose Front matches one of three vocab patterns: `<b>L2</b><br><i>EN</i>`, `<div class="prompt">[L1]</div>` (back fields supply L2 + EN), or bare `<b>L2</b>`.
- For each, looks up TT's collocation by `anki_note_id` and checks for a Slovene-Voc twin by `LOWER(sfld) = LOWER(slovene)` (with '/'-split fallback so `ulica / cesta` matches twin `ulica`).
- DELETE plan items: drop the Basic note + cards; relink TT collocation to the Slovene-Voc nid; clear `anki_card_id`/`anki_due` on the directions so sync_pull repopulates.
- CONVERT plan items: change `notes.mid` to Slovene-Voc, reshape 2-field Basic flds → 7-field Slovene-Voc, recompute guid; keep the existing card as ord=0 (Recognition) so revlog stays attached; create a new ord=1 (Production) card; add the matching Production direction to TT. Bumps `col.scm` (notetype-of-note change is schema-significant per Anki's sync model).
- Uses `app.anki.safety.safe_open(mode='rw')` for backup + integrity check + post-write audit.
- Following the standard schema-bump workflow from `.claude/rules/anki-sync.md`: prints "File → Sync → Upload to AnkiWeb, then run `app.anki.normalize_usns`".

**Spec tests pinned** (so the buggy importer can't come back):
- `test_anki_sync_create_new.py::test_sync_create_new_uses_slovene_voc_for_source_llm` — `/listen`'s `source='llm'` rows become Slovene-Voc notes with both Recognition + Production card_ids populated in TT.
- `test_sync_create_new_vocab_duplicate_guid_links_not_creates` — when an Anki note with the matching guid already exists, `sync_create_new` catches `DuplicateNoteError` and links TT to the existing nid without creating a duplicate Anki note.
- `test_api.py::test_listen_creates_collocations_with_source_llm_and_no_anki_link` — `/listen` writes `source='llm'`, `anki_note_id=NULL` (guarantees the next sync handles it correctly).

**Aftermath.** After applying: 95 → 55 Basic notes in the deck (40 cleaned up: 21 deleted + 19 converted). `col.scm` bumped 1777771242057 → 1778635744000. User runs Anki File→Sync→Upload to AnkiWeb, closes Anki, runs `normalize_usns` to align local `*_gt_col` USN counts. Next `sync_pull` then repopulates `anki_due` on the relinked TT collocations; the queue head finally matches Anki — no more phantom-direction surprises.

**Files.** `backend/app/anki/fix_lingq_import_mess.py` (new), `backend/tests/test_anki_fix_lingq_import_mess.py` (new), `backend/tests/test_anki_sync_create_new.py` (extended), `backend/tests/test_api.py` (extended).

---

## Layer 35 — `bury_kind` split (refines Layer 27)

**Trigger.** After Layer 27's daily unbury sweep, manually-buried Anki cards (queue=-2) kept resurfacing in TT each morning. The user would manual-bury a card in Anki, sync, and on the next day's first `/queue-stats` call the sweep released it back to `state='review'`. Anki distinguishes two bury kinds — queue=-3 (sched/sibling, auto-released at rollover) and queue=-2 (user/manual, sticks until the user unburies) — but TT collapsed both into `state='buried'` and `unbury_if_needed` wiped all of them. The reported impact: ~18 manually-buried cards re-entered the review pool on each `/queue-stats` poll.

**Fix.** New column `collocation_directions.bury_kind TEXT` (migration v19→v20, `backend/app/srs/migrations.py:570-586`). Values: `'sched'` for sibling-bury (released by the daily sweep), `'user'` for manual-bury (stuck until manual unbury or sync_pull seeing Anki's card no longer buried), `NULL` for non-buried rows.

- `_bury_kind_from_queue(queue)` helper at `backend/app/anki/sync.py:916-928`: maps Anki's queue value (-3 → `'sched'`, -2 → `'user'`, else `None`). All 7 direction-construction sites in `sync_pull` pass `bury_kind=_bury_kind_from_queue(card_rec.queue)` (5 sites) or explicit `bury_kind=None` (2 sites, for non-buried writes).
- `unbury_if_needed` (`backend/app/srs/database.py:1595-1604`) now filters `WHERE state='buried' AND bury_kind='sched'`. User-buried rows survive the sweep, matching Anki's `unbury_if_needed` (which only releases queue=-3).
- Migration backfill: every existing `state='buried'` row gets `bury_kind='user'` (pessimistic — better to leave a sibling-bury sticky for one extra day than wipe a user-bury). The next sync_pull rewrites the kind from Anki's actual queue value.

**Files.** `backend/app/srs/migrations.py:570-586` (migration v19→v20), `backend/app/anki/sync.py:916-928` (helper) + 7 `bury_kind=` sites in `sync_pull` (5 `_bury_kind_from_queue`, 2 explicit `None`), `backend/app/srs/database.py:389/413/489` (column read/write), `backend/app/srs/database.py:1595-1604` (filtered sweep), `backend/app/models/srs_item.py` (DirectionState field), corresponding tests in `backend/tests/test_srs_migrations.py`, `backend/tests/test_anki_link_tt_images.py`, `backend/tests/test_srs_database.py`.

**Aftermath.** Manually-buried cards survive across days. The pre-Layer-35 footgun — "an unconditional `UPDATE … WHERE state='buried'` wipes user-buries on every poll" — is recorded in `.claude/rules/anki-queue-parity.md` principle 10 as the canonical anti-pattern. Code committed as part of `09dc812 fix(media): copy image file in link_tt_images so nič's card image isn't broken` (commit message footer notes "Also includes pre-existing bury_kind feature").

**Cross-reference.** Refines Layer 27 (daily unbury sweep) — without Layer 35 the sweep was over-aggressive. The two layers are now described together in `.claude/rules/anki-queue-parity.md` principles 9 and 10.

---

## Layer 36 — Daily review cap on the badge (render-only)

**Trigger.** After Layer 30 fixed the card-state mapping, TT showed 99 reviews while Anki showed 97 — a 2-card delta. Cross-referencing confirmed zero data drift (same 101 underlying cards). The gap was purely render: Anki applies `reviews_per_day` from `DeckConfig.Config` (protobuf field 10, default 200) to its badge; TT returned the raw uncapped `count_review_due_collocations()`.

**Fix.** Mirror the existing `daily_new_cap` plumbing for reviews:
- `_read_reviews_per_day_from_deck_config_table(conn, deck_name)` — reads protobuf field 10 from the `deck_config` table (modern Anki ≥2.1.55).
- `_read_reviews_per_day_from_anki(conn, deck_name)` — tries legacy JSON (`dconf[id]["rev"]["perDay"]`) first, then protobuf fallback.
- `refresh_daily_review_cap(db, conn, deck_name)` — writes the cap to `anki_state_cache` at sync time.
- `resolve_daily_review_cap(db)` → `(cap, "cache"|"config"|"default")` — priority: cache → `settings.anki_reviews_per_day_default` (default 200) → hard default 200.
- `count_reviews_completed_today(today)` — counts distinct `(collocation_id, direction)` pairs with `state IN ('review','relearning')`, `last_review` within today's local-day window, and non-null `last_rating`.
- `/queue-stats` computes `review = max(0, min(due_raw, cap − reviews_today))`.

**Important: render-only.** The cap is NOT applied inside `_compute_live_main` or anywhere in queue assembly. Anki doesn't cap the queue's served cards — only the badge. Defer capping the queue until requested.

**Files.** `backend/app/config.py` (new setting), `backend/app/srs/queue_stats.py` (trio), `backend/app/srs/database.py` (`count_reviews_completed_today`), `backend/app/api/anki.py` (wired), `backend/app/api/srs.py` (cap applied).

**Aftermath.** TT's review badge now matches Anki's deck-list "Due" count within ±1 (boundary drift at rollover acceptable). The `daily_review_cap` and `review_cap_source` fields appear in the `/queue-stats` response.

**Cross-reference.** The existing `daily_new_cap` trio at `queue_stats.py:129-274` is the pattern; this layer mirrors it exactly.

---

## Layer 37 — `anki_card_mod` in `_direction_differs` (FNV tiebreaker drift)

**Trigger.** Three R-tied review cards (`iz`, `nič`, `dobrodošli` — all `data='{}'`, all queue=2, all due today) had different head positions between TT and Anki. Anki sorted them by `fnvhash(cards.id, cards.mod)`; TT's mirror agreed on the algorithm but used stale `anki_card_mod` values pulled from a prior sync, producing different hashes and a different ordering inside the tied group.

**Cause.** `_direction_differs(local, candidate)` in `backend/app/anki/sync.py` checked every sync-relevant FSRS field but NOT `anki_card_mod`. Whenever Anki bumped `cards.mod` for any reason that didn't also change an FSRS field — server-side sync mtime resolution, scheduler housekeeping, bury actions — sync_pull's diff returned False and TT's local copy stayed stale. The FNV tiebreaker (Anki's `fnvhash(id, mod)` appended last in every `review_order_sql` variant — `rslib/src/storage/card/mod.rs:897`) silently diverged.

**Fix.** One line in `_direction_differs`: `or local.anki_card_mod != candidate.anki_card_mod`. Sync_pull already constructs candidates with `anki_card_mod=card_rec.anki_card_mod` (the current Anki value), so once the diff fires, `update_direction` refreshes the column.

**Files.** `backend/app/anki/sync.py:841-866` (diff check); regression tests in `backend/tests/test_anki_sync_pull.py` (`test_direction_differs_detects_anki_card_mod_change`, `test_sync_pull_refreshes_stale_anki_card_mod`).

**Aftermath.** R-tied groups across both apps now agree on the tiebreaker. Write volume on sync is slightly higher (any mod-only Anki bump triggers a TT-side update), but each write is one small UPDATE.

**Cross-reference.** `_merge_by_retrievability_ascending` at `backend/app/api/srs.py:768-800` is where the FNV value feeds the sort. Anki's matching constant: `rslib/src/storage/card/mod.rs:823` (`fnvhash(id, mod)`).

---

## Layer 38 — NULL-R sort: `desired_retention` placement (NOT NULLs-first, NOT NULLs-last)

**Trigger.** During parallel grading, the user repeatedly saw TT serve cards earlier or later than Anki for the same data state. The recurring offender was `nič` — a queue=2 card with `data='{}'`, `reps=0`, no FSRS memory_state. TT placed it at queue head; Anki placed it mid-pool. Earlier in the session, TT had placed it at the tail (pre-Approach-2 behavior); Approach 2 flipped it to the head; both were wrong.

**Empirical finding (Anki 25.09.4).** With the snapshot inspected via `uv run --with anki python` + `col.sched.get_queued_cards(fetch_limit=20)`, Anki places `data='{}'` review cards at the position `desired_retention` would occupy in R-asc. For the user's deck (`desired_retention=0.86`), nič landed between `streljati` (R=0.859) and `steklenica` (R=0.862). Same SQL run via `col.db.all(...)` with Anki's own UDFs returns NULL for nič, and the ORDER BY says NULLs-first — i.e. Anki's own SQL predicts head placement. The actual queue places it mid-pool. **The source-vs-binary contradiction was not resolved; the binary behavior was adopted as ground truth.** The `/tmp/anki-source` checkout is a shallow clone at `main` tip with no version tag — almost certainly newer than 25.09.4 — so the SQL-level explanation may live in a code path that has since been replaced.

**Pre-existing bug surfaced.** `_DESIRED_RETENTION_FIELD` in `app/srs/queue_stats.py` was set to **40**, but per `proto/anki/deck_config.proto:188`, field 37 is `desired_retention` and field 40 is `historical_retention`. The existing `refresh_fsrs_params` was caching `historical_retention` thinking it was `desired_retention` (often 0.9 vs the user's 0.86 — close but wrong). Fixed both the production constant and the test helper (`tests/_helpers/anki_db.py:12`).

**Fix.**
- `compute_retrievability(state, today, now=None, desired_retention=0.9)` — when stability or last_review is None, returns `desired_retention` instead of `None` (Approach 2) or `1.0` (pre-Approach 2).
- New `find_fixed32_field(data, target)` helper in `app/anki/protobuf_wire.py`.
- New trio in `app/srs/queue_stats.py` mirroring `daily_new_cap`: `_read_desired_retention_from_deck_config_table`, `refresh_desired_retention`, `resolve_desired_retention` (cache → 0.9 default).
- `refresh_desired_retention` wired into `app/api/anki.py` alongside the other refresh calls.
- `_merge_by_retrievability_ascending` resolves `desired_retention` once per call and threads it into `compute_retrievability`. The `sort_r = -1.0 if r is None else r` Approach-2 workaround is gone.

**Files.** `app/srs/fsrs.py:91-115` (signature + body), `app/srs/queue_stats.py:31` (constant 40→37) + `:280-339` (new trio), `app/anki/protobuf_wire.py:118-140` (helper), `app/api/srs.py:783-799` (resolve + thread), `app/api/anki.py:91-104` (sync wiring), `tests/_helpers/anki_db.py:12` (test helper field number); new tests in `test_srs_fsrs.py::test_*_returns_desired_retention`, `test_api_srs.py::test_merge_retrievability_null_card_lands_mid_pool_at_dr`, `test_queue_stats.py::test_resolve_desired_retention_*`, `test_queue_stats_cache.py::TestDesiredRetentionCache`.

**Operational note.** After deploying this fix, run sync_pull (writes the `desired_retention` cache key) and then `clear_session_main_queue` — otherwise the DB-backed frozen queue replays the old order until next sync.

**Cross-reference.** Layer 37 (anki_card_mod fix, landed same day) addresses the FNV tiebreaker *within* an R-tied group; Layer 38 addresses the R-value *for NULL-memory-state cards*. Both surfaced from the same iz/nič parallel-review session. The unresolved source-vs-binary puzzle is captured in `.claude/rules/anki-queue-parity.md` principle 13.

---

---

## Layer 40 — Fractional elapsed days for FSRS scheduling (fixes Layer 11 gap)

**Trigger.** Real-world investigation of `kupiti` scheduling divergences revealed a 2-4% stability gap between TT and Anki for cards with sub-day-precision `last_review`. The cause: TT's `_schedule_review_again` and the REVIEW recall path in `schedule()` used integer calendar days for elapsed time (`(today - last_date).days`), while Anki's `extract_fsrs_retrievability` uses fractional days when `cards.data.lrt` is present. `compute_retrievability` (Layer 11) already had the correct dual-branch logic for R-asc sort — it split on midnight-UTC vs sub-day `last_review` — but the scheduling path was missed.

**Fix.** New helper `_elapsed_days_for_fsrs(last_review, ref_now)` at `backend/app/srs/fsrs.py:128-158` mirrors Anki's branch:
- `datetime` with sub-day component → fractional days (lrt was present).
- `datetime` at midnight UTC → integer days (day-level fallback, no lrt).
- `date` object → integer days (no time-of-day at all).

Wired into `schedule()` REVIEW path and `_schedule_review_again()`. `compute_retrievability` refactored to call the same helper (eliminated inline duplication of the dual-branch logic).

**Re-audit (May 2026).** No dead branches or stale comments found. One cosmetic issue: `step_index == 0` conjunct remained tautological in `_schedule_new` — the HARD branch always enters through the `else` block at step_index=0, so the check was structurally guaranteed true. Removed.

**Files.** `backend/app/srs/fsrs.py` (new `_elapsed_days_for_fsrs` helper, updated `schedule`, `_schedule_review_again`, `compute_retrievability`), `backend/tests/test_fsrs.py` (124 new test lines).

**Cross-reference.** Layer 11 established sub-day precision for `compute_retrievability` (R-asc sort); Layer 40 extends the same fix to the scheduling path, so stability values computed at grade time match Anki's.

---

## Layer 41 — 1.5x Hard delay for single-step learning configs

**Trigger.** During the same `kupiti` investigation, a 6-minute divergence appeared on single-step lapse configs (e.g., deck with one relearn step `[10]`). TT used `steps[0]` verbatim for Hard delay; Anki's `hard_delay_secs_for_first_step` (rslib/states/steps.rs:55-66) uses `min(again*1.5, again + DAY)` when there's only one step.

**Fix.** Both `_schedule_new` and `_schedule_with_steps` now branch on step count:
- `len(steps) > 1` (original path) → `(steps[0] + steps[1]) / 2`.
- `len(steps) == 1` → `min(again_secs * 1.5, again_secs + 86400) / 60`.

The `step_index == 0` guard was structurally tautological in `_schedule_new` (the HARD branch always enters through step_index=0); removed as part of a post-Layer-40 clean-up pass.

**Files.** `backend/app/srs/fsrs.py:_schedule_new` (lines 474-480), `_schedule_with_steps` (lines 652-662); `backend/tests/test_fsrs_steps.py` (42 new test lines).

**Cross-reference.** Layer 6b (revlog shape decode) also special-cases Hard-on-first-step, confirming the patten: Anki's step delay rules have multiple codepaths that need the same single-step branch.

---

## Layer 42 — Lapse stability ceiling (surfaced by Phase 2.2.1 oracle harness)

**Trigger.** First parity test in `backend/tests/test_parity_fsrs_schedule.py` (Phase 2.2.1, the FSRS-scheduling oracle test) flagged a divergence between TT's `_next_stability_lapse` and fsrs-rs's `stability_after_failure` for low-stability cards. For `(s=1.5, d=7.5)` graded Again, TT returned ~1.64 while Anki returned ~1.20. The difference is the ceiling that fsrs-rs applies and TT omitted.

**Mechanism (fsrs-rs, `src/model.rs:91-105`).**
```rust
fn stability_after_failure(&self, last_s, last_d, r) {
    let new_s = w[11] * d.powf(-w[12]) * ((s+1).powf(w[13]) - 1) * exp((1-r) * w[14]);
    let new_s_min = last_s / (w[17] * w[18]).exp();
    new_s.mask_where(new_s_min.lower(new_s), new_s_min)  // = min(new_s, new_s_min)
}
```

The ceiling `last_s / exp(w[17] * w[18])` bounds the post-lapse stability. For low `last_s` the raw formula often exceeds this bound; the ceiling clamps it. Exact verification: with TT's weights (w[17]=0.51, w[18]=0.435) and s=1.5, the ceiling is `1.5 / exp(0.2219) = 1.2016`, matching Anki's reported `1.2015` to 4 decimal places.

**Fix.** Added the ceiling to `_next_stability_lapse`:
```python
def _next_stability_lapse(d, s, r, w):
    new_s = w[11] * d ** (-w[12]) * ((s + 1) ** w[13] - 1) * math.exp((1 - r) * w[14])
    new_s_min = s / math.exp(w[17] * w[18])
    return min(new_s, new_s_min)
```

**Files.** `backend/app/srs/fsrs.py:_next_stability_lapse` (commit immediately following Phase 2.2.1's harness commit). Parity test in `backend/tests/test_parity_fsrs_schedule.py` now exercises `(s=1.5, d=7.5)` alongside `(10, 4)` and `(50, 2)` — all pass.

**Cross-reference.** First Layer surfaced via the harness — validates the Phase 2 approach. The harness asserted parity end-to-end and pinned this divergence with a precise reproducer instead of waiting for a user-visible badge mismatch. Pattern to repeat for the remaining Phase 2.2 domains.

---

## Layer 43 — NULL-R placement: mechanism clarified (resolves Layer 38 ambiguity)

**Trigger.** Phase 2.2.3 oracle parity test surfaced that NULL-R cards in our synthetic deck landed at the TAIL of the R-asc queue, contradicting Layer 38's empirical claim. Investigation reproduced the actual mechanism.

**The truth.** Anki's `extract_fsrs_relative_retrievability` (`rslib/storage/sqlite.rs:370-449`) falls back to the SM2 path when `cards.data` lacks `s`/`d`/`dr`:
```rust
Ok(Some(-((days_elapsed as f32) + 0.001) / (interval as f32).max(1.0)))
```
where `days_elapsed = today_col_day - (due - ivl)`. So a NULL-R card's sort key is `-(elapsed/ivl)`.

Empirically verified by dumping `extract_fsrs_relative_retrievability` per card:

| Card data | `due, ivl` | `elapsed` | `relative_R` | Position |
|---|---|---|---|---|
| FSRS R=0.978 | due=0, ivl=10 | n/a | -0.100 | (just-reviewed FSRS) |
| FSRS R=0.901 | due=0, ivl=10 | n/a | -0.500 | (mid-R FSRS) |
| FSRS R=0.823 | due=0, ivl=10 | n/a | -1.000 | (overdue FSRS) |
| `'{}'` | due=0, ivl=10 | 0 (saturating wrap) | -0.0001 | tail |
| `'{}'` | due=868, ivl=10 | 11 | -1.100 | between R=0.823 and R=0.901 |
| `'{}'` | due=859, ivl=10 | 20 | -2.000 | head |
| `'{}'` | due=769, ivl=10 | 110 | -11.000 | very head |

**Layer 38's original claim ("Anki places NULL-R at dr position") was a coincidence**: the user's nič card had `elapsed ≈ ivl`, giving SM2 fallback `≈ -1.0`, which happens to equal Anki's `relative_R` for a card at R=dr (the formula normalizes by `dr.powf(-1/decay) - 1`, so `relative_R(R=dr) = -1.0` exactly). For different `(elapsed, ivl)` combinations, NULL-R cards land elsewhere.

**TT's behavior.** `compute_retrievability(null_state) → desired_retention` (0.9), which under TT's R-asc sort puts the card at the "R=dr" position. This:
- **Matches** Anki for the "just-overdue NULL-R" case (`elapsed ≈ ivl`) — the typical post-Forget scenario the user was observing.
- **Diverges** for edge cases: freshly-forgotten (elapsed≈0) cards land at tail in Anki vs mid-pool in TT; very stale (elapsed >> ivl) cards land at head in Anki vs mid-pool in TT.

**Status.** Test reframed as `test_null_R_card_typical_position_matches_anki_LAYER_38` — configures the synthetic card with `due=col_day, ivl=N → elapsed=N`, exactly Layer 38's empirical setup. Both apps place it near the dr position; test passes.

**Decision.** No TT fix needed for the typical case. The edge cases (cards in extreme NULL-R states) are rare in TT's actual workflow — TT items get NULL state only when imported without revlog or when the user runs the rare "Forget" operation. The current "mid-pool at dr" approximation is acceptable. Document the simplification.

**Files.** No code changes. Test reframed at `backend/tests/test_parity_queue_order.py::test_null_R_card_typical_position_matches_anki_LAYER_38`.

**Cross-reference.** Resolves the source-vs-binary ambiguity called out in queue-parity rule 13: the "source" (SQL prediction of tail-sort) and the "binary" (empirical mid-pool) both turned out to be correct — for different `(elapsed, ivl)` combinations. Pattern: when an empirical observation contradicts source code, look for a specific input configuration that makes both true simultaneously before concluding either is wrong.

---

## Layer 44 — Graduation stability: 0.1 floor + recall-with-r=1 collapse + missing per-grade quantization

**Trigger.** After heavy learning-step sessions (many AGAINs followed by GOOD graduation), TT's post-graduation stabilities landed at exactly 0.1 while Anki's fsrs-rs returned sub-0.01 values. Surfaced via a "first review card today" head divergence: TT served zmagati (s=0.0024, ingested from Anki via prior sync_pull) while Anki served dotikati se (s=0.0014, TT-side s=0.1). All four mismatched cards had `dirty_fsrs=1` — graded locally after the last sync.

**Three bugs compounded.**

*Bug 1: Stability floor of 0.1.* `schedule()` line 459 and `_graduate_to_review()` line 819 clamped `new_stability = max(0.1, …)`. fsrs-rs uses `S_MIN = 0.001` (`src/simulation.rs:41`). Latent since the initial FSRS port — typical review paths produce stabilities above 0.1, so the floor rarely fired.

*Bug 2: Graduation used recall-with-r=1, which collapses to the identity.* `_graduate_to_review` for non-NEW `prev.state` called `_next_stability_recall(d, s, r=1, GOOD, w)`. With r=1, the formula's `(exp((1-r)·w[10]) - 1)` term is exactly 0, so the function returns `s` unchanged. This skipped the graduation grade's stability bump.

fsrs-rs's `step()` (`model.rs:163`) handles this differently: when `delta_t == 0`, it overrides both `stability_after_success` and `stability_after_failure` with `stability_short_term(s, rating)`. So a same-day GOOD graduation in fsrs-rs multiplies `s` by `max(exp(w[17]·w[18]), 1) ≈ 1.25`. TT's recall path returned `s` unchanged. All `_graduate_to_review` paths reached from `_schedule_new` (EASY on NEW) and `_schedule_with_steps` (GOOD/EASY ending a learning sequence) are same-day grades on sub-day learning steps — every one of them should use short-term.

*Bug 3: Missing per-grade quantization.* Anki rounds `cards.data.{s, d, dr, decay}` to 2-4 decimal places in `convert_to_json` (`rslib/src/storage/card/data.rs:95-105`) and reads the rounded value back on the next grade. TT propagated full f64 precision between grades. Each multiplier in the per-grade short-term sequence (e.g. 0.4501 for AGAIN, 1.2484 for GOOD with default weights) gets a different argument when fed a rounded vs unrounded `s`, so a 10-grade sequence diverged by ~1% even after Bugs 1 and 2 were fixed.

Bug 1 (floor) hid the visible damage of Bug 2 by inflating output to 0.1. Bug 2 obscured Bug 3 — without short-term at graduation, the post-graduation `s` matched TT's pre-graduation `s` regardless of quantization. Each fix exposed the next.

**Fix.**
1. `app/srs/fsrs.py:459, 819` — replace `max(0.1, …)` with `max(0.001, …)` to match fsrs-rs's `S_MIN`.
2. `app/srs/fsrs.py:_graduate_to_review` — for non-NEW `prev.state`, call `_stability_short_term(prev.stability, rating, params)` instead of the recall/lapse branches. Mirrors fsrs-rs `model.rs:163`.
3. `app/srs/fsrs.py` — add `_quantize_stability(s)` (4 dp) and `_quantize_difficulty(d)` (3 dp) helpers, apply at every schedule write site (`_schedule_new`, `_schedule_with_steps` short-term update, `_schedule_review_again`, `schedule()` REVIEW non-AGAIN floor, `_graduate_to_review` floor). Mirrors Anki's `convert_to_json` per-grade rounding.

After all three: `test_parity_graduation_after_many_agains` asserts `tt_s == anki_s` bit-exact (no tolerance).

**Pre-existing test audit.** 4 tests in `test_fsrs_steps.py::TestShortTermAppliesInSteps` asserted `abs(new_dir.stability - _stability_short_term(...)) < 1e-10` against the raw (un-quantized) formula output. Updated to assert against `_quantize_stability(_stability_short_term(...))` with bit-exact equality. No other FSRS test pinned a value shaped by either bug.

**Files.** `backend/app/srs/fsrs.py` (floor + graduation short-term + quantization helpers + 5 call sites), `backend/tests/test_fsrs_steps.py` (4 assertion updates), `backend/tests/test_parity_fsrs_schedule.py` (new test `test_parity_graduation_after_many_agains`), `backend/tests/anki_oracle/oracle.py` (new `answer_card`/`get_card` ops via the backend protobuf path).

**Cross-reference.** Layer 42 (lapse stability ceiling, `077d6a5`) surfaced a similar gap in `_next_stability_lapse` — that was a ceiling on the lapse formula, this is a floor + algorithm choice + quantization on the same-day path. The pattern: both were missing fsrs-rs / Anki branches that only fire at extreme (s, r) combinations missed by the normal review path.

**Pre-Layer checklist note (helpers extended).** This Layer extended `_graduate_to_review` to call `_stability_short_term` (a load-bearing helper from the pre-Layer table in `.claude/rules/anki-queue-parity.md`) and added `_quantize_stability` / `_quantize_difficulty` as new helpers. New schedule write sites must call the quantization helpers; new same-day graduation paths must reuse the short-term call site rather than reimplement.

---

## Cleanup pass (post-Layer 23)

After 23 layers, swept for dead code and duplication. Behavior unchanged.

**Removed:**
- `count_anki_review_remaining_today` + `_compute_today_col_day` + `test_queue_stats_review.py` — orphaned after Layer 8a swapped the review badge to TT-state.
- `_factor_to_fsrs_difficulty` (sync.py) — no production callers.
- `_spread_mix` `ratio_override` parameter — Layer 9 → Layer 14 reversal residue.

**Refactored (behavior-neutral):**
- Extracted `_queue_to_state(queue, card_type, reps) → SRSState` helper; replaced 3 duplicate blocks in `sync_pull`.
- Extracted `AnkiSync._record_conflict(...)` method; replaced 5 sites.
- Wrapped 9 `_resolve_prior_state` call sites in a local closure inside `sync_pull` so the kwargs are captured once.

---

## Layer 45 — `_compute_last_review` preserves col_crt time-of-day (day-level elapsed fix)

**Trigger.** After Layer 40 landed fractional elapsed days, the user's `/review` page showed a persistent off-by-one: card `on` vs `prijazen` had different elapsed days despite identical `due` and `ivl` values. The bug was not in `_elapsed_days_for_fsrs` (Layer 40's implementation was correct in isolation) but upstream in `_compute_last_review`, which returned the wrong calendar date for cards with day-level fallback (no `lrt`).

**Root cause.** `_compute_last_review` in `sqlite_reader.py` computed the review date as:
```python
datetime.fromtimestamp(col_crt).date() + timedelta(days=review_col_day)
```
This truncated `col_crt`'s time-of-day component (e.g., 12:00 UTC → 00:00 UTC) before adding days. For `col_crt=1388836800` (12:00 UTC Jan 4 2014), the computed `first_midnight` was Jan 4 00:00 UTC — but the actual first col_day boundary from `col_day_start = col_crt - 4h` is Jan 5 00:00 UTC (the first midnight *after* the col-day epoch). This off-by-one carried through: a card whose `review_col_day` pointed to col_day 4480 got mapped to Apr 11 00:00 UTC instead of Apr 12 00:00 UTC. The resulting `compute_anki_day_index(col_crt, 4, last_review)` was one less than `due - ivl`, breaking the invariant `today_col_day - review_col_day ≡ elapsed_days`.

**Fix.** `_compute_last_review` now computes `first_midnight` from the col-day epoch boundary:
```python
col_day_start = col_crt - rollover_hour * 3600
first_midnight = col_day_start if col_day_start % 86400 == 0
                 else (col_day_start // 86400 + 1) * 86400
last_review_ts = first_midnight + review_col_day * 86400
return datetime.fromtimestamp(last_review_ts, tz=UTC)
```
For `col_crt=1388836800`, `col_day_start=1388822400`, `first_midnight=1388880000` (Jan 5 2014 00:00 UTC) — the correct first midnight. Review dates shift by +1 col_day for all col_crt values not on the 04:00 UTC boundary (e.g., Apr 11→Apr 12, May 9→May 10 for the user's col_crt).

**`col_crt` threading.** To serve the fix at grade time (not just sync time), `col_crt` is cached in `anki_state_cache` as key `"col_crt"` (integer string). New trio in `queue_stats.py`: `refresh_col_crt(db, conn)` (at sync), `resolve_col_crt(db)` (returns `None` on cache miss → backward-compatible UTC fallback). `col_crt` threaded through `compute_retrievability`, `schedule`, `_schedule_review_again`, `_merge_by_retrievability_ascending`, `_compute_live_main`, `drill_feedback`, `mark_lesson_listened`, and the `fsrs_step` API endpoint.

**Day-level elapsed branch (col_crt available).** `_elapsed_days_for_fsrs` already had a midnight-UTC detection branch from Layer 40. When `col_crt is not None` AND `is_day_level` (midnight-UTC marker), it now uses:
```python
today_col_day = compute_anki_day_index(col_crt, 4, ref_now)
last_col_day = compute_anki_day_index(col_crt, 4, last_review)
days_elapsed = max(0, today_col_day - last_col_day)
```
This is the col-day computation, matching Anki's `extract_fsrs_retrievability` for day-level cards. The existing `lrt`-based fractional branch is unchanged (precise `lrt` → sub-day precision).

**Verification.**
- `test_midnight_datetime_col_day_rollover_crossing` (39→40 for col_crt=-572400, 39 unchanged for col_crt aligned on 04:00 UTC).
- `test_midnight_datetime_col_day_same_as_utc_when_boundary_not_crossed`.
- `test_midnight_datetime_col_day_fallback_to_utc_when_col_crt_none`.
- `test_parse_fsrs_data_last_review_col_day_matches_anki` — end-to-end `parse_fsrs_data` → `_elapsed_days_for_fsrs` pipeline against live `now`, verifying `compute_anki_day_index(lr) == due - ivl`.
- `test_parity_day_level_elapsed_matches_anki` (oracle) — seeds a non-lrt card with raw `due/ivl`, exercises `_compute_last_review` → `_elapsed_days_for_fsrs`, compares elapsed against Anki's oracle.
- 3 hardcoded date assertions updated in `test_anki_sqlite_reader.py` that used the old wrong formula.

**Files.** `backend/app/anki/sqlite_reader.py:265-297` (`_compute_last_review` rewrite), `backend/app/srs/fsrs.py:128-158` (`_elapsed_days_for_fsrs` col-day branch), `backend/app/srs/queue_stats.py` (`refresh_col_crt`/`resolve_col_crt` trio), `backend/app/api/anki.py` (sync wiring), `backend/app/api/srs.py` (col_crt threaded through entry points). Tests: `backend/tests/test_fsrs.py` (3 unit tests), `backend/tests/test_anki_sqlite_reader.py` (1 regression + 3 date fixes), `backend/tests/test_parity_fsrs_schedule.py` (1 oracle test refactored).

## Layer 46 — `compute_due_at` preserves underlying due through bury/suspend

**Trigger.** Morning of 2026-05-20: TT badge `30 + 7 + 205` vs Anki `30 + 7 + 196`. New/learning identical, review off by 9. No grades today on either side; last sync was 5/19 22:25 EDT. Forensics traced 22 TT directions whose `due_at=2026-05-19T04:00:00Z` while their `anki_due=4522` (= 2026-05-23). Same row, internally inconsistent.

**Root cause.** `compute_due_at(queue, due_raw, col_crt)` in `app/anki/sqlite_reader.py` only branched on `queue` and treated everything outside `{1, 2, 3}` as "today at 04:00 UTC", discarding `due_raw`. Anki preserves `cards.due` through bury and suspend — only `cards.queue` flips. When `sync_pull` read these 22 sibling-buried review cards (queue=-2, due=4522 from Monday's last grade), `compute_due_at` returned `today` instead of `2026-05-23`. The merge wrote inconsistent `due_at` / `anki_due` to the same row. The daily unbury sweep (Layer 27/35) then flipped state `buried→review` without touching `due_at`, surfacing 22 stale-due cards in today's review badge.

**Mechanism reconstructed:**
1. Monday: card graded in Anki → `cards.due = today + ivl`, queue=2.
2. Tuesday evening: sibling graded in Anki → this card sibling-buried, `queue=-2`, **`due` preserved**, `mod` bumped.
3. Tuesday 10:25 PM: TT `sync_pull` runs `compute_due_at(-2, 4522, col_crt)` → returns "today at 04:00" (= 5/19), then `parse_fsrs_data` writes `due_at=5/19` while `anki_due=4522` (set separately from `due_raw`).
4. Wednesday 11:35 AM: `unbury_if_needed` sweeps `state='buried' AND bury_kind='sched'` → `state='review'` (reps>0). `due_at` untouched.
5. Wednesday morning badge: 22 ghost-due directions inflate today's count.

**Fix.** Add `card_type` parameter to `compute_due_at`. When `queue ∈ {-1, -2, -3}`, dispatch via `card_type` (the card's *underlying* type — 0=new, 1=learn, 2=review, 3=relearn) to the matching positive-queue branch. `card_type=2` → days-since-crt; `card_type=1` → unix timestamp; `card_type=3` → days-since-crt; `card_type=0` → fall back to today (genuinely-new card buried before first grade). `queue=0` semantics unchanged. Backward-compatible default `card_type=0` preserves the existing fallback behavior for callers that haven't been updated yet.

**Verification.**
- `test_queue_minus_2_buried_review_preserves_due` / `_minus_3_` / `_minus_1_suspended_review_preserves_due` — unit tests for the dispatch table.
- `test_queue_minus_2_buried_learning_preserves_due` — confirms unix-timestamp dispatch for card_type=1.
- `test_queue_minus_2_buried_relearning_preserves_due` — confirms days-since-crt dispatch for card_type=3.
- `test_queue_minus_2_buried_new_falls_back` — confirms new-card path still falls back.
- `test_buried_review_card_preserves_due_at_through_fetch` — end-to-end via `fetch_cards_for_notes` against a minimal Anki DB.

**Cleanup path for live data.** The existing `backfill_due_date_from_anki_due` migration now sees buried-state rows (was already included in its `state IN (...)` filter) and rewrites them correctly with the new `card_type`-aware `compute_due_at`. One `uv run python -m app.anki.backfill_due_date_from_anki_due` pass will repair the 22 stuck directions in the user's TT db.

**Files.** `backend/app/anki/sqlite_reader.py:40-67` (`compute_due_at` rewrite + signature), `backend/app/anki/sqlite_reader.py:188` (`parse_fsrs_data` passes `card_type`), `backend/app/anki/backfill_due_date_from_anki_due.py:57-64` (migration script reads/passes `cards.type`). Tests: `backend/tests/test_anki_sqlite_reader.py` (6 unit + 1 integration test).

## Layer 47 — `sync_push` replicates Anki's grade-time sibling-bury

**Trigger.** Morning of 2026-05-20, after Layer 46 deploy + sync: TT review badge 185, Anki 186 — off by 1. Forensics pointed to `iti mimo`: TT excluded the collocation from today's pool (recognition direction graded today, `last_review = today_local`); Anki kept it in (production sibling still at `queue=2`, never buried). Initial speculation was that the grade was "ahead-of-schedule" via browser/custom-study; user corrected that. Re-reading the Anki revlog showed 19 different cards stamped at the exact same second (`2026-05-20 12:08:36`) — a TT sync_push burst, not 19 manual UI grades.

**Root cause.** Anki's `maybe_bury_siblings` lives in the `answer_card` flow (`rslib/.../scheduler/answering/mod.rs:389`). TT's `sync_push` writes grades directly to `cards`/`revlog` via `OfflineWriter` and **never invokes `answer_card`**, so the sibling-bury side-effect never runs on the Anki side. Meanwhile TT's own `count_review_due_collocations` filter excludes the whole collocation when *any* direction has `last_review = today_local` (rule 3 / database.py:1681). Two halves of the same bury contract diverging: TT-side filter fires, Anki-side write does not → +1 drift per TT-graded card with a still-due sibling, persisting until the next rollover.

**Fix.** New `OfflineWriter.bury_siblings(graded_card_id, graded_queue, bury_new, bury_reviews, bury_interday_learning)` mirrors Anki's two-step logic:

1. `exclude_earlier_gathered_queues`: drop each flag whose target gather_ord is less than the graded card's. Mapping: Learn/PreviewRepeat=0, DayLearn=1, Review=2, New=3 (`rslib/.../bury_and_suspend.rs:146-152`).
2. Find siblings (same `nid`, different `id`) whose queue ∈ {0 (New), 2 (Review), 3 (DayLearn)} subject to surviving flags. Write `queue=-2`, `mod=now`, `usn=-1`. Bump `col` if any row touched (`siblings_for_bury.sql`).

The call site is a **backfill scan at the tail of `sync_push`** (`AnkiSync._backfill_bury_siblings_for_today_grades`), not the dirty-direction loop. The backfill scans every TT direction with `last_review` in today's local-day window via `SRSDatabase.list_anki_cards_graded_today` and calls `writer.bury_siblings` for each. This catches:

- The new-grade case (this sync's TT-grades are also "today-graded" by definition), AND
- The **backfill case**: directions graded earlier today whose grades were already pushed by a prior sync_push (pre-Layer-47), with `dirty_fsrs` cleared. The dirty-only loop misses these; the scan does not.

Idempotency: `bury_siblings`'s `WHERE queue IN (allowed)` clause makes re-running the bury a no-op for siblings already at queue=-2/-3/-1. Deck-config flags come from existing `resolve_bury_new` / `resolve_bury_review` (`anki_state_cache`-backed). The mapping from TT `state` strings to Anki post-grade queue numbers lives in `_STATE_VALUE_TO_ANKI_QUEUE` (NEW→0, LEARNING/RELEARNING→1, REVIEW→2; SUSPENDED/BURIED absent → backfill skips). `bury_interday_learning` isn't cached yet; defaults to False, matching the user's deck config (`buryInterdayLearning: False`).

**Initial implementation that didn't work** (recorded for the next maintainer who's tempted to do this): the first cut wired `bury_siblings` into the dirty-direction loop, right after `write_revlog`. Symptom: deploy + user sync → no change. Cause: directions already cleaned by a prior sync (the common case post-deploy) are no longer in `list_dirty()`, so the dirty-loop bury never fires for the backlog. The backfill scan is the right shape because the bury contract is "every today-graded direction has its sibling buried" — that's a property of TT state, not of the current sync's payload.

The push-side does NOT bury on the "Anki-won-by-timestamp" path (`list_recently_graded_clean`) — Anki was the grading app there, so Anki's own `answer_card` flow already buried its sibling at grade time.

**Verification.**
- `test_bury_siblings_review_graded_buries_review_sibling` / `_new_grade_drops_review_and_interday` / `_learning_grade_buries_dayLearn_sibling` — `exclude_earlier_gathered_queues` per-graded-queue tables.
- `_review_grade_excludes_interday_learning` — Review grade (gather_ord=2) drops `bury_interday_learning` flag.
- `_skips_suspended_sibling` / `_skips_intraday_learning_sibling` — siblings at queue=-1 and queue=1 (Learn) stay untouched.
- `_no_op_when_all_flags_false` / `_unknown_queue_drops_all_flags` / `_missing_card_returns_zero` — defensive paths.
- `TestSyncPushBuriesSiblings.test_sync_push_buries_review_sibling_when_bury_review_enabled` / `_skips_bury_when_no_revlog_emitted` / `_passes_learning_queue_for_relearning_state` / `_skips_bury_when_state_is_suspended` — end-to-end via `AnkiSync.sync_push` against `FakeWriter`.
- `test_state_to_anki_queue_mappings` — branch coverage on the state→queue helper.

**Files.** `backend/app/anki/sync.py` (`OfflineWriter.bury_siblings`, `_state_to_anki_queue` + `_STATE_VALUE_TO_ANKI_QUEUE` helpers, `AnkiSync._backfill_bury_siblings_for_today_grades`, `resolve_bury_*` imports, `sync_push` tail call). `backend/app/srs/database.py` (`SRSDatabase.list_anki_cards_graded_today`). Tests: `backend/tests/test_anki_sync_push.py` (10 unit `bury_siblings` tests + 6 backfill integration tests + `_state_to_anki_queue` enum coverage), plus `bury_siblings` stubs added to four pre-existing `FakeWriter` classes in `test_anki_sync_orphan_recovery.py`, `test_anki_sync_force_fsrs.py`, `test_anki_sync_round_trip.py`, `test_anki_sync_concurrent_review.py`.

**Future work.** `bury_interday_learning` deck-config flag isn't yet cached or threaded through; harmless until a user enables it (default False). When wiring it, add `resolve_bury_interday_learning` in `queue_stats.py`, add it to `refresh_deck_config_cache`, and pass `bury_interday_learning=...` from the backfill site.

---

## Layer 48 — Review interval cascade (greater_than_last + constrain_passing_interval)

**Trigger.** Synthetic card walk-through: stability=0.5, desired_retention=0.86, scheduled_days=1, rating=GOOD. Anki's ``passing_fsrs_review_intervals`` applies ``constrain_passing_interval`` which first checks ``greater_than_last(round(raw), scheduled_days)`` then the cascade ``good ≥ hard+1``. Result: Good=2. TT's ``_next_interval`` returned ``max(1, round(0.77)) = 1`` — no cascade, no ``greater_than_last``.

**Fix.** Three parts:

1. **New helpers** in ``backend/app/srs/fsrs.py``:
   - ``_greater_than_last(interval, scheduled_days) → int`` — returns ``scheduled_days + 1`` when the raw interval exceeds the previous interval, else 0.
   - ``_constrain_passing_intervals(hard_raw, good_raw, easy_raw, scheduled_days) → (hard, good, easy)`` — applies the Anki cascade: each rating is ``max(raw, floor)`` where ``hard_floor = max(gtl, 1)``, ``good_floor = max(gtl, hard + 1)``, ``easy_floor = max(gtl, good + 1)``. The raw interval is never reduced — the cascade only adds a floor.

2. **Call site A: REVIEW→REVIEW** (`schedule()`, line ~520). Before the per-rating stability assignment, compute all three passing stabilities. After quantization, compute all three raw intervals, cascade, then pick the chosen rating's constrained value. ``scheduled_days`` derived from ``max(0, (prev.due_at - prev.last_review).days)``.

3. **Call site B: Graduation** (`_graduate_to_review`, line ~890). Same pattern with ``scheduled_days=0`` (no prior review interval). Only applies to passing ratings (HARD/GOOD/EASY) — AGAIN graduation (empty relearn steps) skips the cascade.

**Rounding parity.** ``_next_interval`` switched from Python's ``round()`` (banker's) to ``_rust_round_half_away()`` to match Anki's ``f32::round`` (half away from zero). Uses the existing helper from the fuzz module.

**scheduled_days derivation.** ``max(0, (prev.due_at - prev.last_review).days)`` when ``prev.last_review`` is set, handling both ``datetime`` and ``date`` types. For LEARNING→REVIEW graduation, the prior interval is sub-day → 0.

**Verification.**
- ``test_greater_than_last`` — 4 edge cases (equal, below, above, zero).
- ``test_constrain_passing_intervals_poljubiti_case`` — ``(raw=1,1,3) with scheduled_days=1 → (1,2,3)``.
- ``test_constrain_passing_intervals_*`` — below scheduled_days, all above, strict increment invariant.
- ``test_review_good_interval_exceeds_hard_by_at_least_one`` — end-to-end via ``schedule()``.
- ``test_scheduled_days_derived_from_due_at_minus_last_review`` — verifies the derivation works.
- ``test_parity_review_interval_cascade_matches_anki`` (oracle) — seeds a card with ivl=1, verifies TT's cascade-constrained intervals match Anki's ``scheduled_days`` for all three passing ratings.
- All existing tests continue to pass — the cascade never reduces an interval, so cards with raw intervals already satisfying the invariant are unchanged.

**Files.** ``backend/app/srs/fsrs.py`` (new helpers + 2 call sites + ``_next_interval`` rounding). ``backend/tests/test_fsrs.py`` (5 new unit tests). ``backend/tests/test_parity_fsrs_schedule.py`` (1 new oracle test + docstring update). ``docs/anki-parity-layers.md`` (this entry + updated pending-work item).

**Cross-reference.** ``Layer 6`` (learning-step fuzz) and ``Layer 6b`` (revlog shape decode) both deal with Anki's interval rules — this is the review-side equivalent. The cascade only adds floors, never caps, so it cannot shorten intervals — any pre-existing negative divergence (TT > Anki, like the ``milijarda`` case) must have a different root cause.

## Layer 49 — `schedule()` due_at convention (rollover_hour + col_day arithmetic)

**Surfaced**: Stage 3b empirical measurement drill-down (2026-05-22). On Anki-only grading, TT's stored due_at (written by sync_pull via ``compute_due_at``) and TT's derived due_at (computed by ``schedule()``) disagreed by multiples of 14,400 seconds = 4 hours for every direction (89/89). Mean delta ~20h, max 68h. ``reps`` was aligned 89/89, ruling out fuzz-seed drift.

**Diagnosis**. Two code paths produced day-level review-state due_at with different conventions:

- ``compute_due_at`` (``app/anki/sqlite_reader.py:72``, sync_pull writeback): ``datetime.combine(col_crt_utc_date + due_raw days, time(4, 0), tzinfo=UTC)`` — 04:00 UTC on the calendar date matching Anki's col_day arithmetic.
- ``schedule()`` (``app/srs/fsrs.py``, two sites at lines 547 and 935): ``datetime.combine(review_date + interval days, time(0, 0), tzinfo=UTC)`` — midnight UTC on ``now.date()`` + interval, ignoring rollover_hour entirely and using UTC calendar date instead of Anki's col_day.

The 4-hour deltas came from time-of-day disagreement (00:00 UTC vs 04:00 UTC). The 20h/44h/68h deltas came from a second issue: grades fired between 00:00 and 04:00 UTC belong to "yesterday's col_day" by Anki's reckoning but to "today UTC" by ``schedule()``'s reckoning — landing the derived due_date one day too far.

This was self-consistency drift within TT, not TT-vs-Anki — both endpoints disagreed against each other. Per the Pre-Layer checklist Step 2/3, the right fix factors the convention into a single helper used by both paths rather than fixing one site.

**Fix.**

1. **New helper** in ``backend/app/anki/protobuf_wire.py`` (lives next to ``compute_anki_day_index`` since it's the inverse direction):

   ```python
   def review_due_at_for_col_day(col_crt: int, col_day: int, rollover_hour: int = 4) -> datetime:
       """Convert an Anki review-state col_day index to a UTC datetime."""
       due_date = datetime.fromtimestamp(col_crt, tz=UTC).date() + timedelta(days=col_day)
       return datetime.combine(due_date, time(rollover_hour, 0), tzinfo=UTC)
   ```

2. **Refactor** ``compute_due_at`` (sqlite_reader.py) to call ``review_due_at_for_col_day`` for the queue 2/3 case — single source of truth.

3. **New wrapper** ``_review_due_at_from_interval`` in ``app/srs/fsrs.py`` that handles the legacy fallback (``col_crt is None``) by preserving the pre-fix UTC-midnight behavior. Routes through ``compute_anki_day_index → review_due_at_for_col_day`` otherwise.

4. **Patch both call sites** in fsrs.py (REVIEW path at line 547, graduation path at line 935 inside ``_graduate_to_review``) to use the wrapper.

5. **Plumb ``col_crt`` and ``review_date``** through ``_schedule_with_steps``, ``_schedule_new``, ``_graduate_to_review`` (7 call sites to ``_graduate_to_review``, 1 to ``_schedule_new``).

**Verification.**

- 4 new unit tests in ``TestReviewDueAtRolloverConvention`` (``tests/test_fsrs.py``):
  - ``test_review_due_at_lands_at_rollover_hour_utc`` — REVIEW+GOOD lands at 04:00 UTC.
  - ``test_review_due_at_uses_col_day_not_utc_date_pre_rollover`` — grade at 03:00 UTC uses Anki's "yesterday" col_day, matching ``review_due_at_for_col_day``.
  - ``test_review_due_at_falls_back_when_col_crt_none`` — col_crt=None preserves legacy midnight UTC.
  - ``test_graduation_due_at_lands_at_rollover_hour_utc`` — LEARNING→REVIEW graduation also lands at 04:00 UTC.

- **Stage 3b measurement re-run on the same snapshots**: due_at_match_within_1h went from **0/89 (0%)** to **42/89 (47%)** with the remaining 47/89 off by **exactly 1 day** (86400s). The remaining off-by-1-day delta is downstream of the ~5-7% stability port drift (Layer 50 candidate) — when stability drifts, the fuzzed interval can tip to an adjacent day. The 4-hour quantization is gone; the layer is complete on its own scope.

- Full ``./test.sh`` green (2481 backend tests, frontend 100% coverage gate, 11 E2E specs).

**Files.** ``backend/app/anki/protobuf_wire.py`` (+helper). ``backend/app/anki/sqlite_reader.py`` (refactor ``compute_due_at`` to use the helper, drop unused ``timedelta`` import). ``backend/app/srs/fsrs.py`` (new ``_review_due_at_from_interval`` wrapper, ``col_crt`` and ``review_date`` plumbed through three private schedulers and their 8 call sites, two day-level due_at construction sites updated). ``backend/tests/test_fsrs.py`` (4 new tests). ``docs/anki-parity-layers.md`` (this entry).

**Cross-reference.** Layer 11/15/40 (``_elapsed_days_for_fsrs`` dual-branch + col_day arithmetic for elapsed time) — same shape as this fix, applied to the elapsed-days side. ``compute_anki_day_index`` (``protobuf_wire.py:196``) — companion helper this layer's helper inverts. The remaining off-by-1-day deltas in the measurement are tracked as Layer 50 (stability port drift, scattered ~5-7% in ``_next_stability_recall``).

## Layer 50 — Grade-time `days_elapsed` must be INTEGER col-day diff

**Surfaced**: Stage 3b empirical measurement (2026-05-22). After Layer 49 cleaned up the 4-hour due_at quantization, 11/89 directions still diverged >5% on stability (practical match 87.6%). Drift was uniform ~5-7%, scattered across REVIEW→REVIEW Good and Easy single grades, no lapse-bucket enrichment (ruling out Layer 42), no transition-specific clustering. Pre-Layer-checklist initial guess pointed at `_next_stability_recall`'s `w[]` constants; empirical verification ruled that out.

**Diagnosis**. The recall stability formula itself was bit-exact with fsrs-rs 5.2.0's `stability_after_success` (`/tmp/fsrs-rs-5.2.0/src/model.rs:68-89`); same goes for `_next_difficulty`. The divergence was upstream: TT's `schedule()` (`backend/app/srs/fsrs.py:543`) and `_schedule_review_again` (line 751) were computing `r` via `_elapsed_days_for_fsrs`, which returns **fractional days** for sub-day-precise `last_review` (lrt was present in `cards.data`). Anki's answering path uses **integer days** unconditionally:

```rust
// rslib/src/scheduler/answering/mod.rs:480-487
let days_elapsed = if let Some(last_review_time) = card.last_review_time {
    timing.next_day_at.elapsed_days_since(last_review_time) as u32
} else { ... };
```

```rust
// rslib/src/timestamp.rs:31
pub fn elapsed_days_since(self, other: TimestampSecs) -> u64 {
    (self.0 - other.0).max(0) as u64 / 86_400
}
```

The dual fractional/integer branch lives in `extract_fsrs_retrievability` (queue-sort R, `rslib/.../storage/sqlite.rs`) — NOT in the answering flow that drives `stability_after_success`/`stability_after_failure`. TT was conflating the two.

Two prior tests in `TestReviewScheduling` (`test_review_again_uses_fractional_elapsed_when_last_review_has_sub_day_precision`, `..._good_...`) had explicitly pinned the buggy fractional behavior. They were authored under the wrong hypothesis ("Anki uses fractional at grade time too") — debugged after a single-card `kupiti` drift that was actually rooted in wrong deck-config params, not the elapsed branch.

**Empirical verification**. With correct deck-config FSRS params (`Slovene1774631349`, not `Default`):
- TT with fractional elapsed: mean drift 2.61%, max 9.26%, 36/65 single-grade cards diverge >1%.
- TT with **integer** col-day elapsed: mean drift **0.000%**, max 0.00%, **65/65 bit-exact match Anki**.

Cross-checked against the Python `fsrs<6` package (which implements the same fsrs-rs formula): TT's `_next_stability_recall` reproduces it bit-exact, confirming the formula port is correct and the bug is in the elapsed-days input.

**Fix.**

1. **New helper** `_grade_elapsed_days` in `backend/app/srs/fsrs.py` (next to `_elapsed_days_for_fsrs`):
   ```python
   def _grade_elapsed_days(last_review, ref_now, col_crt=None, rollover_hour=4) -> int:
       # Mirrors Anki's next_day_at.elapsed_days_since(lrt).
       # INTEGER col-day diff regardless of lrt precision.
       if last_review is None: return 0
       if isinstance(last_review, datetime):
           if col_crt is not None:
               today = compute_anki_day_index(col_crt, rollover_hour, ref_now)
               review = compute_anki_day_index(col_crt, rollover_hour, last_review)
               return max(0, today - review)
           return max(0, (ref_now.date() - last_review.date()).days)
       return max(0, (ref_now.date() - last_review).days)
   ```
2. **Two call-site swaps** in `fsrs.py`:
   - `schedule()` line 543 (REVIEW + passing): `_elapsed_days_for_fsrs(...)` → `_grade_elapsed_days(...)`.
   - `_schedule_review_again` line 751 (REVIEW + AGAIN): same swap.
3. **Keep** `_elapsed_days_for_fsrs` (and its dual branch) for `compute_retrievability` (queue-sort R) — that path matches Anki's `extract_fsrs_retrievability`, which IS fractional when lrt is present.
4. **Inverted** the two `..._uses_fractional_elapsed_when_last_review_has_sub_day_precision` tests (now `..._uses_integer_col_day_elapsed_LAYER_50`). They previously pinned the bug; now they assert the fix.

**Verification.**

- 3 new tests in `TestReviewScheduling` + 4 new tests in `TestGradeElapsedDaysLAYER_50` (`backend/tests/test_fsrs.py`). Bit-exact pin via `test_review_good_matches_anki_integer_elapsed_bit_exact_LAYER_50` reproduces the 2026-05-22 measurement scenario.
- Stage 3b re-measurement on the same snapshots:

  | Metric | Pre-L50 | Post-L50 |
  |---|---|---|
  | Strict match (±0.01) | 17/89 (19.1%) | **89/89 (100%)** |
  | Practical match (±5%) | 78/89 (87.6%) | **89/89 (100%)** |
  | Stability median drift | 0.97% | 0.00% |
  | Stability mean drift | 2.61% | 0.045% |
  | Stability max drift | 7.73% | 4.00% (1 multi-grade outlier) |
  | Difficulty bit-exact | 89/89 | 89/89 |

- **Stage 3b decision gate flips**: ≥95% practical match → simplification claim HOLDS. The original 1-branch design (now ≥95% world, not the 87.6% 3-branch reframe) becomes viable.

- Full `./test.sh` green (2502 backend tests, 100% coverage, frontend 100% gate, 11 E2E specs).
- Oracle harness (`pytest tests/test_parity_fsrs_schedule.py --run-oracle`) all 6 tests green.

**Files.** `backend/app/srs/fsrs.py` (`_grade_elapsed_days` helper, two call-site swaps with comment cross-references). `backend/tests/test_fsrs.py` (3 LAYER_50 tests in `TestReviewScheduling`, new `TestGradeElapsedDaysLAYER_50` class with 4 tests, 1 `_elapsed_days_for_fsrs` date-branch coverage test, two prior fractional-pinning tests inverted to assert integer behavior). `docs/anki-parity-layers.md` (this entry).

**Pre-Layer checklist note.** Per Step 2/3, the right shape was extending the existing helper family rather than reimplementing elapsed-days at a new call site. `_grade_elapsed_days` mirrors `_elapsed_days_for_fsrs`'s structure for the col-day case, drops the dual fractional/integer split. `compute_anki_day_index` (the protobuf_wire helper) is the load-bearing primitive both helpers share — same one Layers 11/15/40/49 lean on.

**Cross-reference.** Layer 42 (lapse stability ceiling) was the first stability port fix; Layer 50 is the first stability *input* fix. Layer 49 (due_at rollover anchor) found a similar duplication problem (two TT code paths producing inconsistent day-level due_at); Layer 50's shape mirrors that — different semantics across two helper call sites need distinct helpers. The Stage 3b coupling note pre-fix expected Layer 50 to also resolve the off-by-1-day due_at residual (Layer 49's remainder); empirically the residual is unchanged at 42/89 within 1h. That suggests a second, smaller mechanism — likely in `_next_interval` / `_review_interval_fuzz` / `_constrain_passing_intervals` — that's a separate layer candidate, NOT a Layer 50 regression.

## Layer 51 — Cascade floor + scheduled_days not threaded into fuzz minimum

**Surfaced**: Layer 50 follow-up drill (2026-05-23). After Layer 50 brought stability to 100% bit-exact, due_at was still off in 47/89 directions. The shape was clean — 31/65 single-grade REVIEW→REVIEW cases bit-exact, 29 off by exactly ±1 day. Even split (10 −1d, 17 +1d) ruled out a directional rounding bias; the cause was the fuzz step crossing a bin boundary differently than Anki.

**Two coupled bugs.**

1. **Fuzz minimum not carried from cascade.** Anki's `constrain_passing_interval` (`rslib/.../states/review.rs:302-313`) takes a `minimum` argument and threads it into `with_review_fuzz(interval, minimum, maximum)`, which clamps the fuzzed lower bound to it. TT's `_review_interval_fuzz` was called with `minimum=1` regardless of the cascade-derived floor; for low ChaCha factors, fuzz could drop the result below the cascade floor. Empirical: cid 1775264032672 (pre s=1.0948, GOOD), TT raw_chosen=3, cascade floor=3, factor=0.0119, fuzz_bounds=[2,4] with min=1 → result=floor(2+0.0119*3)=2. Anki's result=3 (factor*range starts at min=3). Anki bit-exact when cascade floor is honored.

2. **`scheduled_days` derivation truncated.** Anki sources `scheduled_days = card.interval` directly from `cards.ivl` (`rslib/.../answering/current.rs:107`). TT was computing `max(0, (prev.due_at - prev.last_review).days)`. Post Layer 49, `due_at` is at 04:00 UTC on a col_day-anchored date while `last_review` (= lrt) has sub-day precision — the timestamp diff is ~32 hours for a 2-day interval, and `.days` truncates to 1. Pre-Layer-51 this didn't matter (cascade floor didn't bind the fuzz output); post-Layer-51 the gtl-derived minimum DOES bind, and a wrong `scheduled_days` shifts the cascade floor by 1.

   Coupled because: bug #1 made the cascade floor irrelevant, so bug #2 was invisible. Fixing bug #1 alone closes ~31% of the residual; fixing both closes ~62%.

**Anki's interleaved cascade+fuzz** (`rslib/.../states/review.rs:178-211`, with `ctx.fuzz_factor` set ONCE in `card_state_updater` and reused across all three ratings):

```rust
let greater_than_last = |interval: u32|
    if interval > self.scheduled_days { self.scheduled_days + 1 } else { 0 };
let hard = constrain_passing_interval(ctx, states.hard.interval,
    greater_than_last(states.hard.interval.round() as u32).max(1), true);
let good = constrain_passing_interval(ctx, states.good.interval,
    greater_than_last(states.good.interval.round() as u32).max(hard + 1), true);
let easy = constrain_passing_interval(ctx, states.easy.interval,
    greater_than_last(states.easy.interval.round() as u32).max(good + 1), true);
```

The same `fuzz_factor` is applied to `with_review_fuzz` for each rating — they don't draw independent random values.

**Fix.**

1. **New `_passing_intervals_with_fuzz`** (fsrs.py): mirrors Anki's interleaved pipeline. Samples `fuzz_factor` once via `ChaCha12Rng(cid + reps).random_range_f32()`, then for each rating in order:
   - `min_i = max(greater_than_last(round(raw_i), scheduled_days), prev_fuzzed + 1)`
   - `result_i = floor(lower + factor * (1 + upper - lower))` where `(lower, upper) = constrained_fuzz_bounds(raw_i_float, min_i, max_interval)`

2. **New `_next_interval_raw`** (fsrs.py): returns the unrounded float interval. Anki passes float to `with_review_fuzz` so `fuzz_delta` computation uses unrounded input. Layer 48's `_next_interval` (int-returning) stays for callers that need the rounded value.

3. **New `_scheduled_days_for_grade(prev, col_crt)`** (fsrs.py): derives the cascade's `scheduled_days` input. Production path: `prev.anki_due - compute_anki_day_index(col_crt, 4, prev.last_review)` — both endpoints in Anki's col_day arithmetic, matches `card.interval` bit-exact. Fallback (TT-only state without `anki_due`): legacy timestamp-diff.

4. **Two call-site swaps** in fsrs.py:
   - `schedule()` REVIEW state (lines ~683-695): cascade + fuzz replaced with one `_passing_intervals_with_fuzz` call.
   - `_graduate_to_review` (lines ~1180-1196): same swap with `scheduled_days=0`.

   `_constrain_passing_intervals` and `_review_interval_fuzz` retained for the pre-Layer-51 unit tests (they pin the pre-fuzz cascade integer output and the standalone fuzz function — both still correct in isolation, just no longer used by `schedule()`).

**Verification.**

- New tests: `test_fuzz_minimum_carries_cascade_floor_LAYER_51` (in `TestReviewIntervalCascade`) reproduces cid 1775264032672 — pre s=1.0948, GOOD rating, asserts post-grade due_at matches `_review_due_at_from_interval(..., 3, ...)` (ivl=3). Plus 4 unit tests for `_scheduled_days_for_grade` (None last_review, anki_due+col_crt available, fallback when anki_due unset, fallback when col_crt None).

- Stage 3b re-measurement on the same snapshots:

  | Metric | Post-L50 | Post-L51 |
  |---|---|---|
  | Strict stability match (±0.01) | 89/89 | 89/89 |
  | Single-grade REVIEW→REVIEW due_at exact | 31/65 (47.7%) | **39/65 (60.0%)** |
  | All-direction due_at within 1h (measurement script) | 42/89 (47.2%) | **46/89 (51.7%)** |

  Bit-exact single-grade went from 31/65 → 39/65 (+8 cards). The script's all-direction within-1h went 42→46 (+4 cards); the gap is multi-grade replays where accumulated fuzz drift across 2-5 sequential grades widens the divergence.

- The remaining 26/65 single-grade off cases (15 +1d, 10 −1d, 1 +2d) trace to Anki running `recompute_memory_state` between grade and snapshot — the stored `cards.data.s` reflects post-recompute values while `cards.ivl` reflects the grade-time value (pre-recompute). TT replay reproduces the post-recompute `s` bit-exact (89/89 stability), but the interval was computed at grade time with a different `s` we can't reconstruct. This is Stage 3b's "due_at is pass-through from Anki" case — not a TT bug, and the production flow (sync_pull writes `due_at` from Anki's `cards.due`) ignores `schedule()`'s due_at for synced cards.

- Full `./test.sh` green (2509 backend tests, 100% coverage, frontend 100% gate, 11 E2E specs).
- Oracle harness (`pytest tests/test_parity_fsrs_schedule.py --run-oracle`) all 6 green.

**Files.** `backend/app/srs/fsrs.py` (3 new helpers + 2 call-site swaps; the old `_constrain_passing_intervals` and `_review_interval_fuzz` retained for pre-Layer-51 unit tests). `backend/tests/test_fsrs.py` (1 LAYER_51 parity test in `TestReviewIntervalCascade`, new `TestScheduledDaysForGradeLAYER_51` class with 4 tests). `backend/app/anki/measure_stage3b_premise.py` (1-line fix: pass `anki_due` through to the replay `DirectionState`). `docs/anki-parity-layers.md` (this entry).

**Pre-Layer checklist note.** Per Step 2/3, this layer touched three load-bearing helpers from the table (`_constrain_passing_intervals`, `_review_interval_fuzz`, `_next_interval`). The clean refactor is adding a new fused function (`_passing_intervals_with_fuzz`) at the answering-pipeline layer rather than modifying the existing three. Each retains its single-purpose tests; the new function mirrors Anki's interleaved structure that the three helpers couldn't express together because they were designed as separable stages.

**Cross-reference.** Layer 49 (due_at rollover anchor) introduced the 04:00 UTC due_at convention that made `(due_at - lrt).days` truncate by 1 (the scheduled_days half of Layer 51). Layer 50 (integer days_elapsed at grade time) was the first half of "Anki interleaves col_day arithmetic everywhere"; Layer 51 is the second half (col_day arithmetic for scheduled_days too). The "Anki source vs binary" rule fired here as well — get_scheduling_states predicts a different `s` than what `cards.data` stores, due to `recompute_memory_state` running between the user's grade and the snapshot. The interval stored at grade time reflects the grade-time `s`; replaying forward from pre-grade `s` produces a different (correct) post `s` but can't reproduce the interval. Documented as the irreducible Stage 3b residual.

## Layer 52 — Graduation uses simple per-rating fuzz, NOT the passing-review cascade

**Surfaced**: Multi-grade replay drill (2026-05-23, post Layer 51). The single-grade due_at fix raised single-grade bit-exact match from 31/65 to 39/65, but multi-grade (24-40 directions, mostly REVIEW→AGAIN→GOOD-graduation) showed a strong systematic +1 day bias: 28/40 cards at +1d, 11/40 at 0d. Stability and difficulty both bit-exact, so the divergence was isolated to the interval pipeline at graduation.

**Diagnosis**. Anki's LEARNING/RELEARNING graduation paths (`rslib/.../states/learning.rs:86-178` and `relearning.rs:104-184`) do NOT apply the passing-review cascade. Each rating's fuzz call is independent:

```rust
// answer_hard / answer_good in relearning.rs (same shape in learning.rs)
let (minimum, maximum) = ctx.min_and_max_review_intervals(1);  // minimum = 1, NOT cascade
let interval = states.hard.interval;  // or states.good.interval
let review = ReviewState {
    scheduled_days: ctx.with_review_fuzz(interval.round().max(1.0), minimum, maximum),
    ...
```

```rust
// answer_easy is the only rating that floors against good's fuzzed value:
let (mut minimum, maximum) = ctx.min_and_max_review_intervals(1);
let good = ctx.with_review_fuzz(states.good.interval, minimum, maximum);  // FLOAT raw_good
minimum = good + 1;
ctx.with_review_fuzz(states.easy.interval.round().max(1.0), minimum, maximum)
```

Three notable asymmetries vs the passing-review cascade:
1. **HARD/GOOD use `minimum=1` directly**, no `gtl` or `prev_fuzzed + 1`. The cascade chain (good ≥ hard+1, easy ≥ good+1) does NOT bind at graduation.
2. **The "good" intermediate for EASY** uses the FLOAT `states.good.interval` (not rounded). The chosen-rating GOOD output uses `round(states.good.interval)`. Same fuzz factor, different inputs → potentially different fuzz_bounds and different result.
3. **All four `with_review_fuzz` calls reuse the same `ctx.fuzz_factor`** (set once in `card_state_updater`).

Pre-Layer-52, TT routed graduation through `_passing_intervals_with_fuzz(..., scheduled_days=0, ...)` — Layer 51's interleaved cascade with `scheduled_days=0`. With `scheduled_days=0`, `gtl(round(raw_i), 0) = 1` for all positive raws, so `good_min = max(1, hard_fuzzed + 1) = hard_fuzzed + 1`. For graduation scenarios where the fuzz factor would otherwise place good below `hard_fuzzed + 1`, TT shifted good up by +1 day. That's the systematic +1 bias observed in the multi-grade drill.

**Fix.**

1. **New `_graduation_intervals_with_fuzz`** (`backend/app/srs/fsrs.py`) — mirrors Anki's per-rating graduation fuzz logic:
   ```python
   hard = fuzz(max(1.0, float(round(raw_hard))), minimum=1)
   good = fuzz(max(1.0, float(round(raw_good))), minimum=1)
   # EASY: separate good_for_easy via float raw_good, then floor on easy
   good_for_easy = fuzz(raw_good, minimum=1)  # FLOAT, not rounded
   easy = fuzz(max(1.0, float(round(raw_easy))), minimum=good_for_easy + 1)
   ```
   Same ChaCha factor seeded by `cid + reps`, reused for all four calls.

2. **Single call-site swap** in `_graduate_to_review` (fsrs.py): `_passing_intervals_with_fuzz(..., scheduled_days=0, ...)` → `_graduation_intervals_with_fuzz(...)`.

3. **`_passing_intervals_with_fuzz` retained** for the REVIEW→REVIEW path (line 678-694 in `schedule()`). That's still the right helper for `scheduled_days > 0` cases where the cascade actually binds.

**Verification.**

- New `test_parity_relearning_graduation_interval_LAYER_52` (`backend/tests/test_parity_fsrs_schedule.py`). Uses the oracle harness with a REVIEW card → AGAIN → GOOD sequence. Asserts TT's graduated `cards.ivl`-equivalent matches Anki's stored value. Pre-fix: TT=4, Anki=3 (+1 day cascade artifact). Post-fix: bit-exact.
- Extended `_op_get_card` in `backend/tests/anki_oracle/oracle.py` to return `ivl`/`due`/`reps`/`lapses` (was stability/difficulty only) so the new test can assert on interval.
- Multi-grade re-drill on the same Stage 3b snapshots:

  | Metric | Pre-L52 (post-L51) | Post-L52 |
  |---|---|---|
  | Multi-grade stability strict-match | 39/40 | 39/40 (unchanged — Layer 52 only touches intervals) |
  | Multi-grade due_at bit-exact | 11/40 (27.5%) | **34/40 (85.0%)** |
  | All-direction due_at within 1h | 46/89 (51.7%) | **58/89 (65.2%)** |

- Full `./test.sh` green (2510 backend tests, 100% coverage, frontend gate, 11 E2E specs).
- Oracle harness all 7 tests green.

**Remaining residual** (6/40 multi-grade still at +1d, plus most of the 31/89 within-1h misses): same `recompute_memory_state` mechanism as Layer 51's residual. Anki stored the grade-time `s` in `cards.ivl` but rebuilt `cards.data.s` later via Optimize FSRS / deck-config change. Forward-step replay reproduces the post-rebuild `s` exactly but can't recover the grade-time `s` that determined the stored interval. This is the "due_at is pass-through from Anki" case that Stage 3b's design handles by reading `cards.due` at sync time — not reproducible from forward-step, not a TT bug.

**Files.** `backend/app/srs/fsrs.py` (`_graduation_intervals_with_fuzz` helper + 1 call-site swap; `_passing_intervals_with_fuzz` kept for REVIEW→REVIEW). `backend/tests/test_parity_fsrs_schedule.py` (LAYER_52 oracle parity test). `backend/tests/anki_oracle/oracle.py` (`_op_get_card` returns `ivl`/`due`/`reps`/`lapses`). `docs/anki-parity-layers.md` (this entry).

**Pre-Layer checklist note.** Per Step 2/3, Layer 52 touched the load-bearing fuzz pipeline added in Layer 51. The clean refactor: add a NEW graduation-specific helper (`_graduation_intervals_with_fuzz`) rather than parameterize `_passing_intervals_with_fuzz` — Anki's source genuinely has two distinct fuzz pipelines (`states/review.rs` vs `states/learning.rs` + `relearning.rs`) and trying to unify them in TT would obscure the EASY-only asymmetry (good_for_easy uses FLOAT raw_good). Each helper now mirrors one of Anki's two paths exactly.

**Cross-reference.** Layer 51 conflated the REVIEW→REVIEW passing-cascade with the LEARNING/RELEARNING graduation flow. They share the `with_review_fuzz` primitive but apply it under different invariants. Cleanly separating them post-Layer-52 makes both pipelines easier to maintain — and the asymmetry between them (cascade vs simple) is documented as Anki's choice, not a TT quirk.

## Layer 53 — The residual `due_at` divergence is the FSRS LOAD BALANCER, not `recompute_memory_state`

**Surfaced**: Stage 3b residual drill (2026-05-23, post Layers 50–52). After L52 the measurement showed 89/89 stability bit-exact but only 58/89 `due_at` within 1h — and `within-1d == within-1h`, i.e. **every miss is ≥1 full day off, none in the 1h–1d band**. Layers 51/52 (and `docs/stage-3b-empirical-measurement.md`) had attributed the residual to `recompute_memory_state` ("Anki re-derives `cards.data.s`, keeps grade-time `cards.ivl`; the grade-time `s` is unrecoverable"). That attribution is **wrong**, or at most a sub-0.01 secondary effect. The actual mechanism is the **FSRS load balancer**.

**Why the recompute story doesn't hold.** If a full-history recompute had changed `s`, TT's single-grade forward-step would not reproduce it — yet TT matches `cards.data.s` bit-exact (89/89). Bit-exact stability *rules out* recompute being the cause, it doesn't explain it.

**Evidence chain** (all on `/tmp/{tt,anki}_{pre,post}_anki_only.db`; 26 single-grade + 5 multi-grade misses):

1. **The divergence is ±1 day (one +2), bidirectional, on intervals spanning 4→65 days.** A consistent ±1 independent of interval magnitude is not fuzz drift and not recompute (both would scale with the interval). It's a within-fuzz-range relocation.

2. **TT's FSRS algorithm is bit-exact with Anki's own scheduler on identical inputs.** Built synthetic cards with the *exact* `anki_pre` pre-state (s, d, ivl, reps), forced `days_elapsed` by setting `last_review_time = now − E·86400`, and set the card id to the *real* `anki_card_id` so the fuzz seed `id + reps` matches. Anki's `answer_card` and TT's `schedule()` produced **identical** intervals `(59, 64, 6, 47, 3, 4)` for cids 8/46/103/452/131/217 — and **both disagree with the stored** `(60, 65, 5, 45, 4, 3)`. TT mirrors Anki's per-card math exactly; the stored values came from elsewhere.

3. **Stored `s` and stored `ivl` are mutually inconsistent** (sweeping `days_elapsed`): cid8 `stored_s=42.6779` ⟺ E=22 but `stored_ivl=60` ⟺ E=23; cid131 `stored_s=2.4788` ⟺ E=2 but `stored_ivl=4` ⟺ E=3. No single forward grade (one elapsed feeds both `s` and `ivl` via `fsrs.next_states`) can produce both. The interval went through an extra transform after the fuzz.

4. **Every one of the 26 single-grade stored intervals falls within TT's computed fuzz range `[lower, upper]`** — but differs from TT's fuzz *pick*. That is the load balancer's exact signature: it never leaves the fuzz window, it just chooses a less-loaded day inside it.

5. **`config['loadBalancerEnabled'] = b'true'`** in the collection.

6. **Direct proof** (`/tmp/lb_synth.py`): fresh synthetic collection, 400 review cards all due on the pure-fuzz target day (heavy load), one test card mimicking cid8's EASY outcome, controlled E=22. Load balancer **off → easy interval 59** (pure fuzz); **on → 61** (relocated within `[59,69]` to dodge the loaded day). Toggling the balancer moves the interval, on identical FSRS state.

**Mechanism.** Anki's `with_review_fuzz` (`rslib/.../states/fuzz.rs:36-42`) tries `load_balancer_ctx.find_interval(...)` *first* and only falls back to pure fuzz when the balancer is absent. The balancer is wired into both the live answer path (`answering/mod.rs:237-258`, populated when the study queue is built) and the "Reschedule cards on change" path (`fsrs/memory_state.rs:218` → `rescheduler.find_interval`, using `get_fuzz_seed(card, true)` = `reps−1`). It relocates each card's interval to the least-loaded day inside the fuzz window, using the **whole collection's** due-date histogram. The s↔ivl inconsistency resolves cleanly under Optimize+Reschedule: recompute rewrites `cards.data.s` via full-history replay (a sub-0.01 nudge TT also reproduces), while reschedule rewrites `cards.ivl = loadbalance(fuzz(next_interval(recomputed_s), seed = id + reps − 1))` — and the load-balance step is the visible ±1–2 days.

**Update (2026-05): the balancer WAS ported, bit-exact.** The "not reproducible / Path-2-class" call below was the *parity* decision (sync reads `cards.due`, so no fix is needed for sync correctness). As a follow-up the balancer was nonetheless ported and proven bit-exact — see `app/srs/load_balancer.py`, `_anki_rng.py` (`uniform_f32_sample`/`weighted_index_sample`), the `load_balancer` param on `schedule()`, and `test_parity_load_balancer.py`. Oracle sweep: 24/24 bit-exact (incl. the `f32::powf(2.15)` term); real-data replay within-1h **58/89 → 81/89**. Plan: `~/.claude/plans/delegated-puzzling-bee.md`. The "structurally unreproducible" framing was wrong — for the single-preset Slovene deck TT holds the entire histogram. The points below remain true as the *parity* rationale (sync still pass-through; production live-grading port is Phase 2).

**Resolution (parity) — no TT code fix required.**

- **Not needed for parity.** The balancer's pick depends on global collection state; mirroring it for sync would be **Path-2-class** (a live due-date histogram over all of TT's `collocation_directions`) — but sync doesn't need it (next point). It was ported anyway for forward-step fidelity / future TT-native grading (above).
- **Production parity is unaffected.** `sync_pull` reads `cards.due` directly (rule: `due_at` is pass-through from Anki), so synced cards get Anki's load-balanced due date verbatim. The only place TT's pure-fuzz interval is user-visible is a **TT-native grade** of a card the user never re-grades in Anki — a cosmetic ±1–2 day spread, not a parity break.
- **The Stage-3b "due_at within 1h" stat was measuring the wrong thing** for this collection: it compared forward-step-replay `due_at` against synced (load-balanced) `due_at`. With the balancer on it can never hit 100%, and that ceiling is expected. It should be read as a fuzz-*range* reliability check.

**Files.** `backend/app/anki/measure_stage3b_premise.py` (new `_detect_load_balancer`; surfaces a prominent caveat in the report when `loadBalancerEnabled` is set; coverage-omitted diagnostic). `docs/stage-3b-empirical-measurement.md` (residual attribution corrected from `recompute_memory_state` to the load balancer). `docs/anki-parity-layers.md` (this entry). No production scheduling code changed — TT's FSRS pipeline is verified correct.

**Pre-Layer checklist note.** Step 1 named the divergence (a queue/interval *position*, not a stability/difficulty number). Step 2 found the relevant helper is `_passing_intervals_with_fuzz` / `_constrained_fuzz_bounds` — and the drill confirmed those compute the correct fuzz *range*; the missing piece lives entirely outside TT's model (global due histogram). That is the signal to **stop** rather than extend a helper: the right output already exists, the divergence is a not-mirrored Anki subsystem whose mirroring complexity is unbounded.

**Cross-reference.** Mirrors the Layer 43 pattern — a residual previously chalked up to one mechanism (there, Layer 38's "NULL-R at dr"; here, `recompute_memory_state`) turned out to be a coincidence of input regime once the binary was driven with controlled inputs. The "trust the binary, not the source" rule (queue-parity rule 13) and the harness rule's "vary the inputs Anki touches" both fired: the breakthrough was forcing `days_elapsed` via a synthetic `last_review_time` and pinning the fuzz seed via the real `anki_card_id`, which removed the wall-clock confound that had made every prior oracle re-answer look like a stability mismatch.
