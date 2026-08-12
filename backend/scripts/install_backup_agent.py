"""Install the daily off-box backup as a macOS LaunchAgent.

``backup_offbox.py`` can take a backup; this makes one happen without anybody
remembering to. That is the difference between a backup mechanism and a backup.

**launchd, not cron.** A laptop is asleep at 03:30. cron silently skips the
window and never catches up; launchd's ``StartCalendarInterval`` fires the job
when the machine next wakes. A scheduler that skips is worse than none, because
it looks configured.

Everything here exists because of what a LaunchAgent does NOT inherit, measured
on this machine with a throwaway probe agent (2026-08-12):

- ``PATH`` is ``/usr/bin:/bin:/usr/sbin:/sbin``. Neither ``uv``
  (``~/.local/bin``) nor ``restic`` (``/opt/homebrew/bin``) is on it — so the
  plist calls ``uv`` by absolute path and puts both directories on ``PATH``.
  Both are resolved with ``shutil.which`` at install time rather than hardcoded,
  and a missing one REFUSES to install: an agent that cannot possibly run is
  worse than no agent, because the calendar entry makes the job look covered.
- No environment at all, so ``TT_B2_BUCKET`` is written into the plist.

The one thing that did work unattended: ``security find-generic-password``
returns 0 from an agent. A generic-password item's default ACL trusts
``/usr/bin/security``, and the agent invokes that same binary — so credentials
stay in the Keychain and never touch this file. The plist is world-readable.

    uv run python scripts/install_backup_agent.py --bucket tunatale-backups
    uv run python scripts/install_backup_agent.py --bucket X --dry-run
    uv run python scripts/install_backup_agent.py --uninstall
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "com.tunatale.backup"
PLIST_PATH = Path("~/Library/LaunchAgents").expanduser() / f"{LABEL}.plist"
LOG_PATH = Path("~/.tunatale/logs/backup.log").expanduser()
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REQUIRED_TOOLS = ("uv", "restic")
# What a LaunchAgent is actually given, measured — the floor we extend.
_BASE_PATH = ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]


class MissingTool(Exception):
    """A binary the scheduled job needs is not installable-visible."""


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """The single process boundary (``launchctl``); tests replace this seam."""
    return subprocess.run(cmd, **kwargs)


def resolve_tools() -> dict[str, str]:
    """Absolute paths to every binary the agent needs, or refuse.

    Resolved at install time rather than hardcoded: Homebrew is
    ``/opt/homebrew`` on Apple silicon and ``/usr/local`` on Intel, and ``uv``
    installs to ``~/.local/bin``. Refusing on a missing tool is the point — the
    failure it prevents is an agent that is scheduled, visible in
    ``launchctl list``, and broken every single night.
    """
    found: dict[str, str] = {}
    missing: list[str] = []
    for tool in _REQUIRED_TOOLS:
        path = shutil.which(tool)
        if path is None:
            missing.append(tool)
        else:
            found[tool] = path
    if missing:
        raise MissingTool(
            f"cannot find {', '.join(missing)} on PATH — refusing to install an agent that cannot run. "
            "Install the missing tool (`brew install restic`) and re-run."
        )
    return found


def render_plist(tools: dict[str, str], *, bucket: str, hour: int, minute: int, backend_dir: Path) -> bytes:
    """The LaunchAgent property list, as XML bytes.

    ``RunAtLoad`` is False on purpose: installing the agent must not fire a
    backup, or verifying the install becomes a destructive act.
    """
    tool_dirs = [str(Path(p).parent) for p in tools.values()]
    # dict.fromkeys: de-duplicate while keeping order — the tool directories
    # must precede the system ones.
    path = ":".join(dict.fromkeys([*tool_dirs, *_BASE_PATH]))
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": [
                tools["uv"],
                "run",
                "python",
                "scripts/backup_offbox.py",
                "backup",
                # Without this the job's only failure signal is a log nobody
                # opens — the exact way a scheduled backup dies unnoticed.
                "--notify",
            ],
            "WorkingDirectory": str(backend_dir),
            "EnvironmentVariables": {"PATH": path, "TT_B2_BUCKET": bucket},
            "StartCalendarInterval": {"Hour": hour, "Minute": minute},
            "StandardOutPath": str(LOG_PATH),
            "StandardErrorPath": str(LOG_PATH),
            "RunAtLoad": False,
        }
    )


def _launchctl_domain() -> str:
    return f"gui/{os.getuid()}"


def _bootout() -> None:
    """Unload any existing agent. Never fails the install: 'not loaded' is the
    normal case on a first run and is reported as an error by launchctl."""
    _run(["launchctl", "bootout", f"{_launchctl_domain()}/{LABEL}"], capture_output=True, text=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bucket", help="B2 bucket name (default: $TT_B2_BUCKET)")
    parser.add_argument("--hour", type=int, default=3, help="hour to run, 24h local time (default: 3)")
    parser.add_argument("--minute", type=int, default=30, help="minute to run (default: 30)")
    parser.add_argument("--dry-run", action="store_true", help="print the plist; write and load nothing")
    parser.add_argument("--uninstall", action="store_true", help="unload the agent and delete the plist")
    args = parser.parse_args(argv)

    if args.uninstall:
        _bootout()
        PLIST_PATH.unlink(missing_ok=True)
        print(f"uninstalled {LABEL}")
        return 0

    bucket = args.bucket or os.environ.get("TT_B2_BUCKET")
    if not bucket:
        print("install_backup_agent: no bucket given — pass --bucket or set TT_B2_BUCKET", file=sys.stderr)
        return 2

    try:
        tools = resolve_tools()
    except MissingTool as exc:
        print(f"install_backup_agent: {exc}", file=sys.stderr)
        return 1

    plist = render_plist(tools, bucket=bucket, hour=args.hour, minute=args.minute, backend_dir=_BACKEND_DIR)

    if args.dry_run:
        print(plist.decode())
        return 0

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_bytes(plist)

    # Bootout before bootstrap: launchd refuses to load a label that is already
    # loaded, so a re-install without this silently keeps the OLD definition.
    _bootout()
    loaded = _run(["launchctl", "bootstrap", _launchctl_domain(), str(PLIST_PATH)], capture_output=True, text=True)
    if loaded.returncode != 0:
        print(f"install_backup_agent: launchctl bootstrap failed: {loaded.stderr.strip()}", file=sys.stderr)
        return 1

    print(f"installed {LABEL} — daily at {args.hour:02d}:{args.minute:02d}, logging to {LOG_PATH}")
    print(f"  run it now:  launchctl kickstart -w {_launchctl_domain()}/{LABEL}")
    print(f"  uninstall:   uv run python {Path(__file__).name} --uninstall")
    return 0


if __name__ == "__main__":
    sys.exit(main())
