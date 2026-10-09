"""Invite-token registration: the guardrails (tunatale-1mh / Deploy P4.3).

An invite lets the admin add a person without handing out shell access, and
registration stays CLOSED: the only two ways an account comes into existence
are the ``create-user`` CLI and redeeming an admin-minted invite.

What this file defends, in the order the classes appear:

- **The store** — a token is single-use, expires, is hashed at rest like a
  session token, and a redemption that fails for any reason leaves no account
  behind and (when the fault was the email, not the token) leaves the invite
  usable.
- **The endpoint** — every dead token gets one indistinguishable answer, the
  token is judged before the email (so the endpoint is not an account
  enumeration oracle for anyone without an invite), and redeeming does not log
  you in.
- **No open signup** — no anonymous request to ANY route creates an account,
  and the source has exactly the entry points named above.
- **The CLI** — ``create-invite`` is how a token is minted; there is no API for
  it.

Everything runs against a real ``AuthDatabase`` (in memory, or a file under
``tmp_path`` when the raw rows or real concurrency matter). No ``app.*`` mocks.
"""

from __future__ import annotations

import io
import re
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import database as auth_database
from app.auth.cli import main as cli_main
from app.auth.database import AuthDatabase, EmailExistsError
from app.auth.session import COOKIE_NAME
from app.auth.tokens import hash_token
from app.config import Settings, settings
from app.main import app
from tests.test_auth_route_coverage import EXEMPT_PATHS, MIN_EXPECTED_ROUTES, _concrete_path, _iter_api_routes

OWNER = "owner@example.com"
OWNER_PASSWORD = "the owner's own long passphrase"
INVITEE = "invitee@example.com"
PASSWORD = "a new learner's passphrase"

REDEEM_PATH = "/api/auth/invite/redeem"

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)

APP_DIR = Path(auth_database.__file__).resolve().parents[1]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _rows(path: Path) -> list[sqlite3.Row]:
    """The raw ``invites`` rows, read behind the store's back."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM invites ORDER BY created_at, token_hash").fetchall()
    finally:
        conn.close()


def _bytes_on_disk(path: Path) -> bytes:
    """Every byte the store has written: the database file plus its WAL.

    The WAL matters. The store runs in WAL mode, so a freshly written row can
    live only in ``auth.db-wal`` — a scan of ``auth.db`` alone would report
    "the plaintext is not there" about a file that does not hold the row yet.
    """
    blob = path.read_bytes()
    wal = path.with_name(path.name + "-wal")
    if wal.exists():
        blob += wal.read_bytes()
    return blob


@pytest.fixture(params=["memory", "file"])
def db(request: pytest.FixtureRequest, tmp_path: Path):
    """A store with one account (the owner), in BOTH connection modes.

    Both, because they roll back differently: a file store opens a connection
    per operation and discards uncommitted work when it closes, while the
    in-memory store holds ONE connection for its lifetime and keeps whatever a
    failed operation left pending unless it is rolled back explicitly.
    """
    store = AuthDatabase(":memory:" if request.param == "memory" else str(tmp_path / "auth.db"))
    store.create_user(OWNER, OWNER_PASSWORD)
    yield store
    store.close()


@pytest.fixture
def file_db(tmp_path: Path):
    """``(store, path)`` for tests that read the raw rows or need real threads."""
    path = tmp_path / "auth.db"
    store = AuthDatabase(str(path))
    store.create_user(OWNER, OWNER_PASSWORD)
    yield store, path
    store.close()


# ── The store ────────────────────────────────────────────────────────────────


class TestInviteStore:
    def test_only_the_hash_is_stored(self, file_db) -> None:
        """A read of ``auth.db`` must not yield a usable invite.

        The second assertion is the control: the hash IS in the bytes scanned,
        so the first one is a statement about the plaintext and not about
        having looked in the wrong place.
        """
        store, path = file_db
        token, _invite = store.create_invite()

        assert [row["token_hash"] for row in _rows(path)] == [hash_token(token)]
        blob = _bytes_on_disk(path)
        assert hash_token(token).encode() in blob
        assert token.encode() not in blob

    def test_redeeming_creates_a_working_account(self, db: AuthDatabase) -> None:
        owner = db.get_user_by_email(OWNER)
        token, _invite = db.create_invite()

        user = db.redeem_invite(token, INVITEE, PASSWORD)

        assert user.email == INVITEE
        assert user.is_active
        assert user.id != owner.id
        verified = db.verify_credentials(INVITEE, PASSWORD)
        assert verified is not None
        assert verified.id == user.id

    def test_a_redeemed_token_is_rejected_on_replay(self, db: AuthDatabase) -> None:
        token, _invite = db.create_invite()
        db.redeem_invite(token, INVITEE, PASSWORD)

        with pytest.raises(auth_database.InvalidInviteError):
            db.redeem_invite(token, "second@example.com", PASSWORD)

        assert db.get_user_by_email("second@example.com") is None
        assert [u.email for u in db.list_users()] == [OWNER, INVITEE]

    def test_an_unknown_token_creates_nothing(self, db: AuthDatabase) -> None:
        db.create_invite()  # a live invite exists; the caller just does not hold it

        with pytest.raises(auth_database.InvalidInviteError):
            db.redeem_invite("not-a-real-invite", INVITEE, PASSWORD)

        assert [u.email for u in db.list_users()] == [OWNER]

    def test_expiry_is_exclusive_and_a_late_attempt_does_not_burn_the_invite(self, db: AuthDatabase) -> None:
        """``expires_at <= now`` is expired — the same edge sessions use.

        The order is the point: the failed attempt comes FIRST, so the success
        after it also proves that being rejected did not consume the token.
        """
        token, invite = db.create_invite(ttl=DAY, now=T0)
        assert invite.expires_at == T0 + DAY

        with pytest.raises(auth_database.InvalidInviteError):
            db.redeem_invite(token, INVITEE, PASSWORD, now=T0 + DAY)
        assert db.get_user_by_email(INVITEE) is None

        user = db.redeem_invite(token, INVITEE, PASSWORD, now=T0 + DAY - timedelta(seconds=1))
        assert user.email == INVITEE

    def test_the_default_lifetime_is_a_setting(self, db: AuthDatabase, monkeypatch: pytest.MonkeyPatch) -> None:
        assert Settings.model_fields["invite_ttl_days"].default == 7

        monkeypatch.setattr(settings, "invite_ttl_days", 3)
        _token, invite = db.create_invite(now=T0)

        assert invite.expires_at == T0 + timedelta(days=3)

    def test_a_registered_email_does_not_burn_the_invite(self, db: AuthDatabase) -> None:
        """A typo'd or already-registered address must not cost the invite.

        The redemption marks the token used AND inserts the user; if the insert
        fails, the mark must be undone with it. In the in-memory store that
        needs an explicit rollback, which is why ``db`` runs both modes.

        And it must never touch the existing account: redeeming against a
        registered address as a password reset would be an account takeover.
        """
        token, _invite = db.create_invite()

        with pytest.raises(EmailExistsError):
            db.redeem_invite(token, "  Owner@Example.COM ", PASSWORD)

        assert db.verify_credentials(OWNER, OWNER_PASSWORD) is not None
        assert db.verify_credentials(OWNER, PASSWORD) is None
        assert db.redeem_invite(token, INVITEE, PASSWORD).email == INVITEE

    def test_the_token_is_judged_before_the_email(self, db: AuthDatabase) -> None:
        """Without a live invite, a registered address looks like any other.

        Were the email checked first, ``EmailExistsError`` would answer "is this
        address registered?" for anyone who can reach the endpoint.
        """
        with pytest.raises(auth_database.InvalidInviteError):
            db.redeem_invite("not-a-real-invite", OWNER, PASSWORD)

    def test_the_email_is_normalised(self, db: AuthDatabase) -> None:
        token, _invite = db.create_invite()

        assert db.redeem_invite(token, "  Invitee@Example.COM ", PASSWORD).email == INVITEE

    def test_a_redemption_is_recorded(self, file_db) -> None:
        store, path = file_db
        token, _invite = store.create_invite()
        (before,) = _rows(path)
        assert before["redeemed_at"] is None
        assert before["redeemed_by"] is None

        user = store.redeem_invite(token, INVITEE, PASSWORD)

        (after,) = _rows(path)
        assert after["redeemed_at"] is not None
        assert after["redeemed_by"] == user.id

    def test_no_invite_can_exist_before_the_first_account(self, tmp_path: Path) -> None:
        """An invite minted into an empty store would hand out the OWNER's data.

        ``app.storage.user_dbs.owner_user_id`` makes the lowest-id account the
        owner of the flat databases when ``owner_email`` is unset. With no
        accounts, whoever redeems first IS that account. Accounts are never
        deleted and ids never reused, so refusing at mint time is sufficient.
        """
        path = tmp_path / "auth.db"
        store = AuthDatabase(str(path))
        try:
            with pytest.raises(auth_database.NoAccountsError):
                store.create_invite()
        finally:
            store.close()

        assert _rows(path) == []

    def test_minting_purges_expired_unredeemed_invites_only(self, file_db) -> None:
        """Same reason sessions are purged: an expired row is still a credential on disk.

        A REDEEMED row stays — its hash is useless and it is the only record of
        who came in on which invite.
        """
        store, path = file_db
        _stale, _ = store.create_invite(ttl=DAY, now=T0)
        used, _ = store.create_invite(ttl=DAY, now=T0)
        store.redeem_invite(used, INVITEE, PASSWORD, now=T0 + timedelta(hours=1))

        fresh, _ = store.create_invite(ttl=DAY, now=T0 + 3 * DAY)

        assert {row["token_hash"] for row in _rows(path)} == {hash_token(used), hash_token(fresh)}

    def test_concurrent_redemptions_create_exactly_one_account(self, file_db) -> None:
        """Single-use must hold under a race, not only on a sequential replay.

        The wrong-but-obvious implementation reads the row, sees it unredeemed,
        and then writes — and eight callers all pass the read. The claim and
        the insert have to be one transaction whose FIRST statement is the
        write (or an explicit ``BEGIN IMMEDIATE``): a deferred transaction that
        reads and then upgrades fails with ``database is locked`` under WAL
        instead of waiting, which this test also rejects.
        """
        store, _path = file_db
        token, _invite = store.create_invite()
        racers = 8
        barrier = threading.Barrier(racers)
        outcomes: list[object] = []

        def attempt(index: int) -> None:
            barrier.wait()
            try:
                outcomes.append(store.redeem_invite(token, f"racer{index}@example.com", PASSWORD))
            except Exception as exc:
                outcomes.append(exc)

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(racers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        winners = [o for o in outcomes if not isinstance(o, Exception)]
        losers = [o for o in outcomes if isinstance(o, Exception)]
        assert len(winners) == 1, outcomes
        assert [type(o) for o in losers] == [auth_database.InvalidInviteError] * (racers - 1), outcomes
        assert len(store.list_users()) == 2


# ── The endpoint ─────────────────────────────────────────────────────────────


@pytest.fixture
def auth_db(monkeypatch: pytest.MonkeyPatch):
    """A real in-memory store with the owner in it, bound to the app, gate ON."""
    store = AuthDatabase(":memory:")
    store.create_user(OWNER, OWNER_PASSWORD)
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(app.state, "auth_db", store, raising=False)
    yield store
    store.close()


def _client() -> AsyncClient:
    """Over https: the session cookie is ``Secure`` (see test_auth_login_roundtrip)."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


def _body(token: str, email: str = INVITEE, password: str = PASSWORD) -> dict[str, str]:
    return {"token": token, "email": email, "password": password}


class TestRedeemEndpoint:
    async def test_redeem_then_sign_in(self, auth_db: AuthDatabase) -> None:
        """Redeeming creates the account and does NOT log you in.

        One way in — the login endpoint, with its throttle — rather than two.
        The response therefore sets no cookie at all.
        """
        token, _invite = auth_db.create_invite()
        async with _client() as client:
            redeemed = await client.post(REDEEM_PATH, json=_body(token))
            assert redeemed.status_code == 200, redeemed.text
            assert redeemed.json() == {"email": INVITEE}
            assert "set-cookie" not in redeemed.headers
            assert (await client.get("/api/auth/me")).status_code == 401

            login = await client.post("/api/auth/login", json={"email": INVITEE, "password": PASSWORD})
            assert login.status_code == 200, login.text
            assert COOKIE_NAME in login.cookies
            assert (await client.get("/api/auth/me")).json() == {"email": INVITEE}

    async def test_every_dead_token_gets_the_same_answer(self, auth_db: AuthDatabase) -> None:
        """Unknown, expired and already-used are one response, byte for byte.

        Telling them apart tells a stranger which strings were once real
        invites.
        """
        expired, _ = auth_db.create_invite(ttl=DAY, now=datetime.now(UTC) - 2 * DAY)
        used, _ = auth_db.create_invite()
        async with _client() as client:
            assert (await client.post(REDEEM_PATH, json=_body(used))).status_code == 200
            answers = [
                await client.post(REDEEM_PATH, json=_body(dead, email=f"late{i}@example.com"))
                for i, dead in enumerate(["not-a-real-invite", expired, used])
            ]

        assert [a.status_code for a in answers] == [403, 403, 403]
        assert len({a.text for a in answers}) == 1
        assert [u.email for u in auth_db.list_users()] == [OWNER, INVITEE]

    async def test_a_dead_token_with_a_registered_email_looks_like_any_dead_token(self, auth_db: AuthDatabase) -> None:
        """The enumeration guard, at the HTTP boundary."""
        async with _client() as client:
            registered = await client.post(REDEEM_PATH, json=_body("not-a-real-invite", email=OWNER))
            unregistered = await client.post(REDEEM_PATH, json=_body("not-a-real-invite"))

        assert registered.status_code == 403
        assert (registered.status_code, registered.text) == (unregistered.status_code, unregistered.text)

    async def test_a_registered_email_is_409_and_the_invite_survives(self, auth_db: AuthDatabase) -> None:
        token, _invite = auth_db.create_invite()
        async with _client() as client:
            taken = await client.post(REDEEM_PATH, json=_body(token, email=OWNER))
            assert taken.status_code == 409, taken.text

            assert (await client.post(REDEEM_PATH, json=_body(token))).status_code == 200

        assert auth_db.verify_credentials(OWNER, OWNER_PASSWORD) is not None

    @pytest.mark.parametrize(
        "change",
        [
            pytest.param({"password": "7 chars"}, id="password-too-short"),
            pytest.param({"password": "x" * 1025}, id="password-absurdly-long"),
            pytest.param({"email": "not-an-email"}, id="email-without-an-at"),
            pytest.param({"password": None}, id="password-missing"),
        ],
    )
    async def test_an_unusable_body_is_422_and_the_invite_survives(
        self, auth_db: AuthDatabase, change: dict[str, str | None]
    ) -> None:
        """Validation runs before the token is touched.

        The 1024-character ceiling is not about entropy: this endpoint is
        unauthenticated and argon2 is deliberately slow, so an unbounded
        password is a free CPU-burn primitive.
        """
        token, _invite = auth_db.create_invite()
        bad = {k: v for k, v in (_body(token) | change).items() if v is not None}
        async with _client() as client:
            assert (await client.post(REDEEM_PATH, json=bad)).status_code == 422
            assert [u.email for u in auth_db.list_users()] == [OWNER]

            assert (await client.post(REDEEM_PATH, json=_body(token))).status_code == 200

    async def test_an_eight_character_password_is_accepted(self, auth_db: AuthDatabase) -> None:
        """The floor is 8, inclusive — the boundary the 422 case sits just under."""
        token, _invite = auth_db.create_invite()
        async with _client() as client:
            response = await client.post(REDEEM_PATH, json=_body(token, password="8 chars!"))

        assert response.status_code == 200, response.text

    async def test_no_auth_store_is_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(app.state, "auth_db", None, raising=False)
        async with _client() as client:
            response = await client.post(REDEEM_PATH, json=_body("anything"))

        assert response.status_code == 503

    async def test_redeeming_works_with_the_gate_off(
        self, auth_db: AuthDatabase, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``auth_enabled`` turns the GATE off, not the account machinery (as for login)."""
        monkeypatch.setattr(settings, "auth_enabled", False)
        token, _invite = auth_db.create_invite()
        async with _client() as client:
            response = await client.post(REDEEM_PATH, json=_body(token))

        assert response.status_code == 200, response.text


# ── No open signup ───────────────────────────────────────────────────────────


def _files_mentioning(pattern: str) -> set[str]:
    """Modules under ``backend/app`` whose source matches *pattern*, as posix paths."""
    regex = re.compile(pattern, re.IGNORECASE)
    return {
        path.relative_to(APP_DIR).as_posix() for path in APP_DIR.rglob("*.py") if regex.search(path.read_text("utf-8"))
    }


class TestNoOpenSignup:
    async def test_no_anonymous_request_creates_an_account(self, auth_db: AuthDatabase) -> None:
        """Sweep EVERY route, exempt ones included, with a plausible signup body.

        ``test_auth_route_coverage`` proves the gated routes answer 401. This is
        the other half: of the handful that answer WITHOUT a session, none
        creates an account for a caller who holds no invite. A future
        ``/api/auth/register`` added to the exempt list goes red here.

        The two assertions before the sweep's verdict are anti-vacuity: the
        traversal found the real surface, and the redeem route is in it.
        """
        body = {
            "email": "intruder@example.com",
            "password": "a perfectly good passphrase",
            "token": "not-a-real-invite",
        }
        swept: set[tuple[str, str]] = set()
        for route in _iter_api_routes(app.routes):
            swept.update((method, route.path) for method in route.methods - {"HEAD", "OPTIONS"})
        assert len(swept) >= MIN_EXPECTED_ROUTES
        assert ("POST", REDEEM_PATH) in swept

        async with _client() as client:
            for method, path in sorted(swept):
                await client.request(method, _concrete_path(path), json=body)

        assert [u.email for u in auth_db.list_users()] == [OWNER]

    def test_the_redeem_route_is_the_only_new_hole_in_the_gate(self) -> None:
        """The exempt list, pinned a second time on purpose.

        Opening signup means adding a path here. Having to change it in two
        files, one of which is named for invites, is the speed bump.
        """
        assert (
            frozenset({"/api/health", "/api/auth/login", "/api/auth/logout", "/api/auth/status", REDEEM_PATH})
            == EXEMPT_PATHS
        )

    def test_account_creation_has_exactly_these_entry_points(self) -> None:
        """A grep, deliberately: a new signup path has to go through one of these.

        If this fails because a docstring or comment merely MENTIONS one of the
        calls, reword the prose — do not widen the sets.
        """
        assert _files_mentioning(r"insert\s+into\s+users") == {"auth/database.py"}
        assert _files_mentioning(r"\.create_user\(") == {"auth/cli.py"}
        assert _files_mentioning(r"\.create_invite\(") == {"auth/cli.py"}
        assert _files_mentioning(r"\.redeem_invite\(") == {"api/auth.py"}


# ── The CLI ──────────────────────────────────────────────────────────────────

_LINK_RE = re.compile(r"/invite#([A-Za-z0-9_-]+)")


def _run(argv: list[str]) -> tuple[int, str]:
    """Invoke the CLI, returning ``(exit code, stdout)``. An argparse exit counts."""
    out = io.StringIO()
    try:
        code = cli_main(argv, env={}, stdin=io.StringIO(""), out=out)
    except SystemExit as exc:
        code = exc.code
    return code, out.getvalue()


class TestCreateInviteCli:
    @pytest.fixture(autouse=True)
    def auth_db_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Point the CLI at a throwaway ``auth.db`` and hand back its path."""
        path = tmp_path / "auth.db"
        monkeypatch.setattr(settings, "auth_database_url", f"sqlite:///{path}")
        return path

    @pytest.fixture
    def owner(self, auth_db_path: Path) -> None:
        store = AuthDatabase(str(auth_db_path))
        store.create_user(OWNER, OWNER_PASSWORD)
        store.close()

    @pytest.mark.usefixtures("owner")
    def test_prints_one_redeem_link_and_its_token_works(self, auth_db_path: Path) -> None:
        """The link carries the token in the FRAGMENT (``/invite#…``), never a query.

        A fragment is not sent to the server, so the token stays out of the
        reverse proxy's access log and out of any ``Referer``.
        """
        code, out = _run(["create-invite"])

        assert code == 0
        tokens = _LINK_RE.findall(out)
        assert len(tokens) == 1, out
        assert "?" not in out
        store = AuthDatabase(str(auth_db_path))
        try:
            assert store.redeem_invite(tokens[0], INVITEE, PASSWORD).email == INVITEE
        finally:
            store.close()

    @pytest.mark.usefixtures("owner")
    def test_ttl_days_sets_the_lifetime(self, auth_db_path: Path) -> None:
        code, _out = _run(["create-invite", "--ttl-days", "3"])

        assert code == 0
        (row,) = _rows(auth_db_path)
        lifetime = datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["created_at"])
        assert lifetime == timedelta(days=3)

    @pytest.mark.usefixtures("owner")
    @pytest.mark.parametrize("days", ["0", "-1"])
    def test_a_non_positive_lifetime_is_refused(self, auth_db_path: Path, days: str) -> None:
        """An invite that is born expired is a confusing way to say no."""
        code, out = _run(["create-invite", "--ttl-days", days])

        assert code == 2
        assert _LINK_RE.findall(out) == []
        assert _rows(auth_db_path) == []

    def test_refuses_when_there_are_no_accounts(self, auth_db_path: Path) -> None:
        code, out = _run(["create-invite"])

        assert code == 1
        assert _LINK_RE.findall(out) == []
        assert _rows(auth_db_path) == []
