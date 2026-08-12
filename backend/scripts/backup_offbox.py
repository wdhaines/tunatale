"""Off-box backup driver: restic into a Backblaze B2 bucket.

The local ``~/.tunatale/db-backups`` rotation is the first line of defence and
it sits on the same disk as the thing it protects. This script is the second
line: it snapshots the per-language DBs fresh at backup time, stages them, and
ships them plus the media/output trees to a restic repository in B2.

It is the deliberately opposite half of ``rotate_db_backups``:

- **Freshness.** The rolling snapshots are earliest-wins (a later wipe must not
  clobber the morning's good copy). An off-box job inheriting that rule would
  upload this morning's DB tonight and call it current — so
  ``stage_db_snapshots`` takes its OWN consistent snapshot and overwrites a
  same-day file.
- **Loudness.** ``rotate_db_backups`` swallows every error so a backup hiccup
  cannot block app startup. Nothing is watching this job, so a source that
  cannot be snapshotted raises, and a vanished upload source refuses to run at
  all rather than ship the rest — restic treats a missing path as a warning and
  exits 3 having backed up a partial set, the exact silent failure this exists
  to prevent.

Credentials live in the macOS Keychain (``tunatale-b2``, ``tunatale-restic``),
never in the repo. An unattended job's error message is its only user interface,
so ``MissingSecret`` prints the exact ``security add-generic-password``
invocation that fixes it.

Requires restic installed (``brew install restic``), nothing else.

    uv run python scripts/backup_offbox.py init --bucket my-bucket
    uv run python scripts/backup_offbox.py backup --bucket my-bucket
    uv run python scripts/backup_offbox.py restore --bucket my-bucket --target ~/restore

Exit code is non-zero on any failure so it can be a cron gate.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from app.config import settings
from app.languages import resolve_db_path
from app.storage.db_backup import _DATE_GLOB, _snapshot

SERVICE_B2 = "tunatale-b2"
ACCOUNT_KEY_ID = "key-id"
ACCOUNT_APP_KEY = "app-key"
SERVICE_RESTIC = "tunatale-restic"
ACCOUNT_REPO_PASSWORD = "repo-password"

_BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_STAGING = Path("~/.tunatale/offbox-staging").expanduser()
_RETENTION = ["--keep-daily", "7", "--keep-weekly", "4", "--keep-monthly", "6", "--prune"]
_REDACTED = "<redacted>"
# Written into a staging directory to mark it as this script's to sweep.
_MARKER = ".tt-offbox-staging"


class MissingSecret(Exception):
    """A Keychain item the backup depends on is not there."""


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """The single process boundary. ``security`` and ``restic`` are external
    binaries, so everything shells out through here and nothing else does;
    tests replace this one seam."""
    return subprocess.run(cmd, **kwargs)


def keychain_secret(service: str, account: str) -> str:
    """Fetch a generic-password item via the ``security`` CLI, or raise.

    ``-w`` prints only the password (no keychain structure); ``text=True`` keeps
    it as str. A non-zero exit means the item is simply absent: raise with the
    exact command that fixes it, because an unattended job's stderr is the whole
    user interface it has.
    """
    proc = _run(
        ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise MissingSecret(
            f"no Keychain item {service}/{account}; add it with:\n"
            f"  security add-generic-password -s {service} -a {account} -w"
        )
    return proc.stdout.strip()


def restic_env(bucket: str, repo_path: str, *, base: dict[str, str]) -> dict[str, str]:
    """The caller's environment plus the B2/restic credentials, as a NEW dict.

    Extended, never replaced, and never written back into ``os.environ``: the
    secrets should not outlive the one subprocess they were fetched for.
    """
    env = dict(base)
    env["RESTIC_REPOSITORY"] = f"b2:{bucket}:{repo_path}"
    env["RESTIC_PASSWORD"] = keychain_secret(SERVICE_RESTIC, ACCOUNT_REPO_PASSWORD)
    env["B2_ACCOUNT_ID"] = keychain_secret(SERVICE_B2, ACCOUNT_KEY_ID)
    env["B2_ACCOUNT_KEY"] = keychain_secret(SERVICE_B2, ACCOUNT_APP_KEY)
    return env


def redacted(env: dict[str, str]) -> dict[str, str]:
    """Same mapping with every secret masked; the repository URL survives.

    The repository is not a secret and is the one value worth seeing in a log —
    it says which bucket and path the job is talking to.
    """
    shown = dict(env)
    for key in ("RESTIC_PASSWORD", "B2_ACCOUNT_KEY", "B2_ACCOUNT_ID"):
        if key in shown:
            shown[key] = _REDACTED
    return shown


def _claim_staging(staging: Path) -> None:
    """Create/adopt ``staging`` as a directory this script may delete files in.

    The sweep below unlinks files, and the flag that names the directory is one
    typo away from ``backend/`` (the live DBs) or ``~/.tunatale/db-backups`` (the
    rolling snapshots). Destroying data inside the script whose whole job is
    protecting it is the worst available bug, and this project has already lost
    its curricula twice to a path mistake.

    So ownership is explicit: an empty or absent directory is adopted and
    marked; a non-empty directory without the marker is refused, before anything
    is written or deleted.
    """
    if staging.exists() and any(staging.iterdir()) and not (staging / _MARKER).exists():
        raise RuntimeError(
            f"refusing to use {staging} as a staging directory: it is not empty and carries no {_MARKER} marker. "
            "Staging is swept on every run — point --staging at a directory owned by this script."
        )
    staging.mkdir(parents=True, exist_ok=True)
    (staging / _MARKER).touch()


def stage_db_snapshots(db_paths: Iterable[Path | str], staging: Path | str, today: str | None = None) -> list[Path]:
    """A fresh, dated snapshot of each DB into ``staging``; sweep stale ones.

    Returns the paths written. Three properties deliberately opposite to
    ``rotate_db_backups``, each for the same reason — this is the off-box half,
    where nothing else is watching:

    - **Overwrites** a same-day file. ``rotate_db_backups`` is first-wins so a
      later wipe cannot clobber the morning's good copy; an off-box job that
      inherited that rule would ship this morning's data tonight and report
      success.
    - **Raises** on a missing or empty source instead of skipping. The local
      rotation swallows everything so a backup hiccup cannot block startup;
      here silence is how a truncated DB gets shipped and the good copy rotated
      away behind it. A corrupt source propagates ``sqlite3.DatabaseError`` from
      the online-backup helper.
    - **Removes** other snapshots from ``staging`` after writing. Staging
      mirrors *today*; restic's snapshot history provides the time depth, and a
      stale ``*.db`` would grow the uploaded set without bound while handing the
      restore drill an ambiguous "newest".

    The sweep is fenced twice: by the ownership marker ``_claim_staging`` writes,
    and by name — only ``{stem}.{YYYY-MM-DD}.db`` is ever a candidate, borrowing
    ``db_backup._DATE_GLOB`` so a pre-migration ``{stem}.pre-v41.db`` parked in
    the same directory survives. Belt and braces, because the failure mode is
    silent data deletion.
    """
    day = today or date.today().isoformat()
    staging = Path(staging)
    _claim_staging(staging)
    written: list[Path] = []
    for raw in db_paths:
        src = Path(raw)
        if not src.exists():
            raise FileNotFoundError(f"source database missing: {src}")
        if src.stat().st_size == 0:
            raise ValueError(f"source database is empty (0 bytes): {src}")
        dest = staging / f"{src.stem}.{day}.db"
        _snapshot(src, dest)
        written.append(dest)
    kept = {p.name for p in written}
    for stale in staging.glob(f"*.{_DATE_GLOB}.db"):
        if stale.name not in kept:
            stale.unlink()
    return written


def _default_db_paths() -> list[Path]:
    """Every configured language's DB, resolved through the registry.

    Mirrors ``check_schema_compat._db_paths``: reading ``settings.database_url``
    (singular) would silently pick one fixed language and ship only its DB
    off-box while every other language's data went unprotected.
    """
    codes = list(settings.database_urls) if settings.database_urls else [settings.target_language]
    return [resolve_db_path(code, settings) for code in codes]


# ── subcommands ───────────────────────────────────────────────────────────────


def _cmd_init(args, env) -> int:
    return 0 if _run(["restic", "init"], env=env).returncode == 0 else 1


def _cmd_check(args, env) -> int:
    return 0 if _run(["restic", "check"], env=env).returncode == 0 else 1


def _cmd_snapshots(args, env) -> int:
    return 0 if _run(["restic", "snapshots"], env=env).returncode == 0 else 1


def _cmd_restore(args, env) -> int:
    target = args.target.expanduser()
    return 0 if _run(["restic", "restore", "latest", "--target", str(target)], env=env).returncode == 0 else 1


def _cmd_backup(args, env) -> int:
    db_paths = [Path(p) for p in (args.db or _default_db_paths())]
    media = args.media_src.expanduser()
    output = args.output_src.expanduser()
    staging = args.staging.expanduser()

    # Validate EVERY source before staging, before anything reaches restic.
    # Restic treats a vanished path as a warning and exits 3 having backed up
    # the rest — a silent partial backup is precisely what this script exists
    # to prevent, so a missing tree refuses to run at all.
    missing = [str(p) for p in [*db_paths, media, output] if not p.exists()]
    if missing:
        print(f"BACKUP FAILED: missing sources (refusing to upload a partial set): {', '.join(missing)}")
        return 1

    try:
        snapshots = stage_db_snapshots(db_paths, staging)
    except (OSError, RuntimeError, ValueError, sqlite3.DatabaseError) as exc:
        # Same banner as every other failure: an operator scanning a log should
        # not have to tell a refusal apart from a traceback.
        print(f"BACKUP FAILED: cannot stage a DB snapshot: {exc}")
        return 1

    print(f"staged {len(snapshots)} fresh DB snapshot(s) to {staging}")
    # Only the repository, never the environment: this stdout is a log that
    # outlives the run, and `redacted` masks restic's four keys — not whatever
    # unrelated API keys the invoking shell happened to be carrying.
    print(f"uploading to {redacted(env)['RESTIC_REPOSITORY']}")

    cmd = ["restic", "backup", str(staging), str(media), str(output)]
    if _run(cmd, env=env).returncode != 0:
        print("BACKUP FAILED: restic backup returned non-zero")
        return 1

    # Retention runs AFTER the upload, so the new snapshot is never pruned while
    # still uploading. A forget failure must not undo the backup, but it must
    # still fail the job: a repo that never prunes eventually fills the free
    # tier, and nothing else is watching.
    forget = ["restic", "forget", *_RETENTION]
    if _run(forget, env=env).returncode != 0:
        print("BACKUP FAILED: restic forget --prune returned non-zero (the upload itself succeeded)")
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--bucket", help="B2 bucket name (default: $TT_B2_BUCKET)")
    common.add_argument("--repo-path", default="tunatale", help="repository path inside the bucket (default: tunatale)")

    p_init = sub.add_parser("init", parents=[common], help="create the restic repository")
    p_init.set_defaults(handler=_cmd_init)

    p_check = sub.add_parser("check", parents=[common], help="verify repository integrity")
    p_check.set_defaults(handler=_cmd_check)

    p_snapshots = sub.add_parser("snapshots", parents=[common], help="list snapshots")
    p_snapshots.set_defaults(handler=_cmd_snapshots)

    p_restore = sub.add_parser("restore", parents=[common], help="restore the latest snapshot")
    p_restore.add_argument("--target", type=Path, required=True, help="directory to restore into")
    p_restore.set_defaults(handler=_cmd_restore)

    p_backup = sub.add_parser("backup", parents=[common], help="stage fresh DB snapshots and upload media/output/DBs")
    p_backup.add_argument(
        "--db",
        action="append",
        type=Path,
        default=None,
        help="language DB to back up (repeatable; default: every configured language)",
    )
    p_backup.add_argument(
        "--media-src", type=Path, default=_BACKEND_DIR / "media", help="media tree to upload (default: backend/media)"
    )
    p_backup.add_argument(
        "--output-src",
        type=Path,
        default=_BACKEND_DIR / "output",
        help="output tree to upload (default: backend/output)",
    )
    p_backup.add_argument(
        "--staging",
        type=Path,
        default=DEFAULT_STAGING,
        help="staging dir for fresh DB snapshots (default: ~/.tunatale/offbox-staging)",
    )
    p_backup.set_defaults(handler=_cmd_backup)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    bucket = args.bucket or os.environ.get("TT_B2_BUCKET")
    if not bucket:
        # An error, not a traceback: this is a cron job whose stderr is the log.
        print("backup_offbox: no bucket given — pass --bucket or set TT_B2_BUCKET", file=sys.stderr)
        return 2

    try:
        env = restic_env(bucket, args.repo_path, base=dict(os.environ))
        return args.handler(args, env)
    except MissingSecret as exc:
        print(f"backup_offbox: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
