#!/usr/bin/env python
"""Give a second learner the owner's deck for one language: same cards, never studied.

    uv run python scripts/seed_user_deck.py --language ceb --email learner@example.com           # dry run
    uv run python scripts/seed_user_deck.py --language ceb --email learner@example.com --apply

The account must already exist (``python -m app.auth.cli create-user``) and must
not be the owner. The source is the owner's deck for the language, opened
read-only; the copy is written where the app will look for that account
(``app.storage.user_dbs``) and it refuses to overwrite one that is there. What
is kept, reset and dropped — and why — is in ``app.srs.user_deck_seed``.

Run it on the LIVE side (``./switch.sh status``) under that side's env, so
``DATABASE_URLS`` and ``AUTH_DATABASE_URL`` name the live files.
"""

from __future__ import annotations

import argparse
import sys

from app.auth.database import AuthDatabase
from app.config import settings
from app.languages import resolve_db_path, resolve_language_context
from app.srs.user_deck_seed import seed_user_deck
from app.storage.user_dbs import UserDatabases, owner_user_id, user_data_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--language", required=True)
    parser.add_argument("--email", required=True, help="the learner's account")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args(argv)

    if args.language not in settings.database_urls:
        # resolve_language_context falls back to the SINGULAR database_url for a
        # code it does not know, which would seed the learner with some other
        # language's deck. Almost always: not run under the live side's env.
        print(f"{args.language!r} is not in DATABASE_URLS here — run under the live side's env", file=sys.stderr)
        return 1
    context = resolve_language_context(args.language, settings)
    source = resolve_db_path(args.language, settings)
    auth_db = AuthDatabase(settings.auth_database_url)
    user = auth_db.get_user_by_email(args.email)
    if user is None:
        print(f"No account {args.email!r} — create it first with: python -m app.auth.cli create-user", file=sys.stderr)
        return 1
    if user.id == owner_user_id(auth_db, settings.owner_email):
        print(f"{args.email} is the OWNER, whose deck is the source — refusing", file=sys.stderr)
        return 1
    root = user_data_root(context.db_url, settings.user_data_dir)
    dest = UserDatabases(root, [args.language]).path_for(user.id, args.language)

    print(f"source: {source}\ndest:   {dest}  (user {user.id}, {args.email})")
    if dest.exists():
        print("REFUSING: the destination already exists", file=sys.stderr)
        return 1
    if not args.apply:
        print("Dry run. Re-run with --apply to write it.")
        return 0
    report = seed_user_deck(source, dest)
    print(
        f"Seeded {report.cards} cards ({report.directions} directions): {report.moved_to_front} moved to the front"
        f" in first-review order, {report.suspended} suspended directions kept suspended; dropped"
        f" {report.revlog_dropped} reviews, {report.lessons_dropped} lessons, {report.curricula_dropped} curricula."
    )
    if report.new_without_position:
        print(f"Note: {report.new_without_position} new directions have no position and will be served first.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
