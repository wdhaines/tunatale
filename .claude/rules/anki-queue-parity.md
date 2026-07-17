---
paths:
  - "backend/app/api/srs.py"
  - "backend/app/srs/**"
  - "backend/app/plugins/anki_sync/**"
  - "backend/tests/test_parity_*.py"
  - "backend/tests/test_api_srs*.py"
  - "backend/tests/test_srs*.py"
  - "backend/tests/test_fsrs*.py"
  - "backend/tests/test_direction_*.py"
---

# TT ↔ Anki Queue Parity

*Path-scoped rule: auto-loads when a file matching the `paths:` frontmatter is read — keeps session startup lean. Don't remove the frontmatter; if you need this without touching those files (e.g. a divergence report in conversation), read it directly.*

Required reading before changing `backend/app/api/srs.py`, `backend/app/srs/fsrs.py`, `backend/app/srs/anki_mirror/queue_stats.py`, or the sync modules (`backend/app/plugins/anki_sync/sync.py` — runner + facade; since the 2026-06-11 split, the reconcile engine lives in `sync_engine.py`, the collection I/O in `sync_reader.py`/`sync_writer.py`, shared leaf helpers in `sync_common.py`; `app.plugins.anki_sync.sync` re-exports everything, so "sync.py" references below mean the facade surface). Full layer history at `docs/anki-parity-layers.md` — open only when "have we hit this exact thing before?"

## What "parity" means

User grades the same deck in both apps. Between syncs they run independently; next-served card and the three badge counts (`new`/`learning`/`review`) must stay close enough that switching apps doesn't feel discontinuous. **Sync is the alignment moment.** Drift between syncs is bounded and acceptable.

Anki is the reference implementation. TT mirrors Anki's algorithms — but **reads `collection.anki2` only at sync time**, never on the live request path. A request handler that consults Anki's collection is a regression unless it's `sync_pull`/`sync_push`.

## Three most common benign divergences

These three account for the bulk of "TT and Anki disagree on head card" reports. **Check all three before pattern-matching to anything algorithmic.** All three resolve at the next TT sync.

### #1 — Cutoff frozen at last grade (rule 11)

**Signature (TT-frozen)**: Anki serves a *learning* card, TT serves a *main/review* card, both apps have the card, TT's `learning_cutoff` is within seconds-to-minutes of the learning card's `due_at`.

**Signature (Anki-frozen, mirror case)**: TT serves a *learning* card, Anki serves a *main/review* card. TT's `?session_start=1` advanced its cutoff (frontend `/review` mount), but Anki's cutoff froze at the last grade and the deck hasn't been rebuilt since. Same mechanism, opposite direction. Resolution is the same shape but applied to whichever side is behind.

**Why**: cutoff advances only on grade / session-start / sync_pull / end-of-session auto-bump. If you grade in Anki 2s *after* the learning card ripens, Anki advances past `due` and serves it. TT, last graded *before* the ripening, keeps cutoff frozen and the card waits in `pending_learning` tail.

**Resolution**: refresh `/review` (frontend `onMount` sends `?session_start=1` → cutoff = `now`) or grade any TT card. For the mirror case (TT ahead, Anki behind): grade any Anki card, deck-navigate away and back, or close+reopen Anki. **File→Sync alone won't help if sync brings no card changes** — see divergence #3. **Do not** add a "live `now`" path or per-poll cutoff advance — stickiness is intentional and Anki works the same way.

**Diagnostic**: `docs/anki-parity-diagnostics.md` §"Cutoff frozen" — if the earliest learning `due_at` is just past the cutoff, you've found it.

### #2 — Independent grading drift

Each app stamps its own `last_review` at the moment the grade fires, computes `due_at = last_review + step + fuzz`. When you grade the same card in both apps, sub-second-to-few-seconds button-press variation produces `due_at` deltas of 1–5s — enough to invert order of two cards whose Anki gap is only 1s.

**Signature (cross-queue)**: TT shows review/main, Anki shows learning, TT's `anki_due` is much older than Anki's `cards.due`.

**Signature (intra-learning)**: BOTH apps show a learning card at head, but *different* cards from the same stack — positions 0/1 (or 0/2) swapped. Per-card `due_at` deltas are O(seconds).

**Why**: identical FSRS fuzz on slightly different timestamps. peti graded in Anki 3s before TT, risati 2s after. Anki: peti(X) → risati(X+1). TT: risati(X−2) → peti(X+3). 5s total swing inverts a 1s gap.

**Resolution**: sync. `_direction_differs` includes `due_at` (rule 6); after one round-trip both apps converge on the later grader's timestamp per card. **Do not** overwrite timestamps client-side — convergence is sync's job.

**Diagnostic**: `docs/anki-parity-diagnostics.md` §"Grading drift" — if per-card deltas are O(seconds) and the step (due − grade) matches between apps, it's drift, not a bug.

### #3 — Asymmetric queue-rebuild cadence (R-asc inversion captured by only one app)

Both apps freeze the main review queue and never re-sort mid-session (Anki `CardQueues.main` is a `VecDeque`, pop-only; `rslib/.../queue/main.rs:8-46`). But **Anki's rebuild trigger set is much larger than TT's**, so the two frozen queues can capture different snapshots of the same state.

**Anki rebuilds on** any op where `OpChanges::requires_study_queue_rebuild()` returns true (`rslib/src/ops.rs:168-181`): any card change (`c.card`, except `Op::SetFlag`), any deck change (`c.deck`), specific config ops (`SetCurrentDeck` / `UpdatePreferences` / `UpdateDeckConfig` / `ToggleLoadBalancer`), or any deck-config change (`c.deck_config`). The dispatch site is `queue/mod.rs:211-215`. In practice: day rollover, **profile/collection reopen** (lazy-built on first access via `get_queues()` at `queue/mod.rs:246-254`), **deck-config/prefs change**, **undo**, and any non-grade card/deck mutation.

**"Deck navigation" is narrower than it sounds.** `Op::SetCurrentDeck` only fires `c.config = true` (and thus the rebuild) when the value actually changes — i.e. switching to a *different* deck. Clicking the *same* deck you're already studying, pressing Escape to the deck list and re-entering, or any UI action that doesn't move `col.conf["curDeck"]` to a new value is a no-op for the rebuild path. If you want a rebuild via deck nav, switch to a different deck and back (two ops, two rebuilds).

**File→Sync is conditional**, not unconditional. A sync only triggers rebuild when AnkiWeb sends back actual card / deck / config mutations: TT push → AnkiWeb → another device's File→Sync pulls the row with a new `mod` → `c.card = true` → rebuild. A no-op round-trip (nothing changed remotely) returns `c.card = c.deck = c.deck_config = false` → no rebuild. End-of-session auto-bump (`queue/mod.rs:190-196`) only fires when `counts().all_zero()` — having any review/new/learning card with non-zero count blocks it.

**Reliable Anki-side rebuild triggers** (when you need to force it): quit + reopen Anki (most reliable, hits `Collection::open` → lazy `get_queues()`); grade any card in the deck (`update_queues_after_answering_card` → `update_learning_cutoff_and_count` → `current_learning_cutoff = now`); switch to a different deck and back; toggle any deck preference. Bare File→Sync, re-clicking the same deck, or refreshing the reviewer don't fire it.

**TT rebuilds only on `sync_pull`** (rule 2). Grading does not rebuild in either app.

**Critical non-obvious trigger: TT sync itself triggers an Anki rebuild.** TT's `safe_open(mode="rw")` (`backend/app/plugins/anki_sync/safety.py:148-162`) requires Anki to be closed. User closes Anki → runs TT sync → reopens Anki → **the reopen rebuilds Anki's queue with current R values**. TT's sync_pull moment and Anki's reopen moment can be seconds-to-minutes apart, and an R-asc cross can land between them. This is how "I only synced once, never touched Anki" still produces divergent heads. Forensic signature: a multi-minute gap in Anki's revlog spanning the cross point.

**Signature**: both apps show a *review* card at head, same candidate set, two different cards with **wildly different stabilities** (e.g. s=0.65 vs s=7.5), R values near-tie (delta ~0.001–0.005). R decays at `1/(s·86400)`/sec, so low-s decays ~12× faster than high-s — small wall-clock gaps between rebuild moments invert near-tie pairs.

**Worked example** (May 17, 2026): synced TT ~20:45 PDT. Frozen TT: prodati (R=0.8635) < cloze "se" (R=0.8639). At ~20:55 they crossed. At ~21:00 user hit **File→Sync in Anki** which pulled card mutations TT had pushed to AnkiWeb earlier, satisfying `c.card = true` and clearing Anki's cache. Anki's next rebuild: cloze (R=0.8620) < prodati (R=0.8633). TT still showed prodati first. (Note: this only worked because the sync brought real card changes back. A no-op File→Sync would not have cleared the cache — see "File→Sync is conditional" above.)

**Resolution**: `sync_pull` from TT to rebuild its frozen queue with current R values.

**Don't try to "fix" this** with mid-session re-sort or by mirroring all of Anki's rebuild triggers. The freeze-at-build design is intentional (rule 2). Cheap fix: "sync more often." Structural fix (match Anki's trigger set) only if this class proves frequent.

## Architectural principles (don't undo these)

1. **Anki is reference, not runtime dependency.** Every badge endpoint and queue-build path reconstructs from TT state alone (`collocation_directions` + `anki_state_cache`). If you find yourself opening `collection.anki2` in `app/api/`, wrong turn — look for the TT-state-only equivalent (`count_new_introduced_today`, `count_review_due_collocations`, etc.).

2. **Cache invalidation + eager rebuild on sync.** Non-dry-run `sync_pull` *clears AND eagerly rebuilds* `session_main_queue` via `build_and_freeze_main_queue` (Layer 29). Freeze moment = sync time. Cache key lifecycle is declared in `app/srs/anki_mirror/cache_registry.py` (19 keys, source/day-scoped/max-age/logic-version). All `ANKI_CONFIG` source keys are re-written by `sync_pull` via refresh_* calls (conserved by `tests/test_sync_cache_conservation.py`); missing a `refresh_*` call is caught at sync time. **Deploy-time pitfall**: cache lives in `anki_state_cache` (DB-backed), survives restarts. After changing queue-assembly logic, the cache replays the OLD order until next sync — restart alone doesn't invalidate. **Always run `clear_session_main_queue` first** before concluding a fix is broken.

   **Pre-Layer checklist** (one line added): queue-order change ⇒ bump `session_main_queue` `logic_version` in cache_registry.py (frozen queues with mismatched version are discarded, like day mismatch → rebuild path).

3. **Sibling-bury via `last_review` filter + learning-queue filter (Layer 56).** Anki's `bury_reviews=true` removes a note from today's review pool when a sibling is active. Two triggers: (a) a sibling was *graded today* — TT mirrors by excluding collocations where any direction has `last_review` today; (b) a sibling is in the *learning queue* (`queue=1/3`), **including interday learning steps graded on a prior day** — TT mirrors by also excluding collocations where any direction is `state IN ('learning','relearning')` (the `count_review_due_collocations` subquery). Filter (b) alone fixed a 214→208 badge over-count. This filter is the *review* badge: a review card is **not** dropped for merely having a NEW sibling (`bury_new` buries the *new* card, not the review). The converse — the **new** badge mirroring `bury_new` — is **Layer 64** (`count_new_available_collocations`): a NEW card is buried when a sibling is review-due-today or learning (a *future*-due review sibling does **not** bury). Don't write count queries that ignore either filter. (`count_review_due`, the old per-direction counter, was deleted in Layer 56 — use `count_review_due_collocations`.)

4. **Sync rebuilds with current pool.** Both apps gather with current-pool counts at sync time, not session-start. Intersperser ratio is `(one_len+1)/(two_len+1)` over natural list lengths — do **not** add a session-start override (Layer 9, reverted at 14).

5. **Two-branch R formula.** `extract_fsrs_retrievability` has lrt branch (sub-day fractional elapsed) and day-level fallback (integer-day elapsed). TT mirrors both in `compute_retrievability`.

6. **Sync must merge both directions.** `sync_push` defers to Anki when Anki is ahead (graduated or smaller `total_remaining`). `sync_pull` defers to Anki when Anki is ahead. `_direction_differs` must compare `left`, `due_at`, `prior_state`, `bury_kind`, `anki_card_mod` so self-heal writes actually fire — since 2026-07-05 it (and `_DIR_COLUMNS`) derives from the field registry `app/srs/direction_fields.py`; register new columns there with an explicit `sync_comparable` decision, never hand-edit either list (`tests/test_direction_fields.py` pins registry ↔ schema ↔ model ↔ diff).

7. **`prior_state='new'` is sticky.** Set on intro by `_resolve_prior_state` (sync) or `_grade_prior_state` (TT). Persists across same-state-class grades and LEARNING→REVIEW graduation. Released only on REVIEW→RELEARNING (lapse) for revlog `type=1` correctness. **Do not** overwrite `prior_state='new'` without checking new state. *Now declared as `WritePolicy.STICKY_NEW` in `app/srs/direction_fields.py`, pinned to `_grade_prior_state` by `tests/test_direction_invariants.py`; `prior_state`'s value domain is a v35 SQL CHECK.*

8. **`introduced_at` is a one-shot stamp, NOT a sticky marker (Layer 26).** Written exactly once per direction on first NEW→non-NEW transition by `fsrs.schedule` or `sync_pull._resolve_introduced_at` (from `MIN(revlog.id)`). `count_new_introduced_today` queries this column, NOT `prior_state` + `last_review`. Don't conflate: `prior_state='new'` lives for the entire intro arc (revlog correctness); `introduced_at` is a fixed timestamp (anchors Anki's `newToday` parity). *Now declared as `WritePolicy.ONE_SHOT` in `app/srs/direction_fields.py`, pinned to `_resolve_introduced_at` by `tests/test_direction_invariants.py`.*

9. **Daily unbury sweep at queue-build (Layer 27, refined 35).** `db.unbury_if_needed(today)` runs at the top of `/queue-stats`, `/review-queue`, `sync_pull`. Restores `state='buried' AND bury_kind='sched'` to `state='review'` (reps>0) or `'new'` (reps=0). Tracked via `anki_state_cache['last_unbury_day']`; idempotent within local day. Mirrors Anki's `unbury_on_day_rollover` (releases both `queue=-3` AND `queue=-2`). **Do NOT** let `state='buried' bury_kind='sched'` rows accumulate.

10. **`bury_kind` split (Layer 35, corrected 2026-05-16; Layer 39, corrected 2026-05-17).** Anki has `queue=-3` and `queue=-2` for buried. Source claims grade-time sibling-bury writes -3 and only explicit UI actions write -2. **Binary contradicts**: `col.sched.answerCard` placed the sibling at `queue=-2` (per rule 13). Both released by `unbury_on_day_rollover` (`rslib/.../bury_and_suspend.rs:44-50` — SQL `c.queue in (-3, -2)`). TT mirrors via `collocation_directions.bury_kind`:
    - `'sched'` → released by daily sweep
    - `'user'` → sticks across rollover (from `POST /api/srs/bury`)
    - `NULL` → non-buried

    This tri-state is now declared as `domain=BURY_KIND_DOMAIN` in `app/srs/direction_fields.py` (the single source), hard-enforced at write time by a **v35 SQL CHECK** (`bury_kind IN (NULL,'sched','user')`), and swept per-sync into `INVARIANT_TRACE` soak lines (plus the `bury_kind`-set-⇒-`state='buried'` coupling); `tests/test_direction_invariants.py` pins the CHECK domain back to the registry.

    `_bury_kind_from_queue` maps **both `queue=-2` and `queue=-3` to `'sched'`**. Pre-Layer-35 buried rows backfilled to `'user'` (pessimistic). **Do NOT** add an unconditional `UPDATE … WHERE state='buried'` — pre-Layer-35 bug that wiped 18 manually-buried cards per poll.

    **Two follow-up bugs found 2026-05-16:**
    - `_DIR_COLUMNS` in `database.py` didn't include `bury_kind` — every read returned `None`. Fixed.
    - `_direction_differs` didn't check `bury_kind` — kind-only flips were silent no-ops. Fixed.

    **BURY_TRACE diagnostic.** Every sync_pull emits `BURY_TRACE` INFO lines per buried direction + summary (`anki_queue_minus2_seen`, `anki_queue_minus3_seen`, `buried_to_released_writes`, etc.). `anki_queue_minus2_seen > 0` is no longer suspicious — Anki's binary writes -2 for sibling-bury.

11. **`learning_cutoff` has 4 advancement triggers (Layer 36).** Mirrors Anki's `current_learning_cutoff`:
    1. **Grade** — `drill_feedback` → `advance_learning_cutoff(db, now)` (Anki: `queue/mod.rs:217-243`).
    2. **Session-start** — `/review-queue?session_start=1` → cutoff = `now`. Anki: queue build at deck open.
    3. **sync_pull ingest** — advances cutoff to latest revlog timestamp pulled.
    4. **counts.all_zero auto-bump** — when `ready_learning` AND `ordered_main` are both empty AND any `pending_learning.due_at ≤ now`, advance cutoff to `now` and re-split. Mirrors `CardQueues::counts()` end-of-session "surface ripened card without a grade" path.

    **Stickiness invariant**: if main OR ready_learning has items, cutoff is frozen between grades. Auto-bump is end-of-session only. A learning card that ripens mid-session does NOT preempt the card on screen. Don't add a "live `now`" or per-poll advance.

12. **Daily caps limit the served queue, not only the badge (corrected Layer 75; review→new coupling Layer 77).** Anki gathers at most `new_limit - introduced_today` new cards AND `review_limit - reviews_today - introduced_today` review cards into the study session (Layer 76: new intros charge the review budget too) — the limits cap the actual review flow, not just the deck-list badge. **The review limit also caps new cards** (`new_cards_ignore_review_limit` defaults off): Anki caps `new = min(new, review)` at build (limits.rs:104-108) and re-mins it per gathered review (`decrement()`, limits.rs:131-141), so new gathered = `min(new_quota, review_budget − reviews_gathered)`. `_compute_live_main` mirrors **all three**: `nonlearning_due[:review_remaining]` (after sibling-bury, keeping lowest-R survivors), then `new_quota = min(new_quota, review_remaining − len(due slice))`, then `nonlearning_new[:new_quota]`. *Intraday* learning cards (queue=1) are exempt from the review cap; *interday* learning (queue=3) DOES charge it — **mirrored as Layer 79 (2026-07-10)**: Anki gathers day-learning under `LimitKind::Review` before reviews (gathering.rs:35-61, same `decrement()` that re-mins new), oracle-pinned by `test_parity_daily_caps.py::test_anki_interday_learning_charges_review_limit`; TT charges `count_interday_learning_due(today)` (state learning/relearning, `due_at − last_review ≥ 1` day, due within today's 4am window) inside `effective_review_budget`, flag or no flag — the cards still display in the *learning* badge and serve from the learning queue uncapped. Known residual: when interday count EXCEEDS the budget, Anki gathers only budget-many (its learning count shrinks); TT doesn't cap the learning queue. The earlier claim here ("caps are render-only / queue does NOT cap / Anki only caps the badge") was wrong — the new-card cap was always applied in assembly, and a 50-review cap that served 1499 reviews was the bug (user report, 2026-06-28). The freeze model stays consistent: `reviews_today` grows as you grade so the cap tightens, but graded cards leave the due pool, so the surviving frozen reviews always equal the remaining budget — and the same argument keeps the new-card headroom `review_budget − len(due slice)` stable mid-session (no mid-session drops). Badge and queue cap with the same `effective_review_budget`. **Brief #4a (2026-07-10):** `new_cards_ignore_review_limit` is no longer assumed off — it's synced from Anki's config table (collection-level bool `newCardsIgnoreReviewLimit`, confirmed empirically vs the 26.05 binary) via `refresh_new_cards_ignore_review_limit`, resolved by `resolve_new_cards_ignore_review_limit(db)` (default False), and threaded through `effective_review_budget(..., new_cards_ignore_review_limit=…)` plus both the badge and served-queue new-caps; when ON, new intros don't charge the review budget and the review budget doesn't cap new cards.

13. **Trust the binary, not the source, when they disagree.** `/tmp/anki-source/` is a shallow clone of `main`, not a release tag — can be ahead of or behind the user's Anki. When TT mirrors source and still diverges, reproduce against the binary (recipe in `docs/anki-parity-diagnostics.md` §"Reproduce queue head against the Anki binary"; Anki must be CLOSED — Collection wants exclusive write access). Layer 38 was found this way. Source remains the right starting point — just not the final word.

14. **NULL R-value sorts at `desired_retention` (Layer 38).** Cards with no FSRS memory_state (`cards.data='{}'`) are placed by Anki at the position `desired_retention` occupies in R-asc — between R<dr and R>dr, NOT NULLs-first, NOT NULLs-last. `compute_retrievability` returns `desired_retention` (default 0.9) instead of None. Cached at sync via `refresh_desired_retention` (**proto field 37**, NOT field 40 = `historical_retention`, a pre-Layer-38 footgun).

15. **`anki_card_mod` must be in `_direction_differs` (Layer 37).** FNV tiebreaker `fnvhash(cards.id, cards.mod)` is appended to every Anki `review_order_sql` variant (`rslib/.../card/mod.rs:897`). Anki bumps `cards.mod` for non-FSRS reasons (sync mtime, housekeeping, bury); if the diff misses the bump, TT's FNV hash drifts. Keep `anki_card_mod` marked `sync_comparable=True` in `app/srs/direction_fields.py` — it's an ORDER BY input.

## Divergence playbook

Walk this tree on a divergence report. Each leaf → mechanism that handles it; verify it's still firing, then look for new edge cases.

**Badge wrong:**
- `new`: badge = `min(remaining_quota, available)` where `remaining_quota = new_cap − count_new_introduced_today(today)` and `available = count_new_available_collocations(today)` when `bury_new` else raw `count_new_available()` (Layer 64). **Two failure modes:**
  - *quota* — `count_new_introduced_today(today)` filters `introduced_at` within today's UTC range (Layer 26). Stamped once per intro arc. Pre-Layer-26 rows have NULL and don't count (intentional). "Introduced X, badge didn't decrement" → check `SELECT introduced_at FROM collocation_directions WHERE collocation_id=...`. Empty = grade path didn't stamp. Don't reintroduce the legacy `prior_state='new' AND last_review today` filter.
  - *availability* — "TT new badge > Anki, no sync" with a graduated sibling → new-sibling bury (Layer 64). `count_new_available_collocations` excludes a NEW direction whose collocation has a sibling that is graded-today / learning / review-due-today (the served queue via `_compute_live_main` already buries these — the badge must match). A *future*-due review sibling does **not** bury; don't widen the filter to "any review sibling." Diagnostic: compare `count_new_available()` (raw) vs `count_new_available_collocations(today)` — if raw is higher, a sibling is suppressing it.
- `learning`: `db.count_learning()` — pure TT state. Drifts on Anki-side grades until sync (expected). **Caveat**: `promote_to_learning` from listen-first UI sets `state='learning'` without `left`/`due_at` — TT counts it while Anki keeps `queue=0`. Documented TT-only addition.
- `review`: `min(db.count_review_due_collocations(today), effective_review_budget(daily_review_cap, reviews_today, introduced_today))` where the budget = `max(0, daily_review_cap − reviews_today − introduced_today)`. **Layer 76**: new cards introduced today ALSO charge the review-per-day limit (Anki `rslib/src/decks/limits.rs:104-108`, gated on `new_cards_ignore_review_limit` = off by default), so the budget nets out `count_new_introduced_today` too — not just `reviews_today`. Signature of the pre-76 bug: TT review badge sits *above* Anki's by exactly the number of new cards introduced today (surfaces "create a card in TT, study it, sync, counts don't match"). Same helper feeds the served-queue cap (`queue_engine.py`) and the new-badge review-budget cap. **Layer 27**: stale `state='buried'` rows under-count. **Layer 36**: cap via `reviews_per_day`. **Layer 67**: the "graded-today" window is the **4 AM-local rollover** (`_anki_day_bounds_utc`), NOT local midnight — a sibling graded in `[midnight, 4 AM)` local is "yesterday" for Anki, so don't let TT bury its review sibling. Signature: TT *under*-counts by exactly the number of dual notes graded in that window; reverse set empty. If consistently lower than raw count, check `anki_state_cache['daily_review_cap']` and `count_reviews_completed_today`. Daily unbury sweep fires on every relevant request — check `COUNT(*) WHERE state='buried'` against siblings of today's grades.

**Queue head wrong (review-state):**
- **CHECK FIRST** — top-of-file divergence #3 (asymmetric rebuild cadence) if heads have wildly different stabilities and R near-tie.
- Compare R values (snippet below). Different R → check which branch of `extract_fsrs_retrievability` Anki used (lrt presence) and whether `compute_retrievability` matched (Layer 11/15).
- Check `session_main_queue` cache — stale + mid-session transition → Layer 7's invalidation may not have fired.
- Check `today_col_day` vs Anki's `last_day_studied` — timezone/rollover bugs (Layer 13).

**Duplicate TT collocations linked to same Anki note (Layer 35 cleanup):**
- Two collocations sharing `anki_note_id` → "phantom direction" mis-classifications in `_merge_directions` (Layer 33). `sync_pull`'s `get_collocation_by_anki_note_id` returns FIRST cid SQLite finds → one gets live updates, other stays stale → Layer 33 sinks the stale one to bottom. Symptom: "card disappears from TT new-head though Anki shows it next."
- Diagnostic: `SELECT anki_note_id, COUNT(*) FROM collocations WHERE anki_note_id IS NOT NULL GROUP BY anki_note_id HAVING COUNT(*) > 1`. Should be 0 (3 pairs merged in Layer 35: ulica, Bog, ura). If more appear, use `scripts/anki_archive/dedupe_tt_collocations.py` pattern.
- Architectural note: any new code path that creates a second collocation for an existing Anki note MUST also handle dedupe.

**Queue head wrong (Anki=learning, TT=main, both have card):**
- **CHECK FIRST** — top-of-file divergence #1. Almost always one of:
  1. **Cutoff frozen** — refresh `/review` or grade any card.
  2. **Independent grading drift (no sync)** — sync TT then File→Sync in Anki (TT push brings card mods that satisfy Anki's `requires_study_queue_rebuild`); refresh TT.
- Signature: TT's `anki_due` is much older than Anki's `cards.due`.

**User-buried cards keep coming back in TT (Layer 35):**
- Anki has card at `queue=-2`, TT shows `'review'` or `'new'`. Causes:
  1. Sync hasn't run → expected.
  2. **Expected after rollover.** Both -2 and -3 map to `'sched'`; both released by rollover. NOT a bug unless before rollover or you explicitly used `POST /api/srs/bury`.
  3. `_bury_kind_from_queue` wasn't called for this write path. Audit all 5 DirectionState sites in `sync_pull`.
  4. **Rollover + true user-bury.** `_bury_kind_from_queue` can't distinguish user-bury from sibling-bury (both = -2). After rollover Anki releases; only way to keep TT-buried is `POST /api/srs/bury` (writes `'user'` directly). Check `BURY_TRACE` for `buried_to_released_writes`.

**TT shows a stuck cohort of `state='buried' bury_kind='user'` rows (post-Layer-35 lock):**
- Cause: Layer 35's pessimistic `'user'` backfill on existing buried rows, compounded if Layer 35-era latent bugs are still present (`_DIR_COLUMNS` missing `bury_kind`, or `_direction_differs` not checking `bury_kind` — both fixed 2026-05-16). The cohort releases via state-mismatch on the next sync once those two bugs are in.
- Diagnostic: `SELECT bury_kind, state, COUNT(*) FROM collocation_directions GROUP BY bury_kind, state`. Non-trivial `'user'` count AND no `'sched'` rows anywhere = locked cohort. Check `BURY_TRACE` `buried_to_released_writes` after next sync.

**Queue head wrong (new card placement):**
- **CHECK FIRST — Phase 3 introduction gate (Layer 65).** A **production** NEW card is withheld from the new pool until its recognition sibling graduates past the learning arc (`get_new_items` `NOT EXISTS` clause, production direction only; recognition never gated; cloze always introducible). So for a paired both-NEW note, **recognition is introduced first and production is held** — TT does *not* surface production first. This **supersedes Layer 28's "production-first" claim below**, which was empirically wrong: the user's Anki introduces recognition first (604/36 across paired notes; Anki orders by deck position and recognition sits at a lower position). Don't "restore" production-first to match the stale Layer 28 text. The badge (`count_new_available_collocations`) already matches — no badge change. Realizes `word-learning-state-machine.md` Phase 3.
- Verify intersperser ratio `(R_remaining + 1) / (N_quota + 1)`. NO session-start override (Layer 14).
- Sibling-bury: gather `new_quota * 4`, bury siblings, cap at `new_quota` post-bury.
- **Sort key (Layer 25)**: `get_new_items` ORDER BY = `d.anki_due DESC NULLS FIRST, c.created_at DESC, d.anki_card_id ASC, c.id ASC`. **Requires Anki deck setting**: New card gather order = "Descending position".
- **Cross-direction gather + bury + Template sort (Layer 28)**: per-direction isn't enough. Anki gathers BOTH ords in one pass and buries second-seen → higher-due wins (`rslib/.../queue/builder/gathering.rs:157-169`). Then `sort_new` stably re-sorts by `ord`. TT pipeline: `_merge_directions` (gather), `_bury_siblings_in_queue` (keep first-seen), `get_review_queue` (stable sort by ord). If TT new-head disagrees with Anki:
  1. **Cache check first.** `clear_session_main_queue` and refetch.
  2. `_merge_directions` output sorted by `(anki_due DESC NULLS FIRST, ord ASC, anki_card_id ASC, row_id ASC)`?
  3. After bury: each collocation_id once, on the higher-anki_due direction (or ord=0 on ties).
  4. After stable sort by ord: all surviving rec before surviving prod, gather order preserved per group.
  5. **Don't re-introduce per-direction-only sorts in `get_new_items`.** Layer 25's ORDER BY is necessary input ordering, not sufficient alone — `_merge_directions` re-sorts the combined pool.

**Queue head wrong (both apps learning, different card):**
- **CHECK FIRST** — top-of-file divergence #2.
- Resolution: sync. Each card converges to later grader's `due_at` per rule 6 / Layer 17.
- **Do not** chase this in queue-assembly code — both sorts work correctly; inputs differ.

**Queue head wrong (learning card re-appears immediately after grading):**
- Anki's "collapse" (`rslib/.../queue/learning.rs:94-113`) shifts a just-graded card past next-soonest pending when main is empty. TT mirrors in `get_review_queue` by swapping `pending_learning[0]` and `[1]` when head's `last_review == cutoff`. If you change queue assembly, re-verify the collapse.

**`left`/`total_remaining` mismatch:**
- `_direction_differs` must compare `left` (Layer 17). If still wrong:
  - `sync_pull`: dirty_fsrs + `_anki_step_ahead` (Layer 18) takes Anki's `left` when ahead.
  - `sync_push`: `OfflineWriter.get_current_card_state` + skip-when-Anki-ahead (Layer 19).
- Push doesn't currently write `reps`/`lapses` — open issue.

**Sync silently skipped a card:**
- TT row missing for Anki note. `sync_pull` doesn't create new TT rows; only `import_seed` does. Run `uv run python -m app.plugins.anki_sync.import_seed --deck "0. Slovene"`.

## Diagnostic commands

Moved to `docs/anki-parity-diagnostics.md` (snapshot-the-DBs, live badges + queue head, introduced-today, step-state, R-value compare, force-fresh-queue, binary repro, soak classifier). Open it when actively debugging.

## Maintenance strategy — keep the mirror, hold it cheaply (decided 2026-06-12)

**The Anki mirror is the product, not a means to sync.** Anki is a reference FSRS implementation; TT mirrors it because its behavior *is* the correct SRS behavior we want users to get. Sync is a bonus on top. Two corollaries:

- **The Layers are encoded SRS correctness, not tech debt.** Most Anki choices (sibling-bury, fuzz, the load balancer, R-ascending, learning steps, NULL-R placement) exist for a good reason. **"Fewer Layers" is not a goal.** Do not propose deleting mirror behavior to simplify.
- **The leak rate has slowed way down** — decelerating toward a fixed point (Anki's branch set is finite and mostly covered). Evidence in the commit log: **34 Layer commits in 2026-05 vs 6 in the first 12 days of 2026-06**, and Layers 69–72 are all *sync-seam* fixes (push→pull state, recency guards) — the queue/FSRS **mirror itself** hasn't needed a fix since ~Layer 64 (2026-05-31). The mirror is stable; the recent trickle is the sync cherry, not the cake.

**Goal: minimize the cost of *holding* the mirror — behavior-preserving only.** Two cost drivers:
1. **Duplication** — the same mirror logic living in N places (the two-branch R formula, the 21-helper table; the 4 AM rollover was single-sourced into `app/srs/anki_mirror/rollover.py` 2026-07-03 — follow that pattern). Single-source it so the next Layer is applied once, not N times. Highest value, lowest risk. (The "Pre-Layer checklist" exists to compensate for exactly this — fix the cause.)
2. **Illegibility** — RESOLVED for `database.py` (god-module split, 2026-07-04): it is now a ~60-line composition facade over per-concern mixins (`db_base` infra + `db_collocations`, `db_directions`, `db_queue`, `db_counts`, `db_revlog`, `db_sync`, plus the inert `db_media`/`db_kv_cache`/`db_histogram`/`db_lemma_cache`/`db_ignored_lemmas`/`db_sync_conflicts`); import and patch through `app.srs.database` as before. `api/srs.py` had its queue engine extracted to `app/srs/anki_mirror/queue_engine.py` in the same effort; what remains there is HTTP-layer code. Keep decomposing by concern **opportunistically** (when already in there for another reason), never a big-bang teardown of parity code.

The **oracle harness** (TT output == Anki output) and the **soak** (FSRS bit-exactness) are the safety nets that make de-dup / decompose safe — keep them green, expand the harness. They are the asset that lets you simplify *without* losing the mirror.

### Path 2 — considered and REJECTED (do not re-pitch)

Recorded so it isn't re-proposed every session: **Path 2** would be — at sync, run Anki's `review_order_sql` (R supplied via a registered SQLite UDF, so still no `import anki`) and persist the resulting card-id sequence as an "anchor queue"; between syncs serve from that snapshot filtered by "graded since sync." It would dissolve the freeze / intersperser / R-asc reconstruction code.

**Rejected, because it trades away the live mirror, which is the product.** TT rebuilds the queue on every `/review` mount (`session_start=1` → `build_and_freeze_main_queue`, re-sorting by current R — `api/srs.py:1537`) — a valued, Anki-faithful behavior the user explicitly wants to keep. Path 2 makes the sync-time snapshot authoritative and **cannot re-derive on the live request path** (rule 1 forbids opening the collection there), so it would kill refresh-rebuild. It also isn't triggered by its own former threshold ("only when the leak count won't stop") — the leak rate is *falling*. **Default answer: no.** Revisit only if leaks *accelerate* into a genuinely new fundamental-divergence class; otherwise maintain the mirror per the strategy above.

**The FSRS load balancer — now MIRRORED in the live grade path (Layer 53 port → Layer 55 wiring).** If `config['loadBalancerEnabled']` is set, Anki relocates every graded card's interval to a less-loaded day *within* the fuzz range, using the whole collection's due-date histogram (`states/fuzz.rs:36-42` tries `load_balancer_ctx.find_interval` before pure fuzz; wired into both the live answer path `answering/mod.rs:237-258` and the reschedule path `fsrs/memory_state.rs:218`). **History: this section used to say "TT does not mirror this and should not" — that is now stale.** Layer 53 ported the balancer bit-exact but applied it only at sync (read Anki's load-balanced `cards.due` verbatim). Layer 55 then **wired it into TT's live grade path**: a TT-native grade now load-balances like Anki via `build_live_load_balancer` (`queue_stats.py`) threaded into `schedule(load_balancer=…)` at the three grade call sites (`api/srs.py`), gated on `resolve_load_balancer_enabled`. The "needs a global histogram so TT can't" argument was wrong for the single-preset Slovene deck — TT's own `collocation_directions` *is* that histogram (`load_balancer.py`, `_anki_rng.py`, `test_parity_load_balancer.py`; 24/24 oracle bit-exact). **Sync is still pass-through** (`sync_pull` reads `cards.due` directly), so synced cards get Anki's pick verbatim regardless. **Residual `due_at` ±1–2 day signature now means a real config mismatch**, not an accepted gap: if TT's stored interval lands *inside* the fuzz `[lower, upper]` but differs from Anki's pick, check that `resolve_load_balancer_enabled` agrees with `config['loadBalancerEnabled']` and that the histogram (`get_load_balancer_histogram` + session replay) matches — don't dismiss it as cosmetic.

**Already-decided non-issue: the 05-21 restore difficulty divergences in the Stage 3b compare shadow (2026-05-28, washed out 2026-05-30).** A Check Database / forced AnkiWeb download (restore) on 2026-05-21 re-stamped ~2333 revlog rows (ids collapsed into sequential runs within single seconds, one `usn` per cluster). Many are duplicate re-gradings Anki **never applied to `card.data`** — proving Anki's `card.data` is not a pure replay of its revlog. The event-sourcing shadow replay over-applied them, producing a transient cohort of difficulty-only divergences (`fsrs_difficulty_replayed` vs `fsrs_difficulty`) that decayed **104 → 6 → 0** as those cards were re-graded with clean revlog rows (so Anki's `card.data` caught back up to the replay). This was always **shadow-only (zero production impact)** — legacy/authoritative takes Anki's `cards.data` verbatim. The cohort was a lingering historical artifact aging out, **not structural**; never "fix" TT's FSRS/replay to match a restore. The anchor-to-`card.data` design for compare→new still stands as the prophylactic for a *future* restore/import (it would prevent the cohort ever appearing), not for an active floor (see `docs/archive/stage-3b-empirical-measurement.md` "Design note (2026-05-28)" and Path 2 above).

**Soak health check — the signal depends on `event_sync_pull` mode. Check the mode first** (`SELECT value FROM anki_state_cache WHERE key='event_sync_pull'`):

- **`new` mode (live since 2026-06-02 — current).** The signal is **`recompute_divergences ≈ 0` per sync**, NOT the shadow classifier. In new mode `_write_compare_shadow` is gated off (`sync.py`, `if event_mode == "compare"`), so `stability_replayed`/`fsrs_difficulty_replayed` are **frozen** at the last compare-era sync — running the shadow classifier below yields **false positives** (every direction graded since the flip reads as "diverging"; e.g. 2026-06-02 showed 58 stability_diverge, all from the post-flip native-grade session, clean partition at the last compare sync, 0 from any prior day). Where to read the real signal:
  - **`~/.tunatale/logs/sync.log`** — each non-dry CLI sync appends a `SYNC_SOAK mode=… recompute_divergences=N` heartbeat plus one `RECOMPUTE_DIVERGENCE cid=… replay_s=… anki_s=…` line per divergence (`_write_sync_soak_log`). `grep RECOMPUTE_DIVERGENCE ~/.tunatale/logs/sync.log` → expect empty; a hit means a genuine Anki recompute event (Optimize / FSRS-param / retention / FSRS-toggle / restore) the forward-step replay couldn't reproduce.
  - **API/server stderr** — `_record_recompute_divergence` emits the same `RECOMPUTE_DIVERGENCE` WARNING; the `/api/anki/sync` JSON carries `recompute_divergences`.
  - **Read-only proxy (no sync, safe while Anki open)** — compare TT-authoritative `stability`/`fsrs_difficulty` vs Anki `cards.data` (parse JSON `s`/`d` per `anki_card_id`). New-mode's take-Anki-verbatim contract ⇒ bit-exact post-sync (verified 1345/1345, 2026-06-02). This is the strongest correctness check and what to run for an ad-hoc soak check.

- **`compare` mode (historical / rollback target).** When the shadow columns ARE maintained, **both signals target 0**:
  - **`stability_replayed` divergence should be 0.** Any stability divergence is a genuine issue worth investigating (this is what Layer 58 fixed).
  - **`fsrs_difficulty_replayed` divergence should be 0 too.** The old "~104 benign floor" is **retired** — the 05-21 restore cohort washed out by 2026-05-30 (confirmed 0/1349 across two same-day syncs with 245 rows ingested, 422 recently-graded directions all bit-exact on both fields). A non-zero difficulty count now warrants the same scrutiny as stability: check whether it's a *recently-graded* card (real regression — e.g. a `_next_difficulty` op-order/f32 drift, cf. Layer 59) versus a *new* historical restore/import cohort (re-confirm the `card.data ≠ pure revlog replay` signature before declaring benign, and treat it as transient that will age out — don't let it normalize a standing floor).

  Classifier (TT-side only, no Anki needed) — **compare-mode only**: `docs/anki-parity-diagnostics.md` §"Soak compare-shadow classifier". `stability_diverge=0` and `difficulty_only_diverge=0` ⇒ healthy. Full trail: memory `project_stage3b_soak_finding_difficulty_replay`.

## Source references

The `anki-source-expert` subagent reads `/tmp/anki-source/` and cites file:line, pairing Anki's behavior with TT's parallel code path — ask it when in doubt. The key-files map (queue builder, learning, intersperser, R-extraction, timing, fuzz seed) lives in `docs/anki-parity-diagnostics.md` §"Source references".

## Pre-Layer checklist — read before opening a new Layer fix

Phase 1's elapsed-days collapse (commit `3ec0aa5`) and Phase 2.2.1's Layer 42 finding both came from the same shape: a Layer-style divergence fix was authored as fresh code that duplicated logic already living somewhere in TT. Before opening a new Layer fix, walk this list.

**Step 1: name the divergence.** What's TT computing that doesn't match Anki? Be specific about *which output* — a stability number, a queue position, a badge count, a state transition.

**Step 2: scan the load-bearing helpers for an existing implementation.** If your fix is going to compute X, and one of these helpers already computes something X-shaped, the fix should extend the helper, not reimplement it elsewhere. The full helper↔path↔coverage table (21 helpers across `fsrs.py` / `queue_engine.py` / `sync.py` / `queue_stats.py` / the `db_*` mixins) is in `docs/anki-parity-diagnostics.md` §"Load-bearing helpers" — read it before writing any new stability/difficulty/queue/sync code.

**Step 3: ask the duplication question.** *"Would my fix compute or branch on the same thing one of those helpers already does?"* If yes:
- **Factor first.** Extend the existing helper (or extract a shared sub-helper, like `_elapsed_days_for_fsrs` did for Layers 11/15/40) before writing the fix at the new call site.
- **Add ONE call site for the new path**, then verify the existing call sites still produce the right values for their cases.
- *Then* write the Layer fix.

If you skip Step 2/3, you'll end up with two independent code paths reverse-engineering the same Anki branch (Phase 1 Learning 1). The duplication won't show up in normal tests; it shows up the next time Anki changes that branch and only one of your two paths gets updated.

**Step 4: check whether Phase 2's harness already covers this.** Run `cd backend && uv run pytest tests/test_parity_*.py --run-oracle --no-cov`. If a harness test fails on the input that triggered your divergence report, you've reproduced the bug — fix TT, the harness will re-go-green. If no harness test fails, consider whether the divergence is in a domain the harness should cover (see `.claude/rules/anki-oracle-harness.md` for when to add a new harness test).

**Step 5: append to `docs/anki-parity-layers.md`.** Number the Layer (next free integer). Lead with the bug, then the mechanism, then files touched. Cross-link any helper you extended.

## Cross-references

- `.claude/rules/anki-sync.md` — USN, safety envelope, schema-change workflow.
- `.claude/rules/anki-oracle-harness.md` — Phase-2 parity harness: when to add harness vs unit tests, subprocess boundary, synthetic-collection gotchas.
- `docs/anki-parity-diagnostics.md` — pull-up reference: every diagnostic bash/python snippet, the source file:line map, and the 21-row load-bearing-helper table (moved out of this rule file to keep it lean).
- `docs/anki-mirror-audit.md` — **inspection-driven** audit workflow: pin the source you mirror to the user's exact anki/fsrs-rs versions, the helper↔source map, the `fsrs_rs_python` differential-test recipe, and the live/dormant/inert triage rubric. Run it proactively (found Layers 62–63); the soak's incremental anchoring can't see a `schedule()`-only bug.
- `docs/anki-parity-layers.md` — full layer history.
