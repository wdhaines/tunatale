"""Account management from the command line.

There is deliberately **no self-serve signup** in Phases 1–3, so this is how
every account comes into existence — including the first one, on a freshly
deployed box. See ``docs/deployment.md`` § "Creating the first account" for the
exact ``docker compose exec`` invocation.

    uv run python -m app.auth.cli create-user alice@example.com
    uv run python -m app.auth.cli set-password alice@example.com
    uv run python -m app.auth.cli list-users
    uv run python -m app.auth.cli deactivate-user alice@example.com

⚠️ **There is no ``--password`` flag, and adding one would be a security bug.**
A password in argv is visible in shell history, in ``ps`` output to every user
on the box, and in any process-accounting log. The only two sources are stdin
and the ``TT_AUTH_PASSWORD`` environment variable:

    read -rs NEW_PASSWORD                       # prompts, does not echo
    printf '%s\n' "$NEW_PASSWORD" | uv run python -m app.auth.cli create-user alice@example.com
    TT_AUTH_PASSWORD="$NEW_PASSWORD" uv run python -m app.auth.cli create-user alice@example.com

The examples read a shell variable rather than showing a literal on purpose: a
runbook that prints ``TT_AUTH_PASSWORD=hunter2`` invites the reader to paste a
real password into the exact shell history this module exists to keep it out
of. (It also trips secret scanners, which is the cheap half of the reason.)

An interactive terminal is prompted via ``getpass``, which does not echo.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Mapping
from typing import TextIO

from app.auth.database import AuthDatabase, EmailExistsError
from app.config import settings

#: Environment variable read as the password when set and non-empty.
PASSWORD_ENV = "TT_AUTH_PASSWORD"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BAD_INPUT = 2


class InputError(Exception):
    """The operator gave us something unusable — reported, never traced."""


def read_password(env: Mapping[str, str], stdin: TextIO) -> str:
    """Obtain a password from the environment, a pipe, or an interactive prompt.

    Never from argv — see this module's docstring. The precedence is
    environment first so that an automated deploy can supply one without a
    pipe, then the tty prompt, then a piped line.
    """
    from_env = env.get(PASSWORD_ENV)
    if from_env:
        return from_env
    if stdin.isatty():
        return getpass.getpass("Password: ")
    password = stdin.readline().rstrip("\n")
    if not password:
        raise InputError(f"no password supplied — pipe one on stdin or set {PASSWORD_ENV}")
    return password


def _open_db() -> AuthDatabase:
    return AuthDatabase(settings.auth_database_url)


def _cmd_create_user(args: argparse.Namespace, env: Mapping[str, str], stdin: TextIO, out: TextIO) -> int:
    """Create an account. Refuses to touch an email that already exists.

    The refusal matters more than it looks: the obvious alternative — an
    upsert — turns a typo'd ``create-user`` on an existing address into a
    silent password reset for that account, which is an account takeover
    wearing a convenience feature's clothes.
    """
    password = read_password(env, stdin)
    db = _open_db()
    try:
        user = db.create_user(args.email, password)
    except EmailExistsError:
        print(
            f"create-user: {args.email!r} already exists — use set-password to change its password",
            file=sys.stderr,
        )
        return EXIT_FAILED
    finally:
        db.close()
    print(f"created user {user.id}: {user.email}", file=out)
    return EXIT_OK


def _cmd_set_password(args: argparse.Namespace, env: Mapping[str, str], stdin: TextIO, out: TextIO) -> int:
    """Change an existing account's password, logging its sessions out.

    The session invalidation is ``AuthDatabase.set_password``'s doing, not
    this command's, and it is the point: changing a password because it may
    have leaked is useless if the sessions opened with it keep working.
    """
    password = read_password(env, stdin)
    db = _open_db()
    try:
        user = db.get_user_by_email(args.email)
        if user is None:
            print(f"set-password: no user with email {args.email!r}", file=sys.stderr)
            return EXIT_FAILED
        db.set_password(user.id, password)
    finally:
        db.close()
    print(f"password updated for {user.email}; existing sessions were revoked", file=out)
    return EXIT_OK


def _cmd_list_users(args: argparse.Namespace, env: Mapping[str, str], stdin: TextIO, out: TextIO) -> int:
    """List accounts. Never prints a password hash."""
    db = _open_db()
    try:
        users = db.list_users()
    finally:
        db.close()
    if not users:
        print("no users", file=out)
        return EXIT_OK
    for user in users:
        state = "active" if user.is_active else "INACTIVE"
        print(f"{user.id}\t{user.email}\t{state}\t{user.created_at.isoformat()}", file=out)
    return EXIT_OK


def _cmd_deactivate_user(args: argparse.Namespace, env: Mapping[str, str], stdin: TextIO, out: TextIO) -> int:
    """Deactivate an account and revoke its sessions.

    Deactivation rather than deletion: the account's sessions and its identity
    stay referenceable, and there is no cascade to reason about. Reactivating
    is ``set_active(True)``, which this CLI deliberately does not expose —
    re-enabling an account someone disabled should take more thought than
    retrieving a command from shell history.
    """
    db = _open_db()
    try:
        user = db.get_user_by_email(args.email)
        if user is None:
            print(f"deactivate-user: no user with email {args.email!r}", file=sys.stderr)
            return EXIT_FAILED
        db.set_active(user.id, False)
    finally:
        db.close()
    print(f"deactivated {user.email}; its sessions were revoked", file=out)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.auth.cli",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create-user", help="create an account (password from stdin or $TT_AUTH_PASSWORD)")
    p_create.add_argument("email")
    p_create.set_defaults(handler=_cmd_create_user)

    p_set = sub.add_parser("set-password", help="change a password and revoke that account's sessions")
    p_set.add_argument("email")
    p_set.set_defaults(handler=_cmd_set_password)

    p_list = sub.add_parser("list-users", help="list accounts")
    p_list.set_defaults(handler=_cmd_list_users)

    p_deactivate = sub.add_parser("deactivate-user", help="deactivate an account and revoke its sessions")
    p_deactivate.add_argument("email")
    p_deactivate.set_defaults(handler=_cmd_deactivate_user)

    return parser


def main(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    out: TextIO | None = None,
) -> int:
    """Entry point. ``env``/``stdin``/``out`` are injectable so tests need no mocks."""
    args = build_parser().parse_args(argv)
    try:
        return args.handler(
            args, os.environ if env is None else env, sys.stdin if stdin is None else stdin, out or sys.stdout
        )
    except InputError as exc:
        print(f"{args.command}: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT


if __name__ == "__main__":  # pragma: no cover - CLI entry guard
    sys.exit(main())
