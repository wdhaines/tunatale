"""Tests for the account bootstrap CLI.

Everything here runs against a real ``auth.db`` under ``tmp_path`` with
``settings.auth_database_url`` monkeypatched at it — no mocking of ``app.*``.
``main()`` takes ``env``/``stdin``/``out`` explicitly so a test supplies them as
plain objects rather than patching ``os.environ`` or ``sys.stdin``.
"""

from __future__ import annotations

import io

import pytest

from app.auth.cli import PASSWORD_ENV, InputError, build_parser, main, read_password
from app.auth.database import AuthDatabase
from app.config import settings

EMAIL = "alice@example.com"
PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def auth_db_path(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Point the CLI at a throwaway auth.db and hand back its path."""
    path = tmp_path / "auth.db"
    monkeypatch.setattr(settings, "auth_database_url", f"sqlite:///{path}")
    return path


def run(argv: list[str], *, stdin: str = "", env: dict[str, str] | None = None) -> tuple[int, str]:
    """Invoke the CLI, returning (exit code, stdout)."""
    out = io.StringIO()
    code = main(argv, env=env or {}, stdin=io.StringIO(stdin), out=out)
    return code, out.getvalue()


def store(path) -> AuthDatabase:
    return AuthDatabase(str(path))


class TestPasswordNeverComesFromArgv:
    """The decisive oracle: a password in argv leaks to `ps` and shell history."""

    @pytest.mark.parametrize("command", ["create-user", "set-password"])
    def test_no_password_option_exists(self, command: str) -> None:
        """There must be no --password flag to misuse.

        Asserted against the parser rather than the docs, because this is the
        kind of convenience someone adds in a hurry at 2am on a broken deploy.
        Rejecting the flag at parse time is what makes the leak impossible
        rather than merely discouraged.
        """
        parser = build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args([command, EMAIL, "--password", PASSWORD])
        assert exc.value.code == 2

    def test_positional_password_is_rejected(self) -> None:
        """A bare second positional must not be silently accepted as a password."""
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["create-user", EMAIL, PASSWORD])


class TestReadPassword:
    def test_env_wins(self) -> None:
        assert read_password({PASSWORD_ENV: "from-env"}, io.StringIO("from-stdin\n")) == "from-env"

    def test_empty_env_falls_through_to_stdin(self) -> None:
        """An exported-but-empty variable is unset, not a blank password."""
        assert read_password({PASSWORD_ENV: ""}, io.StringIO("from-stdin\n")) == "from-stdin"

    def test_stdin_line_is_stripped_of_its_newline_only(self) -> None:
        """Trailing spaces are kept — they are legal password characters."""
        assert read_password({}, io.StringIO("  spaced  \n")) == "  spaced  "

    def test_empty_stdin_raises(self) -> None:
        with pytest.raises(InputError):
            read_password({}, io.StringIO(""))

    def test_tty_is_prompted_without_echo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An interactive operator gets getpass, which does not echo."""
        import getpass as getpass_module

        monkeypatch.setattr(getpass_module, "getpass", lambda prompt="": "typed-in")

        class _Tty(io.StringIO):
            def isatty(self) -> bool:
                return True

        assert read_password({}, _Tty()) == "typed-in"


class TestCreateUser:
    def test_creates_and_password_verifies(self, auth_db_path) -> None:
        code, out = run(["create-user", EMAIL], stdin=f"{PASSWORD}\n")
        assert code == 0
        assert EMAIL in out
        with store(auth_db_path) as db:
            assert db.verify_credentials(EMAIL, PASSWORD) is not None

    def test_password_from_env(self, auth_db_path) -> None:
        code, _ = run(["create-user", EMAIL], env={PASSWORD_ENV: PASSWORD})
        assert code == 0
        with store(auth_db_path) as db:
            assert db.verify_credentials(EMAIL, PASSWORD) is not None

    def test_duplicate_email_fails_WITHOUT_resetting_the_password(self, auth_db_path) -> None:
        """The second decisive oracle.

        An upsert here would turn a typo'd create-user into a silent password
        reset on a live account — takeover wearing a convenience feature's
        clothes. So this asserts more than the exit code: the ORIGINAL password
        must still work afterwards, and the attempted new one must not.
        """
        assert run(["create-user", EMAIL], stdin=f"{PASSWORD}\n")[0] == 0
        code, _ = run(["create-user", EMAIL], stdin="attacker-chosen\n")
        assert code == 1
        with store(auth_db_path) as db:
            assert db.verify_credentials(EMAIL, PASSWORD) is not None
            assert db.verify_credentials(EMAIL, "attacker-chosen") is None

    def test_empty_stdin_is_exit_2(self, auth_db_path) -> None:
        code, _ = run(["create-user", EMAIL], stdin="")
        assert code == 2
        with store(auth_db_path) as db:
            assert db.get_user_by_email(EMAIL) is None


class TestSetPassword:
    def test_changes_password_and_revokes_sessions(self, auth_db_path) -> None:
        run(["create-user", EMAIL], stdin=f"{PASSWORD}\n")
        with store(auth_db_path) as db:
            user = db.get_user_by_email(EMAIL)
            token, _ = db.create_session(user.id)

        code, out = run(["set-password", EMAIL], stdin="new-password\n")
        assert code == 0
        assert "revoked" in out
        with store(auth_db_path) as db:
            assert db.verify_credentials(EMAIL, PASSWORD) is None
            assert db.verify_credentials(EMAIL, "new-password") is not None
            assert db.get_session(token) is None, "a leaked password's sessions outlived the reset"

    def test_unknown_email_fails(self, auth_db_path) -> None:
        code, _ = run(["set-password", "nobody@example.com"], stdin="pw\n")
        assert code == 1


class TestListUsers:
    def test_empty(self, auth_db_path) -> None:
        code, out = run(["list-users"])
        assert code == 0
        assert out.strip() == "no users"

    def test_lists_and_marks_inactive(self, auth_db_path) -> None:
        run(["create-user", EMAIL], stdin=f"{PASSWORD}\n")
        run(["create-user", "bob@example.com"], stdin=f"{PASSWORD}\n")
        run(["deactivate-user", "bob@example.com"])
        code, out = run(["list-users"])
        assert code == 0
        lines = out.strip().splitlines()
        assert len(lines) == 2
        assert "active" in lines[0] and "INACTIVE" not in lines[0]
        assert "INACTIVE" in lines[1]

    def test_never_prints_a_password_hash(self, auth_db_path) -> None:
        """`list-users` is the command most likely to be pasted into a ticket."""
        run(["create-user", EMAIL], stdin=f"{PASSWORD}\n")
        _, out = run(["list-users"])
        assert "$argon2" not in out
        with store(auth_db_path) as db:
            assert db.get_user_by_email(EMAIL).password_hash not in out


class TestDeactivateUser:
    def test_deactivates_and_revokes_sessions(self, auth_db_path) -> None:
        run(["create-user", EMAIL], stdin=f"{PASSWORD}\n")
        with store(auth_db_path) as db:
            user = db.get_user_by_email(EMAIL)
            token, _ = db.create_session(user.id)

        code, out = run(["deactivate-user", EMAIL])
        assert code == 0
        assert "revoked" in out
        with store(auth_db_path) as db:
            assert db.verify_credentials(EMAIL, PASSWORD) is None
            assert db.get_session(token) is None

    def test_unknown_email_fails(self, auth_db_path) -> None:
        assert run(["deactivate-user", "nobody@example.com"])[0] == 1

    def test_there_is_no_reactivate_command(self) -> None:
        """Re-enabling a disabled account should take more thought than ↑-Enter."""
        with pytest.raises(SystemExit):
            build_parser().parse_args(["activate-user", EMAIL])
