"""Tests for the LaunchAgent installer (scripts/install_backup_agent.py).

A scheduled backup fails in a way no test can catch: silently, months later,
when someone needs a restore. So these tests target the specific ways a plist
can look installed and never work — every one of them measured on this machine
by a throwaway probe agent on 2026-08-12, not guessed:

- **A LaunchAgent's PATH is `/usr/bin:/bin:/usr/sbin:/sbin`.** Neither `uv`
  (`~/.local/bin`) nor `restic` (`/opt/homebrew/bin`) is on it. A plist that
  assumes a login shell's PATH fails every night, loudly into a log nobody
  reads — which is exactly the silence the notification exists to break.
- **No environment is inherited at all**, so `TT_B2_BUCKET` must be in the
  plist or the job exits 2 before touching the network.

(The one worry that did NOT materialise: `security find-generic-password`
returns 0 unattended from an agent, because the item's default ACL trusts
`/usr/bin/security` and the agent invokes that same binary.)

Outside the coverage gate (`source = ["app"]`), tested anyway for that reason.
"""
# ruff: noqa: I001 — import from scripts/ needs sys.path.insert before it

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from install_backup_agent import (  # noqa: E402
    LABEL,
    MissingTool,
    main,
    render_plist,
    resolve_tools,
)


class FakeRun:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def __call__(self, cmd, *args, **kwargs):
        self.calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, self.returncode, stdout="", stderr="")


@pytest.fixture
def tools(monkeypatch, tmp_path):
    """`uv` and `restic` at realistic, non-default-PATH locations."""
    uv = tmp_path / "home" / ".local" / "bin" / "uv"
    restic = tmp_path / "brew" / "bin" / "restic"
    for p in (uv, restic):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch(mode=0o755)
    monkeypatch.setattr(
        "install_backup_agent.shutil.which",
        lambda name: {"uv": str(uv), "restic": str(restic)}.get(name),
    )
    return {"uv": uv, "restic": restic}


class TestResolveTools:
    def test_finds_both_binaries(self, tools):
        found = resolve_tools()
        assert found["uv"] == str(tools["uv"])
        assert found["restic"] == str(tools["restic"])

    def test_refuses_when_a_tool_is_missing(self, monkeypatch):
        """Installing an agent that cannot possibly run is worse than not
        installing one: the calendar entry exists, so the job looks covered."""
        monkeypatch.setattr("install_backup_agent.shutil.which", lambda name: None if name == "restic" else "/x/uv")
        with pytest.raises(MissingTool, match="restic"):
            resolve_tools()


class TestRenderPlist:
    def _plist(self, tools, **kw):
        opts = {"bucket": "my-bucket", "hour": 3, "minute": 30, "backend_dir": Path("/repo/backend")}
        opts.update(kw)
        return plistlib.loads(render_plist(resolve_tools(), **opts))

    def test_is_valid_plist_xml_with_the_expected_label(self, tools):
        assert self._plist(tools)["Label"] == LABEL

    def test_invokes_uv_by_absolute_path(self, tools):
        """`uv` is in ~/.local/bin, which a LaunchAgent's PATH does not include."""
        argv = self._plist(tools)["ProgramArguments"]
        assert argv[0] == str(tools["uv"])
        assert Path(argv[0]).is_absolute()

    def test_runs_the_backup_subcommand_with_notify(self, tools):
        argv = self._plist(tools)["ProgramArguments"]
        assert "backup" in argv
        assert "--notify" in argv, "an unattended job with no notification is the silent failure this bead is about"

    def test_puts_restics_directory_on_the_path(self, tools):
        """restic is invoked by name from inside backup_offbox, so its directory
        has to be on the PATH the agent actually gets."""
        path = self._plist(tools)["EnvironmentVariables"]["PATH"]
        assert str(tools["restic"].parent) in path.split(":")

    def test_carries_the_bucket(self, tools):
        assert self._plist(tools, bucket="tunatale-backups")["EnvironmentVariables"]["TT_B2_BUCKET"] == (
            "tunatale-backups"
        )

    def test_schedules_at_the_requested_time(self, tools):
        cal = self._plist(tools, hour=4, minute=5)["StartCalendarInterval"]
        assert cal == {"Hour": 4, "Minute": 5}

    def test_does_not_run_at_load(self, tools):
        """Loading the agent must not fire a backup — install should be a
        no-op until the scheduled time, or verifying it becomes destructive."""
        assert self._plist(tools)["RunAtLoad"] is False

    def test_logs_somewhere_findable(self, tools):
        p = self._plist(tools)
        assert p["StandardOutPath"] == p["StandardErrorPath"]
        assert p["StandardOutPath"].endswith(".log")

    def test_runs_from_the_backend_directory(self, tools):
        assert self._plist(tools, backend_dir=Path("/repo/backend"))["WorkingDirectory"] == "/repo/backend"

    def test_carries_no_secret(self, tools):
        """Credentials come from the Keychain at run time. A plist is a
        world-readable file in the user's home directory."""
        raw = render_plist(resolve_tools(), bucket="b", hour=3, minute=30, backend_dir=Path("/repo/backend")).decode()
        for forbidden in ("RESTIC_PASSWORD", "B2_ACCOUNT_KEY", "B2_ACCOUNT_ID"):
            assert forbidden not in raw


class TestCLI:
    def test_dry_run_writes_nothing_and_loads_nothing(self, tools, tmp_path, monkeypatch, capsys):
        run = FakeRun()
        monkeypatch.setattr("install_backup_agent._run", run)
        target = tmp_path / "LaunchAgents" / f"{LABEL}.plist"
        monkeypatch.setattr("install_backup_agent.PLIST_PATH", target)

        assert main(["--bucket", "b", "--dry-run"]) == 0
        assert not target.exists()
        assert run.calls == []
        assert "<plist" in capsys.readouterr().out

    def test_install_writes_the_plist_and_bootstraps_it(self, tools, tmp_path, monkeypatch):
        run = FakeRun()
        monkeypatch.setattr("install_backup_agent._run", run)
        target = tmp_path / "LaunchAgents" / f"{LABEL}.plist"
        monkeypatch.setattr("install_backup_agent.PLIST_PATH", target)

        assert main(["--bucket", "b"]) == 0
        assert plistlib.loads(target.read_bytes())["Label"] == LABEL
        joined = [" ".join(c) for c in run.calls]
        assert any("bootout" in c for c in joined), "a stale agent must be unloaded before the new one loads"
        assert any("bootstrap" in c for c in joined)
        assert joined.index(next(c for c in joined if "bootout" in c)) < joined.index(
            next(c for c in joined if "bootstrap" in c)
        )

    def test_uninstall_removes_and_unloads(self, tools, tmp_path, monkeypatch):
        run = FakeRun()
        monkeypatch.setattr("install_backup_agent._run", run)
        target = tmp_path / "LaunchAgents" / f"{LABEL}.plist"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"<plist/>")
        monkeypatch.setattr("install_backup_agent.PLIST_PATH", target)

        assert main(["--uninstall"]) == 0
        assert not target.exists()
        assert any("bootout" in " ".join(c) for c in run.calls)

    def test_a_missing_tool_refuses_before_writing_anything(self, tmp_path, monkeypatch, capsys):
        run = FakeRun()
        monkeypatch.setattr("install_backup_agent._run", run)
        monkeypatch.setattr("install_backup_agent.shutil.which", lambda name: None)
        target = tmp_path / "LaunchAgents" / f"{LABEL}.plist"
        monkeypatch.setattr("install_backup_agent.PLIST_PATH", target)

        assert main(["--bucket", "b"]) != 0
        assert not target.exists()
        assert run.calls == []
        assert "uv" in capsys.readouterr().err

    def test_no_bucket_is_an_error_not_a_traceback(self, tools, monkeypatch, capsys):
        monkeypatch.delenv("TT_B2_BUCKET", raising=False)
        assert main([]) != 0
        assert "--bucket" in capsys.readouterr().err
