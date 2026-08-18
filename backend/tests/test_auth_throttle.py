"""Login throttling — the policy, the storage, and the client-IP resolution.

Three layers, tested at the layer that owns each claim:

- ``AuthDatabase`` owns the *rows* (what a failure records, what a window
  counts, what a success clears).  Tested against a real ``:memory:`` store.
- ``app.auth.throttle`` owns the *policy* (thresholds, the doubling backoff,
  which of the two scopes wins).  Tested with a real store and an injected
  ``now``, never a frozen clock or a mock.
- ``client_ip`` owns the *proxy question*, which is the one that decides
  whether this feature works at all in production.  Tested against real
  Starlette ``Request`` objects, including the spoofing attempt.

The endpoint-level behaviour (what a locked-out caller actually receives) is
in ``test_api_auth.py::TestLoginThrottling``, because it is a claim about the
route, not about this module.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from starlette.requests import Request

from app.auth import throttle
from app.auth.database import SCOPE_ACCOUNT, SCOPE_IP, AuthDatabase
from app.config import settings

EMAIL = "victim@example.com"
IP = "203.0.113.7"
NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


@pytest.fixture
def db() -> AuthDatabase:
    store = AuthDatabase(":memory:")
    yield store
    store.close()


def _fail(db: AuthDatabase, n: int, *, ip: str = IP, email: str = EMAIL, at: datetime = NOW) -> None:
    """Record *n* failures, one second apart, ending at *at*."""
    for i in range(n):
        db.record_failed_login(ip=ip, email=email, now=at - timedelta(seconds=n - 1 - i))


# ── Storage ──────────────────────────────────────────────────────────────────


class TestFailedLoginStorage:
    def test_one_failure_records_both_scopes(self, db: AuthDatabase) -> None:
        """A single failed attempt counts against the IP *and* the account.

        One INSERT per scope is what makes the two limits independent; a
        shared row would make either limit trip the other.
        """
        db.record_failed_login(ip=IP, email=EMAIL, now=NOW)
        state = db.failed_login_state(ip=IP, email=EMAIL, since=NOW - timedelta(hours=1))
        assert state[SCOPE_IP] == (1, NOW)
        assert state[SCOPE_ACCOUNT] == (1, NOW)

    def test_account_scope_is_normalised(self, db: AuthDatabase) -> None:
        """``Victim@Example.COM `` and ``victim@example.com`` are one account.

        Without this, changing the capitalisation of the email would reset the
        per-account counter — a bypass that costs an attacker one keystroke.
        """
        db.record_failed_login(ip=IP, email="  Victim@Example.COM ", now=NOW)
        state = db.failed_login_state(ip=IP, email=EMAIL, since=NOW - timedelta(hours=1))
        assert state[SCOPE_ACCOUNT][0] == 1

    def test_state_is_empty_before_any_attempt(self, db: AuthDatabase) -> None:
        """No rows means ``(0, None)`` — not a crash and not a zero timestamp."""
        state = db.failed_login_state(ip=IP, email=EMAIL, since=NOW - timedelta(hours=1))
        assert state == {SCOPE_IP: (0, None), SCOPE_ACCOUNT: (0, None)}

    def test_attempts_outside_the_window_do_not_count(self, db: AuthDatabase) -> None:
        """``since`` is exclusive of older rows — the window is the memory."""
        db.record_failed_login(ip=IP, email=EMAIL, now=NOW - timedelta(hours=2))
        db.record_failed_login(ip=IP, email=EMAIL, now=NOW)
        state = db.failed_login_state(ip=IP, email=EMAIL, since=NOW - timedelta(hours=1))
        assert state[SCOPE_IP] == (1, NOW)

    def test_success_clears_the_account_but_not_the_ip(self, db: AuthDatabase) -> None:
        """The decisive asymmetry.

        Clearing the account on success is the point (a user who mistypes
        twice and then gets it right starts clean).  Clearing the IP too would
        hand an attacker who owns *any* valid account an unlimited reset for
        every other account they are guessing from the same address.
        """
        _fail(db, 3)
        assert db.clear_failed_logins_for_account(EMAIL) == 3
        state = db.failed_login_state(ip=IP, email=EMAIL, since=NOW - timedelta(hours=1))
        assert state[SCOPE_ACCOUNT][0] == 0
        assert state[SCOPE_IP][0] == 3

    def test_clearing_an_account_with_no_failures_is_a_no_op(self, db: AuthDatabase) -> None:
        """Every successful login calls this; the common case is zero rows."""
        assert db.clear_failed_logins_for_account(EMAIL) == 0

    def test_recording_purges_rows_that_left_the_window(self, db: AuthDatabase) -> None:
        """The table is bounded by the window, not by all history.

        Nothing else prunes it: rows are only ever created by a failure, so
        the failure path is the only place a purge can live.  Without it this
        is unbounded state on a box with one small disk.
        """
        db.record_failed_login(ip=IP, email=EMAIL, now=NOW - timedelta(days=2))
        assert db.count_login_attempt_rows() == 2  # ip + account
        db.record_failed_login(ip=IP, email=EMAIL, now=NOW)
        assert db.count_login_attempt_rows() == 2  # the old pair is gone


# ── Policy ───────────────────────────────────────────────────────────────────


class TestAccountThrottle:
    def test_below_the_threshold_nothing_is_locked(self, db: AuthDatabase) -> None:
        _fail(db, throttle.ACCOUNT_THRESHOLD - 1)
        assert throttle.check(db, ip=IP, email=EMAIL, now=NOW) is None

    def test_at_the_threshold_the_account_locks(self, db: AuthDatabase) -> None:
        _fail(db, throttle.ACCOUNT_THRESHOLD)
        lock = throttle.check(db, ip=IP, email=EMAIL, now=NOW)
        assert lock is not None
        assert lock.retry_after == int(throttle.BASE_LOCKOUT.total_seconds())

    def test_the_lock_follows_the_account_across_IPs(self, db: AuthDatabase) -> None:
        """The decisive per-account oracle.

        Every failure comes from a different address, so the per-IP counter
        never reaches 2.  A throttle keyed only on the source IP — the easy
        one to build — lets a botnet walk the password list unimpeded.
        """
        for i in range(throttle.ACCOUNT_THRESHOLD):
            db.record_failed_login(ip=f"198.51.100.{i}", email=EMAIL, now=NOW - timedelta(seconds=10 - i))
        lock = throttle.check(db, ip="198.51.100.200", email=EMAIL, now=NOW)
        assert lock is not None

    def test_the_lock_expires_on_its_own(self, db: AuthDatabase) -> None:
        """A locked-out *user* has to get back in without an administrator.

        The counter is only cleared by a success, and a success is impossible
        while locked — so if the lock did not lapse on its own it would be
        permanent.
        """
        _fail(db, throttle.ACCOUNT_THRESHOLD)
        later = NOW + throttle.BASE_LOCKOUT + timedelta(seconds=1)
        assert throttle.check(db, ip=IP, email=EMAIL, now=later) is None

    def test_each_further_failure_doubles_the_wait(self, db: AuthDatabase) -> None:
        """ "Increasing backoff" — each round of guessing costs twice the last."""
        base = int(throttle.BASE_LOCKOUT.total_seconds())
        for extra, expected in ((0, base), (1, base * 2), (2, base * 4), (3, base * 8)):
            store = AuthDatabase(":memory:")
            _fail(store, throttle.ACCOUNT_THRESHOLD + extra)
            lock = throttle.check(store, ip=IP, email=EMAIL, now=NOW)
            assert lock is not None and lock.retry_after == expected, extra
            store.close()

    def test_the_wait_is_capped(self, db: AuthDatabase) -> None:
        """Doubling without a ceiling reaches "locked until the heat death".

        The cap is also what keeps the lock shorter than ``WINDOW``: a lockout
        that outlived its own counting window would release early and silently
        when the rows aged out, which is the opposite of what it claims.
        """
        _fail(db, throttle.ACCOUNT_THRESHOLD + 40)
        lock = throttle.check(db, ip=IP, email=EMAIL, now=NOW)
        assert lock is not None
        assert lock.retry_after == int(throttle.MAX_LOCKOUT.total_seconds())
        assert throttle.MAX_LOCKOUT < throttle.WINDOW


class TestIPThrottle:
    def test_failures_spread_across_accounts_still_throttle_the_IP(self, db: AuthDatabase) -> None:
        """The decisive per-IP oracle.

        Every attempt names a different account, so no per-account counter
        gets past 1.  This is what a user-enumeration sweep looks like, and
        an account-only throttle does not see it at all.
        """
        for i in range(throttle.IP_THRESHOLD):
            db.record_failed_login(ip=IP, email=f"user{i}@example.com", now=NOW - timedelta(seconds=60 - i))
        fresh = "someone-else@example.com"
        assert db.failed_login_state(ip=IP, email=fresh, since=NOW - throttle.WINDOW)[SCOPE_ACCOUNT][0] == 0
        assert throttle.check(db, ip=IP, email=fresh, now=NOW) is not None

    def test_the_ip_threshold_is_looser_than_the_account_one(self) -> None:
        """A shared address (NAT, a household, a CI runner) is many people.

        If the two limits were equal the IP limit would be the only one that
        ever fires, and the per-account protection would be dead code.
        """
        assert throttle.IP_THRESHOLD > throttle.ACCOUNT_THRESHOLD

    def test_the_longer_of_the_two_locks_wins(self, db: AuthDatabase) -> None:
        """Both scopes tripped: the answer is the later unlock, not the first.

        Returning the shorter one would let a caller retry while still locked
        by the other scope, and the retry would be refused — a ``Retry-After``
        that lies about when to come back.

        ⚠️ The account failures below come from a DIFFERENT address on purpose.
        A failure records against both scopes, so failures charged to this IP
        would also inflate its counter and the two locks would trip together —
        which is why the naive version of this test can only ever observe a
        tie, and would pass against an implementation that returned either one.
        """
        # IP scope: exactly at its threshold, 30s ago ⇒ unlocks at NOW + 30s.
        for i in range(throttle.IP_THRESHOLD):
            db.record_failed_login(ip=IP, email=f"user{i}@example.com", now=NOW - timedelta(seconds=30))
        # Account scope: three past its threshold, just now ⇒ a much longer wait.
        _fail(db, throttle.ACCOUNT_THRESHOLD + 3, ip="198.51.100.55")

        lock = throttle.check(db, ip=IP, email=EMAIL, now=NOW)
        ip_only = throttle.check(db, ip=IP, email="nobody@example.com", now=NOW)
        assert lock is not None and ip_only is not None
        assert ip_only.retry_after == 30, "the IP lock alone should have 30s left"
        assert lock.retry_after > ip_only.retry_after


class TestPolicyPlumbing:
    def test_record_failure_and_clear_go_through_the_module(self, db: AuthDatabase) -> None:
        """The endpoint talks to ``throttle``, never to the table directly."""
        for _ in range(throttle.ACCOUNT_THRESHOLD):
            throttle.record_failure(db, ip=IP, email=EMAIL)
        assert throttle.check(db, ip=IP, email=EMAIL) is not None
        throttle.clear_account(db, email=EMAIL)
        assert throttle.check(db, ip=IP, email=EMAIL) is None

    def test_retry_after_never_rounds_down_to_zero(self, db: AuthDatabase) -> None:
        """``Retry-After: 0`` is an invitation to retry immediately.

        The remaining wait is a fraction of a second here; truncating instead
        of rounding up would tell the caller to come back at once and refuse
        them when they did.
        """
        _fail(db, throttle.ACCOUNT_THRESHOLD)
        almost = NOW + throttle.BASE_LOCKOUT - timedelta(milliseconds=1)
        lock = throttle.check(db, ip=IP, email=EMAIL, now=almost)
        assert lock is not None and lock.retry_after == 1


# ── Client IP ────────────────────────────────────────────────────────────────


def _request(headers: dict[str, str] | None = None, *, client: tuple[str, int] | None = ("192.0.2.1", 4242)) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/login",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": client,
    }
    return Request(scope)


class TestClientIP:
    def test_socket_peer_is_used_when_no_proxy_header_is_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "trusted_proxy_header", "")
        assert throttle.client_ip(_request({"X-Forwarded-For": "1.2.3.4"})) == "192.0.2.1"

    def test_a_configured_header_is_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Behind Caddy the socket peer is the proxy — every user, one bucket."""
        monkeypatch.setattr(settings, "trusted_proxy_header", "X-Forwarded-For")
        assert throttle.client_ip(_request({"X-Forwarded-For": "198.51.100.9"})) == "198.51.100.9"

    def test_a_spoofed_forwarded_for_cannot_shift_the_bucket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """THE security oracle for this function.

        Caddy *appends* the peer it actually saw, so a client that sends its
        own ``X-Forwarded-For: <victim>`` produces ``<victim>, <attacker>``.
        Reading the LEFTMOST entry — the obvious choice, and the one most
        naive implementations make — would let an attacker pick a fresh IP
        bucket per request and never throttle at all, while also framing
        whatever address they named.  The rightmost entry is the only one the
        trusted proxy vouched for.
        """
        monkeypatch.setattr(settings, "trusted_proxy_header", "X-Forwarded-For")
        spoofed = _request({"X-Forwarded-For": "10.0.0.1, 203.0.113.9, 198.51.100.4"})
        assert throttle.client_ip(spoofed) == "198.51.100.4"

    def test_a_missing_header_falls_back_to_the_peer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Direct-to-:8000 traffic bypassing the proxy is still throttled."""
        monkeypatch.setattr(settings, "trusted_proxy_header", "X-Forwarded-For")
        assert throttle.client_ip(_request({})) == "192.0.2.1"

    def test_a_header_of_only_separators_falls_back_to_the_peer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``X-Forwarded-For: " , "`` is truthy and yields no address."""
        monkeypatch.setattr(settings, "trusted_proxy_header", "X-Forwarded-For")
        assert throttle.client_ip(_request({"X-Forwarded-For": " , "})) == "192.0.2.1"

    def test_a_request_with_no_peer_gets_one_shared_bucket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ASGI makes ``client`` optional; it is ``None`` over a unix socket.

        Failing closed into a single named bucket throttles those callers
        together, which is wrong-ish but safe.  Returning ``None`` and
        skipping the IP scope would turn an unusual transport into a bypass.
        """
        monkeypatch.setattr(settings, "trusted_proxy_header", "")
        assert throttle.client_ip(_request({}, client=None)) == throttle.UNKNOWN_IP
