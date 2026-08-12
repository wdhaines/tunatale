"""Tests for the off-box backup driver (scripts/backup_offbox.py).

The bead behind this script says a backup that has never been restored is not a
backup. The corollary these tests exist for: a backup job that *silently* backs
up the wrong bytes is worse than one that fails, because it converts an unknown
into a false assurance. So the assertions here are about the two ways this
script could lie:

1. **Staleness.** The rolling `~/.tunatale/db-backups` snapshots are written at
   most once per calendar day, earliest-wins. Backing those up off-box would
   ship this morning's DB tonight and call it fresh. `stage_db_snapshots` takes
   its own consistent snapshot at backup time and must OVERWRITE a same-day
   file — the opposite of `rotate_db_backups`' first-wins rule.
2. **Silence.** `rotate_db_backups` swallows every error so a backup hiccup
   cannot block app startup. Here the tradeoff inverts: a source that cannot be
   snapshotted must raise, because nothing else is watching.

Outside the coverage gate (`source = ["app"]`), tested anyway for that reason.

Real SQLite throughout. The only mock is the process boundary — `security` and
`restic` are external binaries, and one of them talks to a paid remote.
"""
# ruff: noqa: I001 — import from scripts/ needs sys.path.insert before it

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

# Allow importing from scripts/ one level up.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from backup_offbox import (  # noqa: E402
    ACCOUNT_APP_KEY,
    ACCOUNT_KEY_ID,
    ACCOUNT_REPO_PASSWORD,
    SERVICE_B2,
    SERVICE_RESTIC,
    MissingSecret,
    keychain_secret,
    main,
    redacted,
    restic_env,
    stage_db_snapshots,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_db(path: Path, rows: int = 3) -> None:
    """A real SQLite DB in WAL mode, so a snapshot has something to fold in."""
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    con.executemany("INSERT INTO t (v) VALUES (?)", [(f"row{i}",) for i in range(rows)])
    con.commit()
    con.close()


def _count(path: Path) -> int:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return con.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    finally:
        con.close()


class FakeRun:
    """Records argv/env of each external command and returns a canned exit code."""

    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.calls: list[tuple[list[str], dict[str, str] | None]] = []

    def __call__(self, cmd, *args, **kwargs):
        self.calls.append((list(cmd), kwargs.get("env")))
        return subprocess.CompletedProcess(cmd, self.returncode, stdout=self.stdout, stderr="")

    @property
    def argvs(self) -> list[list[str]]:
        return [c for c, _ in self.calls]


@pytest.fixture
def secrets(monkeypatch):
    """Keychain lookups resolve, without touching the real Keychain."""
    values = {
        (SERVICE_B2, ACCOUNT_KEY_ID): "KEYID123",
        (SERVICE_B2, ACCOUNT_APP_KEY): "APPKEY456",
        (SERVICE_RESTIC, ACCOUNT_REPO_PASSWORD): "s3cr3t-passphrase",
    }
    monkeypatch.setattr("backup_offbox.keychain_secret", lambda service, account: values[(service, account)])
    return values


# ── keychain ──────────────────────────────────────────────────────────────────


class TestKeychainSecret:
    def test_returns_the_password_without_its_trailing_newline(self, monkeypatch):
        monkeypatch.setattr("backup_offbox._run", FakeRun(0, stdout="hunter2\n"))
        assert keychain_secret("svc", "acct") == "hunter2"

    def test_missing_item_raises_with_the_command_that_fixes_it(self, monkeypatch):
        monkeypatch.setattr("backup_offbox._run", FakeRun(44))
        with pytest.raises(MissingSecret) as exc:
            keychain_secret("tunatale-b2", "key-id")
        # An unattended job's error message is the whole user interface it has.
        assert "security add-generic-password" in str(exc.value)
        assert "-s tunatale-b2" in str(exc.value)
        assert "-a key-id" in str(exc.value)

    def test_reads_via_the_security_cli_with_w_so_only_the_password_is_printed(self, monkeypatch):
        run = FakeRun(0, stdout="x")
        monkeypatch.setattr("backup_offbox._run", run)
        keychain_secret("svc", "acct")
        assert run.argvs == [["security", "find-generic-password", "-s", "svc", "-a", "acct", "-w"]]


# ── restic environment ────────────────────────────────────────────────────────


class TestResticEnv:
    def test_builds_the_b2_repository_url_and_carries_all_four_credentials(self, secrets):
        env = restic_env("my-bucket", "tunatale", base={"PATH": "/usr/bin"})
        assert env["RESTIC_REPOSITORY"] == "b2:my-bucket:tunatale"
        assert env["RESTIC_PASSWORD"] == "s3cr3t-passphrase"
        assert env["B2_ACCOUNT_ID"] == "KEYID123"
        assert env["B2_ACCOUNT_KEY"] == "APPKEY456"
        assert env["PATH"] == "/usr/bin", "must extend the caller's environment, not replace it"

    def test_secrets_are_not_written_back_into_the_process_environment(self, secrets, monkeypatch):
        import os

        monkeypatch.delenv("RESTIC_PASSWORD", raising=False)
        restic_env("b", "p", base=dict(os.environ))
        assert "RESTIC_PASSWORD" not in os.environ


class TestRedaction:
    def test_every_secret_value_is_masked(self):
        env = {
            "RESTIC_PASSWORD": "s3cr3t-passphrase",
            "B2_ACCOUNT_KEY": "APPKEY456",
            "B2_ACCOUNT_ID": "KEYID123",
            "RESTIC_REPOSITORY": "b2:my-bucket:tunatale",
        }
        shown = redacted(env)
        assert "s3cr3t-passphrase" not in str(shown)
        assert "APPKEY456" not in str(shown)
        assert "KEYID123" not in str(shown)
        # The repository is not a secret and is the one thing worth seeing.
        assert shown["RESTIC_REPOSITORY"] == "b2:my-bucket:tunatale"


# ── staging a fresh snapshot ──────────────────────────────────────────────────


class TestStageDbSnapshots:
    def test_writes_a_dated_snapshot_the_restore_drill_can_read(self, tmp_path):
        src = tmp_path / "tunatale_no.db"
        _make_db(src, rows=7)
        staging = tmp_path / "staging"

        written = stage_db_snapshots([src], staging, today="2026-08-12")

        assert [p.name for p in written] == ["tunatale_no.2026-08-12.db"]
        assert _count(written[0]) == 7

    def test_overwrites_a_stale_same_day_file(self, tmp_path):
        """The freshness property. `rotate_db_backups` is first-wins by design;
        an off-box job that inherited that rule would ship this morning's data
        tonight and report success."""
        src = tmp_path / "tunatale_no.db"
        _make_db(src, rows=2)
        staging = tmp_path / "staging"
        stage_db_snapshots([src], staging, today="2026-08-12")

        con = sqlite3.connect(src)
        con.execute("INSERT INTO t (v) VALUES ('written-after-lunch')")
        con.commit()
        con.close()
        written = stage_db_snapshots([src], staging, today="2026-08-12")

        assert _count(written[0]) == 3

    def test_folds_in_an_open_wal(self, tmp_path):
        """Committed rows sitting in a -wal must be in the snapshot: the online
        backup API materialises them, a plain file copy would not."""
        src = tmp_path / "tunatale_no.db"
        _make_db(src, rows=1)
        con = sqlite3.connect(src)
        con.execute("INSERT INTO t (v) VALUES ('in-the-wal')")
        con.commit()
        try:
            written = stage_db_snapshots([src], tmp_path / "staging", today="2026-08-12")
            assert _count(written[0]) == 2
        finally:
            con.close()

    def test_a_missing_source_raises_instead_of_being_skipped(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="nope.db"):
            stage_db_snapshots([tmp_path / "nope.db"], tmp_path / "staging", today="2026-08-12")

    def test_an_empty_source_raises(self, tmp_path):
        """A zero-byte DB is what a truncated/wiped file looks like. Backing it
        up quietly is how the good copy gets rotated away behind it."""
        empty = tmp_path / "tunatale_sl.db"
        empty.touch()
        with pytest.raises(ValueError, match="empty"):
            stage_db_snapshots([empty], tmp_path / "staging", today="2026-08-12")

    def test_a_corrupt_source_raises(self, tmp_path):
        junk = tmp_path / "tunatale_sl.db"
        junk.write_bytes(b"this is not a database" * 100)
        with pytest.raises(sqlite3.DatabaseError):
            stage_db_snapshots([junk], tmp_path / "staging", today="2026-08-12")

    def test_stale_snapshots_from_other_days_are_removed(self, tmp_path):
        """Staging is a transient mirror of *today*, not a second rolling
        archive — restic's snapshot history provides the depth. Leaving old
        dates behind would grow the uploaded set without bound and hand the
        restore drill an ambiguous 'newest'."""
        staging = tmp_path / "staging"
        src = tmp_path / "tunatale_no.db"
        _make_db(src)
        # Build the prior state the way it really arises — an earlier run —
        # rather than hand-planting a file, so the ownership marker is genuine.
        stage_db_snapshots([src], staging, today="2026-08-01")

        stage_db_snapshots([src], staging, today="2026-08-12")

        assert sorted(p.name for p in staging.glob("*.db")) == ["tunatale_no.2026-08-12.db"]

    def test_refuses_a_staging_directory_it_does_not_own(self, tmp_path):
        """The sweep above deletes files. Pointed at `backend/` or at
        `~/.tunatale/db-backups` — one mistyped flag away — it would delete the
        live databases or the rolling snapshots, i.e. destroy data inside the
        script whose entire job is protecting it. Ownership is claimed by a
        marker file, and an unmarked non-empty directory is refused."""
        occupied = tmp_path / "backend"
        occupied.mkdir()
        live = occupied / "tunatale_no.db"
        _make_db(live, rows=5)
        src = tmp_path / "tunatale_no.db"
        _make_db(src)

        with pytest.raises(RuntimeError, match="staging"):
            stage_db_snapshots([src], occupied, today="2026-08-12")

        assert _count(live) == 5, "refusing must happen BEFORE anything is written or deleted"

    def test_adopts_an_empty_directory_and_reuses_it_next_run(self, tmp_path):
        staging = tmp_path / "staging"
        staging.mkdir()
        src = tmp_path / "tunatale_no.db"
        _make_db(src)

        stage_db_snapshots([src], staging, today="2026-08-12")
        stage_db_snapshots([src], staging, today="2026-08-13")

        assert [p.name for p in staging.glob("*.db")] == ["tunatale_no.2026-08-13.db"]

    def test_sweeps_only_date_shaped_names(self, tmp_path):
        """Second belt to the ownership marker: the sweep is scoped by NAME, so
        even inside a directory it owns it can only ever delete things shaped
        like its own output."""
        staging = tmp_path / "staging"
        src = tmp_path / "tunatale_no.db"
        _make_db(src)
        stage_db_snapshots([src], staging, today="2026-08-12")
        stray = staging / "tunatale_no.pre-v41.db"
        stray.write_bytes(b"a pre-migration snapshot must survive")

        stage_db_snapshots([src], staging, today="2026-08-13")

        assert stray.exists()


# ── the CLI ───────────────────────────────────────────────────────────────────


class TestBackupCommand:
    def test_uploads_the_staged_dbs_and_both_content_trees(self, tmp_path, secrets, monkeypatch):
        run = FakeRun(0)
        monkeypatch.setattr("backup_offbox._run", run)
        src = tmp_path / "tunatale_no.db"
        _make_db(src)
        media, output = tmp_path / "media", tmp_path / "output"
        media.mkdir()
        output.mkdir()

        rc = main(
            [
                "backup",
                "--bucket",
                "my-bucket",
                "--db",
                str(src),
                "--media-src",
                str(media),
                "--output-src",
                str(output),
                "--staging",
                str(tmp_path / "staging"),
            ]
        )

        assert rc == 0
        backup_cmd = next(c for c in run.argvs if "backup" in c)
        assert str(tmp_path / "staging") in backup_cmd
        assert str(media) in backup_cmd
        assert str(output) in backup_cmd

    def test_a_source_that_does_not_exist_is_refused_before_anything_uploads(self, tmp_path, secrets, monkeypatch):
        """Restic treats a vanished path as a warning and exits 3 having backed
        up the rest. That is precisely the silent-partial-backup this bead is
        about, so the check happens here, first, and hard."""
        run = FakeRun(0)
        monkeypatch.setattr("backup_offbox._run", run)
        src = tmp_path / "tunatale_no.db"
        _make_db(src)

        rc = main(
            [
                "backup",
                "--bucket",
                "my-bucket",
                "--db",
                str(src),
                "--media-src",
                str(tmp_path / "gone"),
                "--output-src",
                str(tmp_path / "also-gone"),
                "--staging",
                str(tmp_path / "staging"),
            ]
        )

        assert rc != 0
        assert not any("backup" in c for c in run.argvs), "must not upload a partial set"

    def test_prunes_to_a_retention_policy_after_a_successful_upload(self, tmp_path, secrets, monkeypatch):
        run = FakeRun(0)
        monkeypatch.setattr("backup_offbox._run", run)
        src = tmp_path / "tunatale_no.db"
        _make_db(src)
        (tmp_path / "media").mkdir()
        (tmp_path / "output").mkdir()

        main(
            [
                "backup",
                "--bucket",
                "b",
                "--db",
                str(src),
                "--media-src",
                str(tmp_path / "media"),
                "--output-src",
                str(tmp_path / "output"),
                "--staging",
                str(tmp_path / "staging"),
            ]
        )

        forget = next(c for c in run.argvs if "forget" in c)
        assert "--keep-daily" in forget
        assert "--prune" in forget

    def test_never_prints_the_process_environment(self, tmp_path, secrets, monkeypatch, capsys):
        """A backup job's stdout is a log that outlives the run. Dumping the
        environment into it publishes every unrelated secret the shell happened
        to be carrying — `redacted()` only knows about restic's own four keys."""
        run = FakeRun(0)
        monkeypatch.setattr("backup_offbox._run", run)
        monkeypatch.setenv("SOME_UNRELATED_API_KEY", "sk-live-do-not-log-me")
        src = tmp_path / "tunatale_no.db"
        _make_db(src)
        (tmp_path / "media").mkdir()
        (tmp_path / "output").mkdir()

        main(
            [
                "backup",
                "--bucket",
                "b",
                "--db",
                str(src),
                "--media-src",
                str(tmp_path / "media"),
                "--output-src",
                str(tmp_path / "output"),
                "--staging",
                str(tmp_path / "staging"),
            ]
        )

        out = capsys.readouterr().out
        assert "sk-live-do-not-log-me" not in out
        assert "SOME_UNRELATED_API_KEY" not in out

    def test_an_unstageable_db_fails_with_the_banner_not_a_traceback(self, tmp_path, secrets, monkeypatch, capsys):
        run = FakeRun(0)
        monkeypatch.setattr("backup_offbox._run", run)
        empty = tmp_path / "tunatale_no.db"
        empty.touch()
        (tmp_path / "media").mkdir()
        (tmp_path / "output").mkdir()

        rc = main(
            [
                "backup",
                "--bucket",
                "b",
                "--db",
                str(empty),
                "--media-src",
                str(tmp_path / "media"),
                "--output-src",
                str(tmp_path / "output"),
                "--staging",
                str(tmp_path / "staging"),
            ]
        )

        assert rc != 0
        assert "BACKUP FAILED" in capsys.readouterr().out
        assert not any("backup" in c for c in run.argvs)

    def test_a_failed_upload_is_loud_and_non_zero(self, tmp_path, secrets, monkeypatch, capsys):
        monkeypatch.setattr("backup_offbox._run", FakeRun(1))
        src = tmp_path / "tunatale_no.db"
        _make_db(src)
        (tmp_path / "media").mkdir()
        (tmp_path / "output").mkdir()

        rc = main(
            [
                "backup",
                "--bucket",
                "b",
                "--db",
                str(src),
                "--media-src",
                str(tmp_path / "media"),
                "--output-src",
                str(tmp_path / "output"),
                "--staging",
                str(tmp_path / "staging"),
            ]
        )

        assert rc != 0
        assert "BACKUP FAILED" in capsys.readouterr().out

    def test_pruning_is_never_allowed_to_report_a_failed_backup_as_success(self, tmp_path, secrets, monkeypatch):
        """`forget --prune` runs after the data is safely uploaded. If it fails
        the backup still happened, but the exit code must still say something
        went wrong — a repo that never prunes eventually fills the free tier."""
        calls: list[list[str]] = []

        def run(cmd, *args, **kwargs):
            calls.append(list(cmd))
            rc = 1 if "forget" in cmd else 0
            return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="")

        monkeypatch.setattr("backup_offbox._run", run)
        src = tmp_path / "tunatale_no.db"
        _make_db(src)
        (tmp_path / "media").mkdir()
        (tmp_path / "output").mkdir()

        rc = main(
            [
                "backup",
                "--bucket",
                "b",
                "--db",
                str(src),
                "--media-src",
                str(tmp_path / "media"),
                "--output-src",
                str(tmp_path / "output"),
                "--staging",
                str(tmp_path / "staging"),
            ]
        )

        assert rc != 0
        assert any("backup" in c for c in calls), "the upload itself must still have been attempted"


class TestFailureNotification:
    """`--notify` is the delivery half of loud failure.

    A scheduled backup's non-zero exit goes into a log nobody opens; that is the
    documented way this whole mechanism dies quietly. These tests pin that a
    failure reaches the desktop, that a success does NOT (an alert that fires
    nightly stops being read), and that the alert never carries a secret.
    """

    def _run_backup(self, tmp_path, monkeypatch, *, notify: bool, restic_rc: int):
        calls: list[list[str]] = []

        def run(cmd, *args, **kwargs):
            calls.append(list(cmd))
            rc = restic_rc if cmd[0] == "restic" else 0
            return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="")

        monkeypatch.setattr("backup_offbox._run", run)
        src = tmp_path / "tunatale_no.db"
        _make_db(src)
        (tmp_path / "media").mkdir()
        (tmp_path / "output").mkdir()
        argv = [
            "backup",
            "--bucket",
            "b",
            "--db",
            str(src),
            "--media-src",
            str(tmp_path / "media"),
            "--output-src",
            str(tmp_path / "output"),
            "--staging",
            str(tmp_path / "staging"),
        ]
        if notify:
            argv.append("--notify")
        return main(argv), calls

    def test_a_failure_reaches_the_desktop(self, tmp_path, secrets, monkeypatch):
        rc, calls = self._run_backup(tmp_path, monkeypatch, notify=True, restic_rc=1)
        assert rc != 0
        notify = next(c for c in calls if c[0] == "osascript")
        assert "display notification" in " ".join(notify)
        assert "TunaTale" in " ".join(notify)

    def test_a_success_is_silent(self, tmp_path, secrets, monkeypatch):
        """A nightly alert that always fires is an alert nobody reads."""
        rc, calls = self._run_backup(tmp_path, monkeypatch, notify=True, restic_rc=0)
        assert rc == 0
        assert not any(c[0] == "osascript" for c in calls)

    def test_without_the_flag_nothing_is_posted(self, tmp_path, secrets, monkeypatch):
        """Interactive runs must not pop desktop alerts."""
        rc, calls = self._run_backup(tmp_path, monkeypatch, notify=False, restic_rc=1)
        assert rc != 0
        assert not any(c[0] == "osascript" for c in calls)

    def test_the_notification_carries_no_secret(self, tmp_path, secrets, monkeypatch):
        _, calls = self._run_backup(tmp_path, monkeypatch, notify=True, restic_rc=1)
        text = " ".join(" ".join(c) for c in calls if c[0] == "osascript")
        assert "s3cr3t-passphrase" not in text
        assert "APPKEY456" not in text
        assert "KEYID123" not in text

    def test_a_missing_secret_also_notifies(self, tmp_path, monkeypatch, capsys):
        """The most likely real failure is a Keychain item that stopped
        resolving — it must not be the one path that stays silent."""
        calls: list[list[str]] = []

        def run(cmd, *args, **kwargs):
            calls.append(list(cmd))
            rc = 44 if cmd[0] == "security" else 0
            return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="")

        monkeypatch.setattr("backup_offbox._run", run)
        rc = main(["backup", "--bucket", "b", "--notify"])
        assert rc != 0
        assert any(c[0] == "osascript" for c in calls)
        assert "security add-generic-password" in capsys.readouterr().err


class TestOtherCommands:
    def test_init_creates_the_repository(self, secrets, monkeypatch):
        run = FakeRun(0)
        monkeypatch.setattr("backup_offbox._run", run)
        assert main(["init", "--bucket", "b"]) == 0
        assert run.argvs == [["restic", "init"]]

    def test_check_verifies_the_repository(self, secrets, monkeypatch):
        run = FakeRun(0)
        monkeypatch.setattr("backup_offbox._run", run)
        assert main(["check", "--bucket", "b"]) == 0
        assert run.argvs[0][:2] == ["restic", "check"]

    def test_restore_targets_a_directory(self, tmp_path, secrets, monkeypatch):
        run = FakeRun(0)
        monkeypatch.setattr("backup_offbox._run", run)
        assert main(["restore", "--bucket", "b", "--target", str(tmp_path / "out")]) == 0
        cmd = run.argvs[0]
        assert cmd[:3] == ["restic", "restore", "latest"]
        assert "--target" in cmd and str(tmp_path / "out") in cmd

    def test_snapshots_lists_them(self, secrets, monkeypatch):
        run = FakeRun(0)
        monkeypatch.setattr("backup_offbox._run", run)
        assert main(["snapshots", "--bucket", "b"]) == 0
        assert run.argvs[0][:2] == ["restic", "snapshots"]


class TestMissingConfiguration:
    def test_no_bucket_is_an_error_not_a_traceback(self, monkeypatch, capsys):
        monkeypatch.delenv("TT_B2_BUCKET", raising=False)
        rc = main(["snapshots"])
        assert rc != 0
        assert "--bucket" in capsys.readouterr().err

    def test_the_bucket_can_come_from_the_environment(self, secrets, monkeypatch):
        run = FakeRun(0)
        monkeypatch.setattr("backup_offbox._run", run)
        monkeypatch.setenv("TT_B2_BUCKET", "from-env")
        assert main(["snapshots"]) == 0
        assert run.calls[0][1]["RESTIC_REPOSITORY"] == "b2:from-env:tunatale"

    def test_a_missing_keychain_item_is_reported_with_its_fix(self, monkeypatch, capsys):
        monkeypatch.setattr("backup_offbox._run", FakeRun(44))
        rc = main(["snapshots", "--bucket", "b"])
        assert rc != 0
        assert "security add-generic-password" in capsys.readouterr().err
