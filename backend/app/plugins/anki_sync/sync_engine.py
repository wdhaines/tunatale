"""AnkiSync — the TT↔Anki reconcile engine (pull / push / create-new / orphans).

Moved verbatim out of ``app/anki/sync.py`` (Phase 9 mechanical split), together
with its module-level pull helpers. ``app.plugins.anki_sync.sync`` re-exports the public
names, so existing imports and ``patch("app.plugins.anki_sync.sync.AnkiSync.…")`` targets
keep working (class-attribute patches bind to this class object regardless of
the importing module).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from hashlib import sha256 as _sha256

from app.cards.cloze_source import choose_cloze_sentence
from app.cards.media.vocab_media import safe_stem as _safe_stem
from app.cards.media.vocab_media import store_tt_media as _store_tt_media
from app.common.guid import compute_guid
from app.config import settings
from app.languages import card_surface_variants
from app.models.srs_item import Direction, DirectionState, Rating, RevlogRow, SRSState
from app.models.syntactic_unit import SyntacticUnit, serialize_extras
from app.plugins.anki_sync.sync_common import (
    _FSRS_REPLAY_TOLERANCE,
    KNOWN_ANKI_SCHEMA_VER,
    CardRecord,
    CreateNewReport,
    DuplicateNoteError,
    OrphanThresholdExceededError,
    PromotionReport,
    PullReport,
    PushReport,
    RecomputeDivergence,
    SyncConflict,
    _local_today_4am,
    build_cloze_back_extra,
)
from app.srs.anki_mirror.protobuf_wire import compute_anki_day_index
from app.srs.anki_mirror.rollover import anki_day_bounds_utc_dt, anki_today
from app.srs.database import SRSDatabase
from app.srs.direction_fields import SYNC_COMPARABLE_MODEL_FIELDS
from app.srs.fsrs import is_day_level_last_review
from app.srs.function_words import is_function_word, make_cloze_text
from app.srs.queue_stats import resolve_bury_new, resolve_bury_review, resolve_learning_steps, resolve_relearning_steps

# Logger name pinned to "app.anki.sync": BURY_TRACE assertions in
# tests/test_anki_sync_pull.py (caplog.at_level(..., logger="app.anki.sync"))
# and the log-forensics docs reference this exact name.
_log = logging.getLogger("app.anki.sync")

#: Production cards minted per sync (settled with the user 2026-08-15). The
#: drain is paced on purpose: at a 3 new-cards/day introduction cap a backlog of
#: ~1550 words is years of queue, so minting faster than the learner can meet
#: the cards buys nothing and costs one image fetch each. This is why the bulk
#: backfill of 2990 cards was rejected — see
#: `.beads-tasks/briefs/design-no-production-cards-2026-08.md`.
PRODUCTIONS_PER_SYNC = 10

#: Candidates read per sync. Deliberately far larger than the mint budget: a
#: word this phase cannot serve (its notetype can't carry a production card, or
#: its image search came back empty) must not wedge the drain behind it. Reading
#: a row is one indexed query; only mints and image fetches are budgeted.
PRODUCTION_SCAN_LIMIT = 200

#: Fraction of a deck's notes that must be structurally recognition-only before
#: the tripwire fires. The measured shares sit far either side of it — Slovene
#: 7.2%, Norwegian 99.4% before its migration — so this is a wide gap, not a
#: tuned knob. See ``AnkiSync.warn_if_recognition_only_deck``.
RECOGNITION_ONLY_WARN_SHARE = 0.5


def _direction_differs(local: DirectionState, candidate: DirectionState) -> bool:
    """Return True only if a sync-relevant field changed between local and candidate.

    The compared set derives from the field registry
    (``app/srs/direction_fields.py`` — ``sync_comparable=True`` entries, with
    per-field reasons there). Excluded fields (e.g. last_synced_at,
    last_rating) don't trigger a write on their own, so benign bookkeeping
    updates stay no-ops. Never hand-enumerate fields here again — Layers
    17/35/37 were each a field missing from this list.
    """
    return any(getattr(local, name) != getattr(candidate, name) for name in SYNC_COMPARABLE_MODEL_FIELDS)


def _resolve_prior_state(
    local_dir: DirectionState,
    new_state: SRSState,
    *,
    first_review_ms: int | None = None,
    today_start_ms: int | None = None,
) -> SRSState | None:
    """Return the `prior_state` to write on a sync-merged direction.

    On a state-class transition (e.g. NEW → LEARNING after Anki graded a fresh
    card), `prior_state` captures the local-side state before the transition so
    later queries can identify the event — most importantly
    `count_new_introduced_today`, which filters by `prior_state='new'` to mirror
    Anki's `newToday` counter. When state is unchanged (a no-op sync, or
    within-state grade), preserve `local_dir.prior_state` so earlier transition
    bookkeeping isn't clobbered.

    Self-heal: if Anki's first revlog for this card is today AND the card
    isn't currently in NEW state, force `prior_state='new'` regardless of
    the current value. This covers two cases:
      1. Pre-fix data where sync_pull didn't write prior_state at all.
      2. Cards introduced today that later graduated to REVIEW the same day —
         the LEARNING→REVIEW transition can clobber 'new' in the grade
         endpoint; this restores it. Matches Anki's `newToday` counter
         (sticky for the day, never decremented).
    """
    if new_state != local_dir.state:
        return local_dir.state

    if (
        new_state != SRSState.NEW
        and first_review_ms is not None
        and today_start_ms is not None
        and first_review_ms >= today_start_ms
    ):
        return SRSState.NEW

    return local_dir.prior_state


def _resolve_introduced_at(
    local_dir: DirectionState,
    new_state: SRSState,
    *,
    first_review_ms: int | None,
) -> datetime | None:
    """Return the `introduced_at` to write on a sync-merged direction.

    Layer 26: introduced_at is stamped exactly once per card's intro arc — on
    the first NEW→non-NEW transition observed in EITHER app. Preserves an
    already-set value (sticky for the card's lifetime). Else, if Anki shows
    the card has been graded (new_state != NEW) and we know when Anki's first
    revlog row landed, anchor to that timestamp so `count_new_introduced_today`
    reflects Anki-side introductions after sync.
    """
    if local_dir.introduced_at is not None:
        return local_dir.introduced_at
    if new_state == SRSState.NEW:
        return None
    if first_review_ms is None:
        return None
    return datetime.fromtimestamp(first_review_ms / 1000, tz=UTC)


def _anki_step_ahead(anki_left: int | None, local_left: int | None) -> bool:
    """Return True iff Anki's `total_remaining` is strictly less than TT's.

    Anki encodes `left = today_left * 1000 + total_remaining`; only the low 3
    digits drive the state machine (rslib/.../card/mod.rs:218). A smaller
    `total_remaining` in Anki means Anki has graded the card more times — it's
    further along the learning steps than TT. Used by sync_pull (Fix 2) and
    sync_push (Fix 3) to defer to whichever app has more progress.

    Returns False when either value is missing or zero — there's no "ahead"
    relationship to compare against.
    """
    anki_tr = (anki_left or 0) % 1000
    local_tr = (local_left or 0) % 1000
    return anki_tr > 0 and local_tr > 0 and anki_tr < local_tr


def _tt_memory_newer(local_dir: DirectionState, card_rec: CardRecord) -> bool:
    """True when TT's last grade postdates Anki's FSRS memory-state timestamp.

    Layer 70. ``card_rec.last_review`` is lrt-derived when ``cards.data`` has
    ``lrt`` (parse_fsrs_data prefers it) — i.e. the timestamp of the last grade
    Anki's stored s/d actually incorporate. A TT grade newer than that means
    Anki's memory state is stale relative to TT's: sync_push wrote scheduling +
    revlog for the grade but pull must not let Anki's pre-grade s/d win.
    Strict ``>`` so an equal timestamp (the same grade already round-tripped)
    keeps the take-Anki default; either side missing keeps it too.

    Layer 72: a midnight-UTC local ``last_review`` is `parse_fsrs_data`'s
    day-level reconstruction round-tripped back from a no-lrt Anki card, not
    a TT grade time (TT-native grades stamp sub-second ``now``). Day
    truncation can overshoot the real grade by up to 24h, so it may postdate
    Anki's lrt — without this check the guard protected a placeholder s/d
    against every future pull (the stuck upogniti card, 2026-06-12).
    """
    return (
        local_dir.last_review is not None
        and not is_day_level_last_review(local_dir.last_review)
        and card_rec.last_review is not None
        and local_dir.last_review > card_rec.last_review
    )


# Layer 35: bury_kind split (sched/user/None).
# Layer 39 (2026-05-17): queue=-2 now maps to 'sched', not 'user'.
# String-keyed map (TT state value → Anki cards.queue) used by the sync_push
# backfill, which iterates raw DB rows. Avoids re-parsing into the SRSState enum.
_STATE_VALUE_TO_ANKI_QUEUE: dict[str, int] = {
    SRSState.NEW.value: 0,
    SRSState.LEARNING.value: 1,
    SRSState.RELEARNING.value: 1,
    SRSState.REVIEW.value: 2,
}


def _is_anki_grade(local: DirectionState, candidate: DirectionState) -> bool:
    """True when the pulled state represents a review Anki did that TT hasn't seen.

    Narrower than ``_direction_differs`` on purpose: that fires on bury flips,
    ``mod`` bumps and due-date rewrites too, none of which are reviews. Two
    independent signals, because neither alone covers every case — ``reps`` can
    arrive equal after an undo-and-regrade in Anki, and ``last_review`` is NULL
    on a card TT has never seen reviewed.
    """
    if candidate.reps > local.reps:
        return True
    if candidate.last_review is None:
        return False
    return local.last_review is None or candidate.last_review > local.last_review


def _bury_kind_from_queue(queue: int) -> str | None:
    """Return the bury kind for an Anki queue value, or None when not buried.

    Both ``queue=-2`` and ``queue=-3`` map to ``'sched'`` so the daily
    unbury sweep releases them at TT's rollover, matching Anki's own
    behavior (``unbury_on_day_rollover`` releases both, see
    ``rslib/storage/card/sqlwriter.rs:471-476``).

    The Anki *source* claims grade-time sibling-bury writes ``queue=-3``
    (sched) and only explicit UI actions write ``queue=-2`` (user). The
    Anki *binary* contradicts that: grading a card via
    ``col.sched.answerCard`` places the sibling at ``queue=-2``,
    verified 2026-05-17 against a copy of the user's collection. Per
    rule 13 (``.claude/rules/anki-queue-parity.md``), trust the binary.

    The previous mapping (``queue=-2 → 'user'``) left TT hoarding every
    sibling-bury indefinitely while Anki auto-released them at rollover —
    the 19-card cohort observed on 2026-05-17 and the earlier 140-row
    incident on 2026-05-16 (see ``docs/bury-kind-investigation-*``).
    """
    if queue in (-2, -3):
        return "sched"
    return None


def _queue_to_state(queue: int, card_type: int, reps: int) -> SRSState:
    """Map Anki's (queue, type, reps) tuple to TT's SRSState.

    `queue` is the authoritative signal for Anki's current placement — TT
    must mirror it directly. Layer 30: the previous `if reps == 0: NEW`
    fallback wrongly mapped `(queue=2, reps=0)` cards to NEW, surfacing
    already-graduated cards (e.g. via Anki's "Forget" action or a manual
    `cards.due` edit, which clears `reps` but leaves `queue=2`) as fresh
    new cards in TT.
    """
    if queue == -1:
        return SRSState.SUSPENDED
    if queue in (-2, -3):
        return SRSState.BURIED
    if queue == 1:
        return SRSState.RELEARNING if card_type == 3 else SRSState.LEARNING
    if queue == 3:
        return SRSState.RELEARNING
    if queue == 2:
        return SRSState.REVIEW
    if queue == 0:
        return SRSState.NEW
    # Fallback for unknown queue values (shouldn't happen against modern Anki).
    return SRSState.NEW if reps == 0 else SRSState.REVIEW


def _step_minutes_from_left(left: int | None, steps: list[float]) -> float | None:
    """Decode Anki's `cards.left` to the current step's duration in minutes.

    Anki encodes `left = today_left * 1000 + total_remaining`; the low 3 digits
    drive state. Step index = `len(steps) - total_remaining` (matches
    rslib/.../states/steps.rs:23 `get_index`). Returns None when `left`/`steps`
    is missing or out of range.
    """
    if not left or not steps:
        return None
    total_remaining = left % 1000
    if total_remaining <= 0 or total_remaining > len(steps):
        return None
    step_index = len(steps) - total_remaining
    return steps[step_index]


def _derive_revlog_shape(
    ds: DirectionState,
    learn_steps: list[float],
    relearn_steps: list[float],
) -> tuple[int, int, int]:
    """Compute (type_, ivl, last_ivl) for a revlog row reflecting the actual
    transition. Anki encodes sub-day intervals as negative seconds (e.g. -60
    for 1 min, -600 for 10 min) and day-scale intervals as positive ints.

    `revlog.type`: 0=Learning, 1=Review, 2=Relearning. The type recorded is
    determined by the queue the card was *in* at rating time — i.e. the prior
    state, not the new state.
    """
    stability_days = max(1, round(ds.stability))

    if ds.prior_state is None:
        # Pre-migration row: keep the legacy positive-ivl shape so the rating
        # at least lands in revlog. Future grades populate prior_state and use
        # the precise transition mapping below.
        if ds.state == SRSState.LEARNING:
            type_ = 0
        elif ds.state == SRSState.RELEARNING:
            type_ = 2
        else:
            type_ = 1
        return (type_, stability_days, stability_days)

    if ds.prior_state in (SRSState.NEW, SRSState.LEARNING):
        type_ = 0
    elif ds.prior_state == SRSState.RELEARNING:
        type_ = 2
    else:
        type_ = 1

    if ds.state in (SRSState.LEARNING, SRSState.RELEARNING):
        # Anki's revlog records the **unfuzzed** step (e.g. -60 for a 1m step,
        # -330 for Hard-on-first-step's 5.5m avg) — not `due_at - last_review`,
        # which would include the up-to-25%-of-step fuzz applied at scheduling
        # time. Decode the base step from `left` + steps; override for
        # Hard-on-first-step where Anki uses `(steps[0] + steps[1]) / 2`.
        steps = learn_steps if ds.state == SRSState.LEARNING else relearn_steps
        step_min = _step_minutes_from_left(ds.left, steps)
        if step_min is None and ds.state == SRSState.RELEARNING and relearn_steps:
            step_min = relearn_steps[0]
        if (
            step_min is not None
            and ds.last_rating == Rating.HARD.value
            and ds.left is not None
            and (ds.left % 1000) == len(steps)
            and len(steps) > 1
        ):
            step_min = (steps[0] + steps[1]) / 2
        new_ivl = -int(round(step_min * 60)) if step_min is not None else stability_days
    else:
        new_ivl = stability_days

    if ds.prior_state == SRSState.NEW:
        last_ivl = 0
    elif ds.prior_state == SRSState.LEARNING:
        prior_step_min = _step_minutes_from_left(ds.prior_left, learn_steps)
        last_ivl = -int(round(prior_step_min * 60)) if prior_step_min is not None else 0
    elif ds.prior_state == SRSState.RELEARNING:
        prior_step_min = _step_minutes_from_left(ds.prior_left, relearn_steps)
        last_ivl = -int(round(prior_step_min * 60)) if prior_step_min is not None else 0
    elif ds.prior_state == SRSState.REVIEW:
        last_ivl = max(1, round(ds.prior_stability)) if ds.prior_stability is not None else stability_days
    else:
        last_ivl = stability_days

    return (type_, new_ivl, last_ivl)


class AnkiSync:
    """Orchestrate bidirectional sync between TunaTale and Anki."""

    def __init__(
        self,
        *,
        db: SRSDatabase,
        _reader=None,
        _writer=None,
        _anki_col_ver: int | None = None,
        _anki_col_crt: int | None = None,
    ) -> None:
        self._db = db
        self._anki_col_ver = _anki_col_ver
        self._anki_col_crt = _anki_col_crt
        if _reader is not None:
            self._reader = _reader
        else:
            raise ValueError("_reader is required")

        if _writer is not None:
            self._writer = _writer
        else:
            raise ValueError("_writer is required")

        # Populated by detect_and_reset_orphans; consumed by sync_push to force
        # FSRS state onto cards that were just recreated.
        self._recovered_directions: set[tuple[str, str]] = set()

    def warn_if_guid_collisions(self) -> int:
        """Log a WARNING per group of Anki notes that collapse to one TT guid.

        A TT guid is ``(text, language, disambig_key)`` and TT stores the part of
        speech as disambig, so two notes sharing the same ``(text, POS)`` map to
        ONE collocation holding TWO candidate cards — nothing pins which one
        ``anki_card_id`` follows, and it can silently alternate between them
        (see ``db_sync.set_anki_ids``'s RELINK_TRACE). ``foran`` did exactly that
        from 2026-07-14, splitting one word's review history across both cards
        and leaving TT carrying a due date from the card it wasn't tracking; it
        surfaced only as a phantom "new" card on 2026-08-02.

        **The key must include the disambig.** POS homonyms — ``løfte``
        noun/verb, ``vår`` noun/determinative, ``om`` conj/adv/prep — are
        legitimate separate cards, correctly split by TT into separate pinned
        collocations. A tripwire keyed on the bare surface form would flag all
        17 in the Norwegian deck and be useless.

        Cheap sync-time tripwire, not a correctness gate: it reports, never
        blocks. Returns the number of colliding groups (0 = clean).
        """
        groups: dict[tuple[str, str], list[int]] = {}
        for rec in self._reader.get_note_records():
            key = (rec.l2_text.casefold(), rec.disambig_key.casefold())
            groups.setdefault(key, []).append(rec.anki_note_id)

        collisions = 0
        for (text, disambig), note_ids in sorted(groups.items()):
            if len(note_ids) > 1:
                collisions += 1
                _log.warning(
                    "GUID_COLLISION text=%r disambig=%r nids=%s — these Anki notes share one TT"
                    " guid, so one collocation has %d candidate cards and may alternate between"
                    " them; grave the redundant note or give them distinct parts of speech",
                    text,
                    disambig,
                    sorted(note_ids),
                    len(note_ids),
                )
        return collisions

    def warn_if_recognition_only_deck(self) -> float:
        """WARN when most of the deck's notes can only ever yield a recognition card.

        The failure this exists for was **silence**: Norwegian carried 2990 of
        3009 words with no production direction and every consumer degraded
        quietly — the transcript skipped the affordance, ``/inflection-clozes``
        returned a plausible 409, mastery computed over a shorter component
        list, the drill simply never served an L2-production card. The number
        surfaced only from an ad-hoc query, months later.

        Same genre as ``warn_if_guid_collisions``: reports, never blocks, runs
        on dry-runs too because it is a pure read of the collection. Returns the
        fraction (0.0 for an empty or unknown deck).

        It keys on the **root cause** — notes on a notetype with a single
        template, which can only ever make one card — rather than on the
        symptom, "collocations lacking a production direction". The symptom
        fires 77 legitimate false positives on Slovene: phonics notes on
        ``Basic`` and clozes, neither of which is a defect. Measured against the
        real collection (2026-08-17), the two languages sit far apart:
        Slovene 53/734 = 7.2%, Norwegian 2990/3009 = 99.4% before its migration
        and 0/3023 after. The threshold has a wide, empirically-checked gap to
        clear, so it does not need tuning.

        This is a net for the *next* deck imported from the community, which is
        why it is silent on both configured languages today. The seed import
        faithfully mirrors whatever notetype a shared deck used, so this will
        recur.
        """
        share = self._reader.get_recognition_only_share()
        if share.fraction < RECOGNITION_ONLY_WARN_SHARE:
            return share.fraction
        _log.warning(
            "RECOGNITION_ONLY_DECK %d of %d notes (%.1f%%) sit on notetypes with a single card"
            " template %s — those words can never get a production card, and every consumer"
            " (transcript affordance, inflection clozes, mastery, the drill) degrades silently."
            " Give the notetype a production template:"
            " uv run python -m app.plugins.anki_sync.add_production_template --notetype <name>",
            share.notes,
            share.total,
            share.fraction * 100,
            share.by_notetype,
        )
        return share.fraction

    def detect_and_reset_orphans(self) -> tuple[int, int]:
        """Reset TT pointers to Anki cards/notes that no longer exist.

        Runs at the top of a sync (before sync_create_new). Diffs the TT mirror
        against the live Anki collection — if a TT direction's `anki_card_id`
        is not in `live_card_ids`, the card was deleted ("Empty Cards", manual
        delete, or wiped by a force-full-download from AnkiWeb). Reset clears
        the dead pointer and (if `reps > 0`) flips `dirty_fsrs=1` so the next
        push writes a fresh revlog and force-FSRS into the recreated card.

        Aborts with `OrphanThresholdExceededError` when the orphan ratio
        exceeds 25% — usually a sign of a misconfigured `anki_collection_path`.

        Returns (direction_resets, note_resets) counts.
        """
        records = self._reader.get_note_records()
        live_note_ids = {r.anki_note_id for r in records}
        live_card_ids = {c.anki_card_id for r in records for c in r.cards}

        # Honor intentional deletes first: a TT collocation whose Anki note sits
        # in the graves table was deleted on purpose — hard-delete it instead of
        # resurrecting it below. A note missing *without* a grave falls through
        # to the recovery (reset + re-mint) path, preserving the
        # force-full-download safety net. Deleting here also removes the row's
        # cards from the orphan ratio, so a purge can't trip the threshold.
        deleted_guids = self._db.delete_collocations_for_graves(grave_note_ids=self._reader.get_grave_note_ids())
        if deleted_guids:
            _log.info(
                "Honored %d Anki note grave(s); hard-deleted TT collocations: %s", len(deleted_guids), deleted_guids
            )

        tt_card_ids = self._db.list_anki_card_ids()
        if tt_card_ids:
            orphan_count = len(tt_card_ids - live_card_ids)
            if orphan_count / len(tt_card_ids) > 0.25:
                raise OrphanThresholdExceededError(
                    f"Refusing to reset {orphan_count} orphaned anki_card_ids "
                    f"({orphan_count / len(tt_card_ids):.0%} of {len(tt_card_ids)}). "
                    f"Check that anki_collection_path points at the right deck."
                )

        dir_resets, note_resets = self._db.reset_orphaned_anki_ids(
            live_card_ids=live_card_ids,
            live_note_ids=live_note_ids,
        )
        self._recovered_directions = {(guid, direction) for guid, direction in dir_resets}
        return len(dir_resets), len(note_resets)

    def _record_conflict(
        self,
        report: PullReport,
        *,
        guid: str,
        direction: str | None,
        field: str,
        local: str,
        remote: str,
        resolution: str,
        dry_run: bool,
    ) -> None:
        conflict = SyncConflict(
            guid=guid,
            direction=direction,
            field=field,
            local_value=local,
            remote_value=remote,
            resolution=resolution,
        )
        report.conflicts.append(conflict)
        if not dry_run:
            self._db.record_sync_conflict(
                guid=guid,
                direction=direction,
                field=field,
                local=local,
                remote=remote,
                resolution=resolution,
            )

    def _record_recompute_divergence(
        self,
        report: PullReport,
        *,
        collocation_id: int,
        direction: Direction,
        replay_stability: float,
        replay_difficulty: float,
        anki_stability: float,
        anki_difficulty: float,
    ) -> None:
        """Record an FSRS recompute-memory-state divergence event on PullReport.

        Called when the forward-step replay of tt_revlog produces
        stability/difficulty outside tolerance (0.01) from Anki's ``cards.data`` —
        indicating Anki ran ``recompute_memory_state`` between syncs. The
        divergence is surfaced via ``report.recompute_divergences`` and the sync
        summary log, but does NOT write to a DB table (it is a diagnostic signal,
        not a permanent record).
        """
        report.recompute_divergences.append(
            RecomputeDivergence(
                collocation_id=collocation_id,
                direction=direction.value,
                replay_stability=replay_stability,
                replay_difficulty=replay_difficulty,
                anki_stability=anki_stability,
                anki_difficulty=anki_difficulty,
            )
        )
        # Soak signal for the Stage-3b `new`-mode roll-out. Expected ≈0 per sync;
        # a non-zero count flags a genuine Anki recompute event (Optimize / FSRS
        # param / retention / FSRS-toggle / restore) the forward-step replay
        # cannot reproduce. WARNING so it surfaces even under the bare CLI (no
        # logging handler configured); grep server stderr / sync.log for
        # "RECOMPUTE_DIVERGENCE".
        _log.warning(
            "RECOMPUTE_DIVERGENCE cid=%s dir=%s replay_s=%.4f anki_s=%.4f replay_d=%.4f anki_d=%.4f",
            collocation_id,
            direction.value,
            replay_stability,
            anki_stability,
            replay_difficulty,
            anki_difficulty,
        )

    def _pull_unbury_sweep(self, dry_run: bool) -> None:
        """Anki-parity daily unbury sweep. Run BEFORE processing Anki records.

        The idempotency guard in ``unbury_if_needed`` prevents re-sweep within the
        same day, so state='buried' rows set by the current pull (today's sibling-
        buries from Anki) stick.

        Keyed by ``anki_today()`` (the 4 AM rollover), not local midnight — a
        pull in the ``[midnight, 4 AM)`` window is still the PRIOR Anki day, so
        keying on ``date.today()`` would fire the sweep up to 4 hours early.
        """
        if not dry_run:
            self._db.unbury_if_needed(anki_today())

    @staticmethod
    def _init_bury_stats() -> dict[str, int]:
        """Return an empty bury_stats accumulator for sync_pull."""
        return {
            "anki_queue_minus2_seen": 0,
            "anki_queue_minus3_seen": 0,
            "buried_to_released_writes": 0,
            "released_to_buried_writes": 0,
            "kind_only_flips_written": 0,
            "buried_state_match_no_write": 0,
        }

    @staticmethod
    def _compute_today_start_ms(now: datetime | None = None) -> int:
        """Return the START OF THE CURRENT ANKI DAY (4 AM rollover) in UTC ms.

        Used to infer ``prior_state='new'`` for cards whose first revlog is today
        but TT lost the transition (synced before sync_pull learned to write prior_state).

        Routes through ``anki_day_bounds_utc_dt`` so the boundary matches Anki's
        4 AM local rollover, not local midnight.
        """
        start, _ = anki_day_bounds_utc_dt(anki_today(now), now)
        return int(start.timestamp() * 1000)

    def _pull_merge_direction(
        self,
        card_rec: CardRecord,
        local_dir: DirectionState,
        direction: Direction,
        resolved_last_review: datetime | None,
        today_start_ms: int,
    ) -> DirectionState:
        """Compute the merged DirectionState for a single card during sync_pull.

        One take-Anki resolution with two keep-TT guards (the Layer-70 recency
        guard and fsrs_unknown), plus suspend/bury via ``_queue_to_state`` /
        ``_bury_kind_from_queue``. The former ``dirty_fsrs`` branches were
        deleted once proven unreachable in a real sync: ``sync_push`` runs
        before pull in ``run_full_sync`` and clears ``dirty_fsrs`` for every
        Anki-linked direction, so pull only ever sees clean state (the
        DIRTY_AT_PULL guard in the caller enforces the invariant; the dead
        branches lived only for dry-run and direct-pull tests). The caller owns
        BURY_TRACE, bury_stats, ``_direction_differs``, and the DB write.
        """
        # One take-Anki resolution with two keep-TT guards (suspend/bury fall out
        # of _queue_to_state / _bury_kind_from_queue):
        #   - tt_newer (Layer 70): TT graded after Anki's stored memory state
        #     (cards.data lrt). sync_push writes scheduling + a revlog row for a
        #     TT grade but Anki's cards.data lags, so without this guard the
        #     take-Anki default reverts the grade's s/d/last_review to Anki's
        #     pre-grade values (the cid=428 lapse-arc loss, 2026-06-10; 165
        #     directions). Keep TT's memory state AND grade timestamp; scheduling
        #     stays pass-through from Anki (sync_push wrote it).
        #   - fsrs_unknown: Anki's cards.data has no real s/d (placeholder 1.0/5.0),
        #     so keep TT's memory state — but last_review still comes from Anki.
        #   - otherwise: Anki's cards.data is authoritative (take-Anki-verbatim).
        new_state = _queue_to_state(card_rec.queue, card_rec.card_type, card_rec.reps)
        tt_newer = card_rec.fsrs_known and _tt_memory_newer(local_dir, card_rec)
        keep_local_memory = tt_newer or not card_rec.fsrs_known
        return DirectionState(
            direction=direction,
            due_at=card_rec.due_at,
            stability=local_dir.stability if keep_local_memory else card_rec.stability,
            difficulty=local_dir.difficulty if keep_local_memory else card_rec.difficulty,
            reps=card_rec.reps,
            lapses=card_rec.lapses,
            state=new_state,
            prior_state=_resolve_prior_state(
                local_dir,
                new_state,
                first_review_ms=card_rec.first_review_ms,
                today_start_ms=today_start_ms,
            ),
            introduced_at=_resolve_introduced_at(
                local_dir,
                new_state,
                first_review_ms=card_rec.first_review_ms,
            ),
            dirty_fsrs=False,
            anki_card_id=card_rec.anki_card_id,
            anki_card_mod=card_rec.anki_card_mod,
            anki_due=card_rec.anki_due,
            last_review=local_dir.last_review if tt_newer else resolved_last_review,
            last_review_time_ms=local_dir.last_review_time_ms if tt_newer else 0,
            last_synced_at=datetime.now(UTC).isoformat(),
            last_rating=local_dir.last_rating if tt_newer else None,
            left=card_rec.left,
            bury_kind=_bury_kind_from_queue(card_rec.queue),
        )

    def _ingest_anki_revlog_for_card(
        self,
        anki_card_id: int,
        collocation_id: int,
        direction: Direction,
    ) -> None:
        """Copy Anki revlog rows for *anki_card_id* into tt_revlog (idempotent).

        Called from ``sync_pull`` for every card before the merge logic runs.

        Gap-proof: reconciles the card's *full* Anki revlog against the ids
        already in tt_revlog rather than trusting a wall-clock ``last_synced_at``
        watermark. A grade made during a multi-day sync gap can land *interior*
        to the ids TT already holds; an ``id > last_synced_at`` filter would skip
        it permanently and silently understate the event-sourced FSRS replay
        (Stage 3b soak finding, 2026-05-27 — gor/zahod missing a 05-25 Good).
        The held-id set keeps it cheap: no per-row query or write for grades we
        already have, so only genuinely-new rows touch the DB.
        """
        held_ids = self._db.get_tt_revlog_ids(collocation_id, direction)
        rows = self._reader.get_revlog_for_card(anki_card_id)
        anki_ids = {r["id"] for r in rows}
        for r in rows:
            if r["id"] in held_ids:
                continue
            # Skip if a TT-*written* row (same direction, ±5s, same ease) already
            # records this grade event — TT wrote it at grade time and the Anki
            # copy round-tripped with a bumped id. PK-equal matches go through
            # INSERT OR IGNORE; exclude the candidate's own id. ``ignore_ids`` =
            # this card's Anki revlog ids, so an already-ingested Anki row never
            # suppresses a distinct rapid grade a few seconds later (Layer 60).
            if self._db.has_revision_near(
                collocation_id,
                direction.value,
                r["id"],
                r["ease"],
                exclude_id=r["id"],
                ignore_ids=anki_ids,
            ):
                continue
            self._db.append_revlog(
                RevlogRow(
                    id=r["id"],
                    collocation_id=collocation_id,
                    direction=direction,
                    button_chosen=r["ease"],
                    interval=r["ivl"],
                    last_interval=r["lastIvl"],
                    factor=r["factor"],
                    taken_millis=r["time"],
                    review_kind=r["type"],
                    anki_card_id=anki_card_id,
                )
            )

    def _replay_incremental(
        self,
        collocation_id: int,
        direction: Direction,
        local_dir: DirectionState,
        since_id: int | None,
        params,
        col_crt: int | None,
    ) -> DirectionState:
        """Incremental forward-step replay for the recompute detector.

        Walks tt_revlog rows newer than ``since_id`` (all rows when None) from the
        stored ``local_dir`` starting state. With zero new rows returns ``local_dir``
        unchanged. A pure wrapper around ``rebuild_from_revlog``.
        """
        return self._db.rebuild_from_revlog(
            collocation_id,
            direction,
            params=params,
            col_crt=col_crt,
            anki_card_id=local_dir.anki_card_id,
            starting_state=local_dir,
            since_id=since_id,
        )

    def _pull_advance_learning_cutoff(self, max_revlog_ms: int, dry_run: bool) -> None:
        """Advance the learning cutoff to the most recent Anki revlog timestamp ingested.

        Anki-parity: without this, an Anki-only grading session would leave
        TT's cutoff frozen at the last *TT* grade, and intraday-learning cards that
        ticked past-due during the Anki session would never become eligible.
        """
        if not dry_run and max_revlog_ms > 0:
            from app.srs.queue_stats import advance_learning_cutoff

            advance_learning_cutoff(self._db, datetime.fromtimestamp(max_revlog_ms / 1000, UTC))

    def _pull_rebuild_session_main_queue(self, dry_run: bool) -> None:
        """Invalidate and eagerly rebuild the frozen session_main_queue on sync completion.

        Anki's ``requires_study_queue_rebuild`` (rslib scheduler/queue/mod.rs:211-215)
        forces a queue rebuild after sync round-trip; mirroring it lazily (clear-only,
        rebuild-on-next-request) means TT freezes at a different moment than Anki's
        session-open rebuild, leading to off-by-N drift on the first-new-card position.
        The eager rebuild aligns the freeze moments. Layer 29.
        """
        if not dry_run:
            from app.srs.queue_engine import build_and_freeze_main_queue
            from app.srs.queue_stats import clear_session_main_queue

            clear_session_main_queue(self._db)
            build_and_freeze_main_queue(self._db)

    def sync_pull(self, dry_run: bool = False) -> PullReport:
        """Pull Anki → TunaTale. Returns a PullReport summarising changes.

        Wrapped in one DB transaction so the hundreds of per-note/per-card reads
        reuse a single connection instead of opening+closing one each — the
        dominant cost of a sync (profiled at ~1.4s of connect/close churn on a
        no-op pull). Writes also become atomic; ``dry_run`` rolls back.
        """
        with self._db.begin_transaction(dry_run=dry_run):
            return self._run_sync_pull(dry_run)

    def _run_sync_pull(self, dry_run: bool = False) -> PullReport:
        report = PullReport()
        max_revlog_ms = 0
        bury_stats = self._init_bury_stats()

        # Forward-step replay runs on every sync purely as a recompute DETECTOR.
        # The merge writes Anki's cards.data verbatim (take-Anki), so a replay
        # that diverges signals a genuine Anki recompute event (Optimize /
        # FSRS-param / retention change / restore), not a stored-state choice.
        from app.srs.queue_stats import resolve_fsrs_params

        replay_params = resolve_fsrs_params(self._db)[0]
        replay_col_crt = self._anki_col_crt

        self._pull_unbury_sweep(dry_run)

        today_start_ms = self._compute_today_start_ms()

        for rec in self._reader.get_note_records():
            # Primary: stable pointer set by sync_create_new. Handles duplicate
            # computed-guid homonyms by ignoring the un-linked orphan Anki notes.
            local_item = self._db.get_collocation_by_anki_note_id(rec.anki_note_id)
            if local_item is None:
                # Fallback: row was never linked (e.g., imported before anki_note_id
                # column was populated). Validate guid before trusting it.
                expected_guid = compute_guid(rec.l2_text, settings.target_language, rec.disambig_key)
                if rec.anki_guid != expected_guid:
                    report.skipped_unknown_guid += 1
                    continue
                local_item = self._db.get_collocation_by_guid(rec.anki_guid)
                if local_item is None:
                    continue
                # If the row is already linked to a different Anki note, this
                # record is an orphan — skip it.
                if local_item.anki_note_id is not None and local_item.anki_note_id != rec.anki_note_id:
                    continue
                guid = rec.anki_guid
            else:
                guid = local_item.guid
            local_dirty_fields = self._db.get_dirty_fields(guid)
            dirty_set = {f for f in local_dirty_fields.split(",") if f}

            local_translation = local_item.syntactic_unit.translation
            local_sent_trans = local_item.syntactic_unit.source_sentence_translation
            local_note = local_item.syntactic_unit.note
            local_article = local_item.syntactic_unit.article
            local_extras = local_item.syntactic_unit.extras
            note_changed = False
            # Article is Anki-sourced display data (never edited in TT). Heal it
            # whenever Anki's value differs — this also backfills every existing
            # row on the first sync after the article feature shipped. None ⇒
            # leave untouched, so we only write when there's an actual change.
            article_update = rec.article if rec.article != local_article else None
            # Extras (rich back-of-card fields) are likewise Anki-sourced and
            # display-only; heal when they differ, backfilling existing rows on
            # the first sync after the feature shipped. Pass the serialized JSON.
            extras_update = (
                serialize_extras(rec.extras) if rec.extras is not None and rec.extras != local_extras else None
            )
            new_dirty_fields = dirty_set.copy()

            if rec.translation != local_translation:
                note_changed = True
                if "translation" in dirty_set:
                    self._record_conflict(
                        report,
                        guid=guid,
                        direction=None,
                        field="translation",
                        local=local_translation,
                        remote=rec.translation,
                        resolution="anki_wins",
                        dry_run=dry_run,
                    )
                    new_dirty_fields.discard("translation")

            if rec.sentence_translation != local_sent_trans:
                note_changed = True

            if rec.note != local_note:
                note_changed = True

            if article_update is not None:
                note_changed = True

            if extras_update is not None:
                note_changed = True

            if note_changed:
                if not dry_run:
                    self._db.update_collocation_for_sync(
                        guid,
                        translation=rec.translation,
                        note=rec.note,
                        sentence_translation=rec.sentence_translation,
                        dirty_fields_str=",".join(sorted(new_dirty_fields)),
                        article=article_update,
                        extras=extras_update,
                    )
                report.notes_updated += 1

            for card_rec in rec.cards:
                if local_item.syntactic_unit.card_type == "cloze":
                    direction = Direction.PRODUCTION
                else:
                    direction = Direction.RECOGNITION if card_rec.ord == 0 else Direction.PRODUCTION
                local_dir = local_item.directions.get(direction)
                if local_dir is None:
                    continue

                # Stage 0: ingest Anki revlog for this card into tt_revlog.
                # `guid` came from local_item we just looked up → row always exists.
                coll_id = self._db.get_collocation_id_by_guid(guid)
                assert coll_id is not None
                # Stage 3b compare/new-mode: the incremental-replay boundary is the
                # newest tt_revlog id BEFORE this sync's ingest. Everything ≤ it
                # is already folded into the stored `local_dir` (TT-native grades
                # applied live, prior Anki grades applied at the last sync); the
                # rows ingested below (this sync's new Anki grades) are > it.
                # Layer 71: anchor by (collocation_id, direction) — the domain
                # the replay walks — not by anki_card_id, which misses pre-link
                # rows (anki_card_id=NULL) and re-minted card ids.
                pre_ingest_revlog_id = self._db.latest_revlog_id_for_direction(coll_id, direction)
                self._ingest_anki_revlog_for_card(
                    card_rec.anki_card_id,
                    coll_id,
                    direction,
                )

                anki_last_ms = card_rec.last_review_ms or 0
                if anki_last_ms > max_revlog_ms:
                    max_revlog_ms = anki_last_ms

                # Invariant guard: sync_push runs before sync_pull in run_full_sync
                # and clears dirty_fsrs for every Anki-linked direction (the three
                # mark_direction_clean paths in sync_push). So a real, non-dry-run
                # sync never reaches _pull_merge_direction's dirty branches — they
                # survive only for dry-run (push doesn't mutate) and direct-pull
                # tests. Make any production violation loud: if DIRTY_AT_PULL ever
                # fires in a real sync, the dirty branches are NOT safe to delete.
                # Pinned by test_anki_sync_merge_equivalence; grep sync.log / stderr.
                if local_dir.dirty_fsrs and not dry_run:
                    _log.warning(
                        "DIRTY_AT_PULL cid=%s dir=%s state=%s — sync_push should have cleared this",
                        coll_id,
                        direction.value,
                        local_dir.state.value,
                    )

                new_dir_state = self._pull_merge_direction(
                    card_rec,
                    local_dir,
                    direction,
                    card_rec.last_review,
                    today_start_ms,
                )

                # Recompute DETECTOR (take-Anki-verbatim, fork resolved 2026-06-02):
                # new_dir_state already holds Anki's cards.data, which IS the
                # authoritative write. The forward-step replay only DETECTS a
                # recompute event (Optimize / FSRS-param / retention change /
                # restore) it can't reproduce — recorded for diagnostics, Anki's
                # value kept. Stored state does NOT depend on f32 replay parity.
                # Layer 70: flag genuine recomputes only — skip placeholder s/d
                # (fsrs_known False, the every-sync noise cohort) and TT-newer
                # grades (a known-stale Anki value the recency guard declined, not
                # a recompute). Suspend/bury skip — _queue_to_state owns them.
                if (
                    not dry_run
                    and card_rec.fsrs_known
                    and not _tt_memory_newer(local_dir, card_rec)
                    and new_dir_state.state
                    not in (
                        SRSState.SUSPENDED,
                        SRSState.BURIED,
                    )
                ):
                    replayed = self._replay_incremental(
                        coll_id,
                        direction,
                        local_dir,
                        pre_ingest_revlog_id,
                        replay_params,
                        replay_col_crt,
                    )
                    stab_diff = abs(card_rec.stability - replayed.stability)
                    diff_diff = abs(card_rec.difficulty - replayed.difficulty)
                    if stab_diff > _FSRS_REPLAY_TOLERANCE or diff_diff > _FSRS_REPLAY_TOLERANCE:
                        self._record_recompute_divergence(
                            report,
                            collocation_id=coll_id,
                            direction=direction,
                            replay_stability=replayed.stability,
                            replay_difficulty=replayed.difficulty,
                            anki_stability=card_rec.stability,
                            anki_difficulty=card_rec.difficulty,
                        )

                differs = _direction_differs(local_dir, new_dir_state)
                # Forensic trace for any direction whose Anki state OR TT state
                # touches BURIED. Lets future investigators reconstruct exactly
                # which queue value Anki returned (sched vs user vs released),
                # what TT had locally, and whether the diff actually fired.
                # Grep server stderr for "BURY_TRACE".
                bury_relevant = (
                    card_rec.queue in (-2, -3)
                    or local_dir.state == SRSState.BURIED
                    or new_dir_state.state == SRSState.BURIED
                )
                if bury_relevant:
                    _log.info(
                        "BURY_TRACE cid=%s text=%r dir=%s anki_queue=%d anki_mod=%s "
                        "local=(state=%s kind=%s last_review=%s) "
                        "candidate=(state=%s kind=%s last_review=%s) "
                        "diff=%s write=%s",
                        card_rec.anki_card_id,
                        local_item.syntactic_unit.text,
                        direction.value,
                        card_rec.queue,
                        card_rec.anki_card_mod,
                        local_dir.state.value,
                        local_dir.bury_kind,
                        local_dir.last_review.isoformat() if local_dir.last_review else None,
                        new_dir_state.state.value,
                        new_dir_state.bury_kind,
                        new_dir_state.last_review.isoformat() if new_dir_state.last_review else None,
                        differs,
                        differs and not dry_run,
                    )
                    if card_rec.queue == -2:
                        bury_stats["anki_queue_minus2_seen"] += 1
                    elif card_rec.queue == -3:
                        bury_stats["anki_queue_minus3_seen"] += 1
                    was_buried = local_dir.state == SRSState.BURIED
                    will_be_buried = new_dir_state.state == SRSState.BURIED
                    if differs and was_buried and not will_be_buried:
                        bury_stats["buried_to_released_writes"] += 1
                    if differs and not was_buried and will_be_buried:
                        bury_stats["released_to_buried_writes"] += 1
                    if (
                        differs
                        and local_dir.state == new_dir_state.state
                        and local_dir.bury_kind != new_dir_state.bury_kind
                    ):
                        bury_stats["kind_only_flips_written"] += 1
                    if not differs and was_buried and will_be_buried:
                        bury_stats["buried_state_match_no_write"] += 1
                if differs:
                    if not dry_run:
                        self._db.update_direction(guid, direction, new_dir_state)
                        # The card was graded in Anki instead of in TT's "Check
                        # your work". That real grade supersedes any provisional
                        # listen grade waiting in TT's pending bucket — leaving
                        # it would re-grade the card on the next "Sync it" and
                        # keep it hidden from TT's main queue on the strength of
                        # a review that already happened. Grades only: a bury
                        # flip or mod bump is not a review.
                        if _is_anki_grade(local_dir, new_dir_state):
                            self._db.clear_pending_grade_by_guid(guid, direction.value)
                    report.directions_updated += 1

        self._pull_advance_learning_cutoff(max_revlog_ms, dry_run)
        self._pull_rebuild_session_main_queue(dry_run)

        _log.info("BURY_TRACE summary dry_run=%s %s", dry_run, bury_stats)
        return report

    def _capture_anki_card_state(self, card_id: int) -> dict | None:
        """Snapshot ``cards.{queue, type, left}`` for *card_id* before mutating it.

        Retained for the anki_ahead conflict-resolution check in sync_push.
        """
        if hasattr(self._writer, "get_current_card_state"):
            return self._writer.get_current_card_state(card_id)
        return None

    def _recompute_anki_studied_today_all_decks(self) -> None:
        """Set every revlog-touched deck's ``new_today`` + ``review_today`` counters
        from revlog reality.

        Replaces per-push increment. Walks ``SELECT DISTINCT did`` for cards
        with any revlog ``id >= today_4am_ms``, then for each deck:
        - ``new_today`` = distinct cards whose *first* revlog id falls today
          (Anki's newToday semantic), and
        - ``review_today`` = today's interday-queue answers (Anki's revToday
          semantic — see `OfflineWriter.count_reviews_today_for_deck`),
        writing both back to ``deck.common``.

        Idempotent — running it twice in a row produces the same result.
        Eliminates the per-push double-count drift that the older increment
        approach was prone to (Anki grades a card → Anki bumps; TT pushes the
        same card → push bumped again). Layer 73 added ``review_today`` so the
        rollover branch no longer zeroes the reviews-done counter on AnkiWeb.

        No-op when the writer doesn't support the required methods (e.g., legacy
        AnkiConnect path, FakeReader-only tests). Also no-op when col.crt is
        unknown (can't compute today_day_index).
        """
        if self._anki_col_crt is None:
            return
        required = (
            "list_decks_with_revlog_today",
            "count_first_grades_today_for_deck",
            "count_reviews_today_for_deck",
            "set_deck_studied_today",
        )
        if not all(hasattr(self._writer, m) for m in required):
            return
        today_4am_ms = int(_local_today_4am().timestamp() * 1000)
        day_index = compute_anki_day_index(self._anki_col_crt)
        for deck_id in self._writer.list_decks_with_revlog_today(today_4am_ms):
            new_count = self._writer.count_first_grades_today_for_deck(deck_id, today_4am_ms)
            review_count = self._writer.count_reviews_today_for_deck(deck_id, today_4am_ms)
            self._writer.set_deck_studied_today(deck_id, day_index, new_count, review_count)

    def _push_revlog_for_direction(self, guid: str, direction: Direction, ds: DirectionState) -> None:
        """Push unpushed tt_revlog rows for *direction* to Anki's revlog.

        Per-grade push: each tt_revlog row with id > MAX(revlog.id) for the
        card is inserted at its own grade-time id (Layer 74 collision guard).
        Verbatim row data replaces the old _derive_revlog_shape reconstruction,
        preserving intermediate grades (e.g. a lapse followed by a relearn
        step) that the old collapsed-per-direction approach lost.

        Reps/lapses are bumped once after all inserts with aggregated counts:
        reps += rows inserted, lapses += rows where review_kind=1 AND
        button_chosen=1 (lapse on a review-footing card).

        Falls back to _derive_revlog_shape when there are no candidate rows
        above the watermark (pre-Layer-78 history, test helpers, or the
        accepted phone-grade-newer edge where a phone grade empties the
        candidate set).
        """
        if ds.anki_card_id is None:
            return
        coll_id = self._db.get_collocation_id_by_guid(guid)
        if coll_id is None:
            return
        max_anki_id = self._writer.max_revlog_id_for_card(ds.anki_card_id)
        rows = self._db.get_unpushed_revlog_rows(coll_id, direction, max_anki_id)
        if rows:
            lapse_count = sum(1 for r in rows if r.review_kind == 1 and r.button_chosen == 1)
            for i, row in enumerate(rows):
                is_last = i == len(rows) - 1
                self._writer.write_revlog(
                    cid=ds.anki_card_id,
                    ease=row.button_chosen,
                    ivl=row.interval,
                    last_ivl=row.last_interval,
                    factor=row.factor,
                    time_ms=row.taken_millis,
                    type_=row.review_kind,
                    preferred_id=row.id,
                    is_lapse=(row.review_kind == 1 and row.button_chosen == 1),
                    reps_bump=len(rows) if is_last else 0,
                    lapses_bump=lapse_count if is_last else 0,
                    ds_reps=ds.reps if is_last else None,
                    ds_lapses=ds.lapses if is_last else None,
                )
        else:
            learn_steps, _ = resolve_learning_steps(self._db)
            relearn_steps, _ = resolve_relearning_steps(self._db)
            type_, ivl, last_ivl = _derive_revlog_shape(ds, learn_steps, relearn_steps)
            ease = ds.last_rating if ds.last_rating is not None else 3
            # Anki writes factor = round(difficulty_shifted × 1000) where
            # difficulty_shifted = (difficulty − 1.0)/9.0 + 0.1 (rslib/src/card/mod.rs:115-125).
            # No clamp: Anki stores the raw shifted value; for real FSRS difficulty [1, 10]
            # the result is always in [100, 1100].
            difficulty_shifted = (ds.difficulty - 1.0) / 9.0 + 0.1
            factor = round(difficulty_shifted * 1000)
            preferred_id = int(ds.last_review.timestamp() * 1000) if ds.last_review else None
            is_lapse = ds.prior_state == SRSState.REVIEW and ds.last_rating == Rating.AGAIN.value
            self._writer.write_revlog(
                cid=ds.anki_card_id,
                ease=ease,
                ivl=ivl,
                last_ivl=last_ivl,
                factor=factor,
                time_ms=ds.last_review_time_ms,
                type_=type_,
                preferred_id=preferred_id,
                is_lapse=is_lapse,
                ds_reps=ds.reps,
                ds_lapses=ds.lapses,
            )

    def sync_push(self, dry_run: bool = False, force_fsrs: bool = False) -> PushReport:
        # Deferred: lives in app.plugins.anki_sync.sync so it reads the patched _MEDIA_DIR.
        from app.plugins.anki_sync.sync import _copy_tt_media_to_anki

        """Push TunaTale → Anki. Returns a PushReport summarising changes."""
        from app.srs.queue_stats import resolve_fsrs_params

        report = PushReport()
        # Layer 70: threaded into update_card_memory_state so a card whose
        # cards.data lacks `dr` (TT-only-graded — Anki never wrote it) still
        # sorts at its real R position in Anki's R-asc queue instead of the
        # SM2 fallback (oracle gotcha #1).
        push_desired_retention = resolve_fsrs_params(self._db)[0].desired_retention

        for guid, anki_note_id, dirty_fields_str, item, coll_id in self._db.list_dirty_field_edits():
            if anki_note_id is None:
                continue
            dirty_set = {f for f in dirty_fields_str.split(",") if f}
            fields: dict[str, str] = {}
            if item.syntactic_unit.card_type == "cloze":
                # Cloze notes: any of {translation, sentence_translation, note, audio}
                # dirty → rebuild Back Extra. Cloze has no separate "English" field.
                if dirty_set & {"translation", "sentence_translation", "note", "grammar", "audio"}:
                    sentence_audio = self._db.get_sentence_audio_filename(coll_id)
                    fields["Back Extra"] = build_cloze_back_extra(
                        item.syntactic_unit.translation,
                        item.syntactic_unit.source_sentence_translation,
                        note=item.syntactic_unit.note,
                        grammar=item.syntactic_unit.grammar,
                        sentence_audio_filename=sentence_audio,
                    )
                    if sentence_audio and not dry_run:
                        _copy_tt_media_to_anki(self._writer, sentence_audio)
                if "source_sentence" in dirty_set:
                    # The cloze front (Anki "Text" field) is the clozed sentence.
                    fields["Text"] = item.syntactic_unit.source_sentence or ""
            else:
                if "text" in dirty_set:
                    l2_field = self._writer.get_l2_field_for_note(anki_note_id)
                    fields[l2_field] = item.syntactic_unit.text
                if "translation" in dirty_set:
                    fields["English"] = item.syntactic_unit.translation
                if "source_sentence" in dirty_set:
                    # Vocab example sentence lives in the Anki "Note" field.
                    fields["Note"] = item.syntactic_unit.source_sentence or ""
                if "image" in dirty_set:
                    # A TT-side image swap or removal. Write the Anki note's Image
                    # field so the media-refresh collapse (which runs LATER in
                    # run_full_sync) sees the new file referenced and preserves it
                    # instead of reverting the swap; copy the bytes into
                    # collection.media alongside, same as the sentence-audio path
                    # above. Removal (no TT image) clears the field to "".
                    img = self._db.get_image_filename(coll_id)
                    fields["Image"] = f'<img src="{img}">' if img else ""
                    if img and not dry_run:
                        _copy_tt_media_to_anki(self._writer, img)
            if not fields:
                # A cloze note has no Image field and no bare L2 field, so stray
                # "image"/"text" flags would never produce a field here and would
                # pin dirty_fields across every future sync (the create/push cycle
                # never clears them). Drop just those flags so the row goes clean.
                stray = dirty_set & {"image", "text"}
                if stray and not dry_run:
                    self._db.set_dirty_fields(guid, ",".join(sorted(dirty_set - stray)))
                continue
            if not dry_run:
                self._writer.update_note_fields(anki_note_id, fields)
                self._db.set_dirty_fields(guid, "")
            report.notes_pushed += 1

        # First loop: dirty directions (TunaTale's grade is latest)
        recovered = self._recovered_directions
        for guid, direction, ds in self._db.list_dirty():
            if ds.anki_card_id is None:
                continue
            # Reset-to-new ("Forget"): a NEW-state dirty direction with no reps is
            # a TunaTale reset (reset_collocation / set_state_by_id→NEW). Propagate
            # it as an Anki forget rather than the default review-promoting
            # set_due_date, so both apps agree the card is new. Push runs before
            # pull, so once Anki is forgotten the subsequent pull reads queue=0 and
            # keeps NEW. Skip recovered directions — orphan re-mint already rebuilds
            # those fresh. Idempotent: no-op when Anki already has the card new.
            if ds.state == SRSState.NEW and ds.reps == 0 and (guid, direction.value) not in recovered:
                if not dry_run:
                    anki_state_before = self._capture_anki_card_state(ds.anki_card_id)
                    if anki_state_before is not None and anki_state_before["type"] != 0:
                        self._writer.forget_card(ds.anki_card_id)
                    self._db.mark_direction_clean(guid, direction)
                report.directions_pushed += 1
                continue
            # Recovery: when detect_and_reset_orphans cleared this direction's
            # anki_card_id earlier in the run and sync_create_new just minted a
            # fresh one, force_fsrs writes the TT-side stability/difficulty into
            # the new card's data JSON regardless of the global flag.
            # ds.fsrs_force_next: a restored ("un-marked known") direction is in
            # review state, so it lacks the ds.state==KNOWN force signal; the
            # flag carries the force so its restored stability overwrites Anki's
            # still-inflated cards.data before the next take-Anki-verbatim pull.
            row_force_fsrs = (
                force_fsrs or (guid, direction.value) in recovered or ds.state == SRSState.KNOWN or ds.fsrs_force_next
            )
            # Anki's set-due-date interprets "days from today" against ITS rollover
            # day; computing the delta against calendar-today in [midnight, 4 AM)
            # lands the due date one day early.
            days_str = str(max(0, (ds.due_at.date() - anki_today()).days))
            if not dry_run:
                # Snapshot Anki's pre-push card state for the anki_ahead
                # conflict-resolution check. Must be captured BEFORE
                # set_learning_state / set_due_date, which mutate cards.queue.
                anki_state_before = self._capture_anki_card_state(ds.anki_card_id)

                if ds.state == SRSState.SUSPENDED:
                    self._writer.suspend([ds.anki_card_id])
                else:
                    self._writer.unsuspend([ds.anki_card_id])

                # Handle learning/relearning cards differently: update left and due (absolute timestamp)
                if (
                    ds.state in (SRSState.LEARNING, SRSState.RELEARNING)
                    and ds.left is not None
                    and ds.due_at is not None
                ):
                    # Fix 3: defer to Anki if Anki is further along. Push runs
                    # before pull in the sync flow, so without this guard a
                    # stale TT view would clobber Anki's correct step state /
                    # graduation. The matching pull-side defense (Fix 2) then
                    # carries Anki's view into TT and clears dirty_fsrs.
                    anki_now = anki_state_before
                    anki_ahead = False
                    if anki_now is not None:
                        if anki_now["queue"] == 2:
                            anki_ahead = True  # graduated
                        elif anki_now["queue"] in (1, 3) and _anki_step_ahead(anki_now["left"], ds.left):
                            anki_ahead = True
                        # Recency guard (Layer 69): "Anki is ahead" is a state-rank
                        # heuristic and must not discard a TT grade that is NEWER than
                        # Anki's last change. If TT graded after Anki's cards.mod
                        # (epoch secs), TT is the newer authority — e.g. a fresh TT
                        # lapse on a card Anki/AnkiWeb still shows graduated (the
                        # 'Imam dovolj časa' loss). Push instead of deferring.
                        if anki_ahead and ds.last_review is not None:
                            anki_mod = anki_now.get("mod")
                            if anki_mod is not None and ds.last_review.timestamp() > anki_mod:
                                anki_ahead = False

                    if not anki_ahead:
                        # queue=1 for both; type=1 for LEARNING, type=3 for RELEARNING (lapse)
                        due_timestamp = int(ds.due_at.timestamp())
                        type_ = 3 if ds.state == SRSState.RELEARNING else 1
                        self._writer.set_learning_state(ds.anki_card_id, ds.left, due_timestamp, type_=type_)
                    else:
                        # Skip both the card update and the revlog: TT's grade
                        # is being discarded in favour of Anki's. mark_direction_clean
                        # at end of loop drops the dirty flag so we don't keep
                        # retrying.
                        self._db.mark_direction_clean(guid, direction)
                        report.directions_pushed += 1
                        continue
                else:
                    # Review/new cards: use set_due_date (days since col_crt)
                    self._writer.set_due_date([ds.anki_card_id], days_str)

                if ds.reps > 0:
                    self._push_revlog_for_direction(guid, direction, ds)
                schema_ok = self._anki_col_ver is None or self._anki_col_ver <= KNOWN_ANKI_SCHEMA_VER
                if schema_ok and (ds.reps > 0 or row_force_fsrs):
                    # Layer 70: every grade push carries the post-grade FSRS
                    # memory state (merge-update — preserves pos/decay/dr).
                    # Without this, Anki's s/d/lrt stay at their pre-grade
                    # values and the same sync's pull reverts the TT grade
                    # (the cid=428 lapse-arc loss, 2026-06-10).
                    self._writer.update_card_memory_state(
                        ds.anki_card_id,
                        stability=ds.stability,
                        difficulty=ds.difficulty,
                        last_review_secs=int(ds.last_review.timestamp()) if ds.last_review else None,
                        desired_retention=push_desired_retention,
                    )
                if row_force_fsrs and schema_ok:
                    ivl_val = max(1, round(ds.stability))
                    factor_val = max(1300, min(13000, round(ds.difficulty * 1000)))
                    self._writer.set_specific_value_of_card(
                        ds.anki_card_id,
                        keys=["ivl", "factor"],
                        new_values=[str(ivl_val), str(factor_val)],
                    )
                self._db.mark_direction_clean(guid, direction)
            report.directions_pushed += 1

        # Second loop: clean directions that need revlog (Anki won earlier by timestamp)
        for guid, direction, ds in self._db.list_recently_graded_clean():
            if ds.anki_card_id is None:
                continue
            if not dry_run:
                if ds.reps > 0:
                    self._push_revlog_for_direction(guid, direction, ds)
                # Clear last_rating so it doesn't re-fire next sync
                self._db.mark_direction_clean(guid, direction)
            report.directions_pushed += 1

        # Layer 47: backfill sibling-bury for every today-graded direction.
        # Scans collocation_directions where last_review = today (local) and
        # fires writer.bury_siblings for each — regardless of dirty_fsrs, so
        # already-cleaned grades from earlier in the day also get propagated.
        # bury_siblings's WHERE filter on Anki's sibling queue makes this
        # idempotent: cards already at queue=-2 are no-ops.
        if not dry_run:
            self._backfill_bury_siblings_for_today_grades()
            self._recompute_anki_studied_today_all_decks()

        return report

    def _backfill_bury_siblings_for_today_grades(self) -> None:
        """Replay sibling-bury for every TT direction graded today (local).

        Covers the case where a prior sync_push wrote the grade without
        firing bury (pre-Layer-47), AND the normal case where this sync
        just pushed a grade (idempotent re-bury). Reads deck config flags
        once and short-circuits when both are disabled.
        """
        bury_new, _ = resolve_bury_new(self._db)
        bury_review, _ = resolve_bury_review(self._db)
        if not (bury_new or bury_review):
            return
        today = anki_today()
        for anki_card_id, state_value in self._db.list_anki_cards_graded_today(today):
            graded_queue = _STATE_VALUE_TO_ANKI_QUEUE.get(state_value)
            if graded_queue is None:
                continue
            self._writer.bury_siblings(
                graded_card_id=anki_card_id,
                graded_queue=graded_queue,
                bury_new=bury_new,
                bury_reviews=bury_review,
            )

    async def sync_create_new(
        self,
        *,
        deck_name: str,
        model_name: str,
        dry_run: bool = False,
        _media_fn=None,
    ) -> CreateNewReport:
        # Deferred: lives in app.plugins.anki_sync.sync so it reads the patched _MEDIA_DIR.
        from app.plugins.anki_sync.sync import _copy_tt_media_to_anki

        """Create Anki notes for SRS items that have no anki_note_id yet.

        Returns a CreateNewReport with created/linked/skipped counters.
        """
        items = list(self._db.list_items_without_anki_note())

        # Skip items whose directions are all suspended/buried — they were
        # ignored via "Ignore" (untrack) before ever reaching Anki, or orphan
        # recovery cleared their anki_note_id while they were suspended.
        _FILTER_OUT = {SRSState.SUSPENDED, SRSState.BURIED}
        items = [(g, i, c) for g, i, c in items if not all(ds.state in _FILTER_OUT for ds in i.directions.values())]

        # Hold back a card with NOTHING on its back. The lesson generator drops
        # words from its whole-dialogue gloss, so a card can be created with an
        # empty translation; `cards/gloss_llm` retries per word, but that retry
        # runs after the response and the free tier can exhaust. Without this
        # gate a failed retry ships an empty card to Anki, which is exactly how
        # `ferskt` reached the collection and was failed 12 times
        # (bd tunatale-1wiw).
        #
        # `extras` counts as a back: an imported card can carry IPA / Meaning /
        # Inflections with the translation column empty and still teach
        # something. Only the row with neither is held.
        #
        # VOCAB ONLY. A cloze card has no gloss BY DESIGN — its front is the
        # sentence with a blank and its back is the answer plus Back Extra, so
        # `translation` is empty on every one of them. Gating clozes on it holds
        # back the entire cloze pipeline; six pre-existing tests caught that on
        # the first cut of this filter (2026-08-22), which is why this reads
        # `card_type == "vocab"` rather than a general "has a back".
        #
        # HELD, not dropped — the row stays NEW and studiable in TT, and the next
        # listen retries the gloss. When one lands, this filter stops matching
        # and the ordinary push path mints the note with no special handling.
        items = [
            (g, i, c)
            for g, i, c in items
            if i.syntactic_unit.card_type != "vocab" or i.syntactic_unit.translation.strip() or i.syntactic_unit.extras
        ]

        if dry_run:
            return CreateNewReport(count=len(items))

        # Sort oldest-first so each successive item is allocated AHEAD of the
        # previous one by create_note's front-of-queue allocator, leaving the
        # freshest TT auto-add frontmost in the user's next review (Layer 24).
        # The sort direction is the same under either gather priority — the
        # allocator is what knows which end of the position range the front is
        # (Layer 83). It used to be MAX(due)+1 unconditionally, which is the front
        # only under HighestPosition; on a DECK-gather deck that put every fresh
        # add at the very back.
        items.sort(key=lambda gi: self._db.get_created_at_by_guid(gi[0]) or "")

        used_image_urls: set[str] = set()
        created = 0
        linked = 0
        skipped = 0
        image_ok = 0
        image_no_results = 0
        image_failed = 0

        for guid, item, coll_id in items:
            if item.syntactic_unit.card_type == "cloze":
                cloze_text = make_cloze_text(
                    item.syntactic_unit.text,
                    item.syntactic_unit.source_sentence or "",
                )
                sentence_audio = self._db.get_sentence_audio_filename(coll_id)
                back_extra = build_cloze_back_extra(
                    item.syntactic_unit.translation,
                    item.syntactic_unit.source_sentence_translation,
                    grammar=item.syntactic_unit.grammar,
                    sentence_audio_filename=sentence_audio,
                )
                try:
                    note_id = self._writer.create_cloze_note(
                        deck_name,
                        cloze_text,
                        back_extra=back_extra,
                        tags=["tunatale", "cloze"],
                        language_code=settings.target_language,
                    )
                    created += 1
                except DuplicateNoteError as exc:
                    note_id = exc.note_id
                    linked += 1

                if sentence_audio and not dry_run:
                    _copy_tt_media_to_anki(self._writer, sentence_audio)

                cards_by_ord = self._writer.get_cards_for_note(note_id)
                # Cloze notetype has exactly one template (ord=0)
                card_ids = {Direction.PRODUCTION: cards_by_ord[0]}
                self._db.set_anki_ids(guid, note_id, card_ids)
                continue

            word = item.syntactic_unit.text
            english = item.syntactic_unit.translation
            audio_tag = ""
            image_tag = ""

            # Reuse media already generated at card-creation time — the add-time
            # paths (POST /items, /listen, base/key-phrase) now fetch image+audio
            # inline (app.cards.media.vocab_media), so a card is complete in TT
            # before it ever syncs. Only *fetch* here for cards that still have no
            # TT media (legacy rows, seed imports, Anki-originated notes). This is
            # what keeps a card from getting a second, different Pixabay image.
            existing_audio = self._db.get_audio_filename(coll_id)
            existing_image = self._db.get_image_filename(coll_id)

            media = None
            if _media_fn is not None and (existing_audio is None or existing_image is None):
                media = await _media_fn(
                    word,
                    english,
                    source_sentence=item.syntactic_unit.source_sentence,
                    grammar=item.syntactic_unit.grammar,
                    used_image_urls=used_image_urls,
                )

            if existing_audio is not None:
                _copy_tt_media_to_anki(self._writer, existing_audio)
                audio_tag = f"[sound:{existing_audio}]"
            elif media is not None and media.audio_bytes is not None:
                prefix = settings.target_language if media.audio_source == "forvo" else "tts"
                audio_filename = f"{_safe_stem(word, prefix)}.mp3"
                self._writer.store_media_file(audio_filename, media.audio_bytes)
                audio_tag = f"[sound:{audio_filename}]"
                _store_tt_media(
                    self._db, coll_id, f"audio_{media.audio_source or 'tts'}", audio_filename, media.audio_bytes
                )

            if existing_image is not None:
                _copy_tt_media_to_anki(self._writer, existing_image)
                image_tag = f'<img src="{existing_image}">'
            elif media is not None and media.image_bytes is not None:
                ext = media.image_ext or "jpg"
                img_filename = f"{_safe_stem(english, 'img')}.{ext}"
                self._writer.store_media_file(img_filename, media.image_bytes)
                image_tag = f'<img src="{img_filename}">'
                _store_tt_media(self._db, coll_id, "image", img_filename, media.image_bytes)

            # Classify image fetch status into report counters
            if media is not None:
                img_status = getattr(media, "image_status", None)
                if img_status == "ok":
                    image_ok += 1
                elif img_status == "no_results":
                    image_no_results += 1
                elif img_status and img_status not in (None, "skipped"):
                    image_failed += 1
                    _log.warning(
                        "image fetch failed for %r: status=%s query=%s",
                        word,
                        img_status,
                        getattr(media, "image_query_used", None),
                    )

            # The L2 word lives in the mint notetype's sort field (ord 0) — "Slovene"
            # for Slovene Vocabulary, "Norwegian" for Norwegian Vocabulary.
            l2_field = self._writer.get_sort_field_name(model_name)
            fields = {
                l2_field: word,
                "English": english,
                "Audio": audio_tag,
                "Image": image_tag,
                "Grammar": item.syntactic_unit.grammar or "",
                "Note": item.syntactic_unit.source_sentence or "",
                "DisambigKey": item.syntactic_unit.disambig_key or "",
            }

            try:
                note_id = self._writer.create_note(
                    deck_name, model_name, fields, ["tunatale"], language_code=settings.target_language
                )
                created += 1
            except DuplicateNoteError as exc:
                note_id = exc.note_id
                linked += 1

            cards_by_ord = self._writer.get_cards_for_note(note_id)
            _ORD_TO_DIR = {0: Direction.RECOGNITION, 1: Direction.PRODUCTION}
            card_ids = {_ORD_TO_DIR[ord_]: cid for ord_, cid in cards_by_ord.items() if ord_ in _ORD_TO_DIR}
            self._db.set_anki_ids(guid, note_id, card_ids)

        count = created + linked + skipped

        # Reverse-import pass: mint new TT rows from Anki-only notes (Layer 22)
        records = self._reader.get_note_records()
        linked_anki_ids = self._db.list_linked_anki_note_ids()
        notes_created_from_anki = 0

        for rec in records:
            if rec.anki_note_id in linked_anki_ids:
                continue

            card_type = "cloze" if rec.is_cloze else "vocab"
            word_count = max(1, len(rec.l2_text.split()))
            unit = SyntacticUnit(
                text=rec.l2_text,
                translation=rec.translation,
                word_count=word_count,
                difficulty=1,
                source="anki",
                frequency=0,
                disambig_key=rec.disambig_key,
                # Collapse the reader's "no opinion" None to the creation
                # default. This is a CREATE, not a heal: there is no local value
                # for Anki to decline to overwrite, and both columns are NOT
                # NULL. The None sentinel is meaningful only in the heal block
                # above, where it distinguishes "Anki has no such field" from
                # "Anki's field is blank".
                article=rec.article or "",
                extras=rec.extras or (),
                lemma=rec.l2_text.lower() if word_count == 1 else None,
                source_sentence=rec.note,
                source_sentence_translation=rec.sentence_translation,
                card_type=card_type,
            )

            directions: dict[Direction, DirectionState] = {}
            cards_to_import = rec.cards[:1] if rec.is_cloze else rec.cards
            for card in cards_to_import:
                if rec.is_cloze:
                    direction = Direction.PRODUCTION
                else:
                    direction = Direction.RECOGNITION if card.ord == 0 else Direction.PRODUCTION

                state = _queue_to_state(card.queue, card.card_type, card.reps)

                directions[direction] = DirectionState(
                    direction=direction,
                    due_at=card.due_at,
                    stability=card.stability,
                    difficulty=card.difficulty,
                    reps=card.reps,
                    lapses=card.lapses,
                    state=state,
                    last_review=card.last_review,
                    anki_card_id=card.anki_card_id,
                    anki_due=card.anki_due or 0,
                    anki_card_mod=card.anki_card_mod,
                    left=card.left,
                    dirty_fsrs=False,
                    last_synced_at=datetime.now(UTC).isoformat(),
                    prior_state=None,
                    introduced_at=_resolve_introduced_at(
                        DirectionState(direction=direction, due_at=card.due_at),
                        state,
                        first_review_ms=card.first_review_ms,
                    ),
                )

            if not directions:
                continue

            self._db.upsert_by_guid(unit, settings.target_language, directions, anki_note_id=rec.anki_note_id)
            notes_created_from_anki += 1

        return CreateNewReport(
            count=count,
            created=created,
            linked=linked,
            skipped=skipped,
            notes_created_from_anki=notes_created_from_anki,
            image_ok=image_ok,
            image_no_results=image_no_results,
            image_failed=image_failed,
        )

    async def promote_production_cards(
        self,
        *,
        dry_run: bool = False,
        _media_fn=None,
        limit: int = PRODUCTIONS_PER_SYNC,
    ) -> PromotionReport:
        """Mint production cards for words whose recognition card has graduated.

        A deck imported recognition-only gains the *capability* to carry
        production cards from ``add_production_template``; this is where a word
        actually gets one — one card, one image fetch, when it graduates. It
        serves the forward trigger and the backlog drain through a single query
        (``list_words_awaiting_production``), because a word that graduated
        yesterday and one that graduated before the migration are the same
        thing: graduated recognition, no production direction.

        Paced at *limit* per sync (settled with the user 2026-08-15). The budget
        is spent on **work**, not rows: a word whose notetype cannot carry a
        production card costs one indexed read and is skipped, so the drain is
        never wedged behind an unservable head of the queue.

        Three things this must not do, each measured or observed rather than
        assumed:

        - mint into a notetype with no ``Production`` template (Slovene's
          ``Basic`` phonics notes and its clozes) — the card would be an orphan
          Check Database deletes;
        - mint against an empty ``Image`` — Anki calls that an *empty card* and
          offers to delete it, so those words are counted and left for the cloze
          fallback;
        - create a second collocation. These words already have one; a duplicate
          is the GUID-collision hazard (Layer 33/35).

        The image write and the card mint are one transaction inside
        ``OfflineWriter.mint_production_card`` — see its docstring for why the
        gap between them is the hazard.
        """
        from app.plugins.anki_sync.sync import _copy_tt_media_to_anki

        report = PromotionReport(awaiting=self._db.count_words_awaiting_production())
        if report.awaiting == 0:
            return report
        if dry_run:
            # WARNING, not INFO: start-dev.sh runs uvicorn at --log-level warning
            # and peer-sync only ever runs inside that dev server, so an info line
            # here is written nowhere a human will read it.
            _log.warning("PRODUCTION_MINT awaiting=%d (dry run — nothing minted)", report.awaiting)
            return report

        used_image_urls: set[str] = set()
        fetches_left = limit
        for cand in self._db.list_words_awaiting_production(limit=PRODUCTION_SCAN_LIMIT):
            if report.minted + report.adopted >= limit or fetches_left <= 0:
                break

            if not self._writer.production_capable(cand.anki_note_id):
                report.no_template += 1
                continue

            unit = cand.item.syntactic_unit
            material = self._reader.get_cloze_material(cand.anki_note_id)

            # Closed-class words route to a cloze without spending a fetch: a
            # picture of "foran" is noise, and minting a card with a meaningless
            # image is the failure mode this whole router exists to avoid. The
            # test goes through the language registry — the deck's own POS label
            # is mapped to a UPOS tag by its notetype profile — so this is the
            # same closed-class decision `/listen` makes, not a parallel one.
            if is_function_word(unit.text, settings.target_language, upos=material.upos):
                self._fallback_to_cloze(cand, material, report)
                continue

            filename = self._db.get_image_filename(cand.collocation_id)
            if filename is None:
                # No TT image yet: fetch one now, at promotion time. This is the
                # cost the bulk backfill was rejected for front-loading.
                fetches_left -= 1
                media = (
                    None
                    if _media_fn is None
                    else await _media_fn(
                        unit.text,
                        unit.translation,
                        used_image_urls=used_image_urls,
                        source_sentence=unit.source_sentence or "",
                        grammar=unit.grammar or "",
                    )
                )
                if media is None or media.image_bytes is None:
                    # The one genuinely new decision in the router: a word that
                    # cannot be pictured gets a cloze instead of a bad picture.
                    self._fallback_to_cloze(cand, material, report)
                    continue
                # Hash-suffixed, like `replace_item_image` and unlike
                # `sync_create_new`'s bare `img_<gloss>.jpg`. At 1550 words a
                # shared gloss is common ("beslutning" and "avgjørelse" are both
                # "decision"), and the bare name would have the second fetch
                # overwrite the first word's picture in place — silently changing
                # a card the learner already knows. Identical bytes still collapse
                # to one file.
                digest = _sha256(media.image_bytes).hexdigest()[:8]
                filename = f"{_safe_stem(unit.translation, 'img')}_{digest}.{media.image_ext or 'jpg'}"
                self._writer.store_media_file(filename, media.image_bytes)
                _store_tt_media(self._db, cand.collocation_id, "image", filename, media.image_bytes)
            else:
                # Reuse the picture the card already has — a word imaged at add
                # time must not acquire a second, different one.
                _copy_tt_media_to_anki(self._writer, filename)

            minted = self._writer.mint_production_card(cand.anki_note_id, f'<img src="{filename}">')
            self._db.add_production_direction(
                cand.collocation_id,
                anki_card_id=minted.card_id,
                anki_due=minted.due,
            )
            if minted.created:
                report.minted += 1
            else:
                report.adopted += 1

        # WARNING, not INFO — see the dry-run branch above. This is the only line
        # that reports what the phase DID, and its branch counters are what
        # separate "minted 10" from "skipped 200 as no_template" from "routed 200
        # to cloze". Suppressed at the dev server's log level, the Norwegian
        # backlog sat at 1435 with nothing minted since 2026-07-25 and the reason
        # was unreadable rather than unknown (tunatale-byw, 2026-08-19).
        _log.warning(
            "PRODUCTION_MINT awaiting=%d minted=%d adopted=%d clozed=%d unservable=%d no_template=%d",
            report.awaiting,
            report.minted,
            report.adopted,
            report.clozed,
            report.unservable,
            report.no_template,
        )
        return report

    def _fallback_to_cloze(self, cand, material, report: PromotionReport) -> None:
        """Give *cand* a cloze production card built from the note's own fields.

        The word cannot be pictured — it is closed-class, or its image search
        came back empty — and a production card fronted by a meaningless picture
        is worse than none. A cloze is a **separate note** on Anki's built-in
        ``Cloze`` notetype, so this writes a TT collocation and stops: the next
        sync's ``sync_create_new`` mints the note, reads back the card id and
        links it, exactly as it does for a ``/listen`` cloze. Nothing is written
        to the collection here.

        Declines (counting ``unservable``) in two cases. When no example sentence
        can carry the cloze — the LLM tier that would serve those is not built.
        And when the word's own disambig is empty: a cloze collocation carries no
        disambig, so the two rows would share the ``(text, language, disambig)``
        identity, and ``add_collocation`` would merge into the vocab row and add
        a production direction with no Anki card behind it — the stranding shape,
        arriving by a different road. 28 rows in the real Norwegian deck have an
        empty ``Word class``; none is a candidate today, so this guard costs
        nothing measurable and the failure it prevents would be silent.
        """
        unit = cand.item.syntactic_unit
        choice = choose_cloze_sentence(
            unit.text,
            material.examples,
            material.inflections,
            variants=card_surface_variants(settings.target_language, unit.text),
        )
        if choice is None:
            report.unservable += 1
            _log.warning(
                "PRODUCTION_MINT_UNSERVABLE text=%r cid=%d — no image and no clozable example sentence",
                unit.text,
                cand.collocation_id,
            )
            return

        if not unit.disambig_key.strip():
            report.unservable += 1
            _log.warning(
                "PRODUCTION_MINT_UNSERVABLE text=%r cid=%d — the word carries no disambig, so a cloze"
                " row would collide with its own vocab collocation",
                unit.text,
                cand.collocation_id,
            )
            return

        # Store the cloze pre-built, like `/listen` does: the surface to blank is
        # the sentence's own inflected form, which `sync_create_new` could not
        # re-derive from the headword. `make_cloze_text` is idempotent, so its
        # second pass there leaves this untouched.
        cloze_unit = SyntacticUnit(
            text=unit.text,
            translation=unit.translation,
            word_count=1,
            difficulty=unit.difficulty,
            source="anki",
            frequency=unit.frequency,
            lemma=unit.text.casefold(),
            card_type="cloze",
            source_sentence=make_cloze_text(choice.surface, choice.sentence),
            source_sentence_translation=choice.gloss,
        )
        self._db.add_collocation(cloze_unit, language_code=settings.target_language)
        # Record WHICH word this cloze covers. The rows stay separate — sync maps
        # one Anki note to one collocation — but every reader now resolves the
        # word rather than whichever of the two the lemma index happened to
        # return, and the selection query excludes by this link rather than by a
        # text match that could not tell two homographs apart.
        #
        # Re-derive the id from the guid rather than from `add_collocation`, which
        # returns whether it created the row, not which row: on the idempotent
        # second pass there is no new id to return but the link still has to hold.
        cloze_id = self._db.get_collocation_id_by_guid(
            compute_guid(cloze_unit.text, settings.target_language, cloze_unit.disambig_key or "")
        )
        self._db.set_base_collocation_id(cloze_id, cand.collocation_id)
        report.clozed += 1
