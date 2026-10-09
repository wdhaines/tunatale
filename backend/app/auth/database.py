"""AuthDatabase — identity storage in a standalone SQLite database.

Identity is per-user, not per-language, so it gets its own database rather
than living inside a per-language content DB.  Connection handling mirrors
``app.srs.db_base.SRSDatabaseBase`` — the ~10 lines of ``_configure_connection``
are deliberately copied rather than imported, to keep identity storage
independent of the SRS package.

There is deliberately **no migration-function registry**.  The SRS package has
one because it has 43 versions of history; auth has one version, and a registry
with no entries is machinery whose tests would only shadow themselves.

There is now a v2 (``login_attempts``, for login throttling) and a v3
(``invites``, for invite-token registration) and neither needs a registry,
because the change is a pure *addition*: the
``CREATE TABLE IF NOT EXISTS`` run at every open **is** the migration, and it is
idempotent for a fresh database and an existing one alike.  The first change
requiring an ``ALTER`` is what would force a registry.
"""

from __future__ import annotations

import secrets
import sqlite3
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path

from app.auth.models import Invite, Session, User
from app.auth.passwords import hash_password, needs_rehash, verify_password
from app.auth.tokens import hash_token, mint_token

SCHEMA_VERSION = 3


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


class InvalidInviteError(ValueError):
    """Raised when an invite is unknown, expired, or already redeemed."""


class NoAccountsError(RuntimeError):
    """Raised when minting an invite on a store that has no accounts yet."""


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

_CREATE_LOGIN_ATTEMPTS = """\
CREATE TABLE IF NOT EXISTS login_attempts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scope        TEXT NOT NULL,
    subject      TEXT NOT NULL,
    attempted_at TEXT NOT NULL
)
"""
_CREATE_INDEX_LOGIN_ATTEMPTS = """\
CREATE INDEX IF NOT EXISTS idx_login_attempts ON login_attempts(scope, subject, attempted_at)
"""

_CREATE_INVITES = """\
CREATE TABLE IF NOT EXISTS invites (
    token_hash  TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    redeemed_at TEXT,
    redeemed_by INTEGER REFERENCES users(id)
)
"""

SCOPE_IP = "ip"
SCOPE_ACCOUNT = "account"

# Retention for login_attempts rows.  Must be >= throttle.WINDOW (also 1
# hour) — a shorter retention would silently shrink the counting window and
# release locks early.  database.py must NOT import throttle (throttle
# imports these constants, not the reverse); this comment is the link.
LOGIN_ATTEMPT_RETENTION = timedelta(hours=1)

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
        conn.execute(_CREATE_LOGIN_ATTEMPTS)
        conn.execute(_CREATE_INDEX_LOGIN_ATTEMPTS)
        conn.execute(_CREATE_INVITES)
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
            # Opportunistic purge, in the same transaction as the insert
            # (tunatale-re7p). Nothing called ``purge_expired_sessions`` at all,
            # so the table grew for the life of the deployment and every expired
            # row stayed on disk as a credential the app declines to honour but
            # has not destroyed.
            #
            # Login is the trigger because it needs no scheduler to own — this
            # repo has none, and adding one for this would be disproportionate —
            # and the work is self-limiting: it is bounded by the login rate, and
            # it runs immediately before adding the row that would otherwise be
            # the next thing to expire.
            #
            # DELETE first, INSERT second: the new row's ``expires_at`` is in the
            # future, so ordering cannot matter for correctness — but doing it in
            # this order means the purge can never see, let alone remove, the
            # session this call is about to hand out.
            conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (_to_iso(now),))
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

    # ── Failed-login methods ────────────────────────────────────────────

    def record_failed_login(self, *, ip: str, email: str, now: datetime | None = None) -> None:
        """Record a failed login against both the IP and account scopes.

        Inserts two rows sharing one timestamp, then purges rows older than
        the retention horizon.
        """
        if now is None:
            now = datetime.now(UTC)
        norm = _normalize_email(email)
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO login_attempts (scope, subject, attempted_at) VALUES (?, ?, ?)",
                (SCOPE_IP, ip, _to_iso(now)),
            )
            conn.execute(
                "INSERT INTO login_attempts (scope, subject, attempted_at) VALUES (?, ?, ?)",
                (SCOPE_ACCOUNT, norm, _to_iso(now)),
            )
            cutoff = _to_iso(now - LOGIN_ATTEMPT_RETENTION)
            conn.execute("DELETE FROM login_attempts WHERE attempted_at <= ?", (cutoff,))
            self._commit(conn)

    def failed_login_state(self, *, ip: str, email: str, since: datetime) -> dict[str, tuple[int, datetime | None]]:
        """Return the failure count and latest timestamp for each scope.

        ``since`` is exclusive — only rows with ``attempted_at > since`` are
        counted.  Reuses ``_to_iso`` so the SQL comparison is sound against
        UTC-normalised stored values.
        """
        norm = _normalize_email(email)
        since_iso = _to_iso(since)
        result: dict[str, tuple[int, datetime | None]] = {}
        with self._get_conn() as conn:
            for scope, subject in ((SCOPE_IP, ip), (SCOPE_ACCOUNT, norm)):
                row = conn.execute(
                    "SELECT COUNT(*), MAX(attempted_at) FROM login_attempts"
                    " WHERE scope = ? AND subject = ? AND attempted_at > ?",
                    (scope, subject, since_iso),
                ).fetchone()
                count = row[0]
                latest = _from_iso(row[1]) if row[1] is not None else None
                result[scope] = (count, latest)
        return result

    def clear_failed_logins_for_account(self, email: str) -> int:
        """Delete SCOPE_ACCOUNT rows for the normalised email.

        Must not touch SCOPE_IP rows — see throttle module docstring.
        Returns the number of rows deleted.
        """
        norm = _normalize_email(email)
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM login_attempts WHERE scope = ? AND subject = ?",
                (SCOPE_ACCOUNT, norm),
            )
            self._commit(conn)
            return cursor.rowcount

    def count_login_attempt_rows(self) -> int:
        """Return the total row count of login_attempts.

        Exists for the purge test; see test_auth_throttle.py.
        """
        with self._get_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0]

    # ── Invite methods ───────────────────────────────────────────────────

    def create_invite(self, *, ttl: timedelta | None = None, now: datetime | None = None) -> tuple[str, Invite]:
        """Mint an invite and return ``(plaintext_token, Invite)``.

        The plaintext is returned **once and never stored**; the row holds only
        ``hash_token(token)``. ``ttl=None`` means
        ``timedelta(days=settings.invite_ttl_days)``. Refuses to mint into a
        store with no accounts — an invite redeemed first would become the
        owner of the flat databases.
        """
        if ttl is None:
            from app.config import settings

            ttl = timedelta(days=settings.invite_ttl_days)
        if now is None:
            now = datetime.now(UTC)
        expires = now + ttl
        token = mint_token()
        token_h = hash_token(token)
        with self._get_conn() as conn:
            # Opportunistic purge, in the same transaction as the insert: an
            # expired unredeemed row is still a credential on disk. Only
            # UNREDEEMED rows go — a redeemed row's hash is useless and it is
            # the record of who came in on which invite.
            #
            # It also runs FIRST for the reason ``redeem_invite`` leads with
            # its UPDATE: a transaction that opens with a write waits for the
            # lock, while one that reads and then upgrades fails with "database
            # is locked" if a redemption commits in between.
            conn.execute(
                "DELETE FROM invites WHERE redeemed_at IS NULL AND expires_at <= ?",
                (_to_iso(now),),
            )
            if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None:
                conn.rollback()
                msg = "cannot mint an invite before the first account exists"
                raise NoAccountsError(msg)
            conn.execute(
                "INSERT INTO invites (token_hash, created_at, expires_at, redeemed_at, redeemed_by)"
                " VALUES (?, ?, ?, NULL, NULL)",
                (token_h, _to_iso(now), _to_iso(expires)),
            )
            self._commit(conn)
        invite = Invite(
            token_hash=token_h,
            created_at=now,
            expires_at=expires,
            redeemed_at=None,
            redeemed_by=None,
        )
        return token, invite

    def redeem_invite(self, token: str, email: str, password: str, *, now: datetime | None = None) -> User:
        """Spend an invite to create an account, and return the new user.

        The order of operations is the security property (see the locked tests):
        the token is CLAIMED atomically before anything else, the password is
        hashed only once the token is known good, and a failed insert undoes the
        claim so the invite stays usable.
        """
        if now is None:
            now = datetime.now(UTC)
        norm = _normalize_email(email)
        token_h = hash_token(token)
        with self._get_conn() as conn:
            # The FIRST statement is the claim, not a SELECT: under WAL a
            # read-then-upgrade transaction fails with "database is locked"
            # instead of waiting, and a check-then-write lets two concurrent
            # callers both pass the check.
            cursor = conn.execute(
                "UPDATE invites SET redeemed_at = ? WHERE token_hash = ? AND redeemed_at IS NULL AND expires_at > ?",
                (_to_iso(now), token_h, _to_iso(now)),
            )
            if cursor.rowcount != 1:
                msg = "invite is unknown, expired, or already redeemed"
                raise InvalidInviteError(msg)
            # Hashed only now: argon2 is deliberately slow and this route is
            # reachable unauthenticated, so it must not run for a dead token.
            hashed = hash_password(password)
            try:
                conn.execute(
                    "INSERT INTO users (email, password_hash, created_at, is_active) VALUES (?, ?, ?, 1)",
                    (norm, hashed, _to_iso(now)),
                )
            except sqlite3.IntegrityError:
                # Undo the claim with the insert. A file store discards the
                # pending work when the exception leaves ``_get_conn``; the
                # in-memory store holds ONE connection and needs it explicit.
                conn.rollback()
                raise EmailExistsError(f"Email {norm!r} already registered") from None
            row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("UPDATE invites SET redeemed_by = ? WHERE token_hash = ?", (row_id, token_h))
            self._commit(conn)
        return User(
            id=row_id,
            email=norm,
            password_hash=hashed,
            created_at=now,
            is_active=True,
        )

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
