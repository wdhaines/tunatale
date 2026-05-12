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

1. **Review-interval fuzz** (`rslib/scheduler/states/fuzz.rs`). Anki adds day-scale fuzz to graduated review intervals. TT's `_next_interval` rounds without it. Same `_anki_rng.py` port can serve (different formula).

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
