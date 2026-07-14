"""Queue-assembly engine mirroring Anki's study-queue construction.

Extracted verbatim from app/api/srs.py (2026-07-03 god-module split). The
functions here are the TT side of the Anki queue mirror: gather order,
sibling bury, retrievability-ascending sort, intersperser spread, and the
sync-time freeze (Layer 29). Read .claude/rules/anki-queue-parity.md before
changing anything here.
"""

from __future__ import annotations

import datetime

from app.models.srs_item import Direction, SRSItem, SRSState
from app.srs.anki_mirror.queue_stats import (
    advance_learning_cutoff,
    clear_session_main_queue,
    effective_review_budget,
    get_session_main_queue,
    resolve_bury_new,
    resolve_bury_review,
    resolve_col_crt,
    resolve_daily_new_cap,
    resolve_daily_review_cap,
    resolve_fsrs_params,
    resolve_learning_cutoff,
    resolve_new_cards_ignore_review_limit,
    resolve_new_spread,
    set_session_main_queue,
)
from app.srs.fsrs import compute_retrievability

_FNV_OFFSET_BASIS_64 = 0xCBF29CE484222325
_FNV_PRIME_64 = 0x100000001B3


def _fnv1a_64_i64(*args: int) -> int:
    """Compute Anki's `fnvhash(args...)` over the i64 little-endian bytes.

    Mirrors rslib/src/storage/sqlite.rs:add_fnvhash_function — FNV-1a 64-bit
    hash, fed each i64 argument as 8 little-endian bytes via `write_i64`.
    Returned as a Python int in the signed-i64 range so direct comparison
    matches SQLite's `ORDER BY fnvhash(...)` ordering.
    """
    h = _FNV_OFFSET_BASIS_64
    for a in args:
        for byte in (a & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little"):
            h ^= byte
            h = (h * _FNV_PRIME_64) & 0xFFFFFFFFFFFFFFFF
    return h - (1 << 64) if h >= (1 << 63) else h


def _merge_by_retrievability_ascending(
    rec: list[tuple[int, SRSItem, str]],
    prod: list[tuple[int, SRSItem, str]],
    today: datetime.date,
    col_crt: int | None = None,
) -> list[tuple[int, SRSItem, str, Direction]]:
    """Sort the combined due pool by retrievability ascending.

    Mirrors Anki's SortOrder::RetrievabilityAscending: every card with
    due_date <= today competes in one flat pool, ordered by R alone. An overdue
    but well-remembered card sits behind a today-due card the user is about to
    forget. Tie-break matches Anki exactly: `fnvhash(anki_card_id, anki_card_mod)`
    appended after the primary sort (rslib/src/storage/card/mod.rs:897). When
    either field is missing, fall back to anki_card_id then row_id so the order
    stays deterministic but no longer claims Anki parity.
    """
    params, _ = resolve_fsrs_params()
    dr = params.desired_retention
    decay = params.decay

    combined: list[tuple[int, SRSItem, str, Direction]] = [
        (row_id, item, lang, Direction.RECOGNITION) for row_id, item, lang in rec
    ]
    combined.extend((row_id, item, lang, Direction.PRODUCTION) for row_id, item, lang in prod)

    def _key(t: tuple[int, SRSItem, str, Direction]) -> tuple:
        row_id, item, _, direction = t
        dstate = item.directions[direction]
        r = compute_retrievability(dstate, today, desired_retention=dr, decay=-decay, col_crt=col_crt)
        if dstate.anki_card_id is not None and dstate.anki_card_mod is not None:
            return (r, 0, _fnv1a_64_i64(dstate.anki_card_id, dstate.anki_card_mod), 0)
        # Fallback for rows that haven't been synced from Anki yet.
        return (r, 1, dstate.anki_card_id or 0, row_id)

    combined.sort(key=_key)
    return combined


def _merge_directions(
    rec: list[tuple[int, SRSItem, str]],
    prod: list[tuple[int, SRSItem, str]],
) -> list[tuple[int, SRSItem, str, Direction]]:
    """Merge new-card directions in Anki's gather order.

    Mirrors Anki's `add_new_card` (rslib `queue/builder/gathering.rs:63-169`),
    which fetches cards under `NewCardSorting::HighestPosition` =
    ``"due DESC, ord ASC"`` (storage/card/mod.rs:923) and proactively buries
    the LATER sibling per note. By interleaving both directions in that gather
    order BEFORE sibling-bury runs, the higher-anki_due sibling wins. The
    downstream Template re-sort (applied to the survivors in `get_review_queue`)
    then ranks ord=0 (recognition) ahead of ord=1 (production).

    Sort key (LOWER sorts first):
      1. ``(0,)`` for ``anki_due IS NULL`` else ``(1, -anki_due)`` — NULLS FIRST, DESC
      2. ord ASC (Direction.RECOGNITION = 0, Direction.PRODUCTION = 1)
      3. anki_card_id ASC NULLS LAST (deterministic tiebreak)
      4. row_id ASC (final tiebreak)

    Together with the post-bury Template sort in `get_review_queue`, this
    reproduces the gather → bury → Template-sort pipeline exactly.

    Phase 3 note (Layer 65): the production NEW pool is gated upstream in
    `get_new_items` — a production card is withheld until its recognition
    sibling has graduated past the learning arc. So for a paired both-NEW note
    no production card reaches this merge; recognition wins. The "higher-anki_due
    sibling wins" behavior only applies once recognition is REVIEW (production
    introducible) or among recognition cards / cloze cards.
    """
    combined: list[tuple[int, SRSItem, str, Direction]] = []
    for row_id, item, lang in rec:
        combined.append((row_id, item, lang, Direction.RECOGNITION))
    for row_id, item, lang in prod:
        combined.append((row_id, item, lang, Direction.PRODUCTION))

    def _gather_key(
        t: tuple[int, SRSItem, str, Direction],
    ) -> tuple[int, int, int, int, int]:
        row_id, item, _lang, direction = t
        ds = item.directions[direction]
        ord_value = 0 if direction == Direction.RECOGNITION else 1
        # Layer 33: distinguish fresh /listen-added rows from stale/phantom
        # directions when anki_due is NULL. A fresh add has no anki_note_id at
        # the COLLOCATION level (never pushed to Anki); it should sit at the top
        # of the new bucket (NULLS FIRST). A phantom direction belongs to a
        # collocation that IS linked to Anki but whose own anki_due never got
        # populated — typically a cross-note homonym link that sync_pull can't
        # reach via the parent collocation. Sinking phantoms to the bottom keeps
        # them out of the queue head while preserving the listen-first benefit.
        primary = ((0, 0) if item.anki_note_id is None else (2, 0)) if ds.anki_due is None else (1, -ds.anki_due)
        return (*primary, ord_value, ds.anki_card_id or (1 << 62), row_id)

    combined.sort(key=_gather_key)
    return combined


def _spread_mix(
    reviews: list[tuple[int, SRSItem, str, Direction]],
    news: list[tuple[int, SRSItem, str, Direction]],
) -> list[tuple[int, SRSItem, str, Direction]]:
    """Interleave news into reviews matching Anki's Intersperser exactly.

    Port of rslib/src/scheduler/queue/builder/intersperser.rs. Uses the
    continuous ratio (one_len + 1) / (two_len + 1) so the first item comes from
    the longer iter when populations are imbalanced, and items are distributed
    evenly between the start and end. For 10 reviews + 2 news the first new
    appears at position 3, not position 5 like a floor-ratio approach.
    """
    if not news:
        return list(reviews)
    if not reviews:
        return list(news)
    one_len = len(reviews)
    two_len = len(news)
    ratio = (one_len + 1) / (two_len + 1)
    one_idx = 0
    two_idx = 0
    result: list[tuple[int, SRSItem, str, Direction]] = []
    while one_idx < one_len or two_idx < two_len:
        if one_idx < one_len and two_idx < two_len:
            relative_idx2 = (two_idx + 1) * ratio
            if relative_idx2 < (one_idx + 1):
                result.append(news[two_idx])
                two_idx += 1
            else:
                result.append(reviews[one_idx])
                one_idx += 1
        elif one_idx < one_len:
            result.append(reviews[one_idx])
            one_idx += 1
        else:
            result.append(news[two_idx])
            two_idx += 1
    return result


def _compute_live_main(db) -> list[tuple[int, SRSItem, str, Direction]]:
    """Build the post-spread `live_main` order from current DB state.

    Layer 29: exposed as a module-level function so `sync_pull` can eagerly
    rebuild the freeze immediately on sync completion, instead of waiting for
    the next `/review-queue` request. Anki rebuilds its queue at session
    open / sync; mirroring the rebuild moment keeps the first-new-card position
    aligned across apps right after sync.

    Mirrors the body of `get_review_queue` up through the spread step. Does NOT
    apply the cache reconciliation, the learning cards, or the collapse hack —
    those live in the route handler where the response is shaped.
    """
    today = datetime.date.today()

    db.unbury_if_needed(today)

    cap, _ = resolve_daily_new_cap(db)
    spread, _ = resolve_new_spread(db)
    bury_new, _ = resolve_bury_new(db)
    bury_review, _ = resolve_bury_review(db)
    col_crt = resolve_col_crt(db)

    introduced_today = db.count_new_introduced_today(today)
    new_quota = max(0, cap - introduced_today)
    # Review cap mirrors the new cap: Anki gathers at most
    # `review_limit - reviews_today` review cards into the study session, so the
    # served queue (not just the badge) stops at the cap. reviews_today grows as
    # the user grades, so the cap tightens — but graded cards leave the due pool,
    # so the surviving frozen reviews always equal the remaining budget (no
    # mid-session drops). Intraday learning cards (queue=1) are NOT review-capped;
    # interday ones (queue=3) charge the budget via Layer 79 below.
    review_cap, _ = resolve_daily_review_cap(db)
    reviews_today = db.count_reviews_completed_today(today)
    # Anki's "New cards ignore review limit" deck option (default OFF, synced from
    # `newCardsIgnoreReviewLimit` — brief #4a): when OFF, new intros charge the
    # review budget (Layer 76) AND the review budget caps the new slice (Layer 77);
    # when ON, both couplings are lifted.
    ignore_review_limit = resolve_new_cards_ignore_review_limit(db)
    # New cards introduced today also consume the review budget (Layer 76 —
    # rslib/decks/limits.rs:104-108), so the served-review cap nets them out too,
    # matching Anki's queue build (introducing new cards shrinks review headroom).
    # Interday learning cards due today charge the same budget (Layer 79 — Anki
    # gathers queue=3 under LimitKind::Review before reviews); the cards
    # themselves still serve from the learning queue, uncapped.
    review_remaining = effective_review_budget(
        review_cap,
        reviews_today,
        introduced_today,
        interday_learning_due=db.count_interday_learning_due(today),
        new_cards_ignore_review_limit=ignore_review_limit,
    )
    buried = db.list_collocations_reviewed_today(today)

    due_rec = db.get_due_items(today, Direction.RECOGNITION)
    due_prod = db.get_due_items(today, Direction.PRODUCTION)
    due = _merge_by_retrievability_ascending(due_rec, due_prod, today, col_crt=col_crt)
    if bury_review:
        due = [t for t in due if t[0] not in buried]

    # Layer 32: fetch the FULL per-direction new pool, not a quota-based overfetch.
    # The bug was that a small per-direction limit truncates one direction before
    # the other, breaking cross-direction sibling-bury. For a paired note whose
    # prod sits outside the limit but whose rec slips in (because new_rec has
    # fewer total cards), the merge sees rec without prod → no bury → rec
    # survives → Template sort puts it ahead. Fetching unbounded per direction
    # makes the bury step see both siblings whenever both are state=new.
    # `count_new_available` is the total across both directions; using it as the
    # per-direction cap is a strict upper bound.
    _NEW_OVERFETCH = max(db.count_new_available(), new_quota + 50)
    new_rec = db.get_new_items(direction=Direction.RECOGNITION, limit=_NEW_OVERFETCH)
    new_prod = db.get_new_items(direction=Direction.PRODUCTION, limit=_NEW_OVERFETCH)
    new_combined = _merge_directions(new_rec, new_prod)
    if bury_new:
        new_combined = [t for t in new_combined if t[0] not in buried]

    learning_rec = db.get_learning_items(direction=Direction.RECOGNITION)
    learning_prod = db.get_learning_items(direction=Direction.PRODUCTION)
    learning_collocation_ids = {row_id for row_id, _, _ in learning_rec}
    learning_collocation_ids.update(row_id for row_id, _, _ in learning_prod)

    nonlearning_due = [t for t in due if t[1].directions[t[3]].state not in (SRSState.LEARNING, SRSState.RELEARNING)]
    nonlearning_new = [t for t in new_combined if t[0] not in learning_collocation_ids]

    seen_collocation_ids: set[int] = set(learning_collocation_ids)

    def _bury(cards, when):
        survivors = []
        for t in cards:
            if t[0] in seen_collocation_ids and when:
                continue
            seen_collocation_ids.add(t[0])
            survivors.append(t)
        return survivors

    nonlearning_due = _bury(nonlearning_due, bury_review)
    # Apply the review cap AFTER sibling-bury (Anki counts post-bury survivors
    # toward the limit), keeping the lowest-R reviews (already R-ascending).
    nonlearning_due = nonlearning_due[:review_remaining]
    nonlearning_new = _bury(nonlearning_new, bury_new)
    nonlearning_new.sort(key=lambda t: 0 if t[3] == Direction.RECOGNITION else 1)
    # Layer 77: the review limit also caps NEW cards (`new_cards_ignore_review_limit`
    # defaults off). Anki caps `new_limit = min(new_limit, review_limit)` at build
    # (limits.rs:104-108) and re-mins it as each review is gathered (`decrement()`,
    # limits.rs:131-141) — so the new slice stops at the review budget left after
    # the review slice. Self-consistent mid-session like the review cap above:
    # grading a review shrinks the budget by 1 but also removes it from the due
    # pool, so `review_remaining - len(due slice)` is stable (no mid-session drops).
    # Brief #4a: skip this cap when "New cards ignore review limit" is ON.
    if not ignore_review_limit:
        new_quota = min(new_quota, review_remaining - len(nonlearning_due))
    nonlearning_new = nonlearning_new[:new_quota]

    if spread == 1:
        return nonlearning_due + nonlearning_new
    if spread == 2:
        return nonlearning_new + nonlearning_due
    return _spread_mix(nonlearning_due, nonlearning_new)


def build_and_freeze_main_queue(db) -> None:
    """Compute live_main and write it to session_main_queue cache.

    Called by sync_pull post-ingest so the freeze moment is at sync completion,
    matching when Anki rebuilds its own queue. Without this, TT freezes on the
    first /review-queue request after sync — which can be much later, with a
    different pool state, causing drift on the very-first-new-card position.
    """
    today = datetime.date.today()
    live_main = _compute_live_main(db)
    set_session_main_queue(db, today, [(t[0], t[3].value) for t in live_main])


def assemble_review_queue(db, *, session_start: bool) -> list[tuple[int, SRSItem, str, Direction]]:
    """Assemble the ordered review queue: ready learning + main + pending learning.

    Extracted verbatim from the /review-queue route (god-module split,
    stage 2). Order of operations mirrors Anki's queue build: session-start
    cutoff advance + freeze rebuild, live_main compute (unbury sweep inside),
    learning gather/sort/cutoff split, frozen-order reconciliation with
    NEW-latecomer tail-append, counts.all_zero auto-bump (Layer 36 trigger 4),
    and the learning collapse swap.
    """
    today = datetime.date.today()
    now = datetime.datetime.now(datetime.UTC)

    if session_start:
        advance_learning_cutoff(db, now)
        # Anki parity: deck-open also rebuilds the frozen main queue, not just
        # the learning cutoff. The frontend fires session_start=1 exactly when
        # the user navigates to /review (fresh mount / refresh / new tab) —
        # that's TT's deck-open analog. Without rebuilding here, TT's queue
        # stays frozen at the last sync_pull moment while Anki rebuilds on
        # every reopen, and the two apps' intersperser positions drift
        # irreversibly until next sync.
        clear_session_main_queue(db)
        build_and_freeze_main_queue(db)

    # Build live_main via the shared helper (also called by sync_pull eager
    # rebuild). The unbury sweep runs inside _compute_live_main.
    live_main = _compute_live_main(db)

    # Learning cards live alongside main — gather them separately so they can
    # surface as queue=1 (ready) at the head and queue=1-future (pending) at
    # the tail. Anki's queue dispatcher dispatches intraday-learning first
    # (queue/mod.rs:149-157).
    learning_rec = db.get_learning_items(direction=Direction.RECOGNITION)
    learning_prod = db.get_learning_items(direction=Direction.PRODUCTION)
    learning_cards: list[tuple[int, SRSItem, str, Direction]] = [
        (row_id, item, lang, Direction.RECOGNITION) for row_id, item, lang in learning_rec
    ]
    learning_cards.extend((row_id, item, lang, Direction.PRODUCTION) for row_id, item, lang in learning_prod)

    # Sort learning cards by TT's `due_at` (authoritative after a fresh grade,
    # before sync has refreshed Anki's `anki_due`), then anki_due, then
    # anki_card_id ASC, then row id. Anki's queue=1 sort is `(reps==0, due)`
    # only (rslib scheduler/queue/learning.rs cmp_by_reps_then_due); the
    # underlying SQL has no ORDER BY, so SQLite's stable scan order — effectively
    # cards.id ASC — is the de-facto final tiebreak. We mirror that with
    # anki_card_id; stability is intentionally NOT in the key because two cards
    # lapsed in the same review session share `due_at`/`anki_due` to the second,
    # and Anki ignores stability for ordering.
    _SENTINEL_FUTURE = datetime.datetime.max.replace(tzinfo=datetime.UTC)
    learning_cards.sort(
        key=lambda t: (
            t[1].directions[t[3]].due_at is None,
            t[1].directions[t[3]].due_at or _SENTINEL_FUTURE,
            t[1].directions[t[3]].anki_due is None,
            t[1].directions[t[3]].anki_due or 0,
            t[1].directions[t[3]].anki_card_id is None,
            t[1].directions[t[3]].anki_card_id or 0,
            t[0],
        ),
    )

    # Split learning into ready (past-due / null due_at) vs pending (future).
    # Anki parity: compare due_at against a frozen `cutoff` (Anki's
    # `current_learning_cutoff`), not live `now`. The cutoff is initialized to
    # `now` on first call and only advances on grade events / sync ingest, so a
    # learning card whose timer expires *between* grades stays pending until the
    # next grade — matching Anki's "card on screen is sticky" behavior.
    cutoff = resolve_learning_cutoff(db, fallback=now)
    ready_learning: list[tuple[int, SRSItem, str, Direction]] = []
    pending_learning: list[tuple[int, SRSItem, str, Direction]] = []
    for t in learning_cards:
        ds = t[1].directions[t[3]]
        if ds.due_at is None or ds.due_at <= cutoff:
            ready_learning.append(t)
        else:
            pending_learning.append(t)

    # `live_main` was computed above by `_compute_live_main` (spread already applied).

    # Anki parity: freeze the main queue per day. Anki builds `main` once at
    # deck-open and pops the head as cards are graded — it does NOT re-run the
    # intersperser on every grade. Without this freeze, TT recomputes the order
    # on every poll and always serves the lowest-R review next, diverging from
    # Anki whenever the intersperser would have placed a new card mid-sequence
    # (e.g. with 109 reviews + 30 new, Anki's intersperser puts the first new
    # card at position 3 — TT must surface it at that position too, not just
    # whenever counts shift).
    cached_order = get_session_main_queue(db, today)
    key_to_tuple = {(t[0], t[3].value): t for t in live_main}
    if cached_order is None:
        ordered_main = live_main
        set_session_main_queue(db, today, [(t[0], t[3].value) for t in live_main])
    else:
        seen_keys: set[tuple[int, str]] = set()
        ordered_main = []
        for cid, dir_str in cached_order:
            key = (cid, dir_str)
            if key in seen_keys or key not in key_to_tuple:
                continue
            seen_keys.add(key)
            ordered_main.append(key_to_tuple[key])
        # Anki parity for mid-day latecomers: only NEW-state cards may be
        # tail-appended (mid-day imports via /listen — a TT-only UX allowance).
        # REVIEW-state cards joining live_main without being in the cache are
        # state transitions (learning→review graduation, formerly buried→active);
        # Anki drops these from today's queue entirely
        # (rslib scheduler/queue/learning.rs:60-77 — maybe_requeue_learning_card
        # returns None for non-intraday-learning cards). The legitimate path for
        # review-state changes is cache invalidation on sync / deck-config change,
        # which rebuilds the frozen order from current state on the next call.
        for t in live_main:
            if (t[0], t[3].value) not in seen_keys:
                dstate = t[1].directions[t[3]]
                if dstate.state == SRSState.NEW:
                    ordered_main.append(t)

    # Anki parity: counts.all_zero() auto-bump. (Layer 36 trigger 4)
    # `CardQueues::counts()` in rslib/scheduler/queue/mod.rs:187-196 advances the
    # cutoff whenever the visible counts are all zero — so a pending learning
    # card whose timer ripens between grades surfaces on the next fetch without
    # the user having to grade. We mirror that here: if ready_learning AND
    # ordered_main are both empty, and any pending learning card's due_at is
    # past `now`, advance cutoff to `now` and re-split. Preserves the
    # "card on screen is sticky" invariant: when main has items, the freeze
    # stays in place (test_review_queue_auto_bump_skipped_when_main_has_items).
    if not ready_learning and not ordered_main and pending_learning:
        any_ripe = any(
            t[1].directions[t[3]].due_at is not None and t[1].directions[t[3]].due_at <= now for t in pending_learning
        )
        if any_ripe:
            advance_learning_cutoff(db, now)
            cutoff = now
            ready_learning = []
            new_pending = []
            for t in learning_cards:
                ds = t[1].directions[t[3]]
                if ds.due_at is None or ds.due_at <= cutoff:
                    ready_learning.append(t)
                else:
                    new_pending.append(t)
            pending_learning = new_pending

    # Anki parity "collapse" (rslib/.../queue/learning.rs:94-113): when main
    # is empty and the head of pending_learning was just graded
    # (last_review == cutoff), shift it past the next-soonest pending card so
    # the user doesn't see the same card immediately after grading. Anki does
    # this in `requeue_learning_entry` by bumping the entry's `due` to
    # `next.due + 1s`; we swap positions for the same effect since we rebuild
    # the queue from disk each request.
    if not ordered_main and len(pending_learning) >= 2:
        head_t = pending_learning[0]
        next_t = pending_learning[1]
        head_ds = head_t[1].directions[head_t[3]]
        next_ds = next_t[1].directions[next_t[3]]
        cutoff_ahead = cutoff + datetime.timedelta(seconds=1200)
        if (
            head_ds.last_review == cutoff
            and head_ds.due_at is not None
            and head_ds.due_at <= cutoff_ahead
            and next_ds.due_at is not None
            and next_ds.due_at >= head_ds.due_at
            and next_ds.due_at + datetime.timedelta(seconds=1) < cutoff_ahead
        ):
            pending_learning[0], pending_learning[1] = pending_learning[1], pending_learning[0]

    # 5. Ready learning first (Anki queue=1 priority), then reviews/new,
    #    then pending learning (cards waiting on their step timer).
    return ready_learning + ordered_main + pending_learning
