"""Bidirectional sync between TunaTale and Anki.

S3.4: sync_pull (Anki → TunaTale).
S3.5: sync_push (TunaTale → Anki).
S3.6: --force-fsrs gate + setSpecificValueOfCard.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from app.anki.media.vocab_media import safe_stem as _safe_stem  # noqa: F401 — re-export (archive scripts, cloze_tts)
from app.anki.media.vocab_media import store_tt_media as _store_tt_media  # noqa: F401 — re-export (archive scripts)

# Phase 9 split: leaf helpers live in sync_common / sync_reader; imported here both
# for use by the remaining sync code AND as the stable re-export surface (tests +
# archive scripts import these names from app.anki.sync). The redundant `X as X`
# form marks them as explicit re-exports so ruff's F401 autofix never strips one
# whose last in-module use moves out in a later split commit.
from app.anki.sync_common import (
    _FSRS_REPLAY_TOLERANCE as _FSRS_REPLAY_TOLERANCE,
)
from app.anki.sync_common import (
    KNOWN_ANKI_SCHEMA_VER as KNOWN_ANKI_SCHEMA_VER,
)
from app.anki.sync_common import (
    CardRecord as CardRecord,
)
from app.anki.sync_common import (
    CreateNewReport as CreateNewReport,
)
from app.anki.sync_common import (
    DuplicateNoteError as DuplicateNoteError,
)
from app.anki.sync_common import (
    NoteRecord as NoteRecord,
)
from app.anki.sync_common import (
    OrphanThresholdExceededError as OrphanThresholdExceededError,
)
from app.anki.sync_common import (
    PullReport as PullReport,
)
from app.anki.sync_common import (
    PushReport as PushReport,
)
from app.anki.sync_common import (
    RecomputeDivergence as RecomputeDivergence,
)
from app.anki.sync_common import (
    SyncConflict as SyncConflict,
)
from app.anki.sync_common import (
    _local_today_4am as _local_today_4am,
)
from app.anki.sync_common import (
    _ms_to_datetime as _ms_to_datetime,
)
from app.anki.sync_common import (
    build_cloze_back_extra as build_cloze_back_extra,
)
from app.anki.sync_common import (
    extract_cloze_note as extract_cloze_note,
)
from app.anki.sync_common import (
    extract_cloze_sentence_translation as extract_cloze_sentence_translation,
)
from app.anki.sync_common import (
    extract_cloze_translation as extract_cloze_translation,
)
from app.anki.sync_engine import (
    AnkiSync as AnkiSync,
)
from app.anki.sync_engine import (
    _derive_revlog_shape as _derive_revlog_shape,
)
from app.anki.sync_engine import (
    _direction_differs as _direction_differs,
)
from app.anki.sync_engine import (
    _resolve_introduced_at as _resolve_introduced_at,
)
from app.anki.sync_engine import (
    _step_minutes_from_left as _step_minutes_from_left,
)
from app.anki.sync_reader import OfflineReader as OfflineReader
from app.anki.sync_writer import OfflineWriter as OfflineWriter
from app.srs.database import SRSDatabase

_log = logging.getLogger(__name__)


_MEDIA_DIR = Path(__file__).parent.parent.parent / "media"


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
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().isoformat(timespec="seconds")
    lines = [
        f"{ts} SYNC_SOAK pull_notes={pull.notes_updated} "
        f"pull_dirs={pull.directions_updated} conflicts={len(pull.conflicts)} "
        f"recompute_divergences={len(pull.recompute_divergences)} "
        f"push_notes={push.notes_pushed} push_dirs={push.directions_pushed}"
    ]
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
) -> tuple[CreateNewReport, PushReport, PullReport, dict[str, int]]:
    """The single canonical TT↔Anki sync sequence.

    The one sync path funnels through ``main`` into this function: the peer-sync
    reconcile (``trigger_peer_sync`` → ``peer_sync`` → ``main``, which threads the
    LLM/image ``media_fn`` and the active language via ``_tt_settings`` through).
    ``main`` is the internal reconcile driver — not a standalone command (the
    ``python -m app.anki.sync`` CLI was removed 2026-06-30). The ONLY legitimate
    per-caller difference is the media generator. Everything else —
    orphan recovery, note creation, push, pull, every deck-config refresh, the
    Anki→TT media propagation, the soak heartbeat — lives here so neither path
    can silently drop a phase.

    Do **not** inline a sync phase into one caller. A second entry point that
    runs a different subset of phases is the b0a4b8a regression: the peer-sync
    button dropped ``sync_create_new`` (TT-added cards never reached Anki) AND
    every ``refresh_*`` (Anki-side FSRS-param / retention / daily-cap changes
    never reached TT). New phases go here, not at a call site.

    ``detect_and_reset_orphans`` runs unconditionally (it only resets stale TT
    pointers so create/push can rebuild). create/push/pull honor ``dry_run``;
    the refresh block, media propagation, and soak log run only on a real
    (non-dry) sync. ``media_dir`` activates the Anki→TT media-refresh phase
    (peer-sync path; CLI passes ``None``).
    """
    # Self-healing: reset TT rows pointing at Anki cards/notes that no longer
    # exist, so sync_create_new recreates them and sync_push force_fsrs the
    # rebuild. Must run BEFORE create_new and push to land in this same sync.
    sync.detect_and_reset_orphans()

    create_report = await sync.sync_create_new(
        deck_name=deck_name,
        model_name=model_name,
        dry_run=dry_run,
        _media_fn=media_fn,
    )
    push_report = sync.sync_push(dry_run=dry_run, force_fsrs=force_fsrs)
    pull_report = sync.sync_pull(dry_run=dry_run)

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
        refresh_col_crt(db, conn)
        refresh_daily_new_cap(db, conn, deck_name)
        refresh_daily_review_cap(db, conn, deck_name)
        refresh_desired_retention(db, conn, deck_name)
        refresh_fsrs_params(db, conn, deck_name)
        refresh_fsrs_short_term_flag(db, conn)
        refresh_maximum_review_interval(db, conn, deck_name)
        refresh_review_settings(db, conn, deck_name)
        refresh_learning_steps(db, conn, deck_name)
        refresh_load_balancer_enabled(db, conn)
        refresh_new_cards_ignore_review_limit(db, conn)
        refresh_easy_days(db, conn, deck_name)
        warn_if_multi_deck_preset(conn, deck_name)

        # Anki→TT media propagation: pull the (media-synced) note fields from
        # tt_collection into TT's own media table + backend/media, so an image
        # swapped in Anki shows up in TunaTale. Peer path only (media_dir set);
        # source = where the pulled media lives, dest = _MEDIA_DIR (frontend).
        if media_dir is not None:
            from app.anki.import_seed import refresh_media_from_conn

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

        _write_sync_soak_log(
            sync_log_path,
            pull=pull_report,
            push=push_report,
            db=db,
        )

    return create_report, push_report, pull_report, media_report


def _resolve_model_name(_s, code: str, conn, deck_name: str) -> str:
    """Notetype to mint TT-originated cards into for *code*.

    Precedence: explicit ``anki_model_name`` override > the language's TT vocab
    notetype (e.g. "Norwegian Vocabulary", NOT the imported deck's recognition-only
    notetype discovery would return) > deck-discovered model (the Slovene case,
    where deck notetype == mint notetype).
    """
    from app.anki import model_discovery
    from app.languages import get_vocab_notetype

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

    from app.anki.safety import safe_open
    from app.config import settings as _default_settings

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

    try:
        with _so(_s.anki_collection_path, mode="rw") as ctx:
            col_row = ctx.conn.execute("SELECT ver, crt FROM col").fetchone()
            col_ver = col_row[0]
            col_crt = col_row[1]
            # The single canonical sync sequence (orphans → create → push → pull →
            # refresh-all → soak) against the collection (see run_full_sync /
            # .claude/rules/anki-sync.md). peer_sync drives this with a per-language
            # _settings (db_url + deck + target_language resolved by _tt_settings);
            # the language threading lives there, not in a loop here.
            import asyncio

            reader = OfflineReader(ctx.conn, deck_name)
            writer = OfflineWriter(ctx.conn, media_dir=_media_dir)
            sync = AnkiSync(
                db=db,
                _reader=reader,
                _writer=writer,
                _anki_col_ver=col_ver,
                _anki_col_crt=col_crt,
            )
            model_name = _resolve_model_name(_s, getattr(_s, "target_language", "sl"), ctx.conn, deck_name)
            create, push, pull, media = asyncio.run(
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
                )
            )
            _print_sync_report(create, push, pull, media, dry_run=args.dry_run, media_dir=_media_dir)
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


def _print_sync_report(create, push, pull, media, *, dry_run: bool, media_dir) -> None:
    """Print the sync summary."""
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
