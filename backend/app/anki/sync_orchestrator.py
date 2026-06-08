"""Anki peer-sync orchestrator (anki-free core module).

Drives the sync bracket:

  1. ``sync_collection`` via driver (pull server → tt_collection)
  2. ``sync.main()`` — TT's existing push/pull against tt_collection
  3. ``sync_collection`` via driver (push tt_collection → server)

Uses ``app.anki.sync_driver`` via subprocess for the anki-side operations.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from dataclasses import dataclass
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


@dataclass
class PeerSyncReport:
    auth_success: bool = False
    pull_required: int | None = None
    pull_message: str = ""
    tt_push_pull_exit: int | None = None
    push_required: int | None = None
    push_message: str = ""
    dry_run: bool = False


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
        con = sqlite3.connect(f"file:{real_collection_path}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT val FROM config WHERE key = 'curDeck'").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def _read_col_mod(collection_path: Path) -> int | None:
    """Read ``col.mod`` from TT's own collection (the single mutation chokepoint).

    Every tt_sync write bumps ``col.mod`` via ``_bump_col``, so comparing it across
    ``tt_sync_main`` tells us whether the reconcile changed anything — and thus
    whether the push leg has anything to send. Plain (not ``mode=ro``) connection:
    tt_collection is TT-owned and never held open by the Anki GUI, and a bare
    ``mode=ro`` can trip on a leftover ``-wal`` sidecar. Returns ``None`` (caller
    treats it as "unknown → don't skip the push") if unreadable.
    """
    if not collection_path.exists():
        return None
    try:
        con = sqlite3.connect(collection_path)
        try:
            row = con.execute("SELECT mod FROM col").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return row[0] if row else None


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
    con = sqlite3.connect(tt_collection_path)
    try:
        con.execute(
            "UPDATE config SET val = ?, mtime_secs = ? WHERE key = 'curDeck'",
            (val, int(time.time())),
        )
        con.commit()
    finally:
        con.close()


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


def _sync_leg(auth: dict) -> dict:
    """Run one bidirectional ``sync_collection`` leg against tt_collection."""
    return _run_driver(
        {
            "op": "sync",
            "collection_path": str(settings.tt_collection_path),
            "auth": auth,
        }
    )


def peer_sync(dry_run: bool = False) -> PeerSyncReport:
    """Execute the full peer-sync bracket.

    Returns a ``PeerSyncReport`` with per-step outcomes.
    Raises ``PeerSyncError`` if any step fails.
    """
    report = PeerSyncReport(dry_run=dry_run)

    had_cached_auth = _AUTH_CACHE is not None
    try:
        auth = _get_auth()
    except PeerSyncError as e:
        raise PeerSyncError(f"Login failed: {e}") from None
    report.auth_success = True

    # Anki uploads the whole config blob on every sync — including the pull leg, which
    # is itself a bidirectional sync_collection — so mirror the user's real selected
    # deck before each leg. Without this TT re-asserts whatever curDeck its own
    # collection holds and switches the user's deck out from under them.
    _mirror_real_curdeck_into_tt(settings.anki_collection_path, settings.tt_collection_path)

    try:
        sync_out = _sync_leg(auth)
    except PeerSyncError:
        # A cached hkey can go stale (password change, server invalidation). The pull
        # leg runs before any TT write, so it's safe to re-login and retry once here.
        # Only do so when the auth was cached — a *fresh* login that still fails isn't
        # an expiry, so re-logging in won't help.
        if not had_cached_auth:
            raise
        auth = _get_auth(refresh=True)
        sync_out = _sync_leg(auth)
    report.pull_required = sync_out.get("required")
    report.pull_message = sync_out.get("server_message", "")

    if _full_sync_required(sync_out.get("required")):
        raise PeerSyncError(
            f"Server requested FULL_SYNC (required={sync_out.get('required')}) on pull — aborting. "
            f"Server message: {sync_out.get('server_message', '')}. "
            "Run bootstrap first or check collection compatibility."
        )

    from app.anki.sync import main as tt_sync_main

    # Baseline before the reconcile so we can tell whether it wrote anything (below).
    mod_before_reconcile = _read_col_mod(settings.tt_collection_path)

    report.tt_push_pull_exit = tt_sync_main(
        argv=["--dry-run"] if dry_run else [],
        _settings=_tt_settings(),
    )
    if report.tt_push_pull_exit != 0:
        raise PeerSyncError(
            f"TT sync against tt_collection failed (exit={report.tt_push_pull_exit}) — "
            "aborting before push to avoid pushing a partially-synced collection."
        )

    if not dry_run:
        # Skip the push round-trip when the reconcile changed nothing: the pull leg
        # already synced TT's state (config included) to the server, so a push would
        # be a pure no-op handshake — ~half of syncs in practice. col.mod is the
        # single mutation chokepoint (every tt_sync write bumps it); only skip on a
        # confident read of an unchanged mod, otherwise push to be safe.
        mod_after_reconcile = _read_col_mod(settings.tt_collection_path)
        if mod_before_reconcile is not None and mod_after_reconcile == mod_before_reconcile:
            report.push_required = 0
            report.push_message = "skipped: no local changes to push"
        else:
            # The pull leg may have pulled the server's curDeck back into TT; re-mirror
            # the user's real selection so the push asserts the right deck, not a stale one.
            _mirror_real_curdeck_into_tt(settings.anki_collection_path, settings.tt_collection_path)
            push_out = _sync_leg(auth)
            report.push_required = push_out.get("required")
            report.push_message = push_out.get("server_message", "")
            if _full_sync_required(push_out.get("required")):
                raise PeerSyncError(
                    f"Server requested FULL_SYNC (required={push_out.get('required')}) on push — aborting. "
                    f"Server message: {push_out.get('server_message', '')}."
                )

    return report


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
