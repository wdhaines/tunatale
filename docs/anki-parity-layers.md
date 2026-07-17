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

**Files.** `app/srs/anki_mirror/queue_stats.py` (`resolve_/advance_learning_cutoff`), `app/api/srs.py` (call sites), `app/plugins/anki_sync/sync.py` (advance after ingest).

---

## Layer 3 — frontend never reordered

**Bug.** The Svelte review page cached the queue once on mount and never refetched after grade events. Server state shifted (sync, cutoff advance) but the user saw stale order.

**Fix.** Frontend rewrite: drop `deferred`/`buriedCollocationIds`/`reapDeferred`/`topUpQueue` local logic. Refetch `/review-queue` on every grade. Always render `queue[0]`.

**Files.** `frontend/src/routes/review/+page.svelte`, `frontend/src/lib/api.ts`, page tests rewritten.

---

## Layer 4 — `session_main_queue` freeze

**Bug.** Server rebuilt the main queue (review + new spread mix) on every fetch. The intersperser ratio changed as counts decremented through the session, so new cards drifted to earlier or later positions vs Anki's frozen main.

**Fix.** Cache the built main queue keyed on `today.isoformat()`. First call builds + freezes; later calls keep the cached order, filter out graded cards, and append mid-day arrivals at the tail.

**Files.** `app/srs/anki_mirror/queue_stats.py` (`get_/set_session_main_queue`), `app/api/srs.py`.

---

## Layer 5 — page mount advances cutoff

**Bug.** After page reload, the cutoff stayed frozen at the last grade event. Learning cards whose timers expired between sessions stayed pending forever, never surfacing.

**Fix.** Frontend passes `?session_start=1` on mount; server advances `learning_cutoff` to current now. Mirrors Anki's `update_learning_cutoff_and_count` on deck open.

**Files.** `app/api/srs.py` (session_start path), `frontend/src/lib/api.ts`, `+page.svelte`.

---

## Layer 6 — Bit-exact RNG port for learning fuzz

**Bug.** TT scheduled learning steps with no fuzz. Anki adds uniform `[0, min(0.25*step, 300))` seconds. TT's `due_at` always landed exactly at `+60s` while Anki's was `+60..+74s`, so the cutoff fell between them and the cards diverged.

**Fix.** `app/srs/anki_mirror/_anki_rng.py` — bit-exact port of Rust's `StdRng::seed_from_u64(seed) → rng.random_range(low..high)`. Chain: SplitMix64 → ChaCha12 → Canon's biased widening-multiply method. Seeded by `(card_id + reps) mod 2^64`. SplitMix64 verified against canonical reference values; downstream functions are regression-pinned against our own output.

**Files.** NEW `app/srs/anki_mirror/_anki_rng.py`, `app/srs/fsrs.py:_learning_step_fuzz_seconds`, NEW `tests/test_anki_rng.py`.

### Layer 6b — revlog shape decode

**Bug.** `_derive_revlog_shape` used `due_at - last_review` to decode the step duration. After Layer 6, that included the fuzz, so the shape overcounted by up to 25% of the step.

**Fix.** Decode the unfuzzed step from `left + last_rating`, with Hard-on-first-step special case (Anki uses `(steps[0]+steps[1])/2` there).

**Files.** `app/plugins/anki_sync/sync.py:_derive_revlog_shape`.

---

## Layer 7 — `session_main_queue` invalidation on sync

**Bug.** The Layer-4 freeze never repositioned cards that transitioned learning→review mid-session. A card that graduated yesterday evening showed up at the cached tail (as a latecomer) today, instead of head-of-R-asc.

**Fix.** Three parts:
- `sync_pull` invalidates `session_main_queue` on completion (mirrors Anki's `requires_study_queue_rebuild`).
- `/review-queue` drops review-state latecomers instead of appending them at the tail.
- `clear_session_main_queue` helper + `delete_anki_state_cache` DB method.

**Files.** `app/srs/anki_mirror/queue_stats.py:clear_session_main_queue`, `app/srs/database.py:delete_anki_state_cache`, `app/plugins/anki_sync/sync.py`, `app/api/srs.py`.

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

**Files.** `app/models/syntactic_unit.py:31`, `app/plugins/anki_sync/import_seed.py:115`.

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

**Files.** `app/plugins/anki_sync/sqlite_reader.py:235-241`, `app/plugins/anki_sync/sync.py:755-766`.

---

## Layer 13 — `_compute_today_col_day` mirrors Anki

**Bug.** Naive `(now - crt) // 86400` undercounts when `crt` is at noon UTC and `now` is in morning UTC. Ignores rollover hour entirely. TT thought today=4509 while Anki used 4510, so review-pool filters were off by a day.

**Fix.** Local-date subtraction + rollover-hour adjustment (mirrors `scheduler/timing.rs::sched_timing_today_v2_new`).

**Files.** `app/srs/anki_mirror/queue_stats.py:39-82`.

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

**Files.** `app/api/srs.py`, `app/srs/anki_mirror/queue_stats.py`, `tests/test_queue_stats_cache.py`, `tests/test_api.py`, `tests/test_api_srs.py`.

---

## Layer 17 — `_direction_differs` compares `left`, `due_at`, `prior_state`

**Bug.** `sync_pull`'s diff-before-write check excluded these three fields. A merged direction whose only change was step-state or `prior_state` could be silently dropped.

**Fix.** Added all three to the comparison.

**Files.** `app/plugins/anki_sync/sync.py:506-530`.

---

## Layer 18 — `sync_pull` defers to Anki when Anki is ahead

**Bug.** When `dirty_fsrs=True` AND both apps still saw the card as learning, sync_pull kept TT's `left`. Push then wrote TT's stale `left` over Anki's — un-graduating cards Anki had already advanced past.

**Fix.** Two new branches in the `dirty_fsrs` path:
- **Inverse state-class divergence**: local LEARNING but Anki queue=2 (graduated) → Anki wins, drop dirty, surface `state_class` conflict.
- **Step progress**: both in learning but `_anki_step_ahead(anki.left, local.left)` is true → take Anki's `left`/`due_at`/FSRS state, drop dirty, surface `step_progress` conflict.

New helper `_anki_step_ahead(anki_left, local_left)` encapsulates the `% 1000` comparison (shared with Layer 19).

**Files.** `app/plugins/anki_sync/sync.py`, `tests/test_anki_sync_pull.py`.

---

## Layer 19 — `sync_push` skips when Anki is ahead

**Bug.** Push unconditionally called `set_learning_state(card_id, ds.left, …)`. If Anki had already graduated the card (queue=2) or had a smaller `total_remaining`, the write erased Anki's progress.

**Fix.** New `OfflineWriter.get_current_card_state(card_id)` returns `{queue, type, left} | None`. In `sync_push`, before writing learning state, fetch Anki's current row. If `queue=2` or `_anki_step_ahead(anki.left, ds.left)`, skip the card write **and** the revlog, mark the direction clean, increment `directions_pushed`, continue. Layer 18's pull-side merge then carries Anki's state into TT on the same sync.

`FakeWriter` in `tests/test_anki_sync_push.py` gained `current_states: dict[int, dict]` + `get_current_card_state(card_id)`.

**Files.** `app/plugins/anki_sync/sync.py`, `tests/test_anki_sync_push.py`.

---

## Layer 20 — `sync_pull` sets `prior_state` on state-class transitions

**Bug.** `sync_pull` never wrote `prior_state`. After Anki introduced a card today (queue 0→1 or 0→2), TT mirrored `state=LEARNING/REVIEW` but `prior_state` stayed None. `count_new_introduced_today` filters by `prior_state='new'`, returned 0 → new-card badge stuck at `cap − 0` instead of `cap − N`.

**Fix.** New helper `_resolve_prior_state(local_dir, new_state, *, first_review_ms, today_start_ms)`:
- On state-class change → return `local_dir.state` (captures the transition).
- Else preserve `local_dir.prior_state` (no-op syncs don't clobber).
- **Self-heal**: when state matches and Anki's `first_review_ms` is today AND new_state isn't NEW, force `prior_state='new'`. Recovers stale data from pre-Layer-20 syncs and from same-day graduations that clobbered the marker. Broadened in Layer 22 to fire regardless of current `prior_state` value.

Wired into all 6 direction-construction sites in `sync_pull`. Extended `CardRecord` with `first_review_ms` (sourced from `MIN(revlog.id)` in `OfflineReader`).

**Files.** `app/plugins/anki_sync/sync.py`, `tests/test_anki_sync_pull.py`.

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

**Files.** `app/srs/fsrs.py:209`, `app/plugins/anki_sync/sync.py:_resolve_prior_state`, `tests/test_srs_fsrs.py`, `tests/test_anki_sync_pull.py`.

---

## Layer 23 — Just-graded learning collapse

**Bug.** After grading srebro in TT, srebro re-appeared immediately while Anki served družina next. Cause: Anki's `requeue_learning_entry` (`rslib/scheduler/queue/learning.rs:94-113`) shifts a just-requeued learning card's `due` to `next.due + 1s` when main is empty and the card would otherwise be served immediately — preventing "press Good, see same card." TT had no equivalent.

**Fix.** In `get_review_queue`, after sorting `pending_learning`, if `ordered_main` is empty AND `len(pending) >= 2` AND head's `last_review == cutoff` (i.e. just graded) AND head's `due_at <= cutoff+1200s` AND next's `due_at+1 < cutoff+1200s` AND `next.due_at >= head.due_at`, swap positions [0] and [1]. Since TT rebuilds the queue from disk each request, the swap achieves the same display effect without mutating stored `due_at`.

The `last_review == cutoff` equality check is exact — the grade endpoint sets both from the same `now`, so they match to microsecond precision for the most-recently-graded card.

**Files.** `app/api/srs.py:927-947`, `tests/test_api_srs.py:TestJustGradedLearningCollapse`.

---

## Path 2 (considered and REJECTED 2026-06-12 — do not re-pitch)

> **Decision (2026-06-12): rejected.** Path 2 would trade away the *live mirror*, which
> is the product. TT rebuilds the queue on every `/review` mount (`session_start=1` →
> re-sort by current R) — a valued, Anki-faithful behavior; Path 2's sync-time snapshot
> can't re-derive on the live request path (rule 1), so it would kill refresh-rebuild.
> The leak rate has also slowed *way* down (34 Layer commits in 2026-05 → 6 in early
> 2026-06; the queue/FSRS mirror hasn't needed a fix since ~Layer 64, only the sync seam
> has), so Path 2's own trigger ("only when the leak count won't stop") isn't met. The
> mirror is the asset; maintain it cheaply (de-dup helpers, decompose god-modules
> opportunistically) per `.claude/rules/anki-queue-parity.md` → "Maintenance strategy."
> The description below is kept for context only.

Across Layers 9-15 in particular, every fix to "TT reconstructs Anki's queue from TT state" surfaced another input-quality bug. The pattern: Anki has N code branches, TT had mirrored M of them. Path 2 would dissolve this whole class by snapshotting Anki's actual queue at sync time.

**Mechanism.** At `sync_pull`, while `collection.anki2` is open, execute Anki's `review_order_sql` and persist the resulting card-id sequence as TT's "today's anchor queue." Between syncs, TT serves from the snapshot, filtered by "graded since sync."

**Tradeoffs.**
- Wins: removes session_main_queue freeze, intersperser ratio override, R-asc reconstruction, two-branch R formula mirror, today_col_day computation, sibling-bury reconstruction. Estimated 60% of `app/api/srs.py` queue logic goes away.
- Costs: ~2-3 hour refactor. Single sync-time dependency on `collection.anki2` (already accepted at sync). Slightly less flexible if TT later wants to serve cards Anki wouldn't have served (e.g. self-introduced TT-only cards).

**Decision.** REJECTED 2026-06-12 (see banner at the top of this section). Not "deferred until the next R-asc patch" — actively not the direction, because it would cost the live refresh-rebuild mirror and the leak rate has fallen rather than risen.

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

**Files.** `backend/app/srs/database.py:622-634` (ORDER BY), `backend/app/srs/database.py` — new `get_created_at_by_guid` helper, `backend/app/plugins/anki_sync/sync.py:1295-1297` (sort), `backend/app/srs/migrations.py` (v16→v17), `.claude/rules/anki-queue-parity.md` (playbook), `docs/anki-parity-layers.md` (this entry).

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

**Files.** `backend/app/models/srs_item.py` (DirectionState field), `backend/app/srs/migrations.py` (v17→v18 + CURRENT_VERSION = 18), `backend/app/srs/database.py` (`_DIR_COLUMNS`, `update_direction`, `_row_to_directions`, `count_new_introduced_today`), `backend/app/srs/fsrs.py` (`_schedule_new`, `_graduate_to_review`), `backend/app/plugins/anki_sync/sync.py` (`_resolve_introduced_at` + 9 sync-merge call sites), `backend/tests/test_srs_database.py` (`TestCountNewIntroducedToday`), `backend/tests/test_srs_migrations.py` (v17→v18 test).

---

## Layer 27 — Daily unbury sweep for stale `state='buried'`

**Trigger.** Snapshot showed 151 directions in TT with `state='buried'` but only 4 of those had `queue=-2` in Anki — 147 stale-buried rows accumulated going back to 2026-05-03. Anki's queue builder unburies (queue=-2 → queue=2) once per day on the first rebuild after rollover, but TT relied on `sync_pull` to overwrite `state='buried'` with the next pulled state. Without recent syncs, TT under-counted reviews and silently dropped cards from the queue.

**Fix.** `SRSDatabase.unbury_if_needed(today)` sweeps all `state='buried'` rows to `state='review'` (reps>0) or `state='new'` (reps=0). Idempotent per local day via `anki_state_cache['last_unbury_day']` — second call same-day is a no-op so today's fresh sibling-buries (landed by mid-day `sync_pull`) survive until tomorrow. Hooked into:
- `GET /api/srs/queue-stats` (badge correctness)
- `GET /api/srs/review-queue` (queue head)
- `sync_pull` (before processing Anki state, so any buried rows the pull lands are today's and won't be re-swept)

**Why "reps>0 → review, reps=0 → new"?** TT's BURIED state only enters via `sync_pull` mirroring Anki's queue=-2/-3 (the sibling-bury or user-bury terminal states). For those, the pre-bury state was either review (graded card whose sibling was today's grade) or new (rare, but possible for sibling-buried news under `bury_new=true`). `reps` is the only signal in the row that distinguishes them.

**Files.** `backend/app/srs/database.py` (`unbury_if_needed`), `backend/app/api/srs.py:get_review_queue, get_queue_stats` (call sites), `backend/app/plugins/anki_sync/sync.py:sync_pull` (pre-merge sweep), `backend/tests/test_srs_database.py` (`TestUnburyIfNeeded`), `backend/tests/test_api_srs.py` (queue-endpoint integration test).

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

**Files.** `backend/app/api/srs.py` (new `_compute_live_main` + `build_and_freeze_main_queue`; `get_review_queue` refactored to call the helper), `backend/app/plugins/anki_sync/sync.py:1257-1267` (sync_pull now calls build + freeze), `backend/tests/test_anki_sync_pull.py` (updated `test_sync_pull_clears_…` to `test_sync_pull_rebuilds_…` reflecting the new contract).

**Aftermath / lesson.** The stale-cache trap from Layer 28's aftermath is now eliminated for the sync_pull path. Deploy-time stale cache (cache held in DB across backend restart) is still a concern — `clear_session_main_queue` from a manual diagnostic remains the right escape hatch when reasoning about ordering bugs against an old freeze. Documented in principle 2 of `.claude/rules/anki-queue-parity.md`.

---

## Layer 30 — `_queue_to_state` must trust `queue`, not `reps`

**Trigger.** TT served `ničnothing` as the first-new card while Anki served `zdravo`. `ničnothing` in Anki was `queue=2` (review), `type=2`, `due=4515`, `ivl=16`, `reps=0` — a card that's clearly been graduated but somehow has `reps=0` (e.g., the user used Anki's "Forget" action, which clears `reps` but leaves the card in `queue=2`). TT's `_queue_to_state` had this fallback at the bottom: `if reps == 0: return SRSState.NEW`. The `queue=2` arm was never reached. So sync_pull saw the card as Anki-reviewed but wrote `state='new'` to TT — and TT then surfaced it at the head of the new bucket every day.

**Fix.** `_queue_to_state` now uses `queue` as the authoritative signal: `queue=2 → REVIEW`, `queue=0 → NEW`, regardless of `reps`. The reps fallback only fires for unknown queue values (never happens against current Anki, but defensively kept for future-proofing).

**Files.** `backend/app/plugins/anki_sync/sync.py:_queue_to_state` (explicit `queue == 2` arm added before the reps fallback; `queue == 0` arm explicit too), `backend/tests/test_anki_sync_pull.py::test_queue_to_state_mapping` (added `(queue=2, reps=0) → REVIEW` and `(queue=2, reps=7) → REVIEW` parametrize cases; updated existing `(queue=0, reps=5)` from REVIEW to NEW to reflect "queue is authoritative").

**How to spot in the wild.** Run the diagnostic:
```sql
SELECT c.id, c.queue, c.type, c.due, c.reps, c.ivl, n.flds
FROM cards c JOIN notes n ON c.nid=n.id
WHERE c.queue=2 AND c.reps=0 AND c.did=<your-deck>;
```
Any row here is a "Forget"-style or manually-edited card. After Layer 30 these correctly mirror to TT as REVIEW.

---

## Layer 31 — `<b>L2</b><br><i>EN</i>` import bug + one-shot cleanup script

**Trigger.** User noticed `ničnothing` at the head of TT's new bucket — clearly mangled text (Slovene `nič` concatenated with English gloss `nothing`). Traced to `extract_l2_from_fields` in `app/plugins/anki_sync/sqlite_reader.py`: the HTML-strip fallback `re.sub(r"<[^>]+>", "", field)` removes tags without inserting whitespace, so Anki's Pronunciation/Basic notetype Front field `<b>nič</b><br><i>nothing</i>` collapsed into the single token `ničnothing`. Saved as TT's `text`, English gloss lost. 39 rows affected in the user's deck (every Basic-notetype note that used the `<b>L2</b><br><i>EN</i>` formatting).

**Fix (import side).** Added `extract_gloss_from_fields(fields) -> str | None` that recognises the `<b>L2</b><br><i>EN</i>` pattern and returns the gloss. Updated `extract_l2_from_fields` to short-circuit on the same pattern returning the L2 group. `import_seed.py` now checks `extract_gloss_from_fields` before falling back to the "other field" stripped-HTML translation extractor. Future imports of these notes do the right thing.

**Fix (existing data).** One-shot script `app/anki/fix_html_concat_imports.py` walks every TT collocation linked to an Anki note, parses the Front field, and:
- **renames** the row (`text=L2, translation=EN`) when no other TT collocation already uses the clean L2 text;
- **deletes** the row when a clean-L2 twin already exists (Pronunciation cards duplicate Slovene Vocabulary cards for these words; user opted to drop the dupes).

Defensive: if a rename hits a UNIQUE conflict at apply-time, it falls back to delete. Tests cover both planning and apply phases, plus the CLI's dry-run / missing-DB / mixed-output paths.

**Files.** `backend/app/plugins/anki_sync/sqlite_reader.py` (added `_B_THEN_I_PATTERN`, `extract_gloss_from_fields`, second-pass arm in `extract_l2_from_fields`), `backend/app/plugins/anki_sync/import_seed.py:128-144` (uses `extract_gloss_from_fields` when matched), `backend/app/anki/fix_html_concat_imports.py` (new one-shot script), `backend/tests/test_anki_sqlite_reader.py` (extractor tests), `backend/tests/test_anki_import_seed_readonly.py` (round-trip test), `backend/tests/test_anki_fix_html_concat_imports.py` (script tests).

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
- Uses `app.plugins.anki_sync.safety.safe_open(mode='rw')` for backup + integrity check + post-write audit.
- Following the standard schema-bump workflow from `.claude/rules/anki-sync.md`: prints "File → Sync → Upload to AnkiWeb, then run `app.plugins.anki_sync.normalize_usns`".

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

- `_bury_kind_from_queue(queue)` helper at `backend/app/plugins/anki_sync/sync.py:916-928`: maps Anki's queue value (-3 → `'sched'`, -2 → `'user'`, else `None`). All 7 direction-construction sites in `sync_pull` pass `bury_kind=_bury_kind_from_queue(card_rec.queue)` (5 sites) or explicit `bury_kind=None` (2 sites, for non-buried writes).
- `unbury_if_needed` (`backend/app/srs/database.py:1595-1604`) now filters `WHERE state='buried' AND bury_kind='sched'`. User-buried rows survive the sweep, matching Anki's `unbury_if_needed` (which only releases queue=-3).
- Migration backfill: every existing `state='buried'` row gets `bury_kind='user'` (pessimistic — better to leave a sibling-bury sticky for one extra day than wipe a user-bury). The next sync_pull rewrites the kind from Anki's actual queue value.

**Files.** `backend/app/srs/migrations.py:570-586` (migration v19→v20), `backend/app/plugins/anki_sync/sync.py:916-928` (helper) + 7 `bury_kind=` sites in `sync_pull` (5 `_bury_kind_from_queue`, 2 explicit `None`), `backend/app/srs/database.py:389/413/489` (column read/write), `backend/app/srs/database.py:1595-1604` (filtered sweep), `backend/app/models/srs_item.py` (DirectionState field), corresponding tests in `backend/tests/test_srs_migrations.py`, `backend/tests/test_anki_link_tt_images.py`, `backend/tests/test_srs_database.py`.

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

**Files.** `backend/app/config.py` (new setting), `backend/app/srs/anki_mirror/queue_stats.py` (trio), `backend/app/srs/database.py` (`count_reviews_completed_today`), `backend/app/api/anki.py` (wired), `backend/app/api/srs.py` (cap applied).

**Aftermath.** TT's review badge now matches Anki's deck-list "Due" count within ±1 (boundary drift at rollover acceptable). The `daily_review_cap` and `review_cap_source` fields appear in the `/queue-stats` response.

**Cross-reference.** The existing `daily_new_cap` trio at `queue_stats.py:129-274` is the pattern; this layer mirrors it exactly.

---

## Layer 37 — `anki_card_mod` in `_direction_differs` (FNV tiebreaker drift)

**Trigger.** Three R-tied review cards (`iz`, `nič`, `dobrodošli` — all `data='{}'`, all queue=2, all due today) had different head positions between TT and Anki. Anki sorted them by `fnvhash(cards.id, cards.mod)`; TT's mirror agreed on the algorithm but used stale `anki_card_mod` values pulled from a prior sync, producing different hashes and a different ordering inside the tied group.

**Cause.** `_direction_differs(local, candidate)` in `backend/app/plugins/anki_sync/sync.py` checked every sync-relevant FSRS field but NOT `anki_card_mod`. Whenever Anki bumped `cards.mod` for any reason that didn't also change an FSRS field — server-side sync mtime resolution, scheduler housekeeping, bury actions — sync_pull's diff returned False and TT's local copy stayed stale. The FNV tiebreaker (Anki's `fnvhash(id, mod)` appended last in every `review_order_sql` variant — `rslib/src/storage/card/mod.rs:897`) silently diverged.

**Fix.** One line in `_direction_differs`: `or local.anki_card_mod != candidate.anki_card_mod`. Sync_pull already constructs candidates with `anki_card_mod=card_rec.anki_card_mod` (the current Anki value), so once the diff fires, `update_direction` refreshes the column.

**Files.** `backend/app/plugins/anki_sync/sync.py:841-866` (diff check); regression tests in `backend/tests/test_anki_sync_pull.py` (`test_direction_differs_detects_anki_card_mod_change`, `test_sync_pull_refreshes_stale_anki_card_mod`).

**Aftermath.** R-tied groups across both apps now agree on the tiebreaker. Write volume on sync is slightly higher (any mod-only Anki bump triggers a TT-side update), but each write is one small UPDATE.

**Cross-reference.** `_merge_by_retrievability_ascending` at `backend/app/api/srs.py:768-800` is where the FNV value feeds the sort. Anki's matching constant: `rslib/src/storage/card/mod.rs:823` (`fnvhash(id, mod)`).

---

## Layer 38 — NULL-R sort: `desired_retention` placement (NOT NULLs-first, NOT NULLs-last)

**Trigger.** During parallel grading, the user repeatedly saw TT serve cards earlier or later than Anki for the same data state. The recurring offender was `nič` — a queue=2 card with `data='{}'`, `reps=0`, no FSRS memory_state. TT placed it at queue head; Anki placed it mid-pool. Earlier in the session, TT had placed it at the tail (pre-Approach-2 behavior); Approach 2 flipped it to the head; both were wrong.

**Empirical finding (Anki 25.09.4).** With the snapshot inspected via `uv run --with anki python` + `col.sched.get_queued_cards(fetch_limit=20)`, Anki places `data='{}'` review cards at the position `desired_retention` would occupy in R-asc. For the user's deck (`desired_retention=0.86`), nič landed between `streljati` (R=0.859) and `steklenica` (R=0.862). Same SQL run via `col.db.all(...)` with Anki's own UDFs returns NULL for nič, and the ORDER BY says NULLs-first — i.e. Anki's own SQL predicts head placement. The actual queue places it mid-pool. **The source-vs-binary contradiction was not resolved; the binary behavior was adopted as ground truth.** The `/tmp/anki-source` checkout is a shallow clone at `main` tip with no version tag — almost certainly newer than 25.09.4 — so the SQL-level explanation may live in a code path that has since been replaced.

**Pre-existing bug surfaced.** `_DESIRED_RETENTION_FIELD` in `app/srs/anki_mirror/queue_stats.py` was set to **40**, but per `proto/anki/deck_config.proto:188`, field 37 is `desired_retention` and field 40 is `historical_retention`. The existing `refresh_fsrs_params` was caching `historical_retention` thinking it was `desired_retention` (often 0.9 vs the user's 0.86 — close but wrong). Fixed both the production constant and the test helper (`tests/_helpers/anki_db.py:12`).

**Fix.**
- `compute_retrievability(state, today, now=None, desired_retention=0.9)` — when stability or last_review is None, returns `desired_retention` instead of `None` (Approach 2) or `1.0` (pre-Approach 2).
- New `find_fixed32_field(data, target)` helper in `app/srs/anki_mirror/protobuf_wire.py`.
- New trio in `app/srs/anki_mirror/queue_stats.py` mirroring `daily_new_cap`: `_read_desired_retention_from_deck_config_table`, `refresh_desired_retention`, `resolve_desired_retention` (cache → 0.9 default).
- `refresh_desired_retention` wired into `app/api/anki.py` alongside the other refresh calls.
- `_merge_by_retrievability_ascending` resolves `desired_retention` once per call and threads it into `compute_retrievability`. The `sort_r = -1.0 if r is None else r` Approach-2 workaround is gone.

**Files.** `app/srs/fsrs.py:91-115` (signature + body), `app/srs/anki_mirror/queue_stats.py:31` (constant 40→37) + `:280-339` (new trio), `app/srs/anki_mirror/protobuf_wire.py:118-140` (helper), `app/api/srs.py:783-799` (resolve + thread), `app/api/anki.py:91-104` (sync wiring), `tests/_helpers/anki_db.py:12` (test helper field number); new tests in `test_srs_fsrs.py::test_*_returns_desired_retention`, `test_api_srs.py::test_merge_retrievability_null_card_lands_mid_pool_at_dr`, `test_queue_stats.py::test_resolve_desired_retention_*`, `test_queue_stats_cache.py::TestDesiredRetentionCache`.

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

**Files.** `backend/app/plugins/anki_sync/sqlite_reader.py:265-297` (`_compute_last_review` rewrite), `backend/app/srs/fsrs.py:128-158` (`_elapsed_days_for_fsrs` col-day branch), `backend/app/srs/anki_mirror/queue_stats.py` (`refresh_col_crt`/`resolve_col_crt` trio), `backend/app/api/anki.py` (sync wiring), `backend/app/api/srs.py` (col_crt threaded through entry points). Tests: `backend/tests/test_fsrs.py` (3 unit tests), `backend/tests/test_anki_sqlite_reader.py` (1 regression + 3 date fixes), `backend/tests/test_parity_fsrs_schedule.py` (1 oracle test refactored).

## Layer 46 — `compute_due_at` preserves underlying due through bury/suspend

**Trigger.** Morning of 2026-05-20: TT badge `30 + 7 + 205` vs Anki `30 + 7 + 196`. New/learning identical, review off by 9. No grades today on either side; last sync was 5/19 22:25 EDT. Forensics traced 22 TT directions whose `due_at=2026-05-19T04:00:00Z` while their `anki_due=4522` (= 2026-05-23). Same row, internally inconsistent.

**Root cause.** `compute_due_at(queue, due_raw, col_crt)` in `app/plugins/anki_sync/sqlite_reader.py` only branched on `queue` and treated everything outside `{1, 2, 3}` as "today at 04:00 UTC", discarding `due_raw`. Anki preserves `cards.due` through bury and suspend — only `cards.queue` flips. When `sync_pull` read these 22 sibling-buried review cards (queue=-2, due=4522 from Monday's last grade), `compute_due_at` returned `today` instead of `2026-05-23`. The merge wrote inconsistent `due_at` / `anki_due` to the same row. The daily unbury sweep (Layer 27/35) then flipped state `buried→review` without touching `due_at`, surfacing 22 stale-due cards in today's review badge.

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

**Files.** `backend/app/plugins/anki_sync/sqlite_reader.py:40-67` (`compute_due_at` rewrite + signature), `backend/app/plugins/anki_sync/sqlite_reader.py:188` (`parse_fsrs_data` passes `card_type`), `backend/app/anki/backfill_due_date_from_anki_due.py:57-64` (migration script reads/passes `cards.type`). Tests: `backend/tests/test_anki_sqlite_reader.py` (6 unit + 1 integration test).

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

**Files.** `backend/app/plugins/anki_sync/sync.py` (`OfflineWriter.bury_siblings`, `_state_to_anki_queue` + `_STATE_VALUE_TO_ANKI_QUEUE` helpers, `AnkiSync._backfill_bury_siblings_for_today_grades`, `resolve_bury_*` imports, `sync_push` tail call). `backend/app/srs/database.py` (`SRSDatabase.list_anki_cards_graded_today`). Tests: `backend/tests/test_anki_sync_push.py` (10 unit `bury_siblings` tests + 6 backfill integration tests + `_state_to_anki_queue` enum coverage), plus `bury_siblings` stubs added to four pre-existing `FakeWriter` classes in `test_anki_sync_orphan_recovery.py`, `test_anki_sync_force_fsrs.py`, `test_anki_sync_round_trip.py`, `test_anki_sync_concurrent_review.py`.

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

- ``compute_due_at`` (``app/plugins/anki_sync/sqlite_reader.py:72``, sync_pull writeback): ``datetime.combine(col_crt_utc_date + due_raw days, time(4, 0), tzinfo=UTC)`` — 04:00 UTC on the calendar date matching Anki's col_day arithmetic.
- ``schedule()`` (``app/srs/fsrs.py``, two sites at lines 547 and 935): ``datetime.combine(review_date + interval days, time(0, 0), tzinfo=UTC)`` — midnight UTC on ``now.date()`` + interval, ignoring rollover_hour entirely and using UTC calendar date instead of Anki's col_day.

The 4-hour deltas came from time-of-day disagreement (00:00 UTC vs 04:00 UTC). The 20h/44h/68h deltas came from a second issue: grades fired between 00:00 and 04:00 UTC belong to "yesterday's col_day" by Anki's reckoning but to "today UTC" by ``schedule()``'s reckoning — landing the derived due_date one day too far.

This was self-consistency drift within TT, not TT-vs-Anki — both endpoints disagreed against each other. Per the Pre-Layer checklist Step 2/3, the right fix factors the convention into a single helper used by both paths rather than fixing one site.

**Fix.**

1. **New helper** in ``backend/app/srs/anki_mirror/protobuf_wire.py`` (lives next to ``compute_anki_day_index`` since it's the inverse direction):

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

**Files.** ``backend/app/srs/anki_mirror/protobuf_wire.py`` (+helper). ``backend/app/plugins/anki_sync/sqlite_reader.py`` (refactor ``compute_due_at`` to use the helper, drop unused ``timedelta`` import). ``backend/app/srs/fsrs.py`` (new ``_review_due_at_from_interval`` wrapper, ``col_crt`` and ``review_date`` plumbed through three private schedulers and their 8 call sites, two day-level due_at construction sites updated). ``backend/tests/test_fsrs.py`` (4 new tests). ``docs/anki-parity-layers.md`` (this entry).

**Cross-reference.** Layer 11/15/40 (``_elapsed_days_for_fsrs`` dual-branch + col_day arithmetic for elapsed time) — same shape as this fix, applied to the elapsed-days side. ``compute_anki_day_index`` (``protobuf_wire.py:196``) — companion helper in the *opposite* direction. **Correction (Layer 54):** the two are NOT round-trip inverses — ``review_due_at_for_col_day(N)`` fed back through ``compute_anki_day_index`` yields ``N − 1``. That is intentional and inert (see Layer 54); do not "fix" it. The remaining off-by-1-day deltas in the measurement are tracked as Layer 50 (stability port drift, scattered ~5-7% in ``_next_stability_recall``).

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

**Surfaced**: Stage 3b residual drill (2026-05-23, post Layers 50–52). After L52 the measurement showed 89/89 stability bit-exact but only 58/89 `due_at` within 1h — and `within-1d == within-1h`, i.e. **every miss is ≥1 full day off, none in the 1h–1d band**. Layers 51/52 (and `docs/archive/stage-3b-empirical-measurement.md`) had attributed the residual to `recompute_memory_state` ("Anki re-derives `cards.data.s`, keeps grade-time `cards.ivl`; the grade-time `s` is unrecoverable"). That attribution is **wrong**, or at most a sub-0.01 secondary effect. The actual mechanism is the **FSRS load balancer**.

**Why the recompute story doesn't hold.** If a full-history recompute had changed `s`, TT's single-grade forward-step would not reproduce it — yet TT matches `cards.data.s` bit-exact (89/89). Bit-exact stability *rules out* recompute being the cause, it doesn't explain it.

**Evidence chain** (all on `/tmp/{tt,anki}_{pre,post}_anki_only.db`; 26 single-grade + 5 multi-grade misses):

1. **The divergence is ±1 day (one +2), bidirectional, on intervals spanning 4→65 days.** A consistent ±1 independent of interval magnitude is not fuzz drift and not recompute (both would scale with the interval). It's a within-fuzz-range relocation.

2. **TT's FSRS algorithm is bit-exact with Anki's own scheduler on identical inputs.** Built synthetic cards with the *exact* `anki_pre` pre-state (s, d, ivl, reps), forced `days_elapsed` by setting `last_review_time = now − E·86400`, and set the card id to the *real* `anki_card_id` so the fuzz seed `id + reps` matches. Anki's `answer_card` and TT's `schedule()` produced **identical** intervals `(59, 64, 6, 47, 3, 4)` for cids 8/46/103/452/131/217 — and **both disagree with the stored** `(60, 65, 5, 45, 4, 3)`. TT mirrors Anki's per-card math exactly; the stored values came from elsewhere.

3. **Stored `s` and stored `ivl` are mutually inconsistent** (sweeping `days_elapsed`): cid8 `stored_s=42.6779` ⟺ E=22 but `stored_ivl=60` ⟺ E=23; cid131 `stored_s=2.4788` ⟺ E=2 but `stored_ivl=4` ⟺ E=3. No single forward grade (one elapsed feeds both `s` and `ivl` via `fsrs.next_states`) can produce both. The interval went through an extra transform after the fuzz.

4. **Every one of the 26 single-grade stored intervals falls within TT's computed fuzz range `[lower, upper]`** — but differs from TT's fuzz *pick*. That is the load balancer's exact signature: it never leaves the fuzz window, it just chooses a less-loaded day inside it.

5. **`config['loadBalancerEnabled'] = b'true'`** in the collection.

6. **Direct proof** (`/tmp/lb_synth.py`): fresh synthetic collection, 400 review cards all due on the pure-fuzz target day (heavy load), one test card mimicking cid8's EASY outcome, controlled E=22. Load balancer **off → easy interval 59** (pure fuzz); **on → 61** (relocated within `[59,69]` to dodge the loaded day). Toggling the balancer moves the interval, on identical FSRS state.

**Mechanism.** Anki's `with_review_fuzz` (`rslib/.../states/fuzz.rs:36-42`) tries `load_balancer_ctx.find_interval(...)` *first* and only falls back to pure fuzz when the balancer is absent. The balancer is wired into both the live answer path (`answering/mod.rs:237-258`, populated when the study queue is built) and the "Reschedule cards on change" path (`fsrs/memory_state.rs:218` → `rescheduler.find_interval`, using `get_fuzz_seed(card, true)` = `reps−1`). It relocates each card's interval to the least-loaded day inside the fuzz window, using the **whole collection's** due-date histogram. The s↔ivl inconsistency resolves cleanly under Optimize+Reschedule: recompute rewrites `cards.data.s` via full-history replay (a sub-0.01 nudge TT also reproduces), while reschedule rewrites `cards.ivl = loadbalance(fuzz(next_interval(recomputed_s), seed = id + reps − 1))` — and the load-balance step is the visible ±1–2 days.

**Update (2026-05): the balancer WAS ported, bit-exact.** The "not reproducible / Path-2-class" call below was the *parity* decision (sync reads `cards.due`, so no fix is needed for sync correctness). As a follow-up the balancer was nonetheless ported and proven bit-exact — see `app/srs/anki_mirror/load_balancer.py`, `_anki_rng.py` (`uniform_f32_sample`/`weighted_index_sample`), the `load_balancer` param on `schedule()`, and `test_parity_load_balancer.py`. Oracle sweep: 24/24 bit-exact (incl. the `f32::powf(2.15)` term); real-data replay within-1h **58/89 → 81/89**. Plan: `~/.claude/plans/delegated-puzzling-bee.md`. The "structurally unreproducible" framing was wrong — for the single-preset Slovene deck TT holds the entire histogram. The points below remain true as the *parity* rationale (sync still pass-through; production live-grading port is Phase 2).

**Resolution (parity) — no TT code fix required.**

- **Not needed for parity.** The balancer's pick depends on global collection state; mirroring it for sync would be **Path-2-class** (a live due-date histogram over all of TT's `collocation_directions`) — but sync doesn't need it (next point). It was ported anyway for forward-step fidelity / future TT-native grading (above).
- **Production parity is unaffected.** `sync_pull` reads `cards.due` directly (rule: `due_at` is pass-through from Anki), so synced cards get Anki's load-balanced due date verbatim. The only place TT's pure-fuzz interval is user-visible is a **TT-native grade** of a card the user never re-grades in Anki — a cosmetic ±1–2 day spread, not a parity break.
- **The Stage-3b "due_at within 1h" stat was measuring the wrong thing** for this collection: it compared forward-step-replay `due_at` against synced (load-balanced) `due_at`. With the balancer on it can never hit 100%, and that ceiling is expected. It should be read as a fuzz-*range* reliability check.

**Files.** `backend/app/anki/measure_stage3b_premise.py` (new `_detect_load_balancer`; surfaces a prominent caveat in the report when `loadBalancerEnabled` is set; coverage-omitted diagnostic). `docs/archive/stage-3b-empirical-measurement.md` (residual attribution corrected from `recompute_memory_state` to the load balancer). `docs/anki-parity-layers.md` (this entry). No production scheduling code changed — TT's FSRS pipeline is verified correct.

**Pre-Layer checklist note.** Step 1 named the divergence (a queue/interval *position*, not a stability/difficulty number). Step 2 found the relevant helper is `_passing_intervals_with_fuzz` / `_constrained_fuzz_bounds` — and the drill confirmed those compute the correct fuzz *range*; the missing piece lives entirely outside TT's model (global due histogram). That is the signal to **stop** rather than extend a helper: the right output already exists, the divergence is a not-mirrored Anki subsystem whose mirroring complexity is unbounded.

**Cross-reference.** Mirrors the Layer 43 pattern — a residual previously chalked up to one mechanism (there, Layer 38's "NULL-R at dr"; here, `recompute_memory_state`) turned out to be a coincidence of input regime once the binary was driven with controlled inputs. The "trust the binary, not the source" rule (queue-parity rule 13) and the harness rule's "vary the inputs Anki touches" both fired: the breakthrough was forcing `days_elapsed` via a synthetic `last_review_time` and pinning the fuzz seed via the real `anki_card_id`, which removed the wall-clock confound that had made every prior oracle re-answer look like a stability mismatch.

## Layer 54 — The col-day helpers are NOT inverses (and that's fine): a ground-truthed non-bug

**Surfaced**: load-balancer replay drilling (2026-05-24, post Layer 53). `review_due_at_for_col_day(col_crt, N)` (`app/srs/anki_mirror/protobuf_wire.py:205`) and `compute_anki_day_index(col_crt, 4, now)` (same module) are not round-trip inverses: `N → datetime → N − 1` for every N (verified N=4521/4524/4525/4600 against `col_crt=1388836800`). Layer 49's own cross-reference had called them inverses (corrected above). The lead was `_review_due_at_from_interval` (`app/srs/fsrs.py:65`, the live review-grade path), which *crosses* them — `today = compute_anki_day_index(now)` then `review_due_at_for_col_day(today + interval)` — raising the question: does a TT-native review grade store a `due_at` a full day off from what Anki surfaces for the same `cards.due`?

**Ground truth (the decisive step — taken before any fix).** Opened the real collection with `uv run --with anki` (Anki closed) and read Anki's own scheduler:

```
col.crt          = 1388836800   (2014-01-04 12:00 UTC; created at UTC-5, machine now EDT)
col.sched.today  = 4523          (days_elapsed at 2026-05-24 18:53 UTC)
next_day_at      = 1779696000    (2026-05-25 08:00 UTC = 4am EDT)
rollover=4  creationOffset=300(UTC-5)  localOffset=240(UTC-4, EDT)
```

So col_day `N` surfaces at **4am-LOCAL** on calendar date `2026-05-24 + (N − 4523)` — i.e. 08:00 UTC during EDT. Anki's `days_elapsed` is **calendar-date-based in the local timezone** (`rslib/.../scheduler/timing.rs:69-81`: `end_date.num_days_from_ce() − start_date.num_days_from_ce()`, minus 1 pre-rollover), NOT raw timestamp arithmetic.

**Verdict: NOT a production bug — a latent inconsistency that cancels in every real path.**

1. `compute_anki_day_index(now) == 4523 ==` Anki's `today` **exactly**. The helper is correct (its day boundary, `col_crt − 4h = 08:00 UTC`, coincides with the true 4am-EDT rollover for this user).
2. `review_due_at_for_col_day(N)` returns the **correct calendar date**; only the time-of-day is `04:00 UTC` (Layer 49's anchor) versus the true local rollover `08:00 UTC` — 4h early, **same date**.
3. `DirectionState.due_date == due_at.date()` (`app/models/srs_item.py:185`), the live "due-today" field, is therefore the **correct calendar date**, advancing by exactly `interval` days.
4. The TT-native grade path (`_review_due_at_from_interval`) and the sync writeback path (`compute_due_at`) **both** call `review_due_at_for_col_day`, so they produce byte-identical `due_at` — verified for intervals 1/3/21/77. `_direction_differs` never sees a spurious diff; there is no native-vs-synced discrepancy. The only observable effect is a 4-hour window (midnight–4am EDT, pre-rollover) where TT shows a card due slightly before Anki — uniform across all TT cards, well within "drift between syncs is bounded and acceptable."
5. **No production path feeds a `due_at` back into `compute_anki_day_index`.** The only inversion lives in diagnostic replay code — `measure_stage3b_premise.py:311` (`compute_anki_day_index(col_crt, 4, dv.due_at) − today`), which yields `interval − 1` and is exactly what raised this false alarm. It is a side-stat (the load-balancer histogram offset; `due_at` is excluded from MATCH classification per Layer 53), so it does not affect the proven bit-exact stability/difficulty result. **Flagged, not fixed here** — it lives inside the pending load-balancer work; correct it within that change set by staying in the datetime domain (`(dv.due_at.date() − review_due_at_for_col_day(col_crt, today).date()).days`) if the histogram offset matters.

**Two consistent domains, deliberately not inverses.**

| Domain | Helpers | Property |
|---|---|---|
| **Index** (col-day integer) | `compute_anki_day_index` + `_compute_last_review` (Layer 45) + `_interval_from_state` (Layer 51) | round-trips internally; bit-exact with Anki `today` |
| **Datetime** (`due_at`) | `review_due_at_for_col_day` + `compute_due_at` + `_review_due_at_from_interval` | 04:00-UTC anchored; correct date; self-consistent across all writers |

**Why no fix.** Making `review_due_at_for_col_day` land on the true local rollover (so the round-trip is clean) requires threading the local UTC offset through `compute_due_at`, `_review_due_at_from_interval`, and `schedule()`, and would shift **every** stored `due_at` by that offset — a one-time mass sync write-back (`_direction_differs` fires on all review cards) for **zero** correctness gain, since the calendar date is already right. This is precisely the over-eager-Layer-fix the Pre-Layer checklist warns against: Step 1 named the divergence (a `due_at` time-of-day, not a date), Step 2 found both helpers already correct in their own domain, ground truth confirmed production cancels — **stop**.

**Layers 49/50 re-checked.** Layer 49 (04:00-UTC due_at anchor) holds; its "inverts" cross-reference was imprecise and is corrected above. Layer 50 (integer col-day elapsed via `compute_anki_day_index` *differences*) holds — it lives entirely in the Index domain and never crosses. Layer 45 (`_compute_last_review` built to satisfy `compute_anki_day_index(result) == review_col_day`) and Layer 51 (`_interval_from_state` via `compute_anki_day_index(last_review)`) are both Index-domain and consistent.

**Files.** `backend/tests/test_colday_helper_consistency.py` (new — pins ground truth: `compute_anki_day_index == 4523`, date correctness, native==sync self-consistency, and the intentional off-by-one round-trip as a guard). `docs/anki-parity-layers.md` (this entry + Layer 49 correction). No production code changed.

**Cross-reference.** Same investigation shape as Layer 43/53: a residual chalked up to a bug turned out to be a coincidence of convention once the binary was driven with controlled inputs (queue-parity rule 13, "trust the binary"). The decisive move was reading Anki's own `col.sched.today` / `next_day_at` rather than reasoning about the helpers in isolation.

## Layer 55 — Wire the FSRS load balancer into TT's live grade path

**What changed.** TT-native grades now load-balance like Anki when `loadBalancerEnabled` is set. This consumes the bit-exact port from Layer 53 (`app/srs/anki_mirror/load_balancer.py`, proven oracle + per-card real-deck). Layer 53 only read Anki's load-balanced `cards.due` at sync (`sync_pull` pass-through); a card *graded in TT* still got the pure-fuzz interval. Layer 55 builds a live balancer from TT state and threads it into the three `schedule()` call sites.

**Worth-doing check (done first, per the task's own gate).** Phase 2 only affects TT-native grades — synced cards take Anki's `cards.due` verbatim regardless. Measured the grading surface: of 14,467 `tt_revlog` rows, 13,867 (95.8%) are exact-id imports from Anki and only 600 (4.1%) are TT-native — and those 600 fall on exactly two days (2026-05-20/21), vs 200–700 Anki grades *every* day. So the feature is a UX nicety for a path used 2 days in 2 months. Built anyway at the user's explicit direction; the cost is low because the port and the `schedule(load_balancer=…)` threading already existed.

**Mechanism.**
- **`LoadBalancer.bury_reviews` flag (correctness fix).** Anki only feeds `note_id` into the sibling modifier when the deck buries reviews (`answering/mod.rs:247`, `.then_some(note_id)`). The Layer-53 wiring passed `item.anki_note_id` unconditionally. `LoadBalancer(__init__, *, bury_reviews=True)` now drops `note_id` inside `find_interval` when off. (The user's Slovene deck has `bury_reviews=true`, so this was latent.)
- **Config resolvers** (`queue_stats.py`): `resolve_load_balancer_enabled` (config-table `loadBalancerEnabled`, like `fsrsShortTermWithStepsEnabled`) and `resolve_easy_days` (deck_config protobuf **field 4** `easy_days_percentages`, packed f32 — same encoding as learn/relearn steps). Both refreshed at sync (`api/anki.py`).
- **`build_live_load_balancer(db, *, now, col_crt)`** (`queue_stats.py`): the histogram is `db.get_load_balancer_histogram(today, 99)` — all directions with `anki_due ∈ [today, today+99)`, bucketed by `anki_due − today`, carrying `anki_note_id` (mirrors `get_all_cards_due_in_range`, no queue filter). `today = compute_anki_day_index(col_crt, 4, now)`, `next_day_at = col_crt + (today+1)*86400`. Returns `None` (→ pure fuzz, zero change) when LB off or `col.crt` not yet synced.
- **Session model = build-once + replay.** A TT grade moves `due_at` but **not** `anki_due` (frozen at sync), so this session's grades are absent from the `anki_due` histogram. `db.get_load_balancer_session_replay()` re-adds each `dirty_fsrs=1` direction at its latest `tt_revlog.interval` (the never-remove "stale day-0 entry stays + new position added" semantics). Within a single `/listen` request the same balancer is threaded through every `schedule()` and `_balancer_add`'d after each grade, so later grades see earlier ones — exactly Anki's per-answer `load_balancer.add_card`.
- **Wiring**: `drill_feedback` + the two `/listen` grade paths build the balancer once per request, pass `load_balancer=balancer`, and `_balancer_add(...)` the graded card.

**Single-preset invariant + col-day domain (the two gotchas).** Bit-exactness depends on `0. Slovene` being the only deck on its preset (so TT's `collocation_directions` IS the whole same-preset histogram). `warn_if_multi_deck_preset` logs a WARNING at sync if a second deck joins the preset. Histogram offsets stay in the **index domain** (`anki_due − today`) — never round-trip a `due_at` datetime through `compute_anki_day_index` (Layer 54's flagged side-stat; this build deliberately avoids that inversion). Fuzz seed reused from `schedule()` is `(anki_card_id or 0) + reps`.

**Files.** `app/srs/anki_mirror/load_balancer.py` (`bury_reviews` flag), `app/srs/anki_mirror/queue_stats.py` (resolvers + `build_live_load_balancer` + `warn_if_multi_deck_preset`), `app/srs/database.py` (`get_load_balancer_histogram`, `get_load_balancer_session_replay`), `app/api/srs.py` (`_balancer_add` + 3 wiring points), `app/api/anki.py` (sync-time refresh + warn). Tests: `tests/test_load_balancer.py::TestBuryReviewsGating`, `tests/test_queue_stats_load_balancer.py` (resolvers + builder + warn), `tests/test_api_srs_directions.py::TestDrillLoadBalancerWiring`, `tests/test_parity_load_balancer.py::test_live_builder_matches_anki` (oracle: TT live builder reproduces Anki's relocated interval bit-exact).

**Non-mirror reminder (unchanged).** Synced cards are still read straight from Anki's load-balanced `cards.due` at `sync_pull`. The cosmetic ±1–2 day `due_at` residual from `.claude/rules/anki-queue-parity.md` ("already-decided non-mirror") applies only to a *TT-native grade never re-graded in Anki* — which Layer 55 now also balances, so even that residual shrinks for the single-preset deck.

## Layer 56 — Review badge mirrors sibling-bury for interday-learning siblings, not just "graded today"

**The bug.** TT's review badge read 214 while Anki's deck overview read 208 on the same synced data. No data divergence — both apps agreed exactly: 243 review-due cards (`queue=2, due<=today`) across 214 distinct notes, identical `anki_card_id` sets. The gap was a counting-convention incompleteness in `count_review_due_collocations` (the badge query, `database.py`).

**Mechanism.** Anki's `bury_reviews=true` buries a note's *review* card whenever a sibling is in the learning queue (`queue=1/3`) — **including interday learning steps graded on a prior day**. Rule 3's TT mirror only excluded collocations with a direction `last_review`'d *today*. The 6 over-counted notes (*gor, levo, sever, jug, ponedeljek, smer*) each had a review-due direction plus a sibling stuck in learning from an earlier day (graded 05-24, observed 05-26) — past the "today" window, so TT kept counting them. `214 − 6 = 208`.

**Fix.** Extend the badge's exclusion subquery with `OR state IN ('learning', 'relearning')`, so a collocation drops out of the review pool when any direction sits in a learning queue regardless of last-grade date. The "graded today" filter stays (it still handles the same-day review→review sibling case). New-sibling bury is deliberately **not** mirrored — the measured 214→208 gap was learning-only, and `count_review_due_collocations` keeps counting collocations with a NEW sibling (matches the data: no NEW-sibling over-count was observed).

**Dead-code removal.** `count_review_due` (per-direction `COUNT(*)`, distinct from the note-level `count_review_due_collocations`) had no production caller — only its own test. Deleted both. It was a footgun: a per-direction counter named one underscore-segment away from the real badge helper, exactly the shape that reintroduces the double-count-per-note bug if wired to the badge.

**Files.** `app/srs/database.py` (`count_review_due_collocations` subquery + docstring; removed `count_review_due`). Tests: `tests/test_srs_database.py::TestQueueStatHelpers::test_count_review_due_collocations_excludes_learning_sibling` (7 cases pinning learning/relearning-sibling exclusion, NEW-sibling retention, mixed pool); removed `test_count_review_due`. Verified on the live deck: badge 214 → 208, exact Anki match.

**Surfaced by.** The Stage 3b compare-soak kickoff (2026-05-26) — user noticed the 214/208 badge delta while running the first compare-mode sync. Unrelated to the soak's shadow-column finding (a `schedule()` graduation-arc divergence); this is a pure queue-parity badge fix.

## Layer 57 — Interday LEARNING→REVIEW graduation must use the recall formula, not short-term

**The bug.** A card that graduated from a learning step on a *later day* than its last grade got a stability ~9x too low. Live finding (Stage 3b compare-soak, poletje/production): NEW → AGAIN → AGAIN → GOOD same-day (2026-05-24), then EASY the next day (2026-05-25) graduated it. Anki stored `s=4.4106`; TT's replay computed `s=0.6699`.

**Mechanism.** fsrs-rs `step` (`model.rs:159-166`) applies the `stability_short_term` override ONLY when `delta_t == 0` (same calendar/col-day); for `delta_t > 0` the memory state routes through `stability_after_success` (passing) / `stability_after_failure` (AGAIN) with the actual retrievability. TT's `_graduate_to_review` and `_schedule_with_steps` hard-coded `_stability_short_term` for every non-NEW learning grade, on the assumption that sub-day learning steps are always same-day. That holds for a card graded repeatedly within one session, but NOT when a learning card ripens and is graded the next day — exactly the EASY graduation poletje hit. (The synthetic-collection builder documents the same pivot: "Without `lrt` Anki's `next_states()` sees elapsed=0 and uses `stability_short_term` instead of `stability_after_success`.")

**Fix.** New helper `_next_stability_for_grade(prev, rating, last_review_dt, params, col_crt)` mirrors fsrs-rs `step`: integer col-day `delta_t` via Layer 50's `_grade_elapsed_days`; `delta_t == 0` → short-term; else recall (passing) / lapse (AGAIN) at `r = forgetting_curve(delta_t, s)`. Both `_schedule_with_steps` (stay-in-learning memory update) and `_graduate_to_review` (graduation `new_stability` + the HARD/GOOD/EASY interval cascade) call it. Difficulty was already correct (rating-only, time-independent) and is untouched. Same-day grades are unchanged (delta_t==0 → short-term as before).

**Scope.** Affects the **live TT-native grade path** (where `schedule()` is the only source of truth) and the Stage 3b **incremental shadow replay** (which calls `schedule()` from the stored state). It does NOT change synced cards — those take Anki's `cards.data` verbatim at sync. The live from-scratch replay of poletje still differs (from-scratch is the non-viable Stage 3a path — recompute_memory_state etc.); the incremental-from-stored-state path the soak uses now reproduces Anki.

**Files.** `app/srs/fsrs.py` (`_next_stability_for_grade` + 3 call sites: `_schedule_with_steps`, `_graduate_to_review` new_stability, `_graduate_to_review` cascade). Oracle parity tests live on the Stage 3b branch (`tests/test_parity_transitions.py`): `test_parity_interday_learning_graduation`, `test_parity_interday_learning_again`, `test_parity_graduation_after_many_agains` — all bit-exact against Anki's V3Scheduler; they land on main when Stage 3b merges.

**Surfaced by.** Stage 3b compare-soak first sync (2026-05-26): 1/1349 shadow-column divergence, hand-sampled to this `schedule()` bug (not a recompute event). See `.claude/rules/anki-queue-parity.md` divergence-playbook escalation path.

## Layer 58 — Revlog ingest must reconcile against Anki, not trust a wall-clock watermark (interior sync-gap grades)

**The bug.** Stage 3b compare-soak (2026-05-27): 2/1349 shadow-column divergences (`gor`, `zahod`), both with replay stability *lower* than Anki (gor 4.39 vs 5.28; zahod 0.37 vs 0.61). NOT an FSRS bug — Layer 57's `schedule()` is bit-exact. Each card was **missing one grade in `tt_revlog`**: a GOOD graded 2026-05-25 21:50 local that exists in Anki's revlog. Per-day audit: 05-24/25/27 ingest gap=0; 05-26 = 2 grades in Anki, **0 in tt_revlog** (exactly these two). The grade was made during a ~41h sync gap (last sync 05-24 21:06 → next 05-26 14:49 local). A missing stability-raising GOOD made the event-sourced replay understate. The **legacy** path is immune — it takes Anki's `cards.data.s` verbatim and never depends on revlog completeness — so this only blocks the compare→new flip.

**Mechanism.** `_ingest_anki_revlog_for_card` harvested Anki revlog with `get_revlog_for_card(cid, after_ms=last_synced_at)` → `id > last_synced_at`, a wall-clock high-water mark. Once that watermark advances past a not-yet-ingested grade, the grade is skipped **permanently**. Critically the gap is **interior** (gor held its 05-23 and 05-26 grades but not the 05-25 one between them), so no tail-watermark scheme recovers it. The replay (`rebuild_from_revlog`) is only as complete as `tt_revlog`, and the ingest provided no completeness guarantee or reconciliation.

**Fix.** Drop the watermark. `_ingest_anki_revlog_for_card` now reads the card's **full** Anki revlog and reconciles against the ids already held (`SRSDatabase.get_tt_revlog_ids(collocation_id, direction)`): skip ids already present, else `has_revision_near` dedup (TT-native-grade ±5s same-ease case) then `append_revlog` (INSERT OR IGNORE on the global `id` PK). The held-id set keeps it cheap — only genuinely-new rows touch the DB, so no per-row commit storm despite the deck's ~14.7k revlog rows (avg 11/card, max 78). The `last_synced_at` parameter is removed from the signature; the column itself is untouched (still a merge field). Verified end-to-end on the live snapshot: reconciling ingest backfills the 05-25 grade (gor 2→3, zahod 4→5 rows) and `rebuild_from_revlog` then reproduces Anki bit-exact (gor s=5.2841 d=7.1590 review; zahod s=0.6105 d=8.4930 learning).

**Scope.** Prerequisite for flipping `event_sync_pull` compare→new — the legacy path masks the gap, the new path would persist the understated stability. No effect on legacy/synced cards (still verbatim `cards.data`).

**Files.** `app/plugins/anki_sync/sync.py` (`_ingest_anki_revlog_for_card` reconcile + caller), `app/srs/database.py` (`get_tt_revlog_ids`). Tests: `tests/test_anki_sync_pull.py::TestSyncPullIngestsAnkiRevlogIntoTtRevlog::{test_ingest_ignores_last_synced_at_and_backfills_older_rows, test_interior_revlog_gap_is_backfilled}` (the latter pins the exact incident shape), `tests/test_srs_database.py::TestRevlog::{test_get_tt_revlog_ids_returns_held_ids_for_direction, test_get_tt_revlog_ids_empty_when_none}`.

**Surfaced by.** Stage 3b compare-soak daily check (2026-05-27): shadow-vs-authoritative diff, root-caused via per-day Anki-vs-tt_revlog grade audit + backfilled `rebuild_from_revlog` reproduction.

## Layer 59 — FSRS arithmetic in f32 with fsrs-rs op order (eliminates 4dp quantization false positives)

**The bug.** Stage 3b compare-soak (2026-05-28): 3/1349 shadow-column stability divergences (`baker`, `enaindvajset`, `ventilator`), all with delta = exactly **±0.0001** — single ULPs at 4-decimal storage precision. Production matched Anki's `cards.data.s` bit-exact on every row; only the *replayed* value diverged. Suspected mechanism (then unverified): f64 Python (TT's FSRS) vs f32 Rust (fsrs-rs); ground-truthed by an isolated `fsrs_rs_python.FSRS.next_states` call returning 88.4278182983 where TT's f64 path returned 88.4277496338 for the same `(s, d, elapsed, rating)`.

**Mechanism — three distinct sources of 4dp drift.** Each turned out to matter, found in this order:

1. **f64 vs f32 precision.** fsrs-rs uses `Tensor<B, 1>` over Burn's f32 backend end-to-end (model.rs); TT's `_next_stability_recall`, `_next_stability_lapse`, `_stability_short_term`, `_next_difficulty`, `_forgetting_curve`, `_next_interval`, `_next_interval_raw` were pure-Python `math.exp` / `**` operating on Python f64. A numpy sweep at baker's stability scale showed 20/35 inputs produced a 4dp disagreement between f64 and f32 — exactly the false-positive rate observed.

2. **FACTOR constant — precomputed vs `exp(ln(0.9) / decay) - 1`.** TT's `FACTOR = 19/81 ≈ 0.234567901` was a precomputed approximation of fsrs-rs's `factor = decay.powi_scalar(-1).mul_scalar(0.9f32.ln()).exp() - 1.0` (≈ 0.234567890). Differ by ~1.1e-8 — beneath f64 noise but exactly 1 ULP at f32. The forgetting-curve op order also differs: TT did `(1 + FACTOR * elapsed / s)^decay`, fsrs-rs does `(t / s * factor + 1.0).powf(decay)`. Multiplication-vs-division order matters at f32 ULP.

3. **`linear_damping` op order in `next_difficulty`.** fsrs-rs: `old_d.neg().add_scalar(10.0) * delta_d.div_scalar(9.0)` — divides `delta_d / 9` BEFORE multiplying. TT: `(10 - d) / 9 * delta_d` — divides `(10 - d) / 9` first. Mathematically equivalent, but the f32 ULP lands differently for d=5.0 HARD/EASY (the most common difficulty bracket). Surfaced when `test_parity_fsrs_f32` was added; without that test the divergence would only show as 4dp-rounded difficulty drift on a small fraction of high-grade cards.

4. **Storage rounding direction.** Anki's `round_to_places(value, 4)` (rslib/src/storage/card/data.rs:80-83) does `(value * 10_000.0).round() / 10_000.0` in f32 with `f32::round` = half-away-from-zero. TT's `_quantize_stability` used Python's `round(s, 4)` (banker's rounding on f64). On exact-half ties (`x * 10000.0 == .5 exactly`), banker's rounds to even, half-away rounds away from zero — opposite directions, 1 ULP at 4dp. The interday LEARNING→REVIEW graduation case hit this: f32 raw = 28.7004489899; Anki's path produced 28.7005, Python's `round(28.7004489899, 4) = 28.7004`.

**Fix.** Numpy migration (`numpy>=2.0.0` added to backend deps):
- `_F32 = np.float32` and `_w32(w)` cast weights to f32 per call;
- `_forgetting_curve` rewritten with fsrs-rs's exact factor + op order: `factor = exp(log(_F32(0.9)) / decay) - 1`, formula `(e / s * factor + 1)^decay`;
- `_next_stability_recall` / `_next_stability_lapse` match fsrs-rs's `-d + 11` and `-r + 1` op order (no behavior change in f64, 1 ULP in f32);
- `_next_difficulty` uses `(-d32 + 10) * (delta_d / 9)` for linear damping and unclamps `init_difficulty(EASY)` inside mean-reversion (only clamping the final post-MR result);
- `_next_interval` / `_next_interval_raw` use the same `factor` formula;
- `_quantize_stability` / `_quantize_difficulty` route through a new `_round_to_places_f32` helper that applies Rust's half-away-from-zero rounding on the f32 scaled value, then collapses to a clean f64 4dp representation (so the stored Python float reads as a tidy `28.7005`, matching Anki's JSON serialization, while preserving Anki's rounding direction).

**Validation.** New parity test `tests/test_parity_fsrs_f32.py` pins TT bit-exact against `fsrs_rs_python.FSRS.next_states` across 6 (s, d, elapsed) inputs × 4 ratings (recall + lapse + difficulty + forgetting-curve cascade — 19 assertions). `./test.sh` green (2587 backend + 21 frontend gate + 11 E2E); the parity oracle harness (`--run-oracle`) is green (43 passed); both previously-passing tests (`test_parity_interday_learning_graduation`, `test_parity_review_hard_high_stability`) now match Anki by the *correct* path (was: by f64-numerical-coincidence at boundary cases). Empirical soak validation pending the next sync — by construction, with bit-exact f32 + correct rounding, the `stability_replayed` divergence count should drop to literal 0 once shadow columns refresh.

**Files.** `app/srs/fsrs.py` (the seven arithmetic helpers + `_round_to_places_f32`), `pyproject.toml` (numpy + fsrs-rs-python). Tests: `tests/test_parity_fsrs_f32.py` (new).

**Surfaced by.** 2026-05-28 morning sync — three 4dp-precision shadow divergences with prod==`card.data` bit-exact on all three. The pre-Layer-59 discriminator (`project_stage3b_soak_finding_floor_stability`) classified these as "benign quantization ULP" and recommended classifier refinement; this layer is the structural fix instead.

**Update 2026-05-30 — soak confirmed + test made architecture-aware.**
- **Soak validated:** compare-shadow `stability_replayed` divergence = **0 / 1349**, and `fsrs_difficulty_replayed` = **0 / 1349** too (the transient 05-21 restore difficulty cohort aged out, 104→6→0), across three same-day syncs with 422 recently-graded directions all bit-exact. The "pending" note above is resolved.
- **`test_parity_fsrs_f32` no longer asserts raw f32 `==`.** numpy's f32 `expf`/`logf`/`powf` are bit-reproducible with fsrs-rs's Rust libm only on the **deploy arch (Apple-Silicon arm64)**; on x86 CI they differ by ~1 ULP (the test's first CI run failed 7 cases). Since a real op-order/FACTOR regression is *also* ~1 ULP, the precision pin is only meaningful where transcendental noise is zero. The test now compares at **Anki's storage precision** (`_quantize_stability` 4dp / `_quantize_difficulty` 3dp) and is **architecture-aware**: strict storage-exactness on arm64 (enforced by local `./test.sh` pre-commit), gross-error tolerance (`rel_tol=1e-4`) on x86 CI — the same "precision-pin is local" stance as the oracle harness. This still catches any ≥0.0001/≥0.001 regression. **Do NOT revert to raw `==`** — it's arch-fragile, not a stronger guarantee. (commits `2f47d45`, `b53f05d`; rationale in `project_fsrs_f32_migration_layer59`.)

## Layer 60 — revlog ingest dedup is provenance-aware (rapid same-ease Anki grades no longer collapse)

**The bug.** Live 2026-05-29: after recovering a phone study session via Anki Download, a normal `sync_pull` ingested 81 of 83 Slovene grades into `tt_revlog`. Two were silently dropped — both the *final* "Good" of a fast learning sequence: samoglasnik (coll 467, `…22:38:50 ease3, 22:38:53 ease3` → 22:38:53 lost) and pridevnik (coll 156, `…22:38:47 ease3, 22:38:52 ease3` → 22:38:52 lost). The dropped grade in each case was a genuine, distinct Anki review 3–5s after an identical-ease grade.

**Mechanism.** `_ingest_anki_revlog_for_card`'s near-match guard (`has_revision_near`: same coll+dir, ±5s, same `button_chosen`) exists to suppress a TT-*written* grade's Anki copy — TT writes its row at grade time with `id = now_ms`, then `OfflineWriter.write_revlog` pushes the Anki copy with `rid = max(preferred_id, max_id + 1)`, which can **bump** the Anki id off the TT grade time, so exact-id dedup (`get_tt_revlog_ids`) misses it and the ±5s fuzzy match is needed. But the guard matched on *any* nearby `tt_revlog` row, including one **just ingested from Anki in the same pass**: when processing 22:38:53, it found the already-ingested 22:38:50 (same ease, 3s away) and skipped 22:38:53 as a "duplicate." Two real Anki grades thus collapsed into one — understating the event-sourced replay (the dropped row is a real review affecting stability/difficulty). Live scheduling was unaffected because the direction merge takes Anki's `cards.data` directly (467/156 verified bit-exact: s=0.0045/0.002), so the loss is replay-only.

**The discriminator.** An *already-ingested Anki row* has `tt_revlog.id == ` a real `revlog.id` for that card (ingest copies the Anki id verbatim, `RevlogRow(id=r["id"])`). A *TT-written* row's id is its grade-time ms and is **never** in the card's Anki revlog (its mirror got bumped — that's the whole reason the guard exists). So id-provenance cleanly separates the two: only a row whose id is **not** one of this card's Anki revlog ids may suppress.

**Fix.** `has_revision_near` gains an `ignore_ids: set[int] | None` param (`AND id NOT IN (…)`); `_ingest_anki_revlog_for_card` computes `anki_ids = {r["id"] for r in rows}` once and passes it. An Anki-origin near row is now excluded as a suppressor; a genuine TT-written near row (id ∉ `anki_ids`) still suppresses. Empirically (live snapshot, post-sync): recovers **98** dropped grades (65 with ≥1s gaps — unambiguous rapid grades like 467/156; 33 sub-second Anki-origin near-dups Anki itself replays) while **preserving all 356** legitimate TT-written↔Anki-mirror dedups. The discriminator is exact-set membership, not a tunable threshold.

**Validation.** `./test.sh`-equivalent backend gate green (2590 passed, 100% coverage). New tests: `tests/test_anki_sync_pull.py::TestSyncPullIngestsAnkiRevlogIntoTtRevlog::test_ingest_keeps_distinct_anki_grades_within_5s_same_ease` (two Anki grades 3s apart, same ease → both ingest) and `tests/test_srs_database.py::TestRevlog::test_has_revision_near_ignore_ids_excludes_anki_origin_rows`; `test_skips_anki_row_that_duplicates_tt_grade` still pins the TT-written-mirror suppression.

**Files.** `app/srs/database.py` (`has_revision_near` + `ignore_ids`), `app/plugins/anki_sync/sync.py` (`_ingest_anki_revlog_for_card` passes `anki_ids`).

**Surfaced by.** 2026-05-29 forced-full-sync recovery (`project_forced_full_sync_revlog_risk`): a Download-recovered phone session whose two dropped grades, traced through `tt_revlog`, exposed the same-ease rapid-grade collapse. Replay-only impact; matters when the stage3b compare-soak recomputes from `tt_revlog`.

## Layer 61 — `_bump_col` preserves `col.usn` (stop forcing AnkiWeb full syncs)

**The bug.** Multi-device users hit a spurious "your collection requires a full sync / Check Database" from AnkiWeb after a TunaTale sync, even with no schema change. Confirmed by controlled repro (2026-05-29): TT sync left the desktop at `col.usn = -1`; the phone (AnkiDroid) then advanced the server's USN; the desktop's next File→Sync demanded a full sync. `sync_trace.sh` timeline pinned it — `scm` frozen through the TT sync *and* through opening Anki, then bumped *during* the AnkiWeb sync (Anki's `set_schema_modified`, not TT). The forced sync risks data loss if the user picks Upload (it would erase the other device's grades).

**Mechanism.** `OfflineWriter._bump_col` ran `UPDATE col SET mod = ?, usn = -1` after every batch write (per the old `anki-sync.md` rule). But `col.usn` is the sync **anchor** — the server's last USN — not a per-row dirty flag. In the modern schema the `col` row's content columns (`conf`/`models`/`decks`/…) are empty (those live in their own tables with their own USNs), so the `col` row carries nothing to push; only its `mod` matters for change detection. Setting `col.usn = -1` is invisible single-device (the server only ever advances via that one desktop), but when another device moves the server's USN ahead, the desktop arriving with `col.usn = -1` can't be reconciled incrementally at the meta handshake → AnkiWeb returns FULL_SYNC.

**Fix.** `_bump_col` now does `UPDATE col SET mod = ?` only — preserving `col.usn`. Content rows (cards/notes/revlog/decks) still carry their own `usn = -1` and push normally; `col.mod` still tells Anki the collection changed. Mirrors what Anki itself does on a local edit. The one-shot migration scripts that still write `col.usn = -1` are out of scope — they bump `col.scm` and intentionally force a one-way sync regardless.

**Validation.** Backend suite green (2554 passed, 100% coverage); five col-usn assertions flipped from `== -1` to "preserved" (set a known anchor, assert unchanged): `tests/test_anki_sync_push.py::TestOfflineWriter::{test_write_revlog_bumps_col_mod_preserves_usn, test_update_note_fields_replaces_named_field_and_bumps_usn, <bury_siblings>}`, `tests/test_anki_offline_writer_create_note.py::…::test_bumps_col_mod_preserves_usn`, `tests/test_anki_sync_offline_writer.py::TestBumpDeckNewToday::test_inserts_field_when_absent`. Live validation: re-run the `~/.tunatale/sync_trace.sh` repro — `tonight-after-tt` should now show a *positive* `col.usn` instead of `-1`, and the multi-device File→Sync should go incremental instead of forcing.

**Files.** `app/plugins/anki_sync/sync.py` (`OfflineWriter._bump_col`), `.claude/rules/anki-sync.md` (rule corrected).

**Surfaced by.** 2026-05-29 user report ("why does it force a full sync after TunaTale?") → forensic timeline (`project_forced_full_sync_revlog_risk`) → controlled reproduction with `sync_trace.sh`.

## Layer 62 — REVIEW + passing same-day grade must use FSRS short-term stability, not recall

**The bug.** `schedule()`'s REVIEW + HARD/GOOD/EASY branch computed the post-grade stability with `_next_stability_recall` directly. But fsrs-rs `Model::step` (`fsrs-rs/src/model.rs:163`) overrides the success/failure stability with `stability_short_term` whenever `delta_t == 0` — for **every** rating, not just Again: `new_stability = new_stability.mask_where(delta_t.equal_elem(0), stability_short_term)`. So a same-day re-review of a REVIEW-state card (e.g. graded earlier today, or a cram/second session before the 4am col-day rollover) must use the short-term stability. TT's REVIEW+AGAIN path already did this (via `_schedule_review_again`'s `elapsed==0` branch); the passing path forgot, and at `delta_t==0` `R≈1` makes the recall multiplier ≈0, so a same-day GOOD/EASY *barely moved* stability while Anki applied the (much larger) short-term bump.

**Why the soak never caught it — despite the code being DRY.** The compare-shadow replay *is* the same `schedule()` code (`rebuild_from_revlog` → `schedule`), but the soak runs it **incrementally**: `_write_compare_shadow` → `_replay_incremental` passes `starting_state = local_dir` (the last-synced `DirectionState`, = Anki's authoritative `cards.data.s`) and `since_id`, so it forward-steps only the revlog rows added since the last sync. Already-synced same-day double-grades are baked into the anchor and never re-derived through `schedule`. A **from-scratch** replay (`starting_state=None`) does diverge — reproduced on the live collection: card "How is Slovene…" graded EASY twice on 05-08 (01:50 and 02:31, both pre-rollover ⇒ `delta_t==0`) stored s=132.667 in Anki, while a full `schedule` replay produced **175.05**. The bug is live *between* syncs (TT's R-asc queue uses the wrong stability until the next sync re-anchors). 717 card-days in the live deck have a same-day passing review followed by another grade — a routine pattern for the listen-first/drill workflow.

**Fix.** Route the REVIEW passing cascade through `_next_stability_for_grade` (TT's fsrs-rs `step()`-equivalent, which already selects short-term vs recall/lapse by `delta_t` and is what the learning-step and graduation paths use). For `delta_t > 0` it reduces to the prior `_next_stability_recall(prev.difficulty, prev.stability, r, …)` with the same integer-col-day `r` (Layer 50), so synced/interday grades are bit-identical — only the `delta_t==0` case changes. Removed the now-dead outer `last`/`elapsed`/`r` locals from the REVIEW branch.

**Validation.** New differential test `tests/test_parity_same_day_review.py` drives the public `schedule()` path on a same-day REVIEW re-grade and pins HARD/GOOD/EASY against `fsrs_rs_python.next_states(…, days_elapsed=0)` at 4dp storage precision (3 review states × 3 ratings = 9 assertions). Backend gate green (2604 passed, 100% coverage); oracle harness green (55 passed).

**Files.** `app/srs/fsrs.py` (`schedule` REVIEW branch). Tests: `tests/test_parity_same_day_review.py` (new). Workflow: `docs/anki-mirror-audit.md` F-1.

**Surfaced by.** 2026-05-30 inspection audit against anki 25.09.4 / fsrs-rs 5.1.0 (`docs/anki-mirror-audit.md`) — found by reading `model.rs:163`'s unconditional `delta_t==0` mask, not by a divergence report. The soak's incremental anchoring is precisely what hid it.

## Layer 63 — FSRS stability clamped to `[S_MIN, S_MAX]` like fsrs-rs `step`

**The bug.** fsrs-rs clamps *every* post-grade stability to `[S_MIN=0.001, S_MAX=36500.0]` inside `Model::step` (`fsrs-rs/src/model.rs:178`: `stability: new_s.clamp(S_MIN, S_MAX)`; constants at `fsrs-rs/src/simulation.rs:41-42`). TT clamped inconsistently: `max(0.001, …)` on the REVIEW-passing and graduation storage boundaries, **no clamp at all** on the lapse path (`_schedule_review_again`) and the learning-step path (`_schedule_with_steps`, via `_next_stability_for_grade`), and **never** the `S_MAX` upper bound anywhere.

**Reachability.** The lower bound is dormant-but-reachable: `stability_after_failure`'s own floor (`new_s_min = last_s / exp(w17·w18)`) drops *below* 0.001 once `last_s` is near the minimum-stability regime, so an Again on a floor card lands ≈0.0008 in TT but Anki clamps to 0.001 (verified against `fsrs_rs_python`: s=0.001 AGAIN → fsrs-rs 0.001, TT raw lapse 0.000801). On the live deck the lowest card (`taliti`, s≈0.0048, 7 lapses) is a handful of further lapses from tripping it. The upper bound is effectively unreachable (recall growth stalls as `s → S_MAX`; max live stability ≈310) but is mirrored for faithfulness.

**Fix.** Added `_S_MIN`/`_S_MAX` constants and `_clamp_stability(s)` (f32 `min(S_MAX, max(S_MIN, s))`, matching fsrs-rs's clamp). Applied at the two step-equivalent sites that produce the stored memory state: the return of `_next_stability_for_grade` (covers REVIEW-passing — post Layer 62 — plus learning-step and graduation cascades) and `_schedule_review_again` (the only stability path not routed through `_next_stability_for_grade`). No-op on all current data (everything in `[0.0048, 310]`), so the soak/transition pins are unchanged.

**Validation.** New test `tests/test_parity_stability_clamp.py`: `_clamp_stability` both bounds + passthrough, and a lapse-below-floor case pinned against `fsrs_rs_python` (S_MIN-clamped). Backend gate green (2604 passed, 100% coverage); oracle harness green (55 passed).

**Files.** `app/srs/fsrs.py` (`_clamp_stability` + `_next_stability_for_grade` + `_schedule_review_again`). Tests: `tests/test_parity_stability_clamp.py` (new). Workflow: `docs/anki-mirror-audit.md` F-2.

**Surfaced by.** 2026-05-30 inspection audit (`docs/anki-mirror-audit.md`) — found by comparing fsrs-rs's `clamp(S_MIN, S_MAX)` against TT's inconsistent `max(0.001, …)` write sites.

## Layer 64 — `new` badge mirrors Anki's new-sibling bury (`bury_new`)

**The bug.** The `new` badge (`/queue-stats`) read `db.count_new_available()`, a bury-unaware `COUNT(*) WHERE state='new'`. The *served* queue (`_compute_live_main`) already buries a new card whose sibling is gathered into today's pool — it seeds `seen_collocation_ids` from the review-due pool (`srs.py:1150`) *before* the new-bury pass (`:1151`), and excludes learning siblings (`:1137`) and graded-today collocations (`:1129`). So the queue served **0** new cards while the badge showed **2**: two dual notes (`soglasnik`, `taliti`) whose production direction had graduated to REVIEW (due today) but whose recognition direction was still NEW. Anki buries those recognition cards (`counts.new=0`); TT's badge over-counted them. Reported as "TunaTale shows 2 new, Anki shows 0; I think it's burying" — it was.

**The precise rule (ground-truthed against the binary).** Anki buries a new card at queue-build iff `bury_new` is set and a sibling was *already gathered* into today's queue. Gather order is intraday-learning → interday-learning → review → new (`builder/gathering.rs:14-21`); `add_new_card` buries a card whose note is already in `seen_note_ids` (`builder/burying.rs:75-93`). So the trigger is a sibling that is **review-due-today** or **learning** — **not** "any review sibling." Verified empirically: pushing the production sibling's `due` to the future flips Anki's `counts.new` 0 → 1 (the future-due review isn't gathered, so it doesn't register the note). This is why an approximate "has a review sibling" filter would be wrong (it would bury a new card whose only sibling is a far-future review Anki happily serves).

**Fix.** New `db.count_new_available_collocations(today)` — the mirror image of `count_review_due_collocations` — counts `COUNT(DISTINCT collocation_id)` over NEW directions, excluding collocations whose sibling is graded-today **OR** in learning/relearning **OR** `state='review' AND due_at <= end-of-today`. `DISTINCT` collapses a both-new note to one (Anki buries the 2nd new sibling, Layer 28). The badge endpoint uses it when `resolve_bury_new` is true, else falls back to the raw count (no regression for `bury_new=false` decks). `count_new_available()` stays as-is — it's still the upper bound for `_compute_live_main`'s per-direction new overfetch (`srs.py:1124`), which must not shrink. **The served queue was already correct; this only aligns the badge with it.**

**Validation.** First harness test to use a **2-template notetype** — the cross-direction scaffold `test_parity_bury.py` punted ("Phase 2.2.x if the need recurs"). Extended `SyntheticCollection` with `set_bury()` + the `bury_new`/`bury_reviews`/`bury_interday_learning` deck-config proto fields (27/28/29), defaulting false to keep existing blobs byte-identical. `tests/test_parity_new_sibling_bury.py` pins Anki's V3 scheduler `counts.new` against `count_new_available_collocations` for review-due-today (buried) vs future-due (survives) vs learning-sibling (buried). Live deck: badge **2 → 0**, matching Anki. Backend gate green (2625 passed, 100% coverage); oracle harness green (66 passed); full `./test.sh` green incl. the corrected `review-flow.spec.ts` badge (a both-new note → "3", not "6" — the same over-count as the report, surfaced by new+new siblings).

**Why not mirrored before.** Layer 56 deliberately did *not* mirror new-sibling bury for the **review** badge (the measured 214→208 gap was learning-only; no NEW-sibling over-count was observed *there*). That left the inverse — the *new* badge over-counting when a sibling graduated to review-due-today — unhandled, since `count_new_available` never had a sibling filter. Updates the `anki-queue-parity.md` divergence playbook (`new` badge bullet + Pre-Layer helper table).

**Files.** `app/srs/database.py` (`count_new_available_collocations`); `app/api/srs.py` (badge gating). Harness: `tests/anki_oracle/synthetic_collection.py` (`set_bury` + proto fields 27-29). Tests: `tests/test_srs_database.py::TestCountNewAvailableCollocations`, `tests/test_api_srs.py::TestQueueStats` (2 gating tests), `tests/test_parity_new_sibling_bury.py` (new).

**Surfaced by.** 2026-05-31 divergence report (TT new=2 vs Anki new=0, no sync since the production siblings graduated).

## Layer 65 — production held until recognition graduates; recognition introduced first (Phase 3)

**The bug.** For a paired both-NEW vocab note, TT introduced the **production** sibling before **recognition**. `_merge_directions` gathers new cards by `anki_due DESC` and `_bury` keeps the first-seen (higher-due) sibling; `create_note` gives production (ord 1) a higher `due` than recognition (ord 0), so production won the new slot. Layers 24/25/28 built this to "match Anki's HighestPosition gather," and a regression test asserted *"first new should be PRODUCTION (Anki parity)."*

**The empirical correction (rule 13 — trust the binary).** The user's actual Anki introduces **recognition** first: **604 recognition-first vs 36 production-first** across 640 paired notes in the live collection (`MIN(revlog.id)` per ord, per note). Anki is direction-agnostic — it orders new cards by deck position + template sort, and `create_note` places the recognition card at a lower position than production, so recognition surfaces first. The Layer 28 "production-first" premise was simply wrong (it mis-modeled Anki's gather/sort + sibling handling).

**The fix (Phase 3 of the word-learning state machine — `~/.claude/plans/word-learning-state-machine.md`).** A production NEW card is withheld from the new pool until its recognition sibling has graduated past the learning arc (recognition `state NOT IN ('new','learning','relearning')`). Implemented as a `NOT EXISTS` clause in `get_new_items`, **production direction only**; recognition is never gated; cloze notes (no recognition direction) stay introducible (`NOT EXISTS` is vacuously true). This makes TT introduce recognition first and hold production across the recognition learning arc — which is what Anki does emergently too (position order + sibling-bury holds the production sibling while recognition is gathered into the daily queue). So it is **parity-restoring**, and it realizes the per-lemma progression `base recognition → base production → inflections`. This is also the gate that the click-driven inflection cards (Phase 4) sit behind.

**Badge: no change needed.** `count_new_available_collocations` already excludes collocations with a learning/relearning sibling and counts the collocation via its recognition direction when both are new, so it stays consistent with the gated served queue. Verified case-by-case: rec=new (counts via rec; served introduces rec), rec=learning/relearning (excluded; prod held), rec=review-due-today (excluded; prod held by cross-direction bury), rec=review-future/known/suspended (counts; prod introducible).

**Files.** `app/srs/database.py` (`get_new_items` production gate). Tests: `tests/test_srs_database.py::TestDueQueries::test_get_new_items_production_held_until_recognition_graduates` (new); rewrote the two stale production-first assertions in `tests/test_api_srs.py` — `test_review_queue_new_head_recognition_first_for_paired_new` (renamed from `…_matches_anki_gather_bury_template`) and `test_review_queue_new_head_unaffected_by_overfetch_truncation` — to recognition-first. `_merge_directions` docstring annotated.

**Surfaced by.** 2026-06-01 — user reported "Anki has been introducing recognition before production, which is what I want"; confirmed against the binary (604/36) before inverting the parity test.

---

## Layer 66 — `/listen` no longer mints morphology clozes (Phase 4b)

**Change.** Removed the morphology-cloze creation + analyzer-recall + `_ground_morphology_focus` from `mark_lesson_listened`; the A1-feature detector moved to `function_words.is_a1_morphology_feature` and now feeds the transcript's `inflectable` flag. Click-to-create (`POST /inflection-clozes`) is the sole inflection-mint path.

**No queue-assembly / sort / FSRS change** — only fewer NEW cloze rows created on a content path; assembly, sibling-bury, R-sort untouched. `--run-oracle` goldens unchanged.

**Files.** `app/api/srs.py`, `app/srs/function_words.py`, `app/srs/transcript.py`.

## Layer 67 — badge "today" window uses Anki's 4 AM local rollover, not local midnight

**The bug.** Reported as "TunaTale shows 66 to review, Anki shows 73" (2026-06-02, Slovene deck). The six `database.py` "today" count/list helpers (`count_review_due_collocations`, `count_new_introduced_today`, `count_reviews_completed_today`, `count_new_available_collocations`, `list_anki_cards_graded_today`, `list_collocations_reviewed_today`) bucketed by **local midnight** (`datetime.combine(today, time(0), tzinfo=local_tz)`). Anki rolls the study day over at the configured `rollover` hour — **4 AM local** (the user's, and Anki's default). A direction graded in the `[midnight, 4 AM)` local window is "today" for TT but **"yesterday" for Anki**, so TT's "graded-today" sibling-bury fired on review-due siblings Anki had not yet buried. Exactly 7 dual-direction notes had a direction graded at 04:02–04:06 UTC (= 00:02–00:06 EDT) → TT undercounted 73 → 66. The reverse set (TT counts, Anki doesn't) was empty, confirming a single mechanism.

**Why it was a latent inconsistency, not a fresh regression.** `app/plugins/anki_sync/sync.py` already had the right boundary via `_local_today_4am()` (used by `list_decks_with_revlog_today`, `count_first_grades_today_for_deck`), and `app/srs/anki_mirror/protobuf_wire.py::compute_anki_day_index` defaults `rollover_hour=4`. Only the badge/count side in `database.py` used midnight — so the badge disagreed with both Anki *and* TT's own sync-side counts.

**Fix.** New module-level `ANKI_ROLLOVER_HOUR = 4` + `_anki_day_bounds_utc(today, now=None)` → returns the UTC `[start, end)` ISO bounds of the Anki day anchored on `today`, with a before-rollover shift (when wall-clock `now` is before today's 4 AM, the active Anki day is yesterday's, mirroring `_local_today_4am`). All six helpers now call it. The `today.isoformat()` legacy date-only equality and the `end_of_day_utc` due-cutoff are unchanged (different domains — a date-only `last_review` can't be sub-day bucketed, and the due cutoff is calendar-granular). **Not touched:** `review_due_at_for_col_day`'s 4 AM-**UTC** `due_at` storage convention (Layer 54 "don't fix the helpers" — separate domain).

**Verified.** Recomputing the `count_review_due_collocations` filter against the live `tunatale.db` snapshot with a 4 AM-local boundary flipped 66 → **73**, bit-matching Anki (80 review-due notes − 7 with a learning sibling, 0 graded today). Live badge confirmed 73 after the dev server reloaded.

**Files.** `app/srs/database.py` (`ANKI_ROLLOVER_HOUR`, `_anki_day_bounds_utc`, six call sites). Tests: `tests/test_srs_database.py::TestAnkiRolloverDayBoundary` (helper before/after rollover + before-rollover-not-buried / after-rollover-buried integration), and the updated `TestReviewedToday::test_buckets_by_local_day_when_review_crosses_utc_midnight` control timestamp (00:30 PDT → 09:30 PDT, since pre-4 AM now buckets to the prior Anki day). Backend gate green (2740 passed, 100% coverage); full `./test.sh` green incl. `--run-oracle`.

**Surfaced by.** 2026-06-02 divergence report. Memory: `project_badge_rollover_midnight_vs_4am`.

## Layer 68 — sync honors Anki note graves (stop resurrecting intentionally-deleted cards)

**The bug.** `detect_and_reset_orphans` (sync.py) treats *every* TT pointer whose Anki card/note is missing as a card to **recover**: it clears the dead pointer and arms `dirty_fsrs=1` so the next push **recreates** the note (`reset_orphaned_anki_ids` + `sync_create_new`). That's correct for a force-full-download wipe or "Empty Cards," but it can't distinguish those from an **intentional delete** — it only diffs live id sets, never reading Anki's `graves` table. Result: a card the user deletes in Anki gets **resurrected** under a new note id on the next sync; delete again → loop. Confirmed empirically on the user's base cards (the `1780280xxx`/`1780342xxx` cluster): `note_minted_utc` ran 18 h–24 days *after* the TT `created_at`, and the ids the user deleted were exactly the note-graves in Anki (`1780275695199`, `1780275696286`, `1780280835721`). Reported as "I deleted the cards in Anki but it didn't propagate" — it un-propagated.

**Fix (user chose hard-delete; graves as the signal).** `OfflineReader.get_grave_note_ids()` reads `graves WHERE type=1` (0=card, 1=note, 2=deck; empty set if the table is absent). `detect_and_reset_orphans` now calls `db.delete_collocations_for_graves(grave_note_ids=...)` **first** — a TT collocation whose `anki_note_id` is in graves is hard-deleted (FK cascade drops directions + media), not reset. A note missing **without** a grave still falls through to recovery (reset + re-mint), preserving the force-full-download net. Deleting first also removes those cards from the orphan ratio, so a legitimate purge can't trip `OrphanThresholdExceededError`. Note-level only — a bare card grave (note still alive, e.g. Empty Cards) keeps the recovery behavior, so we don't over-delete a 2-card note because one ord was pruned.

**Honors going forward, not retroactively.** The graves already in Anki are the *old* ids TT no longer points to (the resurrection already advanced TT's pointers to live ids), so they match nothing now (`delete_collocations_for_graves` returns `[]`). The loop breaks the *next* time the user deletes a currently-linked note and syncs.

**Files.** `app/plugins/anki_sync/sync.py` (`OfflineReader.get_grave_note_ids`, `detect_and_reset_orphans` grave-delete branch); `app/srs/database.py` (`delete_collocations_for_graves`). Tests: `tests/test_anki_sync_orphan_recovery.py::TestGraveHonoring` (hard-delete / recover-when-not-graved / unmatched-grave no-op) and `::TestOfflineReaderGraves` (type=1 filter; absent-table → empty). Backend gate green (2745 passed, 100% coverage); full `./test.sh` green incl. `--run-oracle`.

**Surfaced by.** 2026-06-02 user report (frontend-created base cards keep coming back after deletion in Anki).

## Layer 69 — sync `anki_ahead` deferral must not discard a NEWER TT grade (recency guard)

**The bug.** `sync_push`'s "Fix 3" deferral (`anki_ahead`) is a pure **state-rank** heuristic: when TT's card is learning/relearning and Anki's is `queue==2` (graduated) — or further along the same learn steps — it discards TT's grade ("TT's grade is being discarded in favour of Anki's"), with **no recency check**. So a TT grade *newer* than Anki's state is dropped. Surfaced via the new peer-sync button: the user graded `Imam {{c1::dovolj}} časa.` **Again** in TT (cid 877, Jun 7 20:29 → relearning), pushed, and the sync reverted it to AnkiWeb's **Jun 5** Good/review state (`s=0.136`, `lapses=0`) — only `prior_state=relearning` survived, and the Again was left recorded solely in `tt_revlog`; AnkiWeb never received the lapse. Fix 3 was meant to stop a **stale** TT learning view from clobbering Anki's correct graduation, but it can't tell "stale TT" from "TT just lapsed it, newer than Anki." **Pre-existing in `sync.py`** (peer-sync reused it unchanged); peer-sync's two-party→three-party topology — the driver pulls AnkiWeb's aggregated, more-current state *before* the push — made "Anki looks graduated/ahead" common and exposed the latent bug. The existing unit test pinned the deferral as correct (stale-TT case only); the peer-sync convergence gate used fresh single grades, never a lapse-newer-than-remote.

**Fix.** `OfflineWriter.get_current_card_state` now also returns `mod` (Anki `cards.mod`, epoch secs). `sync_push` adds a recency guard: when `anki_ahead` and `ds.last_review` is newer than `anki.mod`, set `anki_ahead = False` (TT is the newer authority → push the lapse). When `mod` is unavailable or TT's grade isn't provably newer, the conservative Fix-3 default (defer) holds. Closes the same hole in the closed-SQLite sync (shared code path).

**Files.** `app/plugins/anki_sync/sync.py` (`OfflineWriter.get_current_card_state`; the `sync_push` recency guard). Tests: `tests/test_anki_sync_push.py::TestSyncPushGuardsAgainstAnkiAhead` — `test_push_relearning_when_tt_graded_after_anki` (the regression), `_defers_when_anki_graduated_and_newer_than_tt`, `_defers_when_anki_graduated_and_mod_unknown`, and the real-writer assertion updated for `mod`. Full `./test.sh` green incl. `--run-oracle` (FSRS parity unaffected).

**Surfaced by.** 2026-06-07 user report after the new AnkiWeb peer-sync button ("studied a card, pushed, it overwrote the relearn"). Memory: `project_anki_subprocess_python314_protobuf`.

## Layer 70 — TT-native grades' FSRS memory state lost in the push→pull seam (push never wrote `cards.data`; pull's clean path had no recency guard)

**The bug.** Every TT-native grade's FSRS memory effect (s/d/last_review) was discarded at the next sync, via a three-link chain: (1) `sync_push` wrote scheduling (`set_due_date`/`set_learning_state`) + a revlog row for a dirty grade but **never `cards.data`** — the s/d write was gated behind `row_force_fsrs`, and `run_full_sync` defaults `force_fsrs=False` — so Anki's stored memory state (incl. `lrt`) went permanently stale for TT-graded cards. (2) Push runs before pull in `run_full_sync` and `mark_direction_clean`s each direction, so pull saw `dirty_fsrs=0` — the dirty-branch TT-ahead defenses (Layers 17–22) were structurally bypassed. (3) Pull's non-dirty `fsrs_known` branch took Anki's `stability`/`difficulty`/`lrt`-derived `last_review` **unconditionally**, reverting TT's own grade minutes after the user made it. Layer 69 fixed the push-side variant of this (anki_ahead deferral discarding a newer TT grade); this is the pull-side mirror image, plus the missing push payload.

**Why every health check read green.** The soak proxy and all "bit-exact" compares test TT == Anki — post-clobber both sides agree on the *wrong* value. The persistent `recompute_divergences` (8–29/sync through the whole 06-03→06-10 new-mode soak) were the detector *correctly recording each clobber* (`replay_s` = TT's true pre-pull state via `local_dir` with zero new rows; `anki_s` = the stale value about to be written) — misread as telemetry noise. The 8→18-card `fsrs_known` cohort (offline-reader clobber, fixed `494c3bd`) was this same bug in its only visible form (empty `cards.data` → obvious 1.0/5.0 placeholder); with old data present the clobber wrote plausible stale values. Forensic anchor: cid=428/production, 2026-06-10 — 4 TT grades (Again/Again/Good/Good lapse arc) present in BOTH revlogs (TT-pushed rows identifiable by `factor=d*1000` ≈ 8883, out of Anki's native 700–950 family), applied to card state on NEITHER side. Blast radius at diagnosis: **165/1367 directions** with `MAX(tt_revlog.id)` > `data.lrt`+1h yet stored s == Anki's stale s (up to 70 days behind).

**Fix (three parts).**
1. **Push carries memory state**: new `OfflineWriter.update_card_memory_state(card_id, stability, difficulty, last_review_secs, desired_retention)` — **merge**-updates the `data` JSON (preserves `pos`/`decay`/existing `dr`; sets `dr` only when absent so a TT-only-graded card R-sorts correctly in Anki instead of the SM2 fallback; `lrt` only when the grade has a timestamp; `usn=-1`/`mod` contract as every card write). Called for every dirty grade push (`reps > 0 or row_force_fsrs`, same schema-ver gate). The force path's old `set_specific_value_of_card(["data", ...])` write — which **replaced** the JSON, dropping `pos`/`dr`/`decay`/`lrt` (itself a latent bug; missing `lrt` routes Anki through the short-term branch, oracle gotcha #1) — now goes through the same merge helper; `set_specific_value_of_card` keeps only `ivl`/`factor`.
2. **Pull recency guard** (`_tt_memory_newer`): in `_pull_merge_direction`, when `card_rec.fsrs_known` and TT's `last_review` is strictly newer than Anki's memory-state timestamp (`card_rec.last_review`, lrt-derived), keep TT's s/d/last_review/last_rating; scheduling fields (due_at/reps/lapses/state/left/bury_kind/anki_*) stay pass-through from Anki, which push just wrote. Equal timestamps or either side missing → take-Anki default holds.
3. **Detector gating**: `_record_recompute_divergence` skips when `not card_rec.fsrs_known` (the 854/858/866/882–886 every-sync placeholder noise) or when `_tt_memory_newer` (a known-stale Anki value the guard declined, not a recompute event). Soak signal `recompute_divergences ≈ 0` is now achievable and meaningful again.

**Healing the 165 stale directions**: no code — run Anki's Optimize (recompute_memory_state) once; Anki's revlog has the full TT grade history (pushed rows), so it re-derives correct values which flow back on the next pull through the now-guarded merge.

**Files.** `app/plugins/anki_sync/sync.py` (`_tt_memory_newer`, guard branch in `_pull_merge_direction`, detector gate, `OfflineWriter.update_card_memory_state`, `sync_push` call site + `push_desired_retention`). Tests: `tests/test_anki_sync_pull.py::TestPullRecencyGuardLayer70` (stale-keeps-local / fresh-takes-Anki / missing-or-equal-takes-Anki / relearning queue mapping / scheduling pass-through), `tests/test_anki_sync_pull_event_mode.py::test_new_mode_detector_skips_*`, `tests/test_anki_sync_push.py::TestPushMemoryStateLayer70` + `TestOfflineWriterUpdateCardMemoryState` (merge semantics incl. malformed/non-dict data), and the end-to-end 428-arc regression `tests/test_anki_sync_round_trip.py::test_tt_grade_memory_state_survives_push_pull_round_trip` against a real collection file. Full `./test.sh` green incl. 100% coverage + Playwright; `--run-oracle` 66/66.

**Surfaced by.** 2026-06-11 soak check for the Stage-3b collapse decision (the failing `recompute_divergences ≈ 0` gate). Memory: `project_pull_clobbers_tt_native_grades`. **Blocks lifted**: this was the prerequisite for the ticklish-questing-fountain step-2 merge-tree collapse (re-soak first).

## Layer 71 — replay anchor keyed by anki_card_id misses pre-link revlog rows (since_id=None → full-history re-walk every sync)

**The bug.** The Stage-3b incremental-replay anchor `pre_ingest_revlog_id` came from `latest_revlog_id_for_card(anki_card_id)`. A direction whose tt_revlog rows were written **before `sync_create_new` minted its Anki card** (TT-created clozes graded same-day — the documented listen-flow contract) carries `anki_card_id=NULL` on those rows, so the card-keyed MAX(id) found nothing, `since_id` resolved to `None`, and `rebuild_from_revlog` re-walked the **full history on top of the already-evolved stored state** — double-applying every grade. Result: identical phantom `RECOMPUTE_DIVERGENCE` lines on every sync, forever (cids 858/866, the biti ste/si clozes — `replay_s=0.7980/0.7607` vs stored==anki `0.6781`, re-fired at 10:53 and 10:55 on 2026-06-11 after the post-Optimize heal). Same hole for re-minted cards (orphan recovery changes the card id; old rows keep the old id). Detector-only noise — `_direction_differs` saw stored==Anki so no authoritative write occurred — but it put a permanent floor of 2 under the soak signal Layer 70 had just cleaned.

**Fix.** `latest_revlog_id_for_direction(collocation_id, direction)` — the anchor now keys on the exact domain `rebuild_from_revlog` walks. Pre-link NULL-akid rows and old-card-id rows count toward the anchor; zero new rows ⇒ replay returns the stored state ⇒ silent. `latest_revlog_id_for_card` deleted (single call site; would otherwise survive as test-only dead code).

**Files.** `app/srs/database.py` (`latest_revlog_id_for_direction`, replacing `latest_revlog_id_for_card`); `app/plugins/anki_sync/sync.py` (anchor call site in the pull loop). Tests: `tests/test_anki_sync_pull_event_mode.py::test_new_mode_detector_ignores_unlinked_revlog_rows` (red-test reproduction: stored state + NULL-akid history + zero new rows must not fire), `tests/test_srs_database.py::TestRevlog` (direction-keyed anchor incl. NULL-akid inclusion + cross-direction exclusion). Full `./test.sh` green (2733 passed, 100% coverage, frontend gate, 11 e2e); `--run-oracle` 66/66.

**Surfaced by.** The Layer-70 post-Optimize verification syncs (2026-06-11): divergences dropped 1375 → 2 → 2, with the residual 2 re-firing identical values. Credit: this is the "since_id boundary" hypothesis from the soak-check review — wrong about the main cohort (that was Layer 70's clobber), right about this one.

## Layer 72 — day-level `last_review` round-trip poisons the Layer-70 recency guard (stuck placeholder s/d)

**The bug.** 2026-06-12 soak: proxy showed 1/1381 stability+difficulty divergence that a direct sync could NOT heal — upogniti recognition stuck at the `fsrs_known`-clobber placeholder (s=1.0/d=5.0) while Anki held real post-Optimize values (s=4.8369/d=3.379). Cause: the card's TT `last_review` was `2026-05-21T00:00:00Z` (`last_review_time_ms=0`) — `parse_fsrs_data`'s **day-level reconstruction** (`due - ivl`, no `lrt` in `cards.data`) round-tripped back from an earlier pull, not a TT grade time. Day truncation overshot the real grade (05-20 16:08Z) by ~8h, so `_tt_memory_newer` read TT as "graded later" and blocked take-Anki on every pull, permanently. Left alone, a TT grade of the card would have pushed placeholder-derived state into Anki.

**Fix.** Extracted the existing midnight-UTC marker check from `_elapsed_days_for_fsrs` into `is_day_level_last_review` (`app/srs/fsrs.py`) and added it to `_tt_memory_newer` (`app/plugins/anki_sync/sync_engine.py`): a day-level local timestamp never counts as "TT newer". Safe because TT-native grades always stamp sub-second `datetime.now(UTC)` — including the listen-grade paths, which pass `time_ms=0` but a real `now` (so an ms-based discriminator would have been WRONG; the midnight marker is the correct house convention, same tradeoff as the R-branch select).

**Data heal.** The stuck row was repaired live (last_review → Anki's real lrt) + re-sync; proxy 1381/1381 bit-exact. Two more day-truncated rows existed (cids 854, 883) with currently-matching s/d — latent, now defused by this fix.

**Files.** `app/srs/fsrs.py` (`is_day_level_last_review`, factored from `_elapsed_days_for_fsrs`); `app/plugins/anki_sync/sync_engine.py` (`_tt_memory_newer` + import). Tests: `tests/test_fsrs.py::TestIsDayLevelLastReview`, `tests/test_anki_sync_pull.py::TestPullRecencyGuardLayer70::test_day_level_local_timestamp_does_not_block_take_anki` (red-test reproduction of the stuck card).

**Surfaced by.** Routine soak (2026-06-12). The detector side benefits too: a day-level local no longer suppresses the recompute detector at the second `_tt_memory_newer` call site, so genuine recompute events on such cards are now recorded instead of silently skipped.

## Layer 73 — sync zeroed Anki's per-deck `review_today`, resetting other devices' reviews-done counter

**The bug.** 2026-06-27 (Norwegian Phase 1): after a TT peer-sync, AnkiDroid's "reviews due" jumped back up (e.g. 47→50). Cause: `_recompute_anki_new_today_all_decks` (now `_recompute_anki_studied_today_all_decks`) rewrites each revlog-touched deck's `decks.common` protobuf blob to set `new_today` for new-badge parity. On the **rollover branch** (`last_day_studied < today_day_index`) the writer `set_deck_new_today` *removed* `review_today` (field 5) and `seconds_today` (field 7) without recomputing them — so it wrote `review_today=0` with `usn=-1`, which pushed to AnkiWeb and reset the reviews-done counter on every other device. Schedule/cards were untouched — only the studied counter. (The same-day branch preserved field 5, so this only fired on the first TT sync of a new day / whenever the desktop collection's `last_day_studied` was stale relative to today's pulled revlog — exactly the AnkiDroid-on-phone, desktop-closed topology.) The original `bump_deck_new_today` TODO had predicted this.

**Ground truth (Anki 25.09, the user's exact version — verified at the `25.09` tag, not `main`).** Anki increments `review_today` from the card's **pre-answer queue**, not the revlog `type`: `update_deck_stats_from_answer` does `CardQueue::Review | CardQueue::DayLearn => review_delta += 1` (`rslib/src/scheduler/answering/mod.rs:423-425`), where `DayLearn` (queue=3) is the *interday* (re)learning queue. So interday learning **and** interday relearning count; *intraday* learn/relearn (`CardQueue::Learn`, queue=1) does not. The revlog `type` is computed separately from the pre-answer **state** (`answering/revlog.rs:31`), so `type` alone can't reproduce the counter: a lapse writes type=1, interday relearn writes type=2, interday learn writes type=0 — yet all three increment. The revlog-only discriminator is the pre-answer interval sign: `lastIvl` is encoded days-positive / seconds-negative (`states/interval_kind.rs` `as_revlog_interval`), so **`lastIvl >= 1` ⟺ interday footing**. Mirror predicate: **`COUNT(*) WHERE type IN (0,1,2) AND lastIvl >= 1`**, per deck, since the 4am rollover — counting rows (per-answer), excluding filtered (3) / manual (4). Neither `type=1`-only (undercounts interday learn/relearn) nor `type IN (1,2)` (includes intraday relearn, misses interday learn) is correct.

**Fix.** Added `OfflineWriter.count_reviews_today_for_deck(deck_id, today_4am_ms)` (the predicate above, the `count_first_grades_today_for_deck` sibling for `new_today`); replaced `set_deck_new_today` with `set_deck_studied_today(deck_id, day_index, new_today, review_today)`, which writes the recomputed `review_today` on **both** branches instead of dropping it; wired both into `_recompute_anki_studied_today_all_decks`. `seconds_today` is still dropped on rollover — it drives only the time-studied stat (no due badge); revlog-reconstructing it (`SUM(revlog.time)`) is a deferred follow-up.

**Files.** `app/plugins/anki_sync/sync_writer.py` (`count_reviews_today_for_deck`, `set_deck_studied_today`); `app/plugins/anki_sync/sync_engine.py` (`_recompute_anki_studied_today_all_decks` + `required` tuple). Tests: `tests/test_anki_sync_offline_writer.py::TestCountReviewsTodayForDeck` (predicate: interday review/relearn/learn count; intraday/filtered/manual/cross-deck/pre-today excluded; rows-not-distinct), `::TestSetDeckStudiedToday::test_rollover_recomputes_review_instead_of_zeroing` (the regression), `tests/test_anki_sync_push.py::...::test_sync_push_recomputes_review_today_from_revlog` (end-to-end through `sync_push`).

**Companion fix — TT's *own* review badge (`count_reviews_completed_today`).** The same `review_today` semantic drives TT's live review badge (`reviews_today` subtracted from the daily review cap in `api/srs.py`). It was computed from `collocation_directions` state — `state IN ('review','relearning') AND last_review today` — which has the *opposite* two divergences from Anki: it **over-counted** intraday relearning (every `state='relearning'` graded today, but Anki answers an intraday relearn from the `Learn` queue → no bump) and **under-counted** interday learning (`state='learning'` excluded, but Anki answers an interday learn from `DayLearn` → bump). Neither is recoverable from current direction state: a card graded today holds its *post*-grade interval (`due_at - last_review`), whereas the discriminator is the *pre*-grade interval. The pre-grade interval lives only in the revlog. Rewrote the helper to read `tt_revlog` with the identical predicate — `review_kind IN (0,1,2) AND last_interval >= 1` over the 4am window (`tt_revlog.last_interval` is written with Anki's days-positive/seconds-negative sign by `_compute_revlog_last_interval`, and carries Anki's verbatim `lastIvl`/`type` for sync-ingested rows). `tt_revlog` holds both TT-native (grade-time) and Anki-pulled (`_ingest_anki_revlog_for_card`) rows, so this dropped the `last_rating` and `introduced_at` crutches: a new-card intro is `last_interval=0` and falls out for free. (Narrow window: a freshly-imported deck's today-in-Anki grades aren't in `tt_revlog` until the first `sync_pull` ingests them — self-heals on that sync, and the badge is only consulted during post-sync review sessions.) Files: `app/srs/database.py` (`count_reviews_completed_today`); tests `tests/test_srs_database.py::TestCountReviewsCompletedToday` (rewritten to seed `tt_revlog`: interday review/relearn/learn count; intraday-relearn/new-intro/filtered/manual/prior-day excluded; rows-not-distinct; both directions).

**Surfaced by.** User-reported AnkiDroid badge jump during Norwegian Phase 1 real-deck use (the deck-counter clobber); the companion TT-badge divergence was caught by the `anki-source-expert` ground-truthing during the same investigation.

## Layer 74 — sync re-ingested TT's own pushed grades as duplicate revlog rows (self-echo double-count)

**The bug.** Surfaced by Layer 73's switch to per-answer `COUNT(*)` over `tt_revlog`: after grading the same card in *both* apps (phone + TT) and syncing, TT's review badge dropped too far (45 where Anki showed 46). Forensics (`tt_revlog` vs Anki `revlog`, Anki closed so the WAL was checkpointed): TT held **2–3 rows per card today** where Anki held the genuine answer-events — extra rows at consecutive-millisecond ids (`…986/987/988`) with Anki-assigned factors. These were TT's **own grades round-tripping back through Anki**: TT writes a `tt_revlog` row at grade time (`id = int(last_review*1000)`, factor=0), pushes it to Anki, then `sync_pull`'s `_ingest_anki_revlog_for_card` re-ingests the pushed copy as a "new" Anki grade — double-counting it.

**Root cause.** `OfflineWriter.write_revlog` computed the pushed id as `rid = max(preferred_id, max_id + 1)`. `preferred_id` is the TT grade time (== the TT-native `tt_revlog` row's id), but whenever Anki's revlog already held a *later* row (e.g. a phone grade from minutes earlier in the same session), `max_id+1` **bumped the pushed id past it** — so the echo landed seconds-to-minutes off the grade time, outside `has_revision_near`'s ±5s dedup window, and at an id not in the ingest's `held_ids` (which only knows the TT-native id). It was therefore ingested as a distinct grade. The bump also corrupted temporal order (a TT grade made *before* the phone grade sorted *after* it).

**Fix.** `write_revlog` now inserts at the exact `preferred_id` when that id is free (PK-collision probe `_revlog_id_exists`), only falling back to `max(base, max_id+1)` on a genuine collision. The pushed copy then lands at the same id as TT's own `tt_revlog` grade row, so the ingest's existing `held_ids` exact-id skip suppresses the echo — no time-window widening (which Layer 60 warns swallows genuinely-distinct rapid grades), no new column, no migration. Also restores temporal honesty in the revlog.

**Not a bug (documented for the next reader):** grading the *same* card in two apps genuinely *is* two answer-events; Anki counts both (per-answer), and so does TT after this fix — that's why parallel-grading 2 cards lands at 46, not the per-*card* "48" one might expect. A residual ~1 TT-vs-Anki gap can remain from the separately-accepted interval-clamp divergence (TT lets a collapsed-stability `review` card hold a sub-day interval where Anki clamps review intervals to ≥1 day, so TT skips that card's grade as intraday while Anki counts it).

**Files.** `app/plugins/anki_sync/sync_writer.py` (`write_revlog` id selection, `_revlog_id_exists`). Tests: `tests/test_anki_sync_push.py::TestOfflineWriter` (`test_write_revlog_uses_preferred_id_when_free`, `…_keeps_preferred_id_even_with_a_later_row_present` = the echo regression, `…_bumps_on_preferred_id_collision`); `tests/test_anki_sync_pull.py::…::test_pushed_grade_at_preferred_id_is_not_re_ingested_as_echo` (sociable: real `OfflineWriter` push → real `OfflineReader` ingest → no duplicate; sabotage-drilled red without the fix).

**Surfaced by.** User parallel-grading `like`/`tenke` in AnkiDroid + TT during Norwegian Phase 1, then syncing.

## Layer 75 — review cap applied to the served queue, not just the badge

**The bug.** With a 50-review daily cap, the `/queue-stats` badge correctly read 50, but `/review-queue` served *all* due reviews (e.g. 1499) — you could review past the cap indefinitely (user report, Norwegian Phase 1, 2026-06-28). Anki's daily limits cap the actual study session: you gather at most `review_limit - reviews_today` review cards (and `new_limit - introduced_today` new cards), study them, and the deck is done. The badge and the flow agree.

**Root cause.** `_compute_live_main` (`app/api/srs.py`) already capped **new** cards (`nonlearning_new[:new_quota]`) but never capped **review** cards (`nonlearning_due`). The old rule-12 text claimed "daily caps are render-only / queue assembly does NOT cap / Anki only caps the deck-list badge" — wrong on all three counts (the new-card cap was always applied in assembly; Anki caps the flow).

**Fix.** Compute `review_remaining = max(0, review_cap - reviews_today)` and slice `nonlearning_due = nonlearning_due[:review_remaining]` **after** sibling-bury (Anki counts post-bury survivors toward the limit), keeping the lowest-R reviews (the list is already R-ascending). Learning/relearning cards stay exempt (Anki gathers them regardless of the review limit). Mirrors the existing new-card cap exactly.

**Freeze-model consistency.** The cap tightens as `reviews_today` grows mid-session, but graded cards leave the due pool (`get_due_items`), so the surviving frozen reviews always equal the remaining budget — no spurious mid-session drops, and the reconciliation in `get_review_queue` (which drops cached keys absent from `live_main`) naturally prunes the now-excess tail. Badge (`min(review_due_raw, review_cap - reviews_today)`) and queue now cap at the same value.

**Files.** `app/api/srs.py` (`_compute_live_main`). Tests: `tests/test_api_srs.py::TestReviewQueue::test_review_queue_caps_review_cards_at_daily_review_cap` (cap=2 over 6 due → 2 served) + `…_uncapped_when_cap_above_available` (ceiling, not fixed size). Oracle harness (66) + full queue suite green.

**Surfaced by.** User reviewing the Norwegian deck: badge 50, but the app served all 1499 due reviews.

## Layer 76 — new cards studied today were not charged against the review-per-day limit

**The bug.** Create a card in TunaTale, study it, sync — TT's review count read higher than Anki's. Specifically TT's review badge exceeded Anki's by the number of new cards introduced today. User report (Norwegian Phase 1, 2026-07-09): "the review count in TunaTale and Anki don't match; it happens when I create a new card in TT then sync; it's interacting with the review limit."

**Root cause.** Anki's daily review limit is consumed by BOTH reviews done today AND new cards introduced today (unless `new_cards_ignore_review_limit` is set — a collection-level bool that defaults **off**). `rslib/src/decks/limits.rs:98-114` (Anki 25.09):

```rust
review_limit -= review_today_count;      // reviews done
new_limit    -= new_today_count;
if !new_cards_ignore_review_limit {
    review_limit -= new_today_count;      // ← new cards introduced ALSO charge the review budget
    new_limit = new_limit.min(review_limit);
}
```

So Anki's remaining review budget = `reviews_per_day − reviews_done − new_introduced_today`. TT computed only `review_cap − reviews_today` (`app/api/srs.py` badge and `app/srs/anki_mirror/queue_engine.py` served queue), missing the `− introduced_today` term. `reviews_today` (`count_reviews_completed_today`, Layer 73) counts genuine reviews only — a new-card intro is `last_interval=0`, excluded — and `introduced_today` (`count_new_introduced_today`, Layer 26) is tracked separately; the two sets are disjoint, so TT had the number available but never subtracted it. With the user's deck (240 review-due ≫ 50 cap) the review count is pinned to `cap − done`, so every TT-introduced card left the badge one too high until the discrepancy was noticed against Anki.

**Why it's cross-rebuild (and how it was proven).** The interaction only shows once reviews saturate the limit, but saturated reviews prevent new cards from being *gathered* in the same fresh build (Anki caps `new` to the remaining review budget via `RemainingLimits::decrement`'s `cap_new_to_review`). So a single queue build can't exhibit it. Proven against the real 25.09 scheduler: study 5 new cards (no reviews present) → `new_studied=5`; then add 100 due reviews → `deck_due_tree` review count = **45** = `50 − 0 − 5` (would be 50 without the charge).

**Fix.** New single-source helper `effective_review_budget(review_cap, reviews_today, introduced_today) = max(0, review_cap − reviews_today − introduced_today)` in `app/srs/anki_mirror/queue_stats.py`, wired into both the `/queue-stats` badge (`app/api/srs.py`, used for `review_remaining` AND the new-badge review-budget cap) and the served-queue cap (`app/srs/anki_mirror/queue_engine.py`, Layer 75's `nonlearning_due[:review_remaining]`). Assumes `new_cards_ignore_review_limit` off — the same assumption TT already makes for the new badge; honouring an explicitly-enabled flag would need the collection bool synced into the cache (follow-up).

**Files.** `app/srs/anki_mirror/queue_stats.py` (`effective_review_budget`), `app/api/srs.py` (badge), `app/srs/anki_mirror/queue_engine.py` (served queue). Tests: `tests/test_queue_stats.py::TestEffectiveReviewBudget` (helper arithmetic + zero-clamp); `tests/test_api.py::TestQueueStatsEndpoint::test_queue_stats_review_budget_excludes_new_introduced_today` (endpoint regression, sabotage-drilled red without the fix); `tests/test_parity_daily_caps.py::test_anki_review_count_charges_new_cards_studied_today` (oracle: answer 2 new → inject 10 reviews via the new `add_review_cards` oracle op → Anki review = 5−2 = 3). Full backend suite green at 100% coverage with `--run-oracle`.

**Surfaced by.** User creating cards in TT, studying them, syncing, and seeing the Norwegian review count sit above Anki's by the number of new cards introduced that day.

**Addendum (2026-07-10, brief #4a).** The "assumes off / follow-up" caveat is closed. `new_cards_ignore_review_limit` is now synced from Anki's config table (collection-level bool, key `newCardsIgnoreReviewLimit` — confirmed empirically against the 26.05 binary, not a deck_config proto field) via `refresh_new_cards_ignore_review_limit` in `run_full_sync`, cached under `anki_state_cache['new_cards_ignore_review_limit']`, resolved by `resolve_new_cards_ignore_review_limit(db)` (default False). `effective_review_budget` takes a keyword `new_cards_ignore_review_limit`; when ON it returns `max(0, review_cap − reviews_today)` (intros no longer charge the budget). Oracle-pinned by `tests/test_parity_daily_caps.py::test_anki_new_cards_ignore_review_limit_flips_new_cap`.

## Layer 77 — served queue ignored the review limit's cap on NEW cards

**The bug.** Layer 76's sibling gap. The `/queue-stats` badge correctly capped the new count at the remaining review budget (`min(new_badge, review_budget − review_remaining)`), but the SERVED queue (`_compute_live_main`) sliced `nonlearning_new[:new_quota]` with `new_quota` computed purely from the new cap — no review-budget coupling. On a day whose review budget is fully consumed by due reviews (the user's normal deck shape: 240 due ≫ 50 cap), the badge said `new: 0` while `/review-queue` still interleaved up to `new_quota` new cards into the session. Same badge-right/queue-wrong split Layer 75 fixed for reviews, on the new side. Worse than cosmetic: each wrongly-served intro increments `introduced_today`, further shrinking the review budget Layer 76 computes (self-amplifying divergence).

**Anki's behavior (both halves).** Construction time (`RemainingLimits::new_for_normal_deck_v3`, rslib/src/decks/limits.rs:98-109): `new_limit = new_limit.min(review_limit)` after both today-counts are charged. Then **dynamically per gathered review** (`decrement()`, limits.rs:131-141): `review −= 1; if cap_new_to_review { new = new.min(review) }` — gather order is reviews → new (scheduler/queue/builder/gathering.rs:14-21), so every review gathered in the same build shrinks the new limit before new gathering starts. Net: new gathered = `min(new_quota, review_budget − reviews_gathered)`. Verified identical in main @ cfe0077 and the 25.09 tag; oracle-confirmed (2 due reviews under cap 5 + 10 new under cap 20 → Anki gathers exactly 3 new).

**Fix.** One line in `_compute_live_main` after the review slice: `new_quota = min(new_quota, review_remaining − len(nonlearning_due))`. Non-negative by construction (the due slice is capped at `review_remaining`). Mid-session self-consistency mirrors Layer 75's argument: grading a review decrements the budget by 1 AND removes the card from the due pool, so the new-card headroom is stable — no mid-session drops of already-frozen new cards. The frozen-queue reconciliation only tail-appends NEW latecomers that survive `_compute_live_main`, so the cap can't leak back in through that path.

**Files.** `app/srs/anki_mirror/queue_engine.py` (the cap), `app/api/srs.py` (stale "badge-only" comment corrected). Tests: `tests/test_api_srs.py::TestReviewQueue::test_review_queue_new_cards_suppressed_when_review_budget_consumed` (exhausted-budget case, red pre-fix: served 2 new where Anki serves 0) and `::test_review_queue_new_cards_capped_by_remaining_review_budget` (partial-budget discriminator, red pre-fix: served 4 where Anki serves 3); `tests/test_parity_daily_caps.py::test_anki_caps_new_cards_to_remaining_review_budget` (oracle pin of the dynamic decrement).

**Still open (known, deliberate).** (a) `new_cards_ignore_review_limit` is assumed off, not synced from the collection config — flipping it in Anki would diverge (documented in Layer 76 too). (b) Source reading during this fix showed *interday* learning cards (queue=3) also charge the review limit in Anki (gathering.rs:16+53) — rule 12's "learning cards are exempt" only holds for intraday (queue=1). Not yet mirrored or oracle-verified; candidate Layer 78 if learning-heavy days show divergence.

**Addendum (2026-07-10, brief #4a).** Caveat (a) is closed — see the Layer 76 addendum. The served-queue new-slice cap `new_quota = min(new_quota, review_remaining − len(nonlearning_due))` in `_compute_live_main` is now skipped when `resolve_new_cards_ignore_review_limit(db)` is True. Endpoint-pinned by `tests/test_api_srs.py::TestReviewQueue::test_review_queue_new_cards_served_when_flag_on_despite_saturated_reviews` and `TestQueueStats::test_queue_stats_new_badge_ignores_review_limit_when_flag_on`. Caveat (b) (interday learning) remains open — that's brief #4b, unchanged.

**Surfaced by.** User report that the review-cap handling vs Anki was "only partially fixed" after Layer 76; the served-new gap was found by auditing every place the budget enters.

## Layer 78 — TT-native revlog rows mis-encoded the pre-answer state (frozen review badge after a lapse)

(Numbering note: Layer 75/77's "candidate Layer 78" — interday learning charging the review limit — took the next free integer, Layer 79.)

**The bug (user report, 2026-07-10).** Badges read `0 0 50` (review pinned at the daily cap). One review card graded **Again** → `0 1 50`: the learning badge incremented but the review badge never charged the grade (Anki shows 49). `count_reviews_completed_today` (Layer 73) counts `tt_revlog` rows with `review_kind IN (0,1,2) AND last_interval >= 1` — but the grade wrote `last_interval=-7236`. Root cause: `_compute_revlog_last_interval` derived the sign from the wall-clock `due_at − last_review` delta, and TT's review `due_at` is **day-granular** (`review_due_at_for_col_day`), so a card that graduated late in the local day sits < 24h before its due boundary. The delta flipped to seconds-negative — intraday-learning footing — and the counter (correctly, per its contract) skipped it. Forensic signature: the card graduated 21:59 local with revlog `interval=1` (day), then the next day's Again row carried `lastIvl=-7236` (≈ the 2h to local midnight).

**Anki's behavior.** `lastIvl` is `current.interval_kind()` through `as_revlog_interval` (interval_kind.rs:32-37) — and `ReviewState::interval_kind()` is `InDays(card.interval)` (states/review.rs:50-54), always ≥ 1 for a review card, never an elapsed/due-derived value. Second divergence in the same row: revlog `type` comes from the **pre-answer** state (`RevlogEntryPartial::new(current, …)`, answering/revlog.rs:18-33) — a lapse (answered FROM review) logs `type=1` Review (states/review.rs:56-62), a graduating answer logs `type=0` Learning; `type=2` Relearning applies only to presses on a card already in relearn steps (relearning.rs:23-25). TT's `_compute_review_kind` keyed on the *transition* and wrote 2 for a lapse, 1 for a graduation.

**Fix (pre-Layer checklist applied).** No new formula: the REVIEW branch of `_compute_revlog_last_interval` reuses `_scheduled_days_for_grade` (the Layer 51 `card.ivl` reconstruction, `anki_due − col_day(last_review)` with wall-clock fallback), floored at 1; `build_revlog_row` gained `col_crt` (threaded from the three grade call sites in `api/srs.py`, where it was already resolved). `_compute_review_kind` now maps the pre-answer state only — NEW/LEARNING→0, RELEARNING→2, else→1 — the exact mapping `_derive_revlog_shape` (sync push) already used, which is why **pushed** Anki revlog rows were always correct: the bug lived only in TT's own `tt_revlog`, which sync ingest never corrects (TT's own rows are held by id, Layer 74).

**Files.** `app/srs/fsrs.py` (`_compute_revlog_last_interval`, `_compute_review_kind`, `build_revlog_row`), `app/api/srs.py` (col_crt threading). Tests: `tests/test_fsrs.py::TestBuildRevlogRow` (day-granular REVIEW → `+1`; `anki_due` col-day reconstruction → exact ivl; interday-learning positive days; relearn step stays seconds-negative; lapse `review_kind=1`; graduation/NEW → 0) and the end-to-end badge pin `tests/test_api_srs.py::TestLearningStatePriority::test_again_on_day_granular_review_charges_review_budget`. All red pre-fix (sabotage-drilled via stash).

**Data repair.** The live `tunatale_no.db` row (id 1783705793007) was corrected `last_interval −7236 → 1` before the next sync (the row had not yet been pushed).

## Layer 79 — interday learning cards didn't charge the review-per-day limit (brief #4b)

**The gap (flagged during Layer 77, closed 2026-07-10).** Anki gathers *interday* learning cards (queue=3, DayLearn — day-scale learn/relearn steps) under the REVIEW limit: `gather_due_cards` hardcodes `LimitKind::Review` for both `DueCardKind::Learning` and `::Review` (gathering.rs:35-61), in gather order intraday → interday-learning → reviews → new (gathering.rs:14-21). Each gathered interday card runs the same `decrement(LimitKind::Review)` that re-mins the new headroom (limits.rs:131-143), and that decrement is NOT gated on `new_cards_ignore_review_limit` (limits.rs:136 gates only the new re-min). The cards still *display* in the learning count (`day_learning` feeds `learn_count`, builder/mod.rs:189-218). TT exempted ALL learning states from the budget — rule 12's old "learning cards are exempt" was true only for intraday (queue=1).

**Oracle pin first** (per the brief: no mirror without the binary's word): `test_parity_daily_caps.py::test_anki_interday_learning_charges_review_limit` — review cap 3, 5 due reviews, 4 new, 2 interday learning cards due today → Anki reports `learning=2, review=1, new=0`. Confirmed against the binary on the first run.

**Mirror.** New counter `count_interday_learning_due(today)` (`db_counts.py`): `state IN ('learning','relearning') AND due_at − last_review ≥ 1 day AND due_at <` end of today's 4am window (day-level dues gather regardless of intra-day time; overdue included; `last_review IS NULL` promote_to_learning rows excluded — Anki keeps those at queue=0). Charged inside `effective_review_budget(..., interday_learning_due=…)` in BOTH branches (flag ON still charges), threaded from the badge path (`api/srs.py`) and the served-queue build (`queue_engine.py`). The interday cards themselves keep serving from the learning queue uncapped, and keep displaying in the learning badge — only the review/new budgets shrink.

**Known residual (documented, not mirrored).** When the interday count *exceeds* the remaining budget, Anki gathers only budget-many interday cards (its learning count shrinks); TT serves them all. Requires day-scale learning steps stacked past the review cap — not reachable with the user's sub-day step config.

**Files.** `app/srs/db_counts.py`, `app/srs/anki_mirror/queue_stats.py`, `app/srs/anki_mirror/queue_engine.py`, `app/api/srs.py`. Tests: the oracle pin; `test_queue_stats.py::TestEffectiveReviewBudget` (charge + flag-ON charge); `test_srs_database.py::TestCountInterdayLearningDue` (footings, window, promote exclusion); endpoint pair `test_api_srs.py::TestReviewQueue::test_review_queue_interday_learning_charges_review_budget` + `test_queue_stats_review_badge_charged_by_interday_learning` (both sabotage-drilled: neutralizing the charge flips them red).

## Layer 80 — sync_push collapsed intermediate TT grades out of Anki's revlog (per-row push + factor fidelity)

**The bug (observed live 2026-07-10).** TT review badge 45 vs Anki 46. Card 1483 was graded twice between syncs (13:49 Again — a countable lapse — then a 15:52 relearn-step press), but `sync_push` wrote **one Anki revlog row per dirty direction** reflecting only the latest state (`_derive_revlog_shape`). The intermediate lapse never reached Anki's revlog → Anki's `review_today` under-counted, `reps`/`lapses` bumped once per push instead of once per grade, and Anki-side FSRS Optimize saw an incomplete history.

A secondary fidelity bug: `build_revlog_row` wrote `factor=0` for all TT-native rows, and the `_push_revlog_for_direction` fallback branch wrote `factor = round(ds.difficulty * 1000)` (SM-2-range values like 5500 that Anki misinterprets as FSRS difficulty). Anki writes `factor = round(((d - 1.0)/9.0 + 0.1) * 1000)` in f32 on every review/learning/relearning path (rslib/src/card/mod.rs:115-125, scheduler/answering/review.rs:29-36).

**Mechanism.** Watermark = `MAX(revlog.id)` per Anki cid (queried via `max_revlog_id_for_card`). Candidate rows = tt_revlog rows for that collocation/direction with `id > watermark`. Each candidate is inserted at its own `tt_revlog.id` via the existing Layer 74 collision guard (`preferred_id` + `_revlog_id_exists` bump). Reps bumped by total rows inserted; lapses bumped by rows where `review_kind=1 AND button_chosen=1`. Aggregated bump on the last row insertion call avoids the `MAX(reps+bump, ds.reps)` floor logic firing N times.

**Factor fidelity fix.** `build_revlog_row` now computes `factor = int(_round_to_places_f32(((d - 1.0)/9.0 + 0.1) * 1000, 0))` — the f32 rounding helper (Layer 59) for bit-exact parity with Rust's `round_to_places`. The fallback branch in `_push_revlog_for_direction` uses the same unclamped shifted formula (a prior `max(1300, min(13000, …))` clamp was wrong — Anki stores the raw value, and real FSRS difficulty [1, 10] always yields [100, 1100]). Existing `factor=0` rows in tt_revlog stay 0 and push as 0 (Anki treats 0 as missing; documented, not backfilled).

**Tests.** Unit: `test_fsrs.py::TestBuildRevlogRow` factor pins (d=5.5→600, d=1.5→156, d=9.001→non-trivial f32); `test_anki_sync_push.py::TestPerGradeRevlogPush` (two-grades unit, reps/lapses by row count, idempotent re-push, two-grade outcome parity, defensive guards). Oracle: `test_parity_revlog_factor.py` (answer card, assert Anki revlog factor == TT formula on post-answer difficulty). Round-trip: sociable push→pull in `test_anki_sync_pull.py` (no duplicate re-ingest, no reviews_today inflation); peer-sync in `test_anki_peer_sync_selfhost.py` (two grades → peer_sync → exact tt ids preserved, idempotent second sync). All sabotage-drilled.

**Documented residuals.**
(a) Phone-grade-newer edge: a phone grade above unpushed TT ids empties the candidate set; the fallback pushes one collapsed row (same loss as old behavior). Rule 6 state convergence still applies.
(b) Orphan-recovery re-mint hole: a re-minted cid has watermark 0, so the full tt_revlog history (including pre-Layer-78 rows) would push to the new cid. Accepted and documented; do NOT add an anki_card_id filter to `get_unpushed_revlog_rows` (it would drop legitimate pre-link rows, Layer 71).
