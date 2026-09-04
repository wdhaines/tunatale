"""Tests for app.auth.database — AuthDatabase + schema + exceptions."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.auth.database import (
    SCHEMA_VERSION,
    AuthDatabase,
    EmailExistsError,
    SchemaTooNewError,
    _from_iso,
    _to_iso,
)
from app.auth.tokens import hash_token


@pytest.fixture
def db():
    """In-memory AuthDatabase."""
    with AuthDatabase(":memory:") as db:
        yield db


# ── User tests ───────────────────────────────────────────────────────────────


class TestCreateUser:
    def test_returns_user_with_id(self, db: AuthDatabase) -> None:
        user = db.create_user("alice@example.com", "pass123")
        assert user.id >= 1
        assert user.email == "alice@example.com"
        assert user.is_active is True
        assert user.created_at.tzinfo is not None

    def test_password_hash_starts_with_argon2(self, db: AuthDatabase) -> None:
        """What is stored is an argon2id PHC string, not the password.

        ⚠️ The password MUST stay long. This ran with ``"pw"`` until 2026-08-18,
        when it went red on CI (run 32175289770, ``backend-hostile-tz
        (Pacific/Kiritimati)``) with::

            'pw' is contained here: Kwk+aY4PAUpw$CeGvEAjl96uCKsr/gakxzaSz0iy9...

        The salt and hash are base64, so a two-character needle turns up inside
        a ~65-character haystack by chance roughly 1.6% of the time — the test
        was a coin flip that lands wrong once every few dozen jobs. The
        timezone was a red herring: the hostile-tz job draws from the same
        distribution as every other one, it just drew first. A 28-character
        password cannot collide, which is what makes this assertion mean what
        it says.
        """
        password = "correct horse battery staple"
        user = db.create_user("a@b.com", password)
        assert user.password_hash.startswith("$argon2id$")
        assert password not in user.password_hash

    def test_duplicate_email_by_case_raises(self, db: AuthDatabase) -> None:
        db.create_user("A@B.com", "pw")
        with pytest.raises(EmailExistsError):
            db.create_user("a@b.com", "pw")

    def test_duplicate_email_by_whitespace_raises(self, db: AuthDatabase) -> None:
        db.create_user("a@b.com", "pw")
        with pytest.raises(EmailExistsError):
            db.create_user("  a@b.com  ", "pw")


class TestGetUser:
    def test_by_email_case_insensitive(self, db: AuthDatabase) -> None:
        db.create_user("A@B.com", "pw")
        user = db.get_user_by_email("a@b.com")
        assert user is not None

    def test_by_email_unknown_returns_none(self, db: AuthDatabase) -> None:
        assert db.get_user_by_email("nobody@nowhere.com") is None

    def test_by_id(self, db: AuthDatabase) -> None:
        created = db.create_user("x@y.com", "pw")
        assert db.get_user_by_id(created.id) == created

    def test_by_id_unknown_returns_none(self, db: AuthDatabase) -> None:
        assert db.get_user_by_id(99999) is None


class TestVerifyCredentials:
    def test_happy_path(self, db: AuthDatabase) -> None:
        db.create_user("a@b.com", "secret")
        user = db.verify_credentials("a@b.com", "secret")
        assert user is not None
        assert user.email == "a@b.com"

    def test_wrong_password(self, db: AuthDatabase) -> None:
        db.create_user("a@b.com", "secret")
        assert db.verify_credentials("a@b.com", "wrong") is None

    def test_unknown_email(self, db: AuthDatabase) -> None:
        assert db.verify_credentials("nobody@nowhere.com", "x") is None

    def test_cross_user_password_check(self, db: AuthDatabase) -> None:
        a = db.create_user("a@b.com", "pass_a")
        b = db.create_user("c@d.com", "pass_b")
        assert db.verify_credentials(b.email, "pass_a") is None
        a_fresh = db.get_user_by_id(a.id)
        assert a_fresh is not None
        assert not verify_password_raw(a_fresh.password_hash, "pass_b")

    def test_inactive_user(self, db: AuthDatabase) -> None:
        user = db.create_user("a@b.com", "pw")
        db.set_active(user.id, False)
        assert db.verify_credentials("a@b.com", "pw") is None

    def test_reactivate_restores(self, db: AuthDatabase) -> None:
        user = db.create_user("a@b.com", "pw")
        db.set_active(user.id, False)
        db.set_active(user.id, True)
        assert db.verify_credentials("a@b.com", "pw") is not None

    def test_rehash_path(self, tmp_path: Path) -> None:
        """Create a user with a weak argon2 hash, then verify re-hash occurs."""
        from argon2 import PasswordHasher

        weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
        legacy = weak.hash("secret")

        db_path = tmp_path / "rehash.db"
        with AuthDatabase(str(db_path)) as db:
            norm_email = "rehash@test.com"
            with db._get_conn() as conn:
                conn.execute(
                    "INSERT INTO users (email, password_hash, created_at, is_active) VALUES (?, ?, ?, 1)",
                    (norm_email, legacy, _to_iso(datetime.now(UTC))),
                )
                conn.commit()

            user = db.verify_credentials(norm_email, "secret")
            assert user is not None
            assert user.password_hash != legacy
            stored = db.get_user_by_email(norm_email)
            assert stored is not None
            assert stored.password_hash != legacy


# ── Password and session cascade tests ───────────────────────────────────────


class TestSetPassword:
    def test_changes_hash(self, db: AuthDatabase) -> None:
        user = db.create_user("a@b.com", "old")
        db.set_password(user.id, "new")
        assert db.verify_credentials("a@b.com", "old") is None
        assert db.verify_credentials("a@b.com", "new") is not None

    def test_deletes_sessions(self, db: AuthDatabase) -> None:
        user = db.create_user("a@b.com", "pw")
        token, _ = db.create_session(user.id)
        db.set_password(user.id, "new")
        assert db.get_session(token) is None


class TestSetActive:
    def test_deactivate_deletes_sessions(self, db: AuthDatabase) -> None:
        user = db.create_user("a@b.com", "pw")
        token, _ = db.create_session(user.id)
        db.set_active(user.id, False)
        assert db.get_session(token) is None


# ── Session tests ────────────────────────────────────────────────────────────


class TestCreateSession:
    def test_returns_token_and_session(self, db: AuthDatabase) -> None:
        user = db.create_user("a@b.com", "pw")
        token, session = db.create_session(user.id)
        assert token != session.token_hash
        assert hash_token(token) == session.token_hash

    def test_expired_session_returns_none(self, db: AuthDatabase) -> None:
        user = db.create_user("a@b.com", "pw")
        token, _ = db.create_session(user.id, ttl=timedelta(seconds=-1))
        assert db.get_session(token) is None


class TestSessionPlaintextNotStored:
    def test_plaintext_not_in_any_column(self, tmp_path: Path) -> None:
        """A read of auth.db must not yield a usable cookie."""
        db_path = tmp_path / "auth.db"
        with AuthDatabase(str(db_path)) as db:
            user = db.create_user("a@b.com", "pw")
            token, _ = db.create_session(user.id)
            db.close()

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT * FROM sessions").fetchall()
        desc = [d[0] for d in conn.execute("SELECT * FROM sessions").description]
        conn.close()

        for row in rows:
            for col_idx in range(len(desc)):
                assert row[col_idx] != token
        assert token not in str(rows)


class TestGetSession:
    def test_unknown_token_returns_none(self, db: AuthDatabase) -> None:
        assert db.get_session("nonexistent") is None


class TestTouchSession:
    def test_advances_last_seen(self, db: AuthDatabase) -> None:
        user = db.create_user("a@b.com", "pw")
        token, session = db.create_session(user.id)
        later = datetime.now(UTC) + timedelta(hours=1)
        db.touch_session(token, now=later)
        updated = db.get_session(token, now=later)
        assert updated is not None
        assert updated.last_seen_at > session.last_seen_at

    def test_unknown_token_noop(self, db: AuthDatabase) -> None:
        db.touch_session("nonexistent")


class TestDeleteSession:
    def test_removes_it(self, db: AuthDatabase) -> None:
        user = db.create_user("a@b.com", "pw")
        token, _ = db.create_session(user.id)
        db.delete_session(token)
        assert db.get_session(token) is None

    def test_unknown_token_noop(self, db: AuthDatabase) -> None:
        db.delete_session("nonexistent")


class TestDeleteSessionsForUser:
    def test_returns_count(self, db: AuthDatabase) -> None:
        a = db.create_user("a@b.com", "pw")
        b = db.create_user("c@d.com", "pw")
        db.create_session(a.id)
        db.create_session(a.id)
        db.create_session(b.id)
        removed = db.delete_sessions_for_user(a.id)
        assert removed == 2
        assert db.get_user_by_email("c@d.com") is not None

    def test_leaves_other_user_sessions_alone(self, db: AuthDatabase) -> None:
        a = db.create_user("a@b.com", "pw")
        b = db.create_user("c@d.com", "pw")
        token_b, _ = db.create_session(b.id)
        db.delete_sessions_for_user(a.id)
        assert db.get_session(token_b) is not None


class TestPurgeExpiredSessions:
    def test_removes_only_expired(self, db: AuthDatabase) -> None:
        user = db.create_user("a@b.com", "pw")
        token_valid, _ = db.create_session(user.id, ttl=timedelta(hours=1))
        token_expired, _ = db.create_session(user.id, ttl=timedelta(seconds=-1))
        now = datetime.now(UTC) + timedelta(seconds=1)
        removed = db.purge_expired_sessions(now=now)
        assert removed >= 1
        assert db.get_session(token_valid) is not None
        assert db.get_session(token_expired) is None

    def test_agrees_with_get_session_for_non_utc_now(self, db: AuthDatabase) -> None:
        """The two expiry paths must answer the same question the same way.

        ``get_session`` parses ``expires_at`` and compares datetimes;
        ``purge_expired_sessions`` compares the ISO string in SQL. They agreed
        only while every ``now`` in the suite was UTC. With ``now`` expressed at
        -01:00 — the same instant, a different offset — ``get_session`` said
        expired and ``purge`` deleted nothing, because ``_to_iso`` preserved the
        caller's offset and the string sorted below the stored ``+00:00`` value.
        Regression guard for that fix; ``_to_iso`` now normalises to UTC.
        """
        user = db.create_user("a@b.com", "pw")
        token, session = db.create_session(user.id, ttl=timedelta(hours=1))
        # Half an hour PAST expiry, written in an offset that sorts below the
        # stored one. The control is the same instant in UTC, below.
        now = (session.expires_at + timedelta(minutes=30)).astimezone(timezone(timedelta(hours=-1)))
        assert now.utcoffset() != timedelta(0), "probe must not be UTC or it cannot discriminate"

        assert db.get_session(token, now=now) is None
        assert db.purge_expired_sessions(now=now) == 1


class TestCreatingASessionPurgesExpiredOnes:
    """tunatale-re7p: nothing ever CALLED purge_expired_sessions.

    The method was written, tested, and referenced in a sibling's docstring
    ("Does not delete the expired row; purge_expired_sessions does that") — a
    sentence true of the method and false of the system, because no code path
    ran it. The sessions table therefore grew for the life of the deployment,
    and an expired row is a credential the app declines to honour but has not
    destroyed.

    ⚠️ The oracle is the ROW COUNT, not whether auth rejects the token. Rejection
    already passed before this change and cannot discriminate a purge from a
    no-op — that is exactly why the gap survived having tests.

    Login is the trigger: no scheduler to own, and the work is self-limiting
    because it is bounded by the login rate.
    """

    def _session_rows(self, db: AuthDatabase) -> int:
        with db._get_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

    def test_the_expired_row_is_gone_from_the_table_not_merely_rejected(self, db: AuthDatabase) -> None:
        user = db.create_user("a@b.com", "pw")
        db.create_session(user.id, ttl=timedelta(seconds=-1))
        assert self._session_rows(db) == 1, "precondition: the expired row exists"

        db.create_session(user.id, ttl=timedelta(hours=1))

        # The fresh one, and nothing else. Before this change the count was 2.
        assert self._session_rows(db) == 1

    def test_a_live_session_survives_someone_elses_login(self, db: AuthDatabase) -> None:
        """The purge must not be a logout-everyone."""
        a = db.create_user("a@b.com", "pw")
        b = db.create_user("c@d.com", "pw")
        token_a, _ = db.create_session(a.id, ttl=timedelta(hours=1))

        db.create_session(b.id)

        assert db.get_session(token_a) is not None

    def test_many_expired_rows_all_go(self, db: AuthDatabase) -> None:
        """A backlog is cleared in one pass, not one row per login.

        ⚠️ The rows are seeded with raw SQL on purpose. Looping over
        ``create_session(ttl=-1)`` cannot build this state any more — each call
        now purges the ones before it, so the loop leaves ONE row and the test
        would assert against a fixture the feature had already dismantled. The
        setup must not go through the mechanism under test.
        """
        user = db.create_user("a@b.com", "pw")
        stale = _to_iso(datetime.now(UTC) - timedelta(days=1))
        with db._get_conn() as conn:
            for n in range(5):
                conn.execute(
                    "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_seen_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"stale-{n}", user.id, stale, stale, stale),
                )
            conn.commit()
        assert self._session_rows(db) == 5, "precondition: a backlog exists"

        db.create_session(user.id, ttl=timedelta(hours=1))

        assert self._session_rows(db) == 1

    def test_the_returned_token_still_works(self, db: AuthDatabase) -> None:
        """A purge running inside create_session must not clobber its own row."""
        user = db.create_user("a@b.com", "pw")
        db.create_session(user.id, ttl=timedelta(seconds=-1))

        token, _ = db.create_session(user.id, ttl=timedelta(hours=1))

        assert db.get_session(token) is not None


# ── Storage and schema tests ─────────────────────────────────────────────────


class TestFileRoundTrip:
    def test_survives_close_reopen(self, tmp_path: Path) -> None:
        db_path = tmp_path / "auth.db"
        with AuthDatabase(str(db_path)) as db:
            user = db.create_user("a@b.com", "pw")
            token, _ = db.create_session(user.id)
            user_id = user.id
        with AuthDatabase(str(db_path)) as db:
            assert db.get_user_by_id(user_id) is not None
            assert db.get_session(token) is not None

    def test_sqlite_url_accepted(self, tmp_path: Path) -> None:
        with AuthDatabase(f"sqlite:///{tmp_path / 'auth.db'}") as db:
            db.create_user("a@b.com", "pw")


class TestIdempotentMigration:
    def test_open_twice_no_error(self, tmp_path: Path) -> None:
        db_path = tmp_path / "auth.db"
        with AuthDatabase(str(db_path)) as db:
            user = db.create_user("a@b.com", "pw")
            user_id = user.id
        with AuthDatabase(str(db_path)) as db:
            # Reopening must leave the stamped version alone. Asserted against
            # SCHEMA_VERSION rather than a literal: this test is about the
            # migration being IDEMPOTENT, not about which version we are on, and
            # a literal here goes red on every future bump for no reason (it did
            # exactly that on the v1 → v2 bump that added login_attempts).
            conn = db._conn if db._in_memory else sqlite3.connect(str(db_path))
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if not db._in_memory:
                conn.close()
            assert version == SCHEMA_VERSION
            assert db.get_user_by_id(user_id) is not None


class TestSchemaTooNew:
    def test_raises_on_future_version(self, tmp_path: Path) -> None:
        db_path = tmp_path / "auth.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA user_version = 99")
        conn.commit()
        conn.close()
        with pytest.raises(SchemaTooNewError):
            AuthDatabase(str(db_path))


class TestForeignKeyEnforcement:
    def test_orphan_session_raises(self, db: AuthDatabase) -> None:
        with pytest.raises(sqlite3.IntegrityError), db._get_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
                (
                    "fake_hash",
                    99999,
                    "2026-01-01T00:00:00+00:00",
                    "2099-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:00+00:00",
                ),
            )


class TestJournalMode:
    def test_wal_for_file(self, tmp_path: Path) -> None:
        with AuthDatabase(str(tmp_path / "auth.db")) as db, db._get_conn() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"

    def test_memory_for_in_memory(self, db: AuthDatabase) -> None:
        with db._get_conn() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "memory"


# ── Naive-datetime branch coverage ───────────────────────────────────────────


class TestIsoHelpersNaive:
    def test_to_iso_naive(self) -> None:
        naive = datetime(2026, 1, 15, 12, 0, 0)
        result = _to_iso(naive)
        assert "+00:00" in result

    def test_from_iso_naive(self) -> None:
        dt = _from_iso("2026-01-15T12:00:00")
        assert dt.tzinfo is UTC


# ── purge_expired_sessions default now ────────────────────────────────────────


class TestPurgeExpiredDefaultNow:
    def test_purge_expired_with_default_now(self, db: AuthDatabase) -> None:
        user = db.create_user("a@b.com", "pw")
        db.create_session(user.id, ttl=timedelta(seconds=-1))
        removed = db.purge_expired_sessions()
        assert removed >= 1


# ── Helpers used by TestVerifyCredentials.test_cross_user_password_check ─────


def verify_password_raw(password_hash: str, password: str) -> bool:
    """Import-free wrapper for the module-level verify_password."""
    from app.auth.passwords import verify_password

    return verify_password(password_hash, password)


class TestListUsers:
    def test_empty_store(self, db: AuthDatabase) -> None:
        assert db.list_users() == []

    def test_returns_all_users_oldest_first(self, db: AuthDatabase) -> None:
        first = db.create_user("a@b.com", "pw")
        second = db.create_user("c@d.com", "pw")
        assert [u.id for u in db.list_users()] == [first.id, second.id]

    def test_includes_deactivated_users(self, db: AuthDatabase) -> None:
        """A disabled account must still be listable, or it cannot be found again."""
        user = db.create_user("a@b.com", "pw")
        db.set_active(user.id, False)
        listed = db.list_users()
        assert len(listed) == 1
        assert listed[0].is_active is False
