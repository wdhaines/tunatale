"""Anki peer-sync orchestrator (anki-free core module).

Drives the sync bracket:

  1. ``sync_collection`` via driver (pull server → tt_collection)
  2. ``sync.main()`` — TT's existing push/pull against tt_collection
  3. ``sync_collection`` via driver (push tt_collection → server)

Uses ``app.anki.sync_driver`` via subprocess for the anki-side operations.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.config import settings

# The driver imports `anki` (+ protobuf), which does NOT import on Python 3.14.
# It is also self-contained (stdlib + anki only, no `app.*` imports), so we run it
# isolated + project-free under a separate interpreter (settings.anki_subprocess_python)
# and invoke it by file path rather than `-m app.anki.sync_driver`.
_DRIVER_PATH = str(Path(__file__).with_name("sync_driver.py"))

# backend/ — the directory the server runs from (start-dev.sh `cd backend`) and the
# anchor for the default CWD-relative `sqlite:///./tunatale.db`. From app/anki/.
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


logger = logging.getLogger(__name__)


def _tt_media_dir() -> Path:
    """tt_collection's own media folder (anki derives ``<stem>.media``)."""
    return settings.tt_collection_path.with_suffix(".media")


def _resolve_media_dir() -> Path:
    """Where TT writes generated media on the peer path.

    Use the real Anki ``collection.media`` when it exists — one physical library,
    no duplicate (the user's requirement). Fall back to tt_collection's own media
    folder when Anki isn't installed, where it's the sole copy.
    """
    real = settings.anki_media_path
    return real if real.exists() else _tt_media_dir()


def _ensure_tt_media_linked() -> None:
    """Point tt_collection's media dir at the real Anki library via symlink.

    The driver's media sync operates on tt_collection's OWN media dir
    (``<stem>.media``). For our sync to *push* media that lives in the real
    ``collection.media``, that dir must be the same physical folder — hence the
    symlink. No-op when Anki isn't installed (tt keeps its own dir as the sole
    copy) or when the link already exists. Never clobbers a non-empty real dir,
    so we don't destroy media a prior TT-only setup accumulated there.
    """
    real = settings.anki_media_path
    if not real.exists():
        return
    tt_media = _tt_media_dir()
    if tt_media.is_symlink():
        return  # idempotent — already linked
    if tt_media.exists() and any(tt_media.iterdir()):
        logger.warning(
            "tt_collection.media (%s) is a non-empty real dir; leaving it as-is rather than "
            "replacing with a symlink to %s. Media push will use the local dir.",
            tt_media,
            real,
        )
        return
    if tt_media.exists():
        tt_media.rmdir()  # empty dir → safe to replace with the link
    tt_media.symlink_to(real, target_is_directory=True)


@dataclass
class PeerSyncReport:
    auth_success: bool = False
    pull_required: int | None = None
    pull_message: str = ""
    tt_push_pull_exit: int | None = None
    push_required: int | None = None
    push_message: str = ""
    dry_run: bool = False
    # Per-leg wall times (seconds), keyed by leg name. Populated by peer_sync and
    # logged as a PEER_SYNC_TIMING line so an occasional slow sync can be diagnosed
    # from sync.log after the fact rather than reproduced live.
    timings: dict[str, float] = field(default_factory=dict)


class PeerSyncError(Exception):
    """Raised when a peer-sync step fails."""


def _full_sync_required(required: int | None) -> bool:
    """True if the server demands a full sync/upload/download.

    Mirrors proto ``SyncCollectionResponse.ChangesRequired``: ``{0 NO_CHANGES,
    1 NORMAL_SYNC}`` mean the incremental sync completed; ``{2 FULL_SYNC,
    3 FULL_DOWNLOAD, 4 FULL_UPLOAD}`` all mean incremental did NOT happen and a
    full operation is required — none of which we perform implicitly.
    """
    return required in (2, 3, 4)


def _full_sync_error(leg: str, required: int | None, server_message: str) -> PeerSyncError:
    """Build an actionable FULL_SYNC abort error for the *leg* ("pull"/"push").

    The bare "Run bootstrap first" wording was opaque in the UI popover (it named
    no command and no cause). This spells out why it happened and the exact fix.
    """
    msg = (server_message or "").strip()
    return PeerSyncError(
        f"AnkiWeb requires a one-way FULL_SYNC (required={required}) on the {leg} leg, so "
        "TunaTale aborted rather than risk clobbering data. This happens when Anki desktop "
        "does a full one-way sync with AnkiWeb — e.g. choosing 'Upload to AnkiWeb' after a "
        "schema / notetype / deck-preset change — which leaves TunaTale's sync mirror behind "
        "the server. Fix by re-downloading the mirror, then sync again:\n"
        "    cd backend && uv run python -m app.anki.sync_orchestrator --bootstrap\n"
        "This is download-only: it does NOT modify your Anki desktop collection or AnkiWeb."
        + (f"\nServer message: {msg}" if msg else "")
    )


def _absolute_sqlite_url(url: str) -> str | None:
    """Anchor a CWD-relative on-disk sqlite URL to the backend dir.

    Returns the absolute URL, or ``None`` (caller keeps the original) when the URL
    is non-sqlite, in-memory (``:memory:``), or already absolute — only a relative
    on-disk path is CWD-sensitive and needs anchoring.
    """
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    path = url[len(prefix) :]
    if path.startswith(":") or os.path.isabs(path):
        return None
    return f"{prefix}{(_BACKEND_DIR / path).resolve()}"


def _tt_settings():
    """Clone settings: anki_collection_path → tt_collection, db path made absolute.

    peer_sync runs inside the long-running server and re-invokes ``tt_sync_main``,
    which builds its ``SRSDatabase`` from ``settings.database_url``. The default
    ``sqlite:///./tunatale.db`` is CWD-relative: invoked from any CWD other than
    backend/ (e.g. the orchestrator CLI from the repo root) it resolves to a
    *different*, empty db — so the real db never gets pull-merged and the soak mode
    mislabels as ``legacy``. Anchor it so the canonical db is opened regardless of CWD.
    """
    update = {"anki_collection_path": settings.tt_collection_path}
    abs_url = _absolute_sqlite_url(settings.database_url)
    if abs_url is not None:
        update["database_url"] = abs_url
    return settings.model_copy(update=update)


def _anki_with_spec() -> str:
    """`--with` spec for the driver subprocess. Empty version → latest anki (a bare
    ``anki==`` would be a malformed specifier)."""
    version = settings.anki_pkg_version
    return f"anki=={version}" if version else "anki"


def _driver_cmd() -> list[str]:
    """Command to run the anki driver subprocess: isolated, project-free, on an
    anki-compatible interpreter (anki's protobuf can't import on 3.14)."""
    return [
        "uv",
        "run",
        "--isolated",
        "--no-project",
        "--python",
        settings.anki_subprocess_python,
        "--with",
        _anki_with_spec(),
        "python",
        _DRIVER_PATH,
    ]


def _run_driver(command: dict, timeout: int = 120) -> dict:
    """Run sync_driver subprocess with *command* JSON, return parsed result."""
    proc = subprocess.run(
        _driver_cmd(),
        input=json.dumps(command),
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    try:
        result = json.loads(proc.stdout)
    except (json.JSONDecodeError, OSError) as e:
        raise PeerSyncError(f"Driver output not valid JSON: {e}\nstderr: {proc.stderr}") from None

    if "error" in result:
        raise PeerSyncError(f"Driver error: {result['error']}")
    return result


def _keychain_password(service: str, account: str) -> str | None:
    """Fetch a generic password from the macOS Keychain via the `security` CLI.

    Returns None if absent or if `security`/Keychain is unavailable. The password is
    never logged or echoed.
    """
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError, subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _resolve_sync_password() -> str:
    """The AnkiWeb password: prefer ``settings.sync_password`` (env/.env override), else
    the macOS Keychain (service=``sync_keychain_service``, account=``sync_username``).
    Keeps the secret out of plaintext .env for recurring use."""
    if settings.sync_password:
        return settings.sync_password
    pw = _keychain_password(settings.sync_keychain_service, settings.sync_username)
    if not pw:
        raise PeerSyncError(
            "No AnkiWeb password found. Store it in the macOS Keychain:\n"
            f"  security add-generic-password -s {settings.sync_keychain_service} "
            f"-a {settings.sync_username or '<your-ankiweb-username>'} -w\n"
            "(or set sync_password in backend/.env as a less-secure fallback)."
        )
    return pw


def _read_real_curdeck(real_collection_path: Path) -> bytes | None:
    """Read the live ``curDeck`` blob from the user's *real* Anki collection.

    ``curDeck`` (the currently-selected deck) is a natively-synced Anki *config*
    value, and Anki uploads the **entire** config blob unconditionally on every
    sync (``rslib`` ``changed_config`` → ``get_all_config``; no per-key usn/mtime
    gating). So TT cannot avoid pushing ``curDeck`` — it can only push the *right*
    value. We mirror the user's real selection so TT stays faithful instead of
    re-asserting whatever stale deck its own collection happens to hold.

    Read-only via ``mode=ro`` (NOT ``immutable=1``, NOT a file copy): Anki keeps the
    collection in WAL mode while open, and only a real read-only connection to the
    original file sees committed WAL data — ``immutable=1`` or copying the bare
    ``.anki2`` reads a stale checkpoint. ``safe_open`` is unusable here: its Gate-1
    lock probe aborts when Anki is running, but peer-sync's whole premise is that
    Anki stays open, and this is a single-row read, not a mutation.

    Returns ``None`` (caller leaves TT's ``curDeck`` untouched) if the collection
    or row is absent or unreadable — a deck-mirroring hiccup must never break sync.
    """
    if not real_collection_path.exists():
        return None
    try:
        # timeout=0.3: this is a best-effort single-row read — when Anki holds a
        # hard lock (observed 2026-06-11: an open dialog kept the collection
        # exclusively locked for hours), sqlite's default 5s busy timeout turned
        # every sync's mirror step into a constant +5.2s stall.
        con = sqlite3.connect(f"file:{real_collection_path}?mode=ro", uri=True, timeout=0.3)
        try:
            row = con.execute("SELECT val FROM config WHERE key = 'curDeck'").fetchone()
        finally:
            con.close()
    except sqlite3.Error as exc:
        # Not silent: a skipped mirror means the push may re-assert TT's stale
        # curDeck — the 188a08b regression class. Surface it so a wedged-lock
        # state is visible in the server log instead of only as slow syncs.
        logger.warning("curDeck mirror skipped: real collection unreadable (%s)", exc)
        return None
    return row[0] if row else None


_PUSHABLE_TABLES = ("cards", "notes", "revlog", "graves")


def _has_pending_push(collection_path: Path) -> bool:
    """True if tt_collection has anything to upload — any ``usn = -1`` row.

    The reconcile's ``sync_push`` stamps ``usn = -1`` on every row it writes toward
    Anki (cards/notes/revlog/graves). When there are none, the push leg's
    ``sync_collection`` is a pure no-op handshake — yet still a 2–4s AnkiWeb
    round-trip — so we skip it. (``col.mod`` is the wrong signal: the reconcile's
    *pull* direction bumps it too, for changes that never need pushing back.)
    Plain connection — tt_collection is TT-owned, never held by the Anki GUI.
    Unreadable / missing → ``True`` (push, the safe default; a stale skip would
    only defer the upload one sync, but we don't risk it).
    """
    if not collection_path.exists():
        return True
    try:
        con = sqlite3.connect(collection_path)
        try:
            for table in _PUSHABLE_TABLES:
                if con.execute(f"SELECT 1 FROM {table} WHERE usn = -1 LIMIT 1").fetchone():  # noqa: S608
                    return True
        finally:
            con.close()
    except sqlite3.Error:
        return True
    return False


def _mirror_real_curdeck_into_tt(real_collection_path: Path, tt_collection_path: Path) -> None:
    """Set TT's ``curDeck`` to the user's real Anki selection before a sync.

    Makes a peer-sync faithful: TT asserts whatever deck the user actually has
    selected rather than imposing one, so it never switches the user's deck out
    from under them. No-op if either collection or the source value is unavailable.
    See :func:`_read_real_curdeck` for why the whole-config-blob upload forces this
    value-mirroring approach rather than excluding ``curDeck`` from the push.
    """
    val = _read_real_curdeck(real_collection_path)
    if val is None or not tt_collection_path.exists():
        return
    try:
        con = sqlite3.connect(tt_collection_path, timeout=0.3)
        try:
            con.execute(
                "UPDATE config SET val = ?, mtime_secs = ? WHERE key = 'curDeck'",
                (val, int(time.time())),
            )
            con.commit()
        finally:
            con.close()
    except sqlite3.Error as exc:
        # Same contract as the read side: a mirroring hiccup must never break
        # (or stall) the sync — skip fast and leave a visible trace.
        logger.warning("curDeck mirror skipped: tt_collection write failed (%s)", exc)


def _login() -> dict:
    """Authenticate to the sync server and return the serialized SyncAuth dict."""
    return _run_driver(
        {
            "op": "login",
            "username": settings.sync_username,
            "password": _resolve_sync_password(),
            "endpoint": settings.sync_endpoint,
        }
    )


# AnkiWeb sync auth (the ``hkey``) is a long-lived session token. Anki authenticates
# once and reuses it across every sync; re-logging in each time costs a whole driver
# subprocess plus a deliberately-slow password round-trip. Cache it in the long-running
# server process and only re-login on a miss or when a sync rejects it (see peer_sync).
_AUTH_CACHE: dict | None = None


def _get_auth(*, refresh: bool = False) -> dict:
    """Return the cached sync auth, logging in on a miss or when *refresh* is set."""
    global _AUTH_CACHE
    if refresh or _AUTH_CACHE is None:
        _AUTH_CACHE = _login()
    return _AUTH_CACHE


def _sync_leg(auth: dict, *, sync_media: bool = False) -> dict:
    """Run one bidirectional ``sync_collection`` leg against tt_collection.

    ``sync_media=True`` also runs anki's media sync (and waits for it) so
    TT-generated media reaches AnkiWeb and other devices.
    """
    return _run_driver(
        {
            "op": "sync",
            "collection_path": str(settings.tt_collection_path),
            "auth": auth,
            "sync_media": sync_media,
        }
    )


def peer_sync(dry_run: bool = False, *, media_fn=None) -> PeerSyncReport:
    """Execute the full peer-sync bracket.

    Returns a ``PeerSyncReport`` with per-step outcomes.
    Raises ``PeerSyncError`` if any step fails.
    """
    report = PeerSyncReport(dry_run=dry_run)
    t_start = time.perf_counter()

    had_cached_auth = _AUTH_CACHE is not None
    t0 = time.perf_counter()
    try:
        auth = _get_auth()
    except PeerSyncError as e:
        raise PeerSyncError(f"Login failed: {e}") from None
    report.timings["auth"] = time.perf_counter() - t0
    report.auth_success = True

    # Point tt_collection's media dir at the real Anki library (when present) so
    # generated media lands in one place and the push leg's media sync uploads it.
    _ensure_tt_media_linked()

    # Anki uploads the whole config blob on every sync — including the pull leg, which
    # is itself a bidirectional sync_collection — so mirror the user's real selected
    # deck before each leg. Without this TT re-asserts whatever curDeck its own
    # collection holds and switches the user's deck out from under them.
    t0 = time.perf_counter()
    _mirror_real_curdeck_into_tt(settings.anki_collection_path, settings.tt_collection_path)
    report.timings["mirror_pre"] = time.perf_counter() - t0

    # Media-enabled: the "pull" leg is a *bidirectional* sync_collection that also
    # pushes any dirty collection rows, so it's the leg that always runs and is where
    # media must sync. (Media only on the conditional push leg strands files: this
    # leg pushes the rows first, clearing has_pending, so the push leg — and its
    # media sync — gets skipped.) The push leg stays media-enabled too, to cover
    # media generated during the reconcile (those are new pending rows).
    t0 = time.perf_counter()
    try:
        sync_out = _sync_leg(auth, sync_media=True)
    except PeerSyncError:
        # A cached hkey can go stale (password change, server invalidation). The pull
        # leg runs before any TT write, so it's safe to re-login and retry once here.
        # Only do so when the auth was cached — a *fresh* login that still fails isn't
        # an expiry, so re-logging in won't help.
        if not had_cached_auth:
            raise
        auth = _get_auth(refresh=True)
        sync_out = _sync_leg(auth, sync_media=True)
    report.timings["pull"] = time.perf_counter() - t0
    report.pull_required = sync_out.get("required")
    report.pull_message = sync_out.get("server_message", "")

    if _full_sync_required(sync_out.get("required")):
        raise _full_sync_error("pull", sync_out.get("required"), sync_out.get("server_message", ""))

    from app.anki.sync import main as tt_sync_main

    t0 = time.perf_counter()
    report.tt_push_pull_exit = tt_sync_main(
        argv=["--dry-run"] if dry_run else [],
        _settings=_tt_settings(),
        _media_fn=media_fn,
        _media_dir=_resolve_media_dir(),
    )
    report.timings["reconcile"] = time.perf_counter() - t0
    if report.tt_push_pull_exit != 0:
        raise PeerSyncError(
            f"TT sync against tt_collection failed (exit={report.tt_push_pull_exit}) — "
            "aborting before push to avoid pushing a partially-synced collection."
        )

    if not dry_run:
        # Skip the push round-trip when there's nothing to upload: the pull leg already
        # synced TT's state (config included) to the server, so with no pending rows the
        # push would be a pure 2–4s AnkiWeb no-op. Profiled as the common case — the user
        # usually grades in Anki, not TT, so the reconcile writes nothing pushable.
        t0 = time.perf_counter()
        has_pending = _has_pending_push(settings.tt_collection_path)
        report.timings["pending_check"] = time.perf_counter() - t0
        if not has_pending:
            report.push_required = 0
            report.push_message = "skipped: no local changes to push"
        else:
            # The pull leg may have pulled the server's curDeck back into TT; re-mirror
            # the user's real selection so the push asserts the right deck, not a stale one.
            t0 = time.perf_counter()
            _mirror_real_curdeck_into_tt(settings.anki_collection_path, settings.tt_collection_path)
            report.timings["mirror_pre_push"] = time.perf_counter() - t0
            # Media-enabled push: uploads any media the reconcile generated for
            # new TT cards. Aligned with the pending-rows gate — new cards (which
            # carry the new media) are exactly what makes the push run.
            t0 = time.perf_counter()
            push_out = _sync_leg(auth, sync_media=True)
            report.timings["push"] = time.perf_counter() - t0
            report.push_required = push_out.get("required")
            report.push_message = push_out.get("server_message", "")
            if _full_sync_required(push_out.get("required")):
                raise _full_sync_error("push", push_out.get("required"), push_out.get("server_message", ""))

    report.timings["total"] = time.perf_counter() - t_start
    _write_peer_sync_timing_log(settings.sync_log, report)
    return report


def _write_peer_sync_timing_log(path: Path, report: PeerSyncReport) -> None:
    """Append one greppable ``PEER_SYNC_TIMING`` line per successful peer-sync.

    Per-leg wall times (auth / mirror_pre / pull / reconcile / pending_check /
    mirror_pre_push / push / total) let us diagnose an occasional slow sync from
    ``~/.tunatale/logs/sync.log`` after the fact — i.e. *which* leg hung — instead
    of trying to reproduce the conditions live. Written only on the success path
    (an aborting sync raises a visible error that already names the failing leg).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().isoformat(timespec="seconds")
    legs = " ".join(f"{name}={secs:.2f}" for name, secs in report.timings.items())
    with open(path, "a") as f:
        f.write(f"{ts} PEER_SYNC_TIMING dry_run={report.dry_run} {legs}\n")


def bootstrap_collection() -> None:
    """Bootstrap a TT-owned collection via full download from sync server.

    Creates a minimal empty collection if *tt_collection_path* doesn't exist,
    then overwrites it with the server's full collection. Safe to re-run on an
    existing collection (re-downloads from server).
    """
    path = settings.tt_collection_path
    needs_create = not path.exists()

    auth = _login()

    if needs_create:
        _run_driver(
            {
                "op": "create_collection",
                "collection_path": str(path),
            }
        )

    _run_driver(
        {
            "op": "full_download",
            "collection_path": str(path),
            "auth": auth,
        }
    )


def main_cli() -> None:
    """CLI entry point: ``python -m app.anki.sync_orchestrator [--bootstrap]``."""
    import argparse

    parser = argparse.ArgumentParser(description="TunaTale Anki peer-sync orchestrator")
    parser.add_argument("--bootstrap", action="store_true", help="Full-download from sync server")
    parser.add_argument("--dry-run", action="store_true", help="Skip push to server")
    args = parser.parse_args()

    if args.bootstrap:
        bootstrap_collection()
        print("Bootstrap complete.")
    else:
        report = peer_sync(dry_run=args.dry_run)
        print(
            f"Pull: required={report.pull_required}, msg={report.pull_message}\n"
            f"TT: exit={report.tt_push_pull_exit}\n"
            f"Push: required={report.push_required}, msg={report.push_message}"
        )


if __name__ == "__main__":  # pragma: no cover
    main_cli()
