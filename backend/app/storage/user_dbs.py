"""Per-user language databases: identity routes to FILES, never to a column.

The OWNER is the account the deployment grew up with, and it keeps resolving to
the flat per-language DBs in ``settings.database_urls`` — nothing of theirs
moves. Every other account resolves to ``<root>/<user id>/tunatale_<code>.db``.
The SRS and queue SQL is untouched, which is the whole point of doing it this
way rather than with a ``user_id`` predicate: nothing in the Anki-parity surface
has to be re-verified (tunatale-3k8).

A user HAS a language exactly when that file exists. This module never creates
one — ``SRSDatabase`` and ``ContentStore`` both create a missing file on open, so
an unguarded open would silently enrol anyone in any language with an empty
deck. Seeding a user's deck is a deliberate act done elsewhere.

No LRU cap and no close-on-evict, deliberately. The bead asked for both, on the
premise that each open DB holds a file handle. It does not: a file-backed
``SRSDatabase`` / ``ContentStore`` opens a fresh sqlite connection per operation
(``_file_conn``) and holds nothing between them, and their ``close()`` is a no-op
for files. So a cached instance costs a few Python objects, not a descriptor,
and caching them forever leaks nothing (pinned by a test that counts the
process's open descriptors on the file). Evicting would ALSO be unsafe: a
Starlette background task — ``_complete_listen_media`` after a listen — keeps
using the request's ``srs_db`` after the response, i.e. after any per-request
lease would have been released.
"""

from __future__ import annotations

import threading
from pathlib import Path

from app.auth.database import AuthDatabase
from app.srs.database import SRSDatabase
from app.storage.db_backup import rotate_db_backups
from app.storage.store import ContentStore


def owner_user_id(auth_db: AuthDatabase | None, owner_email: str) -> int | None:
    """The id of the account that owns the flat DBs, or None for nobody.

    ``owner_email`` names it when set. Unset, the owner is the FIRST account
    ever created (lowest id) — which is the single-user deployment's only
    account, so it keeps its data. That default is fail-safe in the direction
    that matters: a second account created later is never the owner by accident
    and never reads the owner's reviews. A named owner with no such account
    means nobody is the owner, so everyone is routed to their own files.
    """
    if auth_db is None:
        return None
    if owner_email:
        user = auth_db.get_user_by_email(owner_email)
        return None if user is None else user.id
    users = auth_db.list_users()
    return min((u.id for u in users), default=None)


def pipeline_user_id(state) -> int | None:
    """Whose stores a request's LessonPipeline work uses: None = the owner's flat DBs.

    ``state`` is ``request.state`` as ``main._resolve_language_state`` bound it.
    Fails CLOSED: a request that is not the owner's and carries no account id
    raises, rather than returning None and generating into the owner's store.
    """
    if getattr(state, "is_owner", False):
        return None
    user_id = getattr(state, "user_id", None)
    if user_id is None:
        raise RuntimeError("lesson work for a request with no account")
    return user_id


def user_data_root(owner_db_url: str, override: Path | None) -> Path:
    """Where non-owner accounts' decks live: ``override``, else beside the owner's DB.

    "Beside" puts the per-user files on whatever volume the owner's already are
    (the container's /data, the laptop instance's ~/TunaTaleLive/data) with no
    extra setting to forget. See ``settings.user_data_dir``.
    """
    if override is not None:
        return override
    return Path(owner_db_url.removeprefix("sqlite:///")).parent / "users"


class UserDatabases:
    """Lazily opened (SRSDatabase, ContentStore) pairs for non-owner accounts."""

    def __init__(
        self,
        root: Path,
        codes: list[str],
        *,
        backup_dir: Path | None = None,
        backup_keep_days: int = 7,
        migration_backup_dir: Path | None = None,
    ) -> None:
        self._root = Path(root)
        # Order matters: a user's first language is their default.
        self._codes = list(codes)
        self._backup_dir = backup_dir
        self._backup_keep_days = backup_keep_days
        self._migration_backup_dir = migration_backup_dir
        self._open: dict[tuple[int, str], tuple[SRSDatabase, ContentStore]] = {}
        self._lock = threading.Lock()

    def path_for(self, user_id: int, code: str) -> Path:
        """Where ``user_id``'s deck for ``code`` lives. Refuses anything unsafe.

        ``user_id`` must be a real int (a bool is an int in Python, and ``True``
        would name user 1's directory) and ``code`` must be a configured
        language, so neither can smuggle a path separator in.
        """
        if type(user_id) is not int or user_id < 1:
            raise ValueError(f"not a user id: {user_id!r}")
        if code not in self._codes:
            raise ValueError(f"not a configured language: {code!r}")
        return self._root / str(user_id) / f"tunatale_{code}.db"

    def languages_for(self, user_id: int) -> list[str]:
        """The configured languages ``user_id`` has a deck for, in config order."""
        return [code for code in self._codes if self.path_for(user_id, code).is_file()]

    def get(self, user_id: int, code: str) -> tuple[SRSDatabase, ContentStore] | None:
        """The user's (SRSDatabase, ContentStore) for ``code``, or None.

        None when the user has no deck for it — never an empty new one.
        """
        path = self.path_for(user_id, code)
        key = (user_id, code)
        with self._lock:
            opened = self._open.get(key)
            if opened is not None:
                return opened
            if not path.is_file():
                return None
            per_user = str(user_id)
            if self._backup_dir is not None:
                # Beside the owner's snapshots but in their own directory:
                # backups are named by file stem, and every user's Cebuano deck
                # is tunatale_ceb.db.
                rotate_db_backups([str(path)], self._backup_dir / "users" / per_user, keep_days=self._backup_keep_days)
            migration_dir = (
                None if self._migration_backup_dir is None else self._migration_backup_dir / "users" / per_user
            )
            opened = (SRSDatabase(str(path), pre_migration_backup_dir=migration_dir), ContentStore(str(path)))
            self._open[key] = opened
            return opened
