"""Sync facade + runner — the single sync sequence ``run_full_sync``.

Engine/reader/writer/common split per the 2026-06-11 refactor.  Exports stable
re-export surface for tests and archive scripts; internal code imports the
leaf modules directly.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app.cards.media.vocab_media import safe_stem as _safe_stem  # noqa: F401 — re-export (archive scripts, cloze_tts)
from app.cards.media.vocab_media import store_tt_media as _store_tt_media  # noqa: F401 — re-export (archive scripts)
from app.config import settings

# Phase 9 split: leaf helpers live in sync_common / sync_reader; imported here both
# for use by the remaining sync code AND as the stable re-export surface (tests +
# archive scripts import these names from app.plugins.anki_sync.sync). The redundant `X as X`
# form marks them as explicit re-exports so ruff's F401 autofix never strips one
# whose last in-module use moves out in a later split commit.
from app.plugins.anki_sync.sync_common import (
    KNOWN_ANKI_SCHEMA_VER as KNOWN_ANKI_SCHEMA_VER,
)
from app.plugins.anki_sync.sync_common import (
    CardRecord as CardRecord,
)
from app.plugins.anki_sync.sync_common import (
    CreateNewReport as CreateNewReport,
)
from app.plugins.anki_sync.sync_common import (
    DuplicateNoteError as DuplicateNoteError,
)
from app.plugins.anki_sync.sync_common import (
    NoteRecord as NoteRecord,
)
from app.plugins.anki_sync.sync_common import (
    OrphanThresholdExceededError as OrphanThresholdExceededError,
)
from app.plugins.anki_sync.sync_common import (
    PromotionReport as PromotionReport,
)
from app.plugins.anki_sync.sync_common import (
    PullReport as PullReport,
)
from app.plugins.anki_sync.sync_common import (
    PushReport as PushReport,
)
from app.plugins.anki_sync.sync_common import (
    RecomputeDivergence as RecomputeDivergence,
)
from app.plugins.anki_sync.sync_common import (
    _local_today_4am as _local_today_4am,
)
from app.plugins.anki_sync.sync_common import (
    build_cloze_back_extra as build_cloze_back_extra,
)
from app.plugins.anki_sync.sync_common import (
    extract_cloze_note as extract_cloze_note,
)
from app.plugins.anki_sync.sync_common import (
    extract_cloze_sentence_translation as extract_cloze_sentence_translation,
)
from app.plugins.anki_sync.sync_common import (
    extract_cloze_translation as extract_cloze_translation,
)
from app.plugins.anki_sync.sync_engine import (
    AnkiSync as AnkiSync,
)
from app.plugins.anki_sync.sync_engine import (
    _derive_revlog_shape as _derive_revlog_shape,
)
from app.plugins.anki_sync.sync_engine import (
    _direction_differs as _direction_differs,
)
from app.plugins.anki_sync.sync_engine import (
    _resolve_introduced_at as _resolve_introduced_at,
)
from app.plugins.anki_sync.sync_engine import (
    _step_minutes_from_left as _step_minutes_from_left,
)
from app.plugins.anki_sync.sync_reader import OfflineReader as OfflineReader
from app.plugins.anki_sync.sync_writer import MintedCard as MintedCard
from app.plugins.anki_sync.sync_writer import OfflineWriter as OfflineWriter
from app.srs.database import SRSDatabase

_log = logging.getLogger(__name__)


# TT's media dir, from the setting rather than a __file__ walk — see
# Settings.media_dir. (Was `.parent` x4: one level deeper than the pre-Stage-4
# app/anki/, hence the extra one, which is exactly the fragility this removes.)
_MEDIA_DIR = settings.media_dir


@contextmanager
def _phase(timings: dict[str, float] | None, name: str):
    """Record the wall time of one sync phase into *timings* under *name*.

    A no-op when *timings* is None, so the phase list reads identically on the
    untimed path and no call site has to branch.

    Exists because ``reconcile`` — the orchestrator leg that wraps ALL of this —
    was 88-95% of a 46-101s peer-sync while every phase inside it was invisible
    (tunatale-byw). The seven trivial legs around it were each timed to 0.01s.
    ``finally`` so a phase that raises still reports the time it burned first:
    an exception is exactly when you most want to know which phase was running.
    """
    if timings is None:
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        timings[name] = time.perf_counter() - t0


def _write_reconcile_timing_log(path: Path, timings: dict[str, float], *, dry_run: bool) -> None:
    """Append one greppable ``RECONCILE_TIMING`` line per reconcile.

    The inside-the-reconcile counterpart to the orchestrator's
    ``PEER_SYNC_TIMING``, and deliberately the same shape (``name=secs``), so one
    grep of ``~/.tunatale/logs/sync.log`` attributes a slow sync to a phase
    instead of to "the reconcile". Phases are emitted in execution order —
    dicts preserve insertion order and every timer writes on exit — so the line
    also reads as the sequence that ran.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().isoformat(timespec="seconds")
    # 3dp, not the 2dp of PEER_SYNC_TIMING: this line carries ~25 legs, and at
    # 2dp each one rounds away up to 5ms, so the breakdown can silently fail to
    # add up to its own total. Sub-second phases are also the ones you squint at
    # when hunting which of thirteen deck-config refreshes is not free.
    legs = " ".join(f"{name}={secs:.3f}" for name, secs in timings.items())
    with open(path, "a") as f:
        f.write(f"{ts} RECONCILE_TIMING dry_run={dry_run} {legs}\n")


def _copy_tt_media_to_anki(writer: OfflineWriter, filename: str) -> None:
    """Copy a media file from TT's media dir into Anki's collection.media via the writer.

    Silently skips if the file doesn't exist on disk (logs a warning).
    """
    src = _MEDIA_DIR / filename
    if not src.exists():
        _log.warning("Media file not found, skipping copy to Anki: %s", src)
        return
    writer.store_media_file(filename, src.read_bytes())


def _iter_direction_invariant_violations(conn) -> Iterator[str]:
    """Yield a message per post-sync direction row that breaks a column invariant.

    Reuses the single-source validator in ``app/srs/direction_fields.py`` (rather
    than re-encoding the rules in SQL here), reading only the columns it needs. The
    v35 CHECK constraints already reject out-of-domain writes, so in practice this
    surfaces the *coupling* invariant the CHECK can't express (a ``bury_kind`` set on
    a non-buried row — the 2026-05-16 incident class).
    """
    from app.models.srs_item import Direction, DirectionState, SRSState
    from app.srs.direction_fields import iter_direction_invariant_violations

    dummy_due = datetime.now()
    rows = conn.execute(
        "SELECT collocation_id, direction, state, prior_state, bury_kind FROM collocation_directions"
    ).fetchall()
    for cid, direction, state, prior_state, bury_kind in rows:
        st = DirectionState(
            direction=Direction(direction),
            due_at=dummy_due,
            state=SRSState(state),
            prior_state=SRSState(prior_state) if prior_state is not None else None,
            bury_kind=bury_kind,
        )
        for msg in iter_direction_invariant_violations(st):
            yield f"cid={cid} dir={direction} {msg}"


def _write_sync_soak_log(
    path: Path,
    *,
    pull: PullReport,
    push,
    db=None,
    promotion: PromotionReport | None = None,
) -> None:
    """Append a durable, greppable soak line for each non-dry CLI sync.

    The CLI only print()s its summary to stdout, so the recompute-divergence
    health signal would be lost when the terminal scrolled. This persists one
    ``SYNC_SOAK`` heartbeat per sync (even at count 0, so there's positive
    "ran clean" confirmation) plus one ``RECOMPUTE_DIVERGENCE`` detail line per
    divergence. When the TT ``db`` (SRSDatabase) is supplied, also emits one
    ``INVARIANT_TRACE`` line per direction row that breaks a column-level
    invariant (rules 7/8/10). Grep ``~/.tunatale/logs/sync.log`` for any of the
    three.

    When *promotion* is supplied, also emits one ``PRODUCTION_MINT`` line with
    the phase's branch counters. Same reason as everything else here: the phase
    logs that summary through ``logging``, and ``start-dev.sh`` runs uvicorn at
    ``--log-level warning`` AND redirects it nowhere — so the counters reached
    the dev server's terminal at best, and nothing greppable survived a sync.
    ``no_template=200``, ``clozed=200`` and ``minted=10`` are three different
    diagnoses of the same promote wall time, and telling them apart after the
    fact is the whole point (tunatale-byw, 2026-08-19).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().isoformat(timespec="seconds")
    lines = [
        f"{ts} SYNC_SOAK pull_notes={pull.notes_updated} "
        f"pull_dirs={pull.directions_updated} conflicts={len(pull.conflicts)} "
        f"recompute_divergences={len(pull.recompute_divergences)} "
        f"push_notes={push.notes_pushed} push_dirs={push.directions_pushed}"
    ]
    if promotion is not None:
        lines.append(
            f"{ts} PRODUCTION_MINT awaiting={promotion.awaiting} minted={promotion.minted} "
            f"adopted={promotion.adopted} clozed={promotion.clozed} "
            f"unservable={promotion.unservable} no_template={promotion.no_template}"
        )
    for d in pull.recompute_divergences:
        lines.append(
            f"{ts}   RECOMPUTE_DIVERGENCE cid={d.collocation_id} dir={d.direction} "
            f"replay_s={d.replay_stability:.4f} anki_s={d.anki_stability:.4f} "
            f"replay_d={d.replay_difficulty:.4f} anki_d={d.anki_difficulty:.4f}"
        )
    if db is not None:
        with db._get_conn() as tt_conn:
            for msg in _iter_direction_invariant_violations(tt_conn):
                lines.append(f"{ts}   INVARIANT_TRACE {msg}")
    with open(path, "a") as f:
        f.write("\n".join(lines) + "\n")


async def run_full_sync(
    sync: AnkiSync,
    conn,
    db,
    *,
    deck_name: str,
    model_name: str,
    sync_log_path: Path,
    media_fn=None,
    media_dir: Path | None = None,
    dry_run: bool = False,
    force_fsrs: bool = False,
    timings: dict[str, float] | None = None,
) -> tuple[CreateNewReport, PushReport, PullReport, dict[str, int], PromotionReport]:
    """The single canonical TT↔Anki sync sequence.

    The one sync path funnels through ``main`` into this function: the peer-sync
    reconcile (``trigger_peer_sync`` → ``peer_sync`` → ``main``, which threads the
    LLM/image ``media_fn`` and the active language via ``_tt_settings`` through).
    ``main`` is the internal reconcile driver — not a standalone command (the
    ``python -m app.plugins.anki_sync.sync`` CLI was removed 2026-06-30). The ONLY legitimate
    per-caller difference is the media generator. Everything else —
    orphan recovery, note creation, push, pull, every deck-config refresh, the
    Anki→TT media propagation, the soak heartbeat — lives here so neither path
    can silently drop a phase.

    Do **not** inline a sync phase into one caller. A second entry point that
    runs a different subset of phases is the b0a4b8a regression: the peer-sync
    button dropped ``sync_create_new`` (TT-added cards never reached Anki) AND
    every ``refresh_*`` (Anki-side FSRS-param / retention / daily-cap changes
    never reached TT). New phases go here, not at a call site.

    ``timings``, when supplied, is populated with one wall-time entry per phase
    plus a ``run_full_sync`` total. It is diagnostic only — nothing branches on
    it — and exists because this whole function was a single opaque 90-second
    timer in the orchestrator's report (tunatale-byw).

    ``detect_and_reset_orphans`` runs unconditionally (it only resets stale TT
    pointers so create/push can rebuild). create/push/pull honor ``dry_run``;
    the refresh block, media propagation, and soak log run only on a real
    (non-dry) sync. ``media_dir`` activates the Anki→TT media-refresh phase
    (peer-sync path; CLI passes ``None``).
    """
    _t_start = time.perf_counter()
    # Self-healing: reset TT rows pointing at Anki cards/notes that no longer
    # exist, so sync_create_new recreates them and sync_push force_fsrs the
    # rebuild. Must run BEFORE create_new and push to land in this same sync.
    # Tripwire: Anki notes that collapse to one TT guid (same text + POS) leave a
    # collocation with two candidate cards, free to alternate between them. Reports
    # only, never blocks, and runs on dry-runs too — it is a read of the collection.
    with _phase(timings, "guid_collisions"):
        sync.warn_if_guid_collisions()
    # Second read-only tripwire: a deck whose notes sit on single-template
    # notetypes can never carry production cards, and every consumer of that
    # capability degrades silently (the 2990-word Norwegian gap). Runs on
    # dry-runs for the same reason as the line above — it writes nothing.
    with _phase(timings, "recognition_only"):
        sync.warn_if_recognition_only_deck()

    with _phase(timings, "orphans"):
        sync.detect_and_reset_orphans()

    with _phase(timings, "create_new"):
        create_report = await sync.sync_create_new(
            deck_name=deck_name,
            model_name=model_name,
            dry_run=dry_run,
            _media_fn=media_fn,
        )
    with _phase(timings, "push"):
        push_report = sync.sync_push(dry_run=dry_run, force_fsrs=force_fsrs)
    with _phase(timings, "pull"):
        pull_report = sync.sync_pull(dry_run=dry_run)

    # Just-in-time production mint: a paced batch of words whose recognition card
    # has graduated get their production counterpart (tunatale-qf6.2). AFTER the
    # pull on purpose — that is what brings in graduations made in Anki since the
    # last sync, so the trigger fires on the freshest state rather than on a
    # sync-old snapshot. The cards it adds are NEW, which `get_review_queue`
    # tail-appends to the frozen queue, so they still surface today.
    with _phase(timings, "promote"):
        promotion_report = await sync.promote_production_cards(dry_run=dry_run, _media_fn=media_fn)

    # Default media report (returned on dry-run / no media_dir).
    media_report: dict[str, int] = {
        "new_media": 0,
        "updated_media": 0,
        "unchanged_media": 0,
        "collapsed_media": 0,
        "image_fetch_failed": 0,
    }

    if not dry_run:
        from app.srs.queue_stats import (
            refresh_col_crt,
            refresh_daily_new_cap,
            refresh_daily_review_cap,
            refresh_desired_retention,
            refresh_easy_days,
            refresh_fsrs_params,
            refresh_fsrs_short_term_flag,
            refresh_learning_steps,
            refresh_load_balancer_enabled,
            refresh_maximum_review_interval,
            refresh_new_cards_ignore_review_limit,
            refresh_review_settings,
            warn_if_multi_deck_preset,
        )

        # Pull Anki-side deck-config changes into the TT cache. Each is a no-op
        # when the relevant config is absent, so it's safe on a minimal/peer
        # collection. Mirrors the per-day caps, retention, FSRS params, learning
        # steps and load-balancer toggle the queue-parity machinery depends on.
        with _phase(timings, "refresh_col_crt"):
            refresh_col_crt(db, conn)
        with _phase(timings, "refresh_daily_new_cap"):
            refresh_daily_new_cap(db, conn, deck_name)
        with _phase(timings, "refresh_daily_review_cap"):
            refresh_daily_review_cap(db, conn, deck_name)
        with _phase(timings, "refresh_desired_retention"):
            refresh_desired_retention(db, conn, deck_name)
        with _phase(timings, "refresh_fsrs_params"):
            refresh_fsrs_params(db, conn, deck_name)
        with _phase(timings, "refresh_fsrs_short_term_flag"):
            refresh_fsrs_short_term_flag(db, conn)
        with _phase(timings, "refresh_maximum_review_interval"):
            refresh_maximum_review_interval(db, conn, deck_name)
        with _phase(timings, "refresh_review_settings"):
            refresh_review_settings(db, conn, deck_name)
        with _phase(timings, "refresh_learning_steps"):
            refresh_learning_steps(db, conn, deck_name)
        with _phase(timings, "refresh_load_balancer_enabled"):
            refresh_load_balancer_enabled(db, conn)
        with _phase(timings, "refresh_new_cards_ignore_review_limit"):
            refresh_new_cards_ignore_review_limit(db, conn)
        with _phase(timings, "refresh_easy_days"):
            refresh_easy_days(db, conn, deck_name)
        with _phase(timings, "warn_if_multi_deck_preset"):
            warn_if_multi_deck_preset(conn, deck_name)

        # Anki→TT media propagation: pull the (media-synced) note fields from
        # tt_collection into TT's own media table + backend/media, so an image
        # swapped in Anki shows up in TunaTale. Peer path only (media_dir set);
        # source = where the pulled media lives, dest = _MEDIA_DIR (frontend).
        if media_dir is not None:
            from app.plugins.anki_sync.import_seed import refresh_media_from_conn

            with _phase(timings, "media_refresh"):
                media_report = refresh_media_from_conn(
                    conn,
                    deck_name=deck_name,
                    anki_media_path=media_dir,
                    media_dir=_MEDIA_DIR,
                    db=db,
                )

        # Merge AFTER the media refresh: on the media_dir path the line above
        # reassigns media_report to refresh_media_from_conn's dict, which has no
        # image key. Setting it here survives both paths.
        media_report["image_fetch_failed"] = getattr(create_report, "image_failed", 0)

        with _phase(timings, "soak_log"):
            _write_sync_soak_log(
                sync_log_path,
                pull=pull_report,
                push=push_report,
                db=db,
                promotion=promotion_report,
            )

    if timings is not None:
        timings["run_full_sync"] = time.perf_counter() - _t_start
    return create_report, push_report, pull_report, media_report, promotion_report


def _resolve_model_name(_s, code: str, conn, deck_name: str) -> str:
    """Notetype to mint TT-originated cards into for *code*.

    Precedence: explicit ``anki_model_name`` override > the language's TT vocab
    notetype (e.g. "Norwegian Vocabulary", NOT the imported deck's recognition-only
    notetype discovery would return) > deck-discovered model (the Slovene case,
    where deck notetype == mint notetype).
    """
    from app.languages import get_vocab_notetype
    from app.plugins.anki_sync import model_discovery

    vocab = get_vocab_notetype(code)
    return (
        _s.anki_model_name
        or (vocab.name if vocab is not None else "")
        or model_discovery.get_or_discover_model_name_offline(conn, deck_name)
    )


def main(
    argv: list[str] | None = None,
    *,
    _settings=None,
    _safe_open_fn=None,
    _sync_log_path: Path | None = None,
    _db=None,
    _media_dir: Path | None = None,
    _media_fn=None,
) -> int:
    import argparse
    import sys

    from app.config import settings as _default_settings
    from app.plugins.anki_sync.safety import safe_open

    _s = _settings if _settings is not None else _default_settings
    _so = _safe_open_fn if _safe_open_fn is not None else safe_open
    # Default to settings.sync_log (not a hardcoded path) so the conftest
    # isolation fixture's monkeypatch reaches it — otherwise peer-sync tests,
    # which route through tt_sync_main without an explicit _sync_log_path, leak
    # SYNC_SOAK heartbeats into the user's real ~/.tunatale/logs/sync.log.
    # Production is unchanged: settings.sync_log defaults to that same path.
    _sync_log = _sync_log_path if _sync_log_path is not None else _s.sync_log

    # Get database instance
    db = _db if _db is not None else SRSDatabase(_s.database_url.removeprefix("sqlite:///"))

    parser = argparse.ArgumentParser(description="TunaTale ↔ Anki bidirectional sync")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    deck_name = _s.anki_deck_name

    # Phase wall times for the whole reconcile, written as one RECONCILE_TIMING
    # line below. `safe_open` is timed from HERE rather than inside run_full_sync
    # because it runs a lock probe, a full-file SHA256, a whole-collection SQLite
    # backup, a backup validation and a prune before run_full_sync is entered —
    # a fixed per-sync cost that scales with collection SIZE, not with the number
    # of dirty rows. A timer set that started at run_full_sync's first phase could
    # report every phase at ~0 and still leave the wall time unexplained.
    timings: dict[str, float] = {}
    _t_total = time.perf_counter()

    try:
        with _so(_s.anki_collection_path, mode="rw") as ctx:
            timings["open_collection"] = time.perf_counter() - _t_total
            col_row = ctx.conn.execute("SELECT ver, crt FROM col").fetchone()
            col_ver = col_row[0]
            col_crt = col_row[1]
            # The single canonical sync sequence (orphans → create → push → pull →
            # refresh-all → soak) against the collection (see run_full_sync /
            # .claude/rules/anki-sync.md). peer_sync drives this with a per-language
            # _settings (db_url + deck + target_language resolved by _tt_settings);
            # the language threading lives there, not in a loop here.
            import asyncio

            # No getattr fallback: a settings object without target_language used to
            # sync silently as Slovene, which for a Norwegian deck means the wrong
            # vocab notetype AND the wrong L2 markup class. Settings always defines
            # it; the fallback existed only for test doubles that omitted it.
            language_code = _s.target_language
            reader = OfflineReader(ctx.conn, deck_name, language_code=language_code)
            writer = OfflineWriter(ctx.conn, media_dir=_media_dir)
            sync = AnkiSync(
                db=db,
                _reader=reader,
                _writer=writer,
                _anki_col_ver=col_ver,
                _anki_col_crt=col_crt,
            )
            with _phase(timings, "resolve_model"):
                model_name = _resolve_model_name(_s, language_code, ctx.conn, deck_name)
            create, push, pull, media, promotion = asyncio.run(
                run_full_sync(
                    sync,
                    ctx.conn,
                    db,
                    deck_name=deck_name,
                    model_name=model_name,
                    sync_log_path=_sync_log,
                    media_fn=_media_fn,
                    media_dir=_media_dir,
                    dry_run=args.dry_run,
                    timings=timings,
                )
            )
            _print_sync_report(create, push, pull, media, promotion, dry_run=args.dry_run, media_dir=_media_dir)
            timings["total"] = time.perf_counter() - _t_total
            if not args.dry_run:
                # Gated like the soak heartbeat: a dry run is a diagnostic, and
                # leaving the log untouched is a contract the dry-run tests pin.
                _write_reconcile_timing_log(_sync_log, timings, dry_run=args.dry_run)
            return 0
    except OrphanThresholdExceededError as e:
        # run_full_sync runs detect_and_reset_orphans on this path; its threshold
        # guard raises a plain Exception (not RuntimeError). Return non-zero so the
        # caller (peer_sync) aborts cleanly with a PeerSyncError instead of letting
        # an uncaught exception surface as a 500.
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"Error opening collection: {e}", file=sys.stderr)
        return 1


def _print_sync_report(create, push, pull, media, promotion, *, dry_run: bool, media_dir) -> None:
    """Print the sync summary.

    The promotion line carries ``unservable`` on purpose. Those are the words
    that can be neither pictured nor clozed from their own example sentences —
    the population the LLM sentence tier would have served, which was decided
    against (``tunatale-qf6.10``, 2026-08-17: 29 words of 3018, against putting
    an LLM dependency on the sync path). A decision not to serve them is only
    defensible if the count is visible; left as a log WARNING it is
    indistinguishable from the feature silently not working.
    """
    print(f"Create: {create.created} created, {create.linked} linked, {create.notes_created_from_anki} from Anki")
    print(
        f"Pull: {pull.notes_updated} notes updated, "
        f"{pull.directions_updated} directions, "
        f"{len(pull.conflicts)} conflicts, "
        f"{len(pull.recompute_divergences)} recompute divergences"
    )
    if not dry_run and media_dir is not None:
        print(
            f"Media: {media['new_media']} new, {media['updated_media']} updated, {media['collapsed_media']} collapsed"
        )
    print(f"Push: {push.notes_pushed} notes, {push.directions_pushed} directions")
    print(
        f"Promotion: {promotion.minted} minted, {promotion.clozed} clozed, "
        f"{promotion.unservable} unservable, {promotion.awaiting} awaiting"
    )
