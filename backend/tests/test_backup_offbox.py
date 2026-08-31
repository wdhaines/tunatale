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

import re
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
    notify_failure,
    redacted,
    restic_env,
    stage_anki_collection,
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


def _make_collection(path: Path, rows: int = 3) -> None:
    """A fake Anki collection: a real SQLite DB in WAL mode with a `notes` table,
    which is the only schema `snapshot_collection` reads."""
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT)")
    con.executemany("INSERT INTO notes (text) VALUES (?)", [(f"note{i}",) for i in range(rows)])
    con.commit()
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


@pytest.fixture(autouse=True)
def _no_writes_to_the_real_home(tmp_path_factory, monkeypatch):
    """Redirect every real filesystem target away from ``~/.tunatale``.

    ⚠️ This exists because the tests DID escape into the user's home directory.
    ``TestFailureNotification`` patched ``_run`` (so no `open`/`osascript`
    actually fired) but NOT ``FAILURE_MARKER`` — so each full-suite run wrote a
    genuine-looking `~/.tunatale/BACKUP-FAILED.txt` claiming the nightly backup
    had failed. Caught 2026-08-12 by an end-of-session state check that found a
    failure marker sitting beside a launchd exit status of 0.

    A test suite that plants false alarms in the operator's alerting channel is
    worse than one that skips the assertion: the marker is only useful while it
    is trusted, and it took one contradiction to stop trusting it.

    Autouse and module-wide on purpose — an opt-in fixture is one a new test
    forgets, which is precisely how this happened.
    """
    home = tmp_path_factory.mktemp("fake-home")
    monkeypatch.setattr("backup_offbox.FAILURE_MARKER", home / "BACKUP-FAILED.txt")
    monkeypatch.setattr("backup_offbox.DEFAULT_STAGING", home / "offbox-staging")


@pytest.fixture
def secrets(monkeypatch):
    """Keychain lookups resolve, without touching the real Keychain.

    ⚠️ These values are spelled `not-a-real-…-test-fixture` ON PURPOSE. The
    earlier, more realistic-looking fakes (`s3cr3t-passphrase`, `APPKEY456`)
    tripped GitGuardian's Generic Password detector on commit 7423712 — and the
    line it flagged was the assertion that secrets DO NOT leak into the failure
    marker. No real credential was ever committed (verified against the live
    Keychain across `git log -p --all`: zero hits).

    Do not "improve" these back into realistic-looking secrets. A scanner that
    cries wolf on test fixtures is a scanner whose next alert gets ignored, and
    that alert might be the real one.
    """
    values = {
        (SERVICE_B2, ACCOUNT_KEY_ID): "not-a-real-b2-key-id-test-fixture",
        (SERVICE_B2, ACCOUNT_APP_KEY): "not-a-real-b2-app-key-test-fixture",
        (SERVICE_RESTIC, ACCOUNT_REPO_PASSWORD): "not-a-real-passphrase-test-fixture",
    }
    monkeypatch.setattr("backup_offbox.keychain_secret", lambda service, account: values[(service, account)])
    return values


# ── keychain ──────────────────────────────────────────────────────────────────


class TestKeychainSecret:
    def test_returns_the_password_without_its_trailing_newline(self, monkeypatch):
        monkeypatch.setattr("backup_offbox._run", FakeRun(0, stdout="not-a-real-password-test-fixture\n"))
        assert keychain_secret("svc", "acct") == "not-a-real-password-test-fixture"

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
        assert env["RESTIC_PASSWORD"] == "not-a-real-passphrase-test-fixture"
        assert env["B2_ACCOUNT_ID"] == "not-a-real-b2-key-id-test-fixture"
        assert env["B2_ACCOUNT_KEY"] == "not-a-real-b2-app-key-test-fixture"
        assert env["PATH"] == "/usr/bin", "must extend the caller's environment, not replace it"

    def test_secrets_are_not_written_back_into_the_process_environment(self, secrets, monkeypatch):
        import os

        monkeypatch.delenv("RESTIC_PASSWORD", raising=False)
        restic_env("b", "p", base=dict(os.environ))
        assert "RESTIC_PASSWORD" not in os.environ


class TestRedaction:
    def test_every_secret_value_is_masked(self):
        env = {
            "RESTIC_PASSWORD": "not-a-real-passphrase-test-fixture",
            "B2_ACCOUNT_KEY": "not-a-real-b2-app-key-test-fixture",
            "B2_ACCOUNT_ID": "not-a-real-b2-key-id-test-fixture",
            "RESTIC_REPOSITORY": "b2:my-bucket:tunatale",
        }
        shown = redacted(env)
        assert "not-a-real-passphrase-test-fixture" not in str(shown)
        assert "not-a-real-b2-app-key-test-fixture" not in str(shown)
        assert "not-a-real-b2-key-id-test-fixture" not in str(shown)
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


class TestStageAnkiCollection:
    def test_writes_a_dated_collection_and_returns_the_path(self, tmp_path):
        src = tmp_path / "collection.anki2"
        _make_collection(src)
        staging = tmp_path / "staging"

        written = stage_anki_collection(src, staging, today="2026-08-31")

        assert written == staging / "collection.2026-08-31.anki2"
        assert written.exists()

    def test_folds_in_an_open_wal(self, tmp_path):
        """Same freshness property as the DB half: committed rows sitting in a
        -wal must be in the snapshot."""
        src = tmp_path / "collection.anki2"
        _make_collection(src, rows=1)
        con = sqlite3.connect(src)
        con.execute("INSERT INTO notes (text) VALUES ('in-the-wal')")
        con.commit()
        try:
            written = stage_anki_collection(src, tmp_path / "staging", today="2026-08-31")
            with sqlite3.connect(written) as dst:
                count = dst.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            assert count == 2
        finally:
            con.close()

    def test_removes_stale_dated_collections(self, tmp_path):
        """A stale dated collection must not linger in staging — restic's own
        history supplies the depth, and a second 'newest' would be ambiguous."""
        src = tmp_path / "collection.anki2"
        _make_collection(src)
        staging = tmp_path / "staging"
        stage_anki_collection(src, staging, today="2026-08-20")

        stage_anki_collection(src, staging, today="2026-08-31")

        assert not (staging / "collection.2026-08-20.anki2").exists()
        assert (staging / "collection.2026-08-31.anki2").exists()

    def test_an_undated_collection_survives_the_sweep(self, tmp_path):
        """Same name-fence rationale as the .db sweep: only date-shaped names are
        ever a deletion candidate."""
        src = tmp_path / "collection.anki2"
        _make_collection(src)
        staging = tmp_path / "staging"
        stage_anki_collection(src, staging, today="2026-08-31")
        stray = staging / "collection.pre-v41.anki2"
        _make_collection(stray, rows=1)

        stage_anki_collection(src, staging, today="2026-08-31")

        assert stray.exists()

    def test_the_db_and_anki_sweeps_do_not_eat_each_other(self, tmp_path):
        """Oracle 4: neither sweep's glob may match the other's files."""
        db = tmp_path / "tunatale_no.db"
        _make_db(db)
        col = tmp_path / "collection.anki2"
        _make_collection(col)
        staging = tmp_path / "staging"
        stage_db_snapshots([db], staging, today="2026-08-31")

        stage_anki_collection(col, staging, today="2026-08-31")

        assert (staging / "tunatale_no.2026-08-31.db").exists()
        assert (staging / "collection.2026-08-31.anki2").exists()

    def test_a_missing_collection_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="collection.anki2"):
            stage_anki_collection(tmp_path / "collection.anki2", tmp_path / "staging", today="2026-08-31")


class TestStagingSidecarSweep:
    """``-wal`` / ``-shm`` must be pruned with — and only with — their snapshot.

    SQLite grows a sidecar the moment anything OPENS a staged snapshot: a restore
    drill, an ad-hoc ``sqlite3`` read. Neither sweep's glob matched them, so once
    one appeared nothing ever removed it and restic shipped it every night.

    The sweep is by NAME, not by walking the snapshots being deleted — mirroring
    ``db_backup._prune``, whose docstring records why: an interrupted writer can
    leave a sidecar whose base snapshot is already gone, and such an orphan is
    unreachable from the snapshot list forever (that is how 2026-07-16…21
    accumulated in the real backup dir).
    """

    @staticmethod
    def _touch(*paths):
        for p in paths:
            p.write_bytes(b"")

    def test_stale_anki_sidecars_go_with_their_snapshot(self, tmp_path):
        staging = tmp_path / "staging"
        src = tmp_path / "collection.anki2"
        _make_collection(src)
        stage_anki_collection(src, staging, today="2026-08-20")
        self._touch(
            staging / "collection.2026-08-20.anki2-wal",
            staging / "collection.2026-08-20.anki2-shm",
        )

        stage_anki_collection(src, staging, today="2026-08-31")

        assert not (staging / "collection.2026-08-20.anki2").exists()
        assert not (staging / "collection.2026-08-20.anki2-wal").exists()
        assert not (staging / "collection.2026-08-20.anki2-shm").exists()

    def test_an_orphaned_sidecar_is_still_swept(self, tmp_path):
        """No base snapshot — the case walking the deleted list would miss forever."""
        staging = tmp_path / "staging"
        src = tmp_path / "collection.anki2"
        _make_collection(src)
        stage_anki_collection(src, staging, today="2026-08-31")
        self._touch(staging / "collection.2026-08-20.anki2-wal")

        stage_anki_collection(src, staging, today="2026-08-31")

        assert not (staging / "collection.2026-08-20.anki2-wal").exists()

    def test_sqlite_clears_the_current_snapshots_sidecars_itself(self, tmp_path):
        """Why the "retained snapshot" case cannot arise in THIS flow.

        Measured 2026-08-31 while writing the sweep: a placeholder
        ``collection.{today}.anki2-wal`` is gone after a re-stage, and the sweep
        is not what removed it — ``snapshot_collection`` closes the destination
        connection, and SQLite checkpoints and unlinks the sidecars on a clean
        close. So by the time the sweep runs, the current snapshot has none.

        The implementation still refuses to sweep the current snapshot's
        sidecars. That guard is unreachable here and deliberately kept: it
        mirrors ``db_backup._prune``, where deleting a live ``-wal`` under a
        retained snapshot WOULD truncate real data, and it keeps this sweep
        correct if the ordering ever changes. This test pins the reason, so the
        guard does not later look like dead code and get removed.
        """
        staging = tmp_path / "staging"
        src = tmp_path / "collection.anki2"
        _make_collection(src)
        stage_anki_collection(src, staging, today="2026-08-31")
        self._touch(staging / "collection.2026-08-31.anki2-wal")

        stage_anki_collection(src, staging, today="2026-08-31")

        assert not (staging / "collection.2026-08-31.anki2-wal").exists()
        assert (staging / "collection.2026-08-31.anki2").exists()

    def test_an_undated_sidecar_survives(self, tmp_path):
        staging = tmp_path / "staging"
        src = tmp_path / "collection.anki2"
        _make_collection(src)
        stage_anki_collection(src, staging, today="2026-08-31")
        undated = staging / "collection.pre-v41.anki2-wal"
        self._touch(undated)

        stage_anki_collection(src, staging, today="2026-08-31")

        assert undated.exists()

    def test_stale_db_sidecars_go_with_their_snapshot(self, tmp_path):
        staging = tmp_path / "staging"
        db = tmp_path / "tunatale_no.db"
        _make_db(db)
        stage_db_snapshots([db], staging, today="2026-08-20")
        self._touch(
            staging / "tunatale_no.2026-08-20.db-wal",
            staging / "tunatale_no.2026-08-20.db-shm",
        )

        stage_db_snapshots([db], staging, today="2026-08-31")

        assert not (staging / "tunatale_no.2026-08-20.db-wal").exists()
        assert not (staging / "tunatale_no.2026-08-20.db-shm").exists()
        assert (staging / "tunatale_no.2026-08-31.db").exists()

    def test_neither_sidecar_sweep_reaches_the_others_files(self, tmp_path):
        """Same separation the snapshot sweeps have: .db-wal vs .anki2-wal."""
        staging = tmp_path / "staging"
        db = tmp_path / "tunatale_no.db"
        _make_db(db)
        col = tmp_path / "collection.anki2"
        _make_collection(col)
        stage_db_snapshots([db], staging, today="2026-08-31")
        stage_anki_collection(col, staging, today="2026-08-31")

        anki_side = staging / "collection.2026-08-20.anki2-wal"
        db_side = staging / "tunatale_no.2026-08-20.db-wal"
        self._touch(anki_side, db_side)

        stage_db_snapshots([db], staging, today="2026-08-31")
        assert not db_side.exists(), "the .db sweep did not take its own sidecar"
        assert anki_side.exists(), "the .db sweep reached into the .anki2 sidecars"

        # ⚠️ Re-create the .db sidecar BEFORE the anki sweep. Without this the db
        # sweep above has already removed it, so a too-wide anki glob has nothing
        # left to wrongly eat and the assertion below cannot fail. Sabotage-drilled
        # 2026-08-31: widening the anki sweep to `*.{DATE}.*` reddened nothing
        # until this line existed.
        self._touch(db_side)

        stage_anki_collection(col, staging, today="2026-08-31")
        assert not anki_side.exists(), "the .anki2 sweep did not take its own sidecar"
        assert db_side.exists(), "the .anki2 sweep reached into the .db sidecars"


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
                "--no-anki-collection",
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
                "--no-anki-collection",
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
                "--no-anki-collection",
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
                "--no-anki-collection",
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
                "--no-anki-collection",
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
                "--no-anki-collection",
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
                "--no-anki-collection",
            ]
        )

        assert rc != 0
        assert any("backup" in c for c in calls), "the upload itself must still have been attempted"


class TestBackupCommandAnkiCollection:
    def _argv(self, tmp_path, *, extra=()):
        db = tmp_path / "tunatale_no.db"
        _make_db(db)
        (tmp_path / "media").mkdir()
        (tmp_path / "output").mkdir()
        return [
            "backup",
            "--bucket",
            "b",
            "--db",
            str(db),
            "--media-src",
            str(tmp_path / "media"),
            "--output-src",
            str(tmp_path / "output"),
            "--staging",
            str(tmp_path / "staging"),
            *extra,
        ]

    def test_no_anki_collection_flag_stages_no_collection(self, tmp_path, secrets, monkeypatch):
        run = FakeRun(0)
        monkeypatch.setattr("backup_offbox._run", run)

        rc = main(self._argv(tmp_path, extra=["--no-anki-collection"]))

        assert rc == 0
        assert not list((tmp_path / "staging").glob("collection.*.anki2"))

    def test_default_stages_the_configured_collection(self, tmp_path, secrets, monkeypatch):
        run = FakeRun(0)
        monkeypatch.setattr("backup_offbox._run", run)
        col = tmp_path / "collection.anki2"
        _make_collection(col)
        monkeypatch.setattr("backup_offbox.settings.anki_collection_path", col)

        rc = main(self._argv(tmp_path))

        assert rc == 0
        assert list((tmp_path / "staging").glob("collection.*.anki2"))

    def test_missing_anki_collection_is_refused_before_any_upload(self, tmp_path, secrets, monkeypatch, capsys):
        run = FakeRun(0)
        monkeypatch.setattr("backup_offbox._run", run)

        rc = main(self._argv(tmp_path, extra=["--anki-collection", str(tmp_path / "nope.anki2")]))

        assert rc != 0
        assert "BACKUP FAILED" in capsys.readouterr().out
        assert not any("backup" in c for c in run.argvs), "must not upload a partial set"

        # ⚠️ The three assertions above ALSO pass if the collection is left out of
        # the pre-staging validation entirely — the run still fails, just later,
        # from stage_anki_collection. Sabotage-drilled 2026-08-31: dropping the
        # `sources.append(anki_collection)` line reddened nothing. What the check
        # actually buys is refusing BEFORE anything is written, which is the
        # "refuses to run at all" the script's own comment claims; without it,
        # stage_db_snapshots has already populated staging by the time we fail.
        staging = tmp_path / "staging"
        assert not list(staging.glob("*.db")), "staging was written before the source check refused"

    def test_a_non_database_collection_is_refused(self, tmp_path, secrets, monkeypatch, capsys):
        run = FakeRun(0)
        monkeypatch.setattr("backup_offbox._run", run)
        junk = tmp_path / "collection.anki2"
        junk.write_bytes(b"this is not a database" * 100)

        rc = main(self._argv(tmp_path, extra=["--anki-collection", str(junk)]))

        assert rc != 0
        assert "BACKUP FAILED" in capsys.readouterr().out
        assert not any("backup" in c for c in run.argvs)


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
            "--no-anki-collection",
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
        assert "not-a-real-passphrase-test-fixture" not in text
        assert "not-a-real-b2-app-key-test-fixture" not in text
        assert "not-a-real-b2-key-id-test-fixture" not in text

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


class TestDurableFailureMarker:
    """A notification is the wrong primary signal and this project proved it.

    Measured 2026-08-12: `osascript display notification` returned 0 from both a
    terminal and the LaunchAgent, and NEITHER banner appeared — macOS drops them
    silently when the calling process lacks notification permission, which is
    grantable, revocable, and invisible from inside the job.

    Worse, the mechanism is wrong even when it works: the job runs at 03:30. A
    transient banner posted while you are asleep is collected and gone. So the
    load-bearing signal is a FILE that persists until a backup actually
    succeeds, and the notification is downgraded to a bonus.
    """

    @pytest.fixture
    def marker(self, tmp_path, monkeypatch):
        path = tmp_path / "BACKUP-FAILED.txt"
        monkeypatch.setattr("backup_offbox.FAILURE_MARKER", path)
        return path

    def test_a_failure_leaves_a_marker_naming_the_cause(self, marker, monkeypatch):
        monkeypatch.setattr("backup_offbox._run", FakeRun(0))
        notify_failure("restic backup returned non-zero")
        assert marker.exists()
        assert "restic backup returned non-zero" in marker.read_text()

    def test_the_marker_says_when(self, marker, monkeypatch):
        """'The backup is broken' is much less useful than 'since when' — that
        is what tells you how many days of work are unprotected."""
        monkeypatch.setattr("backup_offbox._run", FakeRun(0))
        notify_failure("boom")
        assert re.search(r"\d{4}-\d{2}-\d{2}", marker.read_text())

    def test_the_marker_is_opened_so_it_is_seen(self, marker, monkeypatch):
        """Launching an app needs no permission, unlike posting a notification.
        A window that is still there in the morning beats a 3am banner."""
        run = FakeRun(0)
        monkeypatch.setattr("backup_offbox._run", run)
        notify_failure("boom")
        assert any(c[0] == "open" and str(marker) in c for c in run.argvs)

    def test_the_notification_is_still_attempted(self, marker, monkeypatch):
        """Kept as a bonus: free, and it works the moment permission is granted."""
        run = FakeRun(0)
        monkeypatch.setattr("backup_offbox._run", run)
        notify_failure("boom")
        assert any(c[0] == "osascript" for c in run.argvs)

    def test_the_marker_carries_no_secret(self, tmp_path, secrets, marker, monkeypatch):
        run = FakeRun(0)
        monkeypatch.setattr("backup_offbox._run", run)
        src = tmp_path / "tunatale_no.db"
        _make_db(src)
        (tmp_path / "media").mkdir()
        (tmp_path / "output").mkdir()
        monkeypatch.setattr(
            "backup_offbox._run", lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 1 if cmd[0] == "restic" else 0)
        )
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
                "--no-anki-collection",
                "--notify",
            ]
        )
        text = marker.read_text()
        for secret in (
            "not-a-real-passphrase-test-fixture",
            "not-a-real-b2-app-key-test-fixture",
            "not-a-real-b2-key-id-test-fixture",
        ):
            assert secret not in text

    def test_a_successful_backup_clears_a_stale_marker(self, tmp_path, secrets, marker, monkeypatch):
        """Otherwise the warning outlives the problem, and a signal that stays
        red after it is fixed is one you learn to ignore."""
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("failed last night")
        monkeypatch.setattr("backup_offbox._run", FakeRun(0))
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
                "--no-anki-collection",
            ]
        )

        assert rc == 0
        assert not marker.exists()

    def test_notify_never_raises(self, marker, monkeypatch):
        """Reporting a failure must not itself fail the job — the exit code and
        the log are the floor, and this runs after them."""

        def boom(cmd, *a, **k):
            raise OSError("no such binary")

        monkeypatch.setattr("backup_offbox._run", boom)
        monkeypatch.setattr("backup_offbox.FAILURE_MARKER", Path("/nonexistent-dir/x/marker.txt"))
        notify_failure("boom")  # must not raise


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
