"""AuthDatabase — identity storage in a standalone SQLite database.

Identity is per-user, not per-language, so it gets its own database rather
than living inside a per-language content DB.  Connection handling mirrors
``app.srs.db_base.SRSDatabaseBase`` — the ~10 lines of ``_configure_connection``
are deliberately copied rather than imported, to keep identity storage
independent of the SRS package.

There is deliberately **no migration-function registry**.  The SRS package has
one because it has 43 versions of history; auth has one version, and a registry
with no entries is machinery whose tests would only shadow themselves.  Add it
when there is a v2.
"""

from __future__ import annotations

import secrets
import sqlite3
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path

from app.auth.models import Session, User
from app.auth.passwords import hash_password, needs_rehash, verify_password
from app.auth.tokens import hash_token, mint_token

SCHEMA_VERSION = 1


@cache
def _dummy_hash() -> str:
    """An argon2 hash to verify against when the email is unknown.

    Computed once per process from a random string, rather than stored as a
    literal, for two reasons:

    - Its parameters always match ``PasswordHasher()``'s current defaults. A
      pasted literal silently stops matching when argon2-cffi changes them, and
      the timing equalisation it exists for drifts away without any test
      noticing.
    - A hardcoded ``$argon2id$…`` string is what secret scanners match on.
      GitGuardian's "Generic Password" detector already failed a build over a
      throwaway PHC literal in this package's tests (false positive, but a real
      cost in noise).

    ``@cache`` and not a module-level call: computing this at import would put
    ~50 ms of argon2 work into every process start and every test collection.
    The first unknown-email login pays it instead, once.
    """
    return hash_password(secrets.token_urlsafe(32))


class EmailExistsError(ValueError):
    """Raised when attempting to create a user with an already-registered email."""


class SchemaTooNewError(RuntimeError):
    """Raised when the database schema version is newer than this code expects."""


# ── Schema DDL ───────────────────────────────────────────────────────────────

_CREATE_USERS = """\
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1
)
"""

_CREATE_SESSIONS = """\
CREATE TABLE IF NOT EXISTS sessions (
    token_hash   TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
)
"""

_CREATE_INDEXES = """\
CREATE INDEX IF NOT EXISTS idx_sessions_user    ON sessions(user_id)
"""
_CREATE_INDEXES_EXPIRES = """\
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)
"""

# Journal modes that need no write: ``wal`` is already what we want, and
# ``memory`` is what a ``:memory:`` connection reports — it cannot become WAL.
_JOURNAL_MODES_TO_KEEP = ("wal", "memory")


# ── Connection helper (mirrors app.srs.db_base._configure_connection) ────────


def _configure_connection(conn: sqlite3.Connection) -> None:
    """Apply the standard pragmas to a fresh SQLite connection.

    Copied from ``app.srs.db_base._configure_connection`` rather than imported,
    so identity storage has no dependency on the SRS package.
    """
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() not in _JOURNAL_MODES_TO_KEEP:
        with suppress(sqlite3.OperationalError):
            conn.execute("PRAGMA journal_mode = WAL")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _to_iso(dt: datetime) -> str:
    """Format a datetime as an ISO-8601 string **normalised to UTC**.

    The ``astimezone`` is load-bearing, not cosmetic. ``purge_expired_sessions``
    compares ``expires_at`` as a STRING in SQL, and a lexicographic compare of
    ISO-8601 is only sound when every value carries the same offset. Preserving
    the caller's offset instead made the two expiry paths disagree about the
    same instant: with ``now`` expressed at -01:00, ``get_session`` reported a
    session expired (it parses and compares datetimes) while
    ``purge_expired_sessions`` deleted nothing, because the string
    ``"…T14:50-01:00"`` sorts below ``"…T15:20+00:00"``. Normalising on the way
    in makes the string order agree with the instant order.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _from_iso(value: str) -> datetime:
    """Parse an ISO-8601 string, promoting naive values to UTC."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _normalize_email(email: str) -> str:
    """Normalise an email address (strip + lowercase).

    SQLite's ``COLLATE NOCASE`` folds ASCII only; email normalisation is the
    store's job, done in Python on every write and every lookup.
    """
    return email.strip().lower()


# ── AuthDatabase ─────────────────────────────────────────────────────────────


class AuthDatabase:
    """SQLite-backed identity store.

    Use ``":memory:"`` as *db_path* for in-memory test databases.
    """

    def close(self) -> None:
        """Explicitly close the in-memory connection."""
        if self._in_memory and self._conn is not None:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> AuthDatabase:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __init__(self, db_path: str = ":memory:") -> None:
        self._path: str | None = None
        self._in_memory = db_path == ":memory:"
        if self._in_memory:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            _configure_connection(self._conn)
            self._init_schema(self._conn)
        else:
            if db_path.startswith("sqlite:///"):
                db_path = db_path[10:]
            path = Path(db_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._path = str(path)
            self._conn = None
            with self._file_conn() as conn:
                self._init_schema(conn)

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            msg = f"Schema version {version} is newer than expected {SCHEMA_VERSION}"
            raise SchemaTooNewError(msg)
        conn.execute(_CREATE_USERS)
        conn.execute(_CREATE_SESSIONS)
        conn.execute(_CREATE_INDEXES)
        conn.execute(_CREATE_INDEXES_EXPIRES)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()

    @contextmanager
    def _file_conn(self):  # type: ignore[no-untyped-def]
        conn = sqlite3.connect(self._path, check_same_thread=False)  # type: ignore[arg-type]
        conn.row_factory = sqlite3.Row
        _configure_connection(conn)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def _get_conn(self):  # type: ignore[no-untyped-def]
        if self._in_memory:
            yield self._conn
        else:
            with self._file_conn() as conn:
                yield conn

    def _commit(self, conn: sqlite3.Connection) -> None:
        if self._in_memory:
            conn.commit()

    # ── User methods ─────────────────────────────────────────────────────

    def create_user(self, email: str, password: str) -> User:
        """Create a new user. Raises ``EmailExistsError`` on duplicate email."""
        norm = _normalize_email(email)
        hashed = hash_password(password)
        with self._get_conn() as conn:
            now = datetime.now(UTC)
            try:
                conn.execute(
                    "INSERT INTO users (email, password_hash, created_at, is_active) VALUES (?, ?, ?, 1)",
                    (norm, hashed, _to_iso(now)),
                )
                self._commit(conn)
            except sqlite3.IntegrityError:
                raise EmailExistsError(f"Email {norm!r} already registered") from None
            row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            return User(
                id=row_id,
                email=norm,
                password_hash=hashed,
                created_at=now,
                is_active=True,
            )

    def get_user_by_id(self, user_id: int) -> User | None:
        """Return the user with the given id, or ``None``."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                return None
            return self._row_to_user(row)

    def get_user_by_email(self, email: str) -> User | None:
        """Return the user with the given email, or ``None``.

        Case- and whitespace-insensitive.
        """
        norm = _normalize_email(email)
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (norm,)).fetchone()
            if row is None:
                return None
            return self._row_to_user(row)

    def list_users(self) -> list[User]:
        """Every account, oldest first. Used by the bootstrap CLI.

        Returns full ``User`` objects, ``password_hash`` included, because that
        is what the row is — it is the CALLER's job not to print it, and
        ``cli.py`` does not.
        """
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
            return [self._row_to_user(row) for row in rows]

    def set_password(self, user_id: int, password: str) -> None:
        """Set a new password for *user_id* and delete that user's sessions."""
        with self._get_conn() as conn:
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            self._commit(conn)

    def set_active(self, user_id: int, is_active: bool) -> None:
        """Activate or deactivate a user.

        Deactivating also deletes that user's sessions.
        """
        with self._get_conn() as conn:
            conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (int(is_active), user_id))
            if not is_active:
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            self._commit(conn)

    def verify_credentials(self, email: str, password: str) -> User | None:
        """Verify email + password and return the user, or ``None``.

        Timing-equalised: always runs ``verify_password`` against
        a dummy hash for unknown emails, and checks ``is_active`` only
        after password verification succeeds.  This reduces an obvious timing
        signal; it is not a proof of constant time, and no unit test in this
        suite establishes that.
        """
        norm = _normalize_email(email)
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (norm,)).fetchone()
            if row is None:
                verify_password(_dummy_hash(), password)
                return None
            if not verify_password(row["password_hash"], password):
                return None
            user = self._row_to_user(row)
            if not user.is_active:
                return None
            if needs_rehash(row["password_hash"]):
                new_hash = hash_password(password)
                conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user.id))
                self._commit(conn)
                return User(
                    id=user.id,
                    email=user.email,
                    password_hash=new_hash,
                    created_at=user.created_at,
                    is_active=user.is_active,
                )
            return user

    # ── Session methods ──────────────────────────────────────────────────

    def create_session(self, user_id: int, *, ttl: timedelta | None = None) -> tuple[str, Session]:
        """Create a session and return ``(plaintext_token, Session)``.

        The plaintext is returned **once and never stored**; the row holds
        only ``hash_token(token)``.  ``ttl=None`` means
        ``timedelta(days=settings.session_ttl_days)``.
        """
        if ttl is None:
            from app.config import settings

            ttl = timedelta(days=settings.session_ttl_days)
        token = mint_token()
        token_h = hash_token(token)
        now = datetime.now(UTC)
        expires = now + ttl
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
                (token_h, user_id, _to_iso(now), _to_iso(expires), _to_iso(now)),
            )
            self._commit(conn)
        session = Session(
            token_hash=token_h,
            user_id=user_id,
            created_at=now,
            expires_at=expires,
            last_seen_at=now,
        )
        return token, session

    def get_session(self, token: str, *, now: datetime | None = None) -> Session | None:
        """Return the session for *token*, or ``None`` if unknown or expired.

        Does not delete the expired row; ``purge_expired_sessions`` does that.
        """
        if now is None:
            now = datetime.now(UTC)
        now = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
        token_h = hash_token(token)
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE token_hash = ?", (token_h,)).fetchone()
            if row is None:
                return None
            expires = _from_iso(row["expires_at"])
            if expires <= now:
                return None
            return Session(
                token_hash=row["token_hash"],
                user_id=row["user_id"],
                created_at=_from_iso(row["created_at"]),
                expires_at=expires,
                last_seen_at=_from_iso(row["last_seen_at"]),
            )

    def touch_session(self, token: str, *, now: datetime | None = None) -> None:
        """Update ``last_seen_at`` for *token*.  No-op if unknown."""
        if now is None:
            now = datetime.now(UTC)
        now = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
        token_h = hash_token(token)
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?",
                (_to_iso(now), token_h),
            )
            self._commit(conn)

    def delete_session(self, token: str) -> None:
        """Delete the session for *token*.  No-op if unknown."""
        token_h = hash_token(token)
        with self._get_conn() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_h,))
            self._commit(conn)

    def delete_sessions_for_user(self, user_id: int) -> int:
        """Delete all sessions for *user_id* and return the count removed."""
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            self._commit(conn)
            return cursor.rowcount

    def purge_expired_sessions(self, *, now: datetime | None = None) -> int:
        """Delete all expired sessions and return the count removed."""
        if now is None:
            now = datetime.now(UTC)
        now = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (_to_iso(now),))
            self._commit(conn)
            return cursor.rowcount

    # ── Internal helpers ─────────────────────────────────────────────────

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> User:
        return User(
            id=row["id"],
            email=row["email"],
            password_hash=row["password_hash"],
            created_at=_from_iso(row["created_at"]),
            is_active=bool(row["is_active"]),
        )
