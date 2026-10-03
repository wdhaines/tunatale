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

*Path-scoped: loads when you read a file matching the `paths:` frontmatter. If you
need it without touching those files (e.g. a divergence report arriving in
conversation), read it directly.*

Read this before changing `backend/app/api/srs.py`, `backend/app/srs/fsrs.py`,
`backend/app/srs/anki_mirror/queue_stats.py`, or the sync modules.
`backend/app/plugins/anki_sync/sync.py` is the runner and re-export facade: the
reconcile engine is in `sync_engine.py`, collection I/O in `sync_reader.py` /
`sync_writer.py`, shared leaf helpers in `sync_common.py`, and "sync.py" below
means that facade surface. The full layer history is in
`docs/anki-parity-layers.md` — open it when asking "have we hit this exact thing
before?"

## What "parity" means

The user grades the same deck in both apps. Between syncs they run
independently; the next-served card and the three badge counts
(`new`/`learning`/`review`) must stay close enough that switching apps doesn't
feel discontinuous. **Sync is the alignment moment**, and bounded drift between
syncs is acceptable.

Anki is the reference implementation. TT mirrors Anki's algorithms but **reads
`collection.anki2` only at sync time**, never on the live request path. A request
handler that consults Anki's collection is a regression unless it is
`sync_pull`/`sync_push`.

## Three most common benign divergences

These account for most "TT and Anki disagree on the head card" reports. **Check
all three before pattern-matching to anything algorithmic.** All three resolve at
the next TT sync.

### #1 — Cutoff frozen at last grade (rule 11)

**Signature (TT frozen):** Anki serves a *learning* card, TT serves a
*main/review* card, both apps have the card, and TT's `learning_cutoff` is within
seconds to minutes of the learning card's `due_at`.

**Signature (Anki frozen, the mirror case):** TT serves a *learning* card, Anki a
*main/review* card. TT's `?session_start=1` advanced its cutoff (frontend
`/review` mount), but Anki's cutoff froze at the last grade and its deck hasn't
been rebuilt since. Same mechanism, opposite direction.

**Why:** the cutoff advances only on grade, session start, `sync_pull`, and the
end-of-session auto-bump. If you grade in Anki 2s *after* a learning card ripens,
Anki advances past its `due` and serves it; TT, last graded *before* the
ripening, keeps its cutoff frozen, and the card waits in the `pending_learning`
tail.

**Resolution:** refresh `/review` (the frontend `onMount` sends
`?session_start=1`, setting cutoff = `now`) or grade any TT card. For the mirror
case (TT ahead, Anki behind): grade any Anki card, switch to a different deck and
back, or close and reopen Anki. File→Sync alone won't help if the sync brings no
card changes (see #3). **Don't** add a "live `now`" path or a per-poll cutoff
advance — the stickiness is intentional, and Anki works the same way.

**Diagnostic:** `docs/anki-parity-diagnostics.md` §"Cutoff frozen" — if the
earliest learning `due_at` is just past the cutoff, this is it.

### #2 — Independent grading drift

Each app stamps its own `last_review` when the grade fires and computes
`due_at = last_review + step + fuzz`. Grading the same card in both apps, button
presses a second or a few apart produce `due_at` deltas of 1–5s — enough to
invert two cards whose Anki gap is 1s.

**Signature (cross-queue):** TT shows review/main, Anki shows learning, and TT's
`anki_due` is much older than Anki's `cards.due`.

**Signature (intra-learning):** both apps show a learning card at the head, but
*different* cards from the same stack — positions 0/1 (or 0/2) swapped, with
per-card `due_at` deltas of seconds.

**Why:** identical FSRS fuzz on slightly different timestamps. Card A graded in
Anki 3s before TT, card B 2s after: Anki orders A(X) → B(X+1), TT orders
B(X−2) → A(X+3). A 5s swing inverts a 1s gap.

**Resolution:** sync. `_direction_differs` includes `due_at` (rule 6), so after
one round-trip both apps converge on the later grader's timestamp for each card.
**Don't** overwrite timestamps client-side — convergence is sync's job.

**Diagnostic:** `docs/anki-parity-diagnostics.md` §"Grading drift" — if per-card
deltas are seconds and the step (due − grade) matches between apps, it's drift,
not a bug.

### #3 — Asymmetric queue-rebuild cadence (an R-ascending inversion only one app captured)

Both apps freeze the main review queue and never re-sort it mid-session (Anki's
`CardQueues.main` is a pop-only `VecDeque`, `rslib/.../queue/main.rs:8-46`). But
**Anki rebuilds on far more triggers than TT**, so the two frozen queues can
capture different snapshots of the same state.

**Anki rebuilds on** any op where `OpChanges::requires_study_queue_rebuild()`
returns true (`rslib/src/ops.rs:168-181`): any card change (`c.card`, except
`Op::SetFlag`), any deck change (`c.deck`), specific config ops (`SetCurrentDeck`
/ `UpdatePreferences` / `UpdateDeckConfig` / `ToggleLoadBalancer`), or any
deck-config change (`c.deck_config`); the dispatch site is
`queue/mod.rs:211-215`. In practice: day rollover, **profile/collection reopen**
(lazy-built on first access via `get_queues()`, `queue/mod.rs:246-254`),
**deck-config or preferences change**, **undo**, and any non-grade card or deck
mutation.

**"Deck navigation" is narrower than it sounds.** `Op::SetCurrentDeck` sets
`c.config = true` (and so rebuilds) only when the value actually changes, i.e.
switching to a *different* deck. Clicking the deck you're already studying,
escaping to the deck list and re-entering, or anything else that doesn't move
`col.conf["curDeck"]` is a no-op for rebuilds. To force a rebuild by navigation,
switch to a different deck and back.

**File→Sync rebuilds only conditionally** — when AnkiWeb sends back actual card,
deck or config mutations (e.g. TT pushed rows to AnkiWeb, and another device's
File→Sync pulls them with a new `mod`, setting `c.card = true`). A no-op round
trip returns `c.card = c.deck = c.deck_config = false` and does not rebuild. The
end-of-session auto-bump (`queue/mod.rs:190-196`) fires only when
`counts().all_zero()`, so any card with a non-zero count blocks it.

**Reliable Anki-side rebuild triggers:** quit and reopen Anki (the most reliable —
`Collection::open` → lazy `get_queues()`); grade any card in the deck
(`update_queues_after_answering_card` → `update_learning_cutoff_and_count` →
`current_learning_cutoff = now`); switch to a different deck and back; toggle any
deck preference. A bare File→Sync, re-clicking the same deck, or refreshing the
reviewer does not.

**TT rebuilds only on `sync_pull`** (rule 2). Grading rebuilds in neither app.

**TT sync itself triggers an Anki rebuild.** `safe_open(mode="rw")`
(`backend/app/plugins/anki_sync/safety.py`) requires Anki to be closed, so the
user closes Anki, runs TT sync, and reopens Anki — and **the reopen rebuilds
Anki's queue with current R values**. TT's `sync_pull` and Anki's reopen can be
seconds to minutes apart, and an R-ascending cross can land between them. This is
how "I only synced once and never touched Anki" still produces divergent heads.
Forensic signature: a multi-minute gap in Anki's revlog spanning the cross point.

**Signature:** both apps show a *review* card at the head from the same
candidate set — two different cards with **very different stabilities** (e.g.
s=0.65 vs s=7.5) and near-tied R (delta ~0.001–0.005). R decays at
`1/(s·86400)` per second, so a low-s card decays ~12× faster than a high-s one,
and small wall-clock gaps between rebuild moments invert near-tie pairs.

**Worked example:** after a TT sync, TT froze prodati (R=0.8635) ahead of cloze
"se" (R=0.8639); ten minutes later they crossed. The user then ran File→Sync in
Anki, which pulled card mutations TT had pushed to AnkiWeb, set `c.card = true`,
and rebuilt Anki's queue: cloze (R=0.8620) before prodati (R=0.8633). TT still
showed prodati first. A no-op File→Sync would not have rebuilt Anki's queue.

**Resolution:** `sync_pull` from TT, which rebuilds its frozen queue with current
R values.

**Don't "fix" this** with a mid-session re-sort or by mirroring all of Anki's
rebuild triggers — the freeze-at-build design is intentional (rule 2). The cheap
fix is syncing more often; match Anki's trigger set only if this class proves
frequent.

## Architectural principles (don't undo these)

1. **Anki is a reference, not a runtime dependency.** Every badge endpoint and
   queue-build path reconstructs from TT state alone (`collocation_directions` +
   `anki_state_cache`). If you find yourself opening `collection.anki2` in
   `app/api/`, look for the TT-state-only equivalent
   (`count_new_introduced_today`, `count_review_due_collocations`, etc.).

2. **Cache invalidation plus eager rebuild on sync.** A non-dry-run `sync_pull`
   *clears and eagerly rebuilds* `session_main_queue` via
   `build_and_freeze_main_queue` (Layer 29), so the freeze moment is sync time.
   Each cache key's lifecycle is declared in
   `app/srs/anki_mirror/cache_registry.py` (one spec per key: source, day-scoped,
   max age, logic version; the count is pinned in `tests/test_cache_registry.py`).
   `sync_pull` rewrites every `ANKI_CONFIG` source key via its `refresh_*` calls
   (conserved by `tests/test_sync_cache_conservation.py`), so a missing
   `refresh_*` is caught at sync time. **Deploy pitfall:** the cache lives in
   `anki_state_cache` (DB-backed) and survives restarts, so after changing
   queue-assembly logic it replays the old order until the next sync — a restart
   does not invalidate it. **Always run `clear_session_main_queue` before
   concluding a fix is broken.**

   **Pre-Layer checklist item:** a queue-order change means bumping
   `session_main_queue`'s `logic_version` in `cache_registry.py` (frozen queues
   with a mismatched version are discarded, like a day mismatch).

3. **Sibling-bury via a `last_review` filter plus a learning-queue filter (Layer
   56).** Anki's `bury_reviews=true` removes a note from today's review pool when a
   sibling is active. Two triggers: (a) a sibling was *graded today* — TT excludes
   collocations where any direction has `last_review` today; (b) a sibling is in
   the *learning queue* (`queue=1/3`), **including interday learning steps graded
   on a prior day** — TT also excludes collocations where any direction is
   `state IN ('learning','relearning')` (the `count_review_due_collocations`
   subquery). This is the *review* badge: a review card is **not** dropped for
   merely having a NEW sibling (`bury_new` buries the new card, not the review).
   The converse — the **new** badge mirroring `bury_new` — is **Layer 64**
   (`count_new_available_collocations`): a NEW card is buried when a sibling is
   review-due today or learning; a *future*-due review sibling does **not** bury
   it. Don't write count queries that ignore either filter, and use
   `count_review_due_collocations` (there is no per-direction review counter).

4. **Sync rebuilds with the current pool.** Both apps gather with current-pool
   counts at sync time, not at session start. The intersperser ratio is
   `(one_len+1)/(two_len+1)` over natural list lengths — **don't** add a
   session-start override (tried as Layer 9, reverted at Layer 14).

5. **Two-branch R formula.** `extract_fsrs_retrievability` has an lrt branch
   (sub-day fractional elapsed) and a day-level fallback (integer-day elapsed).
   TT mirrors both in `compute_retrievability`.

6. **Sync must merge both directions.** `sync_push` defers to Anki when Anki is
   ahead (graduated, or smaller `total_remaining`), and so does `sync_pull`.
   `_direction_differs` must compare `left`, `due_at`, `prior_state`, `bury_kind`
   and `anki_card_mod` so self-heal writes actually fire. It and `_DIR_COLUMNS`
   derive from the field registry `app/srs/direction_fields.py`: register a new
   column there with an explicit `sync_comparable` decision and never hand-edit
   either list (`tests/test_direction_fields.py` pins registry ↔ schema ↔ model ↔
   diff).

7. **`prior_state='new'` is sticky.** Set on introduction by `_resolve_prior_state`
   (sync) or `_grade_prior_state` (TT). It persists across same-state-class grades
   and LEARNING→REVIEW graduation, and is released only on REVIEW→RELEARNING
   (lapse), for revlog `type=1` correctness. **Don't** overwrite
   `prior_state='new'` without checking the new state. *Declared as
   `WritePolicy.STICKY_NEW` in `app/srs/direction_fields.py`, pinned to
   `_grade_prior_state` by `tests/test_direction_invariants.py`; its value domain
   is a SQL CHECK.*

8. **`introduced_at` is a one-shot stamp, not a sticky marker (Layer 26).** It is
   written exactly once per direction on the first NEW→non-NEW transition, by
   `fsrs.schedule` or `sync_pull._resolve_introduced_at` (from `MIN(revlog.id)`).
   `count_new_introduced_today` queries this column, not `prior_state` +
   `last_review`. Don't conflate them: `prior_state='new'` lives for the whole
   introduction arc (revlog correctness), while `introduced_at` is a fixed
   timestamp (anchoring Anki's `newToday` parity). *Declared as
   `WritePolicy.ONE_SHOT` in `app/srs/direction_fields.py`, pinned to
   `_resolve_introduced_at` by `tests/test_direction_invariants.py`.*

9. **Daily unbury sweep at queue build (Layers 27, 35).** `db.unbury_if_needed(today)`
   runs at the top of `/queue-stats`, `/review-queue` and `sync_pull`. It restores
   `state='buried' AND bury_kind='sched'` rows to `state='review'` (reps>0) or
   `'new'` (reps=0), tracked via `anki_state_cache['last_unbury_day']` and
   idempotent within the local day. It mirrors Anki's `unbury_on_day_rollover`
   (which releases both `queue=-3` and `queue=-2`). **Don't** let
   `state='buried' bury_kind='sched'` rows accumulate.

10. **`bury_kind` split (Layers 35, 39).** Anki has `queue=-3` and `queue=-2` for
    buried cards. Its source suggests grade-time sibling-bury writes -3 and only
    explicit UI actions write -2, but the binary places the sibling at `queue=-2`
    (rule 13: trust the binary). Both are released by `unbury_on_day_rollover`
    (`rslib/.../bury_and_suspend.rs:44-50` — SQL `c.queue in (-3, -2)`). TT mirrors
    via `collocation_directions.bury_kind`:
    - `'sched'` → released by the daily sweep
    - `'user'` → sticks across rollover. Nothing writes it today: `app/api` has
      no bury route, and the only rows carrying it came from the one-time
      backfill in `app/srs/migrations.py`
    - `NULL` → not buried

    The tri-state is declared as `domain=BURY_KIND_DOMAIN` in
    `app/srs/direction_fields.py`, enforced at write time by a SQL CHECK
    (`bury_kind IN (NULL,'sched','user')`), and swept per sync into
    `INVARIANT_TRACE` soak lines (along with the "`bury_kind` set ⇒
    `state='buried'`" coupling); `tests/test_direction_invariants.py` pins the
    CHECK domain to the registry.

    `_bury_kind_from_queue` maps **both `queue=-2` and `queue=-3` to `'sched'`**.
    **Never** add an unconditional `UPDATE … WHERE state='buried'` — it wipes
    manually buried cards on every poll.

    **BURY_TRACE diagnostic.** Every `sync_pull` emits `BURY_TRACE` INFO lines per
    buried direction plus a summary (`anki_queue_minus2_seen`,
    `anki_queue_minus3_seen`, `buried_to_released_writes`, etc.).
    `anki_queue_minus2_seen > 0` is normal, since Anki writes -2 for sibling-bury.

11. **`learning_cutoff` has four advancement triggers (Layer 36)**, mirroring
    Anki's `current_learning_cutoff`:
    1. **Grade** — `drill_feedback` → `advance_learning_cutoff(db, now)` (Anki:
       `queue/mod.rs:217-243`).
    2. **Session start** — `/review-queue?session_start=1` sets cutoff = `now`
       (Anki: queue build at deck open).
    3. **`sync_pull` ingest** — advances the cutoff to the latest revlog timestamp
       pulled.
    4. **`counts.all_zero` auto-bump** — when `ready_learning` and `ordered_main`
       are both empty and some `pending_learning.due_at ≤ now`, advance the cutoff
       to `now` and re-split. Mirrors `CardQueues::counts()`'s end-of-session
       "surface a ripened card without a grade" path.

    **Stickiness invariant:** while main or ready_learning has items, the cutoff is
    frozen between grades, and the auto-bump is end-of-session only. A learning card
    that ripens mid-session does not preempt the card on screen. Don't add a "live
    `now`" or a per-poll advance.

12. **Daily caps limit the served queue, not only the badge (Layers 75–77, 79).**
    Anki gathers at most `new_limit − introduced_today` new cards and
    `review_limit − reviews_today − introduced_today` review cards into the study
    session (Layer 76: new introductions charge the review budget too), so the
    limits cap the actual review flow. **The review limit also caps new cards**
    when `new_cards_ignore_review_limit` is off: Anki sets `new = min(new, review)`
    at build (`limits.rs:104-108`) and re-mins it for each gathered review
    (`decrement()`, `limits.rs:131-141`), so new cards gathered =
    `min(new_quota, review_budget − reviews_gathered)`.

    `_compute_live_main` mirrors all three steps: `nonlearning_due[:review_remaining]`
    (after sibling-bury, keeping the lowest-R survivors), then
    `new_quota = min(new_quota, review_remaining − len(due slice))`, then
    `nonlearning_new[:new_quota]`. *Intraday* learning cards (queue=1) are exempt
    from the review cap; *interday* learning (queue=3) charges it (Layer 79): Anki
    gathers day-learning under `LimitKind::Review` before reviews
    (`gathering.rs:35-61`, the same `decrement()` that re-mins new), pinned by
    `test_parity_daily_caps.py::test_anki_interday_learning_charges_review_limit`.
    TT charges `count_interday_learning_due(today)` (state learning/relearning,
    `due_at − last_review ≥ 1` day, due within today's 04:00 window) inside
    `effective_review_budget`; those cards still show in the *learning* badge and
    serve from the learning queue uncapped. Known residual: when the interday count
    exceeds the budget, Anki gathers only budget-many (its learning count shrinks),
    while TT doesn't cap the learning queue.

    The freeze model stays consistent: `reviews_today` grows as you grade, so the
    cap tightens, but graded cards leave the due pool, so the surviving frozen
    reviews always equal the remaining budget — and the same argument keeps the
    new-card headroom `review_budget − len(due slice)` stable mid-session (no
    mid-session drops). The badge and the queue cap with the same
    `effective_review_budget`.

    `new_cards_ignore_review_limit` is synced from Anki's collection-level config
    bool `newCardsIgnoreReviewLimit` via `refresh_new_cards_ignore_review_limit`,
    resolved by `resolve_new_cards_ignore_review_limit(db)` (default False), and
    threaded through `effective_review_budget(..., new_cards_ignore_review_limit=…)`
    and both the badge and served-queue new caps. When it is on, new introductions
    don't charge the review budget and the review budget doesn't cap new cards.

13. **Trust the binary, not the source, when they disagree.** The local Anki source
    checkout (`/tmp/anki-source/`, a shallow clone of `main`, not a release tag) can
    be ahead of or behind the user's Anki. When TT mirrors the source and still
    diverges, reproduce against the binary (recipe in
    `docs/anki-parity-diagnostics.md` §"Reproduce queue head against the Anki
    binary"; Anki must be closed, since the collection needs exclusive write
    access). Layer 38 was found this way. The source is the right starting point,
    just not the final word.

14. **A NULL R-value sorts at `desired_retention` (Layer 38).** Anki places cards
    with no FSRS memory state (`cards.data='{}'`) where `desired_retention` falls
    in R-ascending order — between R<dr and R>dr, not first and not last.
    `compute_retrievability` returns `desired_retention` (default 0.9) instead of
    None, cached at sync via `refresh_desired_retention` (**proto field 37** — not
    field 40, which is `historical_retention`).

15. **`anki_card_mod` must be in `_direction_differs` (Layer 37).** The FNV
    tiebreaker `fnvhash(cards.id, cards.mod)` is appended to every Anki
    `review_order_sql` variant (`rslib/.../card/mod.rs:897`). Anki bumps
    `cards.mod` for non-FSRS reasons (sync mtime, housekeeping, bury), and if the
    diff misses the bump, TT's FNV hash drifts. Keep `anki_card_mod` marked
    `sync_comparable=True` in `app/srs/direction_fields.py` — it is an ORDER BY
    input.

## Three day rules — pick the wrong one and nothing announces it

Anki does not have "a day number". It has three answers to three different
questions, and they coincide for most of the day, which is why mixing them up
survives:

- **"What study day is it?"** → `anki_today_col_day(col_crt, now)`: local calendar
  dates counted from crt's local date, minus one until today's local rollover has
  passed, independent of crt's time of day. Use it for anything meaning *today*:
  FSRS elapsed, the deck's studied-today stamp, the load-balancer frame, the
  col_day a native grade schedules from.
- **"What col_day does this stored day-level marker decode to?"** →
  `compute_anki_day_index`. It is not Anki's `today`; it is the exact inverse of
  the marker `_compute_last_review` writes, and re-anchoring it would shift every
  stored `last_review`.
- **"How long since the last review, at grade time?"** → a *duration* back from
  `local_next_rollover()`, integer-divided by 86400. Neither of the above: Anki's
  answering path measures from `next_day_at`, not from `now`.

For a real `col.crt` (04:00 local) read in its own zone, the first two agree
except inside `[local midnight, 04:00)`, and the third agrees with their
difference unless exactly one endpoint falls in that window. Every symptom is a
one-day slip that self-heals, so none announces itself; the failures seen so far
were an FSRS elapsed off by one, a daily cap silently uncharged, ~8–9% stability
drift on grades near the boundary, and review cards scheduled a day late in both
apps.

**When chasing a one-day discrepancy, first check which of the three the call site
uses.** `scripts/check_anki_day_index.py` fails any `compute_anki_day_index` call in
`app/` whose day argument is "now" or omitted (it reached `app/` three times;
Layer 91). `anki-gates` runs the oracle at 04:xx and again at 01:xx on every CI
run so this class fails loudly; see `.claude/rules/anki-oracle-harness.md`.

## Divergence playbook

Walk this tree on a divergence report. Each leaf names the mechanism that handles
it; verify it is still firing, then look for new edge cases.

**Badge wrong:**
- `new`: badge = `min(remaining_quota, available)`, where
  `remaining_quota = new_cap − count_new_introduced_today(today)` and `available`
  is `count_new_available_collocations(today)` when `bury_new` is on, else the raw
  `count_new_available()` (Layer 64). Two failure modes:
  - *Quota* — `count_new_introduced_today(today)` filters `introduced_at` within
    today's range (Layer 26), stamped once per introduction arc. Rows from before
    Layer 26 have NULL and don't count (intentional). "Introduced X, badge didn't
    decrement" → check `SELECT introduced_at FROM collocation_directions WHERE
    collocation_id=...`; empty means the grade path didn't stamp it. Don't
    reintroduce a `prior_state='new' AND last_review today` filter.
  - *Availability* — "TT new badge > Anki, no sync" with a graduated sibling →
    new-sibling bury (Layer 64). `count_new_available_collocations` excludes a NEW
    direction whose collocation has a sibling that is graded today, learning, or
    review-due today (the served queue in `_compute_live_main` already buries
    these, and the badge must match). A *future*-due review sibling does not bury;
    don't widen the filter to "any review sibling". Diagnostic: compare
    `count_new_available()` (raw) with `count_new_available_collocations(today)` —
    if raw is higher, a sibling is suppressing it.
- `learning`: `db.count_learning()`, pure TT state. It drifts on Anki-side grades
  until sync (expected). Caveat: `promote_to_learning` from the listen-first UI sets
  `state='learning'` without `left`/`due_at`, so TT counts it while Anki keeps
  `queue=0` — a documented TT-only addition.
- `review`: `min(db.count_review_due_collocations(today),
  effective_review_budget(daily_review_cap, reviews_today, introduced_today))`,
  where the budget is `max(0, daily_review_cap − reviews_today −
  introduced_today)`.
  - **Layer 76:** new cards introduced today also charge the review-per-day limit
    (Anki `rslib/src/decks/limits.rs:104-108`, gated on
    `new_cards_ignore_review_limit`), so the budget nets out
    `count_new_introduced_today` as well as `reviews_today`. Signature when this is
    wrong: TT's review badge sits above Anki's by exactly the number of new cards
    introduced today ("create a card in TT, study it, sync, counts don't match").
    The same helper feeds the served-queue cap (`queue_engine.py`) and the
    new-badge review-budget cap.
  - **Layer 27:** stale `state='buried'` rows under-count. **Layer 36:** the cap
    comes from `reviews_per_day`.
  - **Layer 67:** the "graded today" window is the **04:00-local rollover**
    (`_anki_day_bounds_utc`), not local midnight. A sibling graded in
    `[midnight, 04:00)` local is "yesterday" for Anki, so don't let TT bury its
    review sibling. Signature: TT under-counts by exactly the number of dual notes
    graded in that window, with an empty reverse set.
  - If the badge is consistently below the raw count, check
    `anki_state_cache['daily_review_cap']` and `count_reviews_completed_today`. The
    daily unbury sweep fires on every relevant request — check
    `COUNT(*) WHERE state='buried'` against the siblings of today's grades.
  - **There is no pending-grade exclusion** (Layer 81 is retired). A staged card
    in `pending_listen_grades` that is due is counted and served exactly as Anki
    does it, and grading it in the main flow applies a real grade and releases the
    staging (`drill_feedback` clears the row unconditionally). A staged card also
    charges the review budget. If a badge gap tempts you to add a pending clause,
    don't — read Layer 81 in `docs/anki-parity-layers.md` and
    `test_pending_grade_inclusion.py`, which pins the current contract.

**Queue head wrong (review state):**
- **Check first:** divergence #3 (asymmetric rebuild cadence) if the heads have
  very different stabilities and near-tied R.
- Compare R values (snippet in the diagnostics doc). If R differs, check which
  branch of `extract_fsrs_retrievability` Anki used (lrt presence) and whether
  `compute_retrievability` matched (Layers 11/15).
- Check the `session_main_queue` cache — stale plus a mid-session transition
  means Layer 7's invalidation may not have fired.
- Check `today_col_day` against Anki's `last_day_studied` for timezone/rollover
  bugs (Layer 13; see "Three day rules").

**Duplicate TT collocations linked to the same Anki note (Layer 35 cleanup):**
- Two collocations sharing an `anki_note_id` cause "phantom direction"
  misclassifications in `_merge_directions` (Layer 33). `sync_pull`'s
  `get_collocation_by_anki_note_id` returns the first cid SQLite finds, so one
  gets live updates and the other goes stale, and Layer 33 sinks the stale one to
  the bottom. Symptom: "the card disappears from TT's new head though Anki shows it
  next."
- Diagnostic: `SELECT anki_note_id, COUNT(*) FROM collocations WHERE anki_note_id
  IS NOT NULL GROUP BY anki_note_id HAVING COUNT(*) > 1` should return 0 rows. If
  any appear, follow the `scripts/anki_archive/dedupe_tt_collocations.py` pattern.
- Any new code path that could create a second collocation for an existing Anki
  note must also handle dedupe.

**Queue head wrong (Anki = learning, TT = main, both have the card):**
- **Check first:** divergence #1. It is almost always one of:
  1. **Cutoff frozen** — refresh `/review` or grade any card.
  2. **Independent grading drift (no sync)** — sync TT, then File→Sync in Anki
     (TT's push brings card mods that satisfy Anki's
     `requires_study_queue_rebuild`), then refresh TT.
- Signature: TT's `anki_due` is much older than Anki's `cards.due`.

**User-buried cards keep coming back in TT (Layer 35):**
- Anki has the card at `queue=-2` while TT shows `'review'` or `'new'`. Causes:
  1. Sync hasn't run — expected.
  2. **Expected after rollover.** Both -2 and -3 map to `'sched'`, and both are
     released at rollover. Not a bug unless it happens before rollover.
  3. `_bury_kind_from_queue` wasn't called on this write path — audit every
     `DirectionState` construction site in `sync_pull`.
  4. **Rollover plus a true user-bury.** `_bury_kind_from_queue` can't tell a
     user-bury from a sibling-bury (both are -2). After rollover Anki releases it, and
     so does TT: no code path writes `'user'` today, so a card cannot be kept
     buried in TT across a rollover. Check `BURY_TRACE` for `buried_to_released_writes`.

**TT shows a stuck cohort of `state='buried' bury_kind='user'` rows:**
- Buried rows backfilled to `'user'` without a matching Anki-side user-bury release
  through a state mismatch on the next sync, provided `_DIR_COLUMNS` includes
  `bury_kind` and `_direction_differs` compares it (both guaranteed by the field
  registry).
- Diagnostic: `SELECT bury_kind, state, COUNT(*) FROM collocation_directions GROUP
  BY bury_kind, state`. A non-trivial `'user'` count with no `'sched'` rows anywhere
  is a locked cohort; check `BURY_TRACE` `buried_to_released_writes` after the next
  sync.

**Queue head wrong (new-card placement):**
- **Check first: the production introduction gate (Layer 65).** A **production**
  NEW card is withheld from the new pool until its recognition sibling graduates
  past the learning arc (`get_new_items` `NOT EXISTS` clause, production direction
  only; recognition is never gated; cloze is always introducible). So for a paired
  both-NEW note, **recognition is introduced first and production is held**. This
  matches the user's Anki, which orders by deck position with recognition at the
  lower position; don't change TT to surface production first. The badge
  (`count_new_available_collocations`) already matches.
- Verify the intersperser ratio `(R_remaining + 1) / (N_quota + 1)`, with no
  session-start override (Layer 14).
- Sibling-bury: `_compute_live_main` gathers
  `_NEW_OVERFETCH = max(count_new_available(), new_quota + 50)` new cards, buries
  siblings, then caps at `new_quota`.
- **Sort key (Layer 25):** `get_new_items` orders by `d.anki_due DESC NULLS FIRST,
  c.created_at DESC, d.anki_card_id ASC, c.id ASC`. **This requires the Anki deck
  setting "New card gather order = Descending position".**
- **Cross-direction gather, bury and Template sort (Layer 28).** Per-direction
  sorting isn't enough: Anki gathers both ords in one pass and buries the
  second-seen sibling, so the higher-due one wins
  (`rslib/.../queue/builder/gathering.rs:157-169`); `sort_new` then stably re-sorts
  by `ord`. TT's pipeline: `_merge_directions` (gather order), the first-seen
  sibling bury in `_compute_live_main`, then the Template (ord) re-sort in
  `get_review_queue`. If TT's new head disagrees with Anki's:
  1. **Cache first:** `clear_session_main_queue` and refetch.
  2. Is `_merge_directions` output sorted by `(anki_due DESC NULLS FIRST, ord ASC,
     anki_card_id ASC, row_id ASC)`?
  3. After the bury, does each collocation_id appear once, on the higher-anki_due
     direction (or ord=0 on ties)?
  4. After the stable sort by ord, do all surviving recognition cards precede
     surviving production cards, with gather order preserved within each group?
  5. **Don't reintroduce per-direction-only sorts in `get_new_items`.** Layer 25's
     ORDER BY is necessary input ordering, not sufficient alone —
     `_merge_directions` re-sorts the combined pool.

**Queue head wrong (both apps learning, different card):**
- **Check first:** divergence #2.
- Resolution: sync. Each card converges to the later grader's `due_at` (rule 6,
  Layer 17).
- **Don't** chase this in queue-assembly code — both sorts are correct; the
  inputs differ.

**Queue head wrong (a learning card reappears immediately after grading):**
- Anki's "collapse" (`rslib/.../queue/learning.rs:94-113`) shifts a just-graded
  card past the next-soonest pending card when main is empty. TT mirrors it in
  `get_review_queue` by swapping `pending_learning[0]` and `[1]` when the head's
  `last_review == cutoff`. If you change queue assembly, re-verify the collapse.

**`left`/`total_remaining` mismatch:**
- `_direction_differs` must compare `left` (Layer 17). If it is still wrong:
  - `sync_pull`: `dirty_fsrs` + `_anki_step_ahead` (Layer 18) takes Anki's `left`
    when Anki is ahead.
  - `sync_push`: `OfflineWriter.get_current_card_state` plus skip-when-Anki-ahead
    (Layer 19).

**Sync silently skipped a card:**
- The TT row is missing for an Anki note. `sync_pull` doesn't create TT rows; only
  `import_seed` does: `uv run python -m app.plugins.anki_sync.import_seed --deck
  "0. Slovene"` (substitute the deck).

## Diagnostic commands

In `docs/anki-parity-diagnostics.md` (snapshot the DBs, live badges and queue
head, introduced-today, step state, R-value compare, force-fresh queue, binary
repro). Open it when actively debugging.

## Maintenance strategy — keep the mirror, hold it cheaply

**The Anki mirror is the product, not a means to sync.** Anki is a reference FSRS
implementation, and TT mirrors it because its behavior *is* the SRS behavior users
should get; sync is a bonus on top. Two corollaries:

- **The Layers are encoded SRS correctness, not tech debt.** Most of Anki's
  choices (sibling-bury, fuzz, the load balancer, R-ascending order, learning
  steps, NULL-R placement) exist for good reasons. "Fewer Layers" is not a goal;
  don't propose deleting mirror behavior to simplify.
- **The goal is to minimize the cost of holding the mirror, behavior-preserving
  only.** Two cost drivers:
  1. **Duplication** — the same mirror logic living in several places. Single-source
     it so the next Layer is applied once (the 04:00 rollover in
     `app/srs/anki_mirror/rollover.py` is the pattern). The Pre-Layer checklist
     exists to compensate for this; fixing the cause is better.
  2. **Illegibility** — `database.py` is a composition facade over per-concern
     mixins (`db_base` infrastructure plus `db_collocations`, `db_directions`,
     `db_queue`, `db_counts`, `db_revlog`, `db_sync`, and the inert
     `db_media`/`db_kv_cache`/`db_histogram`/`db_lemma_cache`/
     `db_ignored_lemmas`/`db_sync_conflicts`); import and patch through
     `app.srs.database`. The queue engine lives in
     `app/srs/anki_mirror/queue_engine.py`, and `api/srs.py` holds HTTP-layer
     code. Keep decomposing by concern opportunistically, when already in the code
     for another reason — never as a big-bang teardown of parity code.

The **oracle harness** (TT output == Anki output) and the **soak** (FSRS
bit-exactness) are the safety nets that make de-duplication and decomposition
safe. Keep them green and expand the harness.

### Rejected: serving a sync-time "anchor queue"

The idea: at sync, run Anki's `review_order_sql` (with R supplied through a
registered SQLite UDF, so still no `import anki`), persist the resulting card-id
sequence, and serve it between syncs filtered by "graded since sync". It would
remove the freeze/intersperser/R-ascending reconstruction code.

**Rejected, because it trades away the live mirror, which is the product.** TT
rebuilds the queue on every `/review` mount (`session_start=1` →
`build_and_freeze_main_queue`, re-sorting by current R), an Anki-faithful behavior
the user wants to keep. A sync-time snapshot cannot be re-derived on the live
request path (rule 1 forbids opening the collection there), so it would kill
refresh-rebuild. Revisit only if a genuinely new class of fundamental divergence
appears and keeps producing leaks.

### The FSRS load balancer is mirrored in the live grade path (Layers 53, 55)

If `config['loadBalancerEnabled']` is set, Anki moves every graded card's interval
to a less-loaded day *within* the fuzz range, using the whole collection's due-date
histogram (`states/fuzz.rs:36-42` tries `load_balancer_ctx.find_interval` before
pure fuzz; wired into the live answer path `answering/mod.rs:237-258` and the
reschedule path `fsrs/memory_state.rs:218`). TT mirrors it bit-exact: a TT-native
grade load-balances via `build_live_load_balancer` (`queue_stats.py`), threaded
into `schedule(load_balancer=…)` at the grade call sites in `api/srs.py` and gated
on `resolve_load_balancer_enabled`. TT's own `collocation_directions` is the
histogram (`load_balancer.py`, `_anki_rng.py`; pinned by
`test_parity_load_balancer.py`). Sync is pass-through: `sync_pull` reads
`cards.due` directly, so synced cards get Anki's pick verbatim.

**A residual `due_at` ±1–2 day difference now means a real config mismatch**, not
an accepted gap. If TT's stored interval lands inside the fuzz `[lower, upper]` but
differs from Anki's pick, check that `resolve_load_balancer_enabled` agrees with
`config['loadBalancerEnabled']` and that the histogram
(`get_load_balancer_histogram` plus the session replay) matches. Don't dismiss it as
cosmetic.

### Anki's `card.data` is not a pure replay of its revlog

A Check Database or forced AnkiWeb download (restore) can re-stamp revlog rows,
including duplicate re-gradings that Anki never applied to `card.data`. A
revlog-replay comparison then shows a transient cohort of difficulty-only
divergences that decays as those cards are re-graded. Treat such a cohort as a
historical artifact aging out, and **never "fix" TT's FSRS or replay to match a
restore**. TT takes Anki's `cards.data` verbatim at sync, which is what makes a
restore harmless.

### Soak health check

`sync_pull` has a single path that takes Anki's `cards.data` verbatim, so the
signal is **`recompute_divergences ≈ 0` per sync**. Where to read it:

- **`~/.tunatale/logs/sync.log`** — every sync appends a
  `SYNC_SOAK … recompute_divergences=N` heartbeat plus one `RECOMPUTE_DIVERGENCE
  cid=… replay_s=… anki_s=…` line per divergence (`_write_sync_soak_log`).
  `grep RECOMPUTE_DIVERGENCE ~/.tunatale/logs/sync.log` should find nothing; a hit
  means a genuine Anki recompute event (Optimize, an FSRS-parameter or retention
  change, toggling FSRS, a restore) that the forward-step replay couldn't
  reproduce.
- **Server stderr** — `_record_recompute_divergence` emits the same
  `RECOMPUTE_DIVERGENCE` warning, and the sync summary reports the count.
- **Preset changes** — an Optimize or a retention edit writes no revlog row, so
  nothing above records it. Every sync writes `FSRS_PRESET deck=… weights_sha=…
  dr=… due_ratio_median=…` (the median `(due_at − last_review)/stability` over
  review rows), plus a `PRESET_CHANGE` line when the synced deck's weights or
  retention moved since the last sync. `grep NOT_RESCHEDULED
  ~/.tunatale/logs/sync.log` finds the dangerous case: the weights moved and Anki
  wrote no `type=5` (Rescheduled) revlog rows, so due dates are now decoupled from
  stability. Detection only — `app/srs/anki_mirror/preset_watch.py` never writes a
  due date.
- **Read-only proxy (no sync, safe while Anki is open)** — compare TT's
  authoritative `stability`/`fsrs_difficulty` with Anki's `cards.data` (parse the
  JSON `s`/`d` per `anki_card_id`). Because sync takes Anki verbatim, they should
  be bit-exact after a sync. This is the strongest correctness check and the one to
  run for an ad-hoc soak check.

## Source references

The `anki-source-expert` subagent reads the local Anki source checkout and cites
file:line, pairing Anki's behavior with TT's parallel code path — ask it when in
doubt. The key-files map (queue builder, learning, intersperser, R extraction,
timing, fuzz seed) is in `docs/anki-parity-diagnostics.md` §"Source references".

## Pre-Layer checklist — read before opening a new Layer fix

Layer fixes have repeatedly been written as fresh code that duplicated logic
already living in TT. Before opening one, walk this list.

**Step 1: name the divergence.** What is TT computing that doesn't match Anki? Be
specific about *which output* — a stability number, a queue position, a badge
count, a state transition.

**Step 2: scan the load-bearing helpers for an existing implementation.** If your
fix will compute X and a helper already computes something X-shaped, extend the
helper instead of reimplementing it elsewhere. The full helper ↔ path ↔ coverage
table (helpers across `fsrs.py` / `queue_engine.py` / `sync.py` /
`queue_stats.py` / the `db_*` mixins) is in `docs/anki-parity-diagnostics.md`
§"Load-bearing helpers" — read it before writing any new stability, difficulty,
queue or sync code.

**Step 3: ask the duplication question.** *Would my fix compute or branch on the
same thing one of those helpers already does?* If yes:
- **Factor first.** Extend the existing helper, or extract a shared sub-helper
  (as `_elapsed_days_for_fsrs` did for Layers 11/15/40), before writing the fix at
  the new call site.
- **Add one call site for the new path**, then verify the existing call sites
  still produce the right values for their cases.
- *Then* write the Layer fix.

Skipping steps 2–3 produces two independent code paths reverse-engineering the
same Anki branch. The duplication doesn't show up in normal tests; it shows up the
next time Anki changes that branch and only one of the two paths gets updated.

**Step 4: check whether the oracle harness already covers it.** Run
`cd backend && uv run pytest tests/test_parity_*.py --run-oracle --no-cov`. If a
harness test fails on the input behind your report, you've reproduced the bug —
fix TT and the harness goes green again. If none fails, consider whether the
divergence is in a domain the harness should cover (see
`.claude/rules/anki-oracle-harness.md`).

**Step 5: append to `docs/anki-parity-layers.md`.** Number the Layer (next free
integer). Lead with the bug, then the mechanism, then the files touched, and
cross-link any helper you extended.

## Cross-references

- `.claude/rules/anki-sync.md` — USN, the safety envelope, the schema-change
  workflow.
- `.claude/rules/anki-oracle-harness.md` — the parity harness: harness vs unit
  tests, the subprocess boundary, synthetic-collection gotchas.
- `docs/anki-parity-diagnostics.md` — every diagnostic snippet, the source
  file:line map, and the load-bearing-helper table.
- `docs/anki-mirror-audit.md` — the inspection-driven audit workflow: pin the
  source you mirror to the user's exact anki/fsrs-rs versions, the helper ↔ source
  map, the `fsrs_rs_python` differential-test recipe, and the live/dormant/inert
  triage rubric. Run it proactively (it found Layers 62–63); the soak's incremental
  anchoring can't see a `schedule()`-only bug.
- `docs/anki-parity-layers.md` — the full layer history.
