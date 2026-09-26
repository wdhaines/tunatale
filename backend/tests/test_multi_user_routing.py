"""Identity routes to data: per-user DB files, the owner keeps the flat ones (tunatale-3k8).

The claims, each of which a leak would falsify:

- Two accounts studying the same language see disjoint state — a write by one
  is invisible to the other, even at the SAME row id (both decks have an item 1).
- A non-owner can select only the languages it has a deck for, and a missing
  deck is never created by asking for it.
- Everything process-global (Anki sync, admin) is the owner's: a non-owner gets
  403 from every such route, enumerated from the live route table.
- Lesson writes are the owner's too (the pipeline is keyed by language alone),
  while lesson READS serve the non-owner's own, empty, store.
- Auth off is the single-user path, unchanged: the flat DBs serve every request.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import admin, anki, audio, curriculum, generation, llm, review_sessions
from app.auth.database import AuthDatabase
from app.auth.session import COOKIE_NAME
from app.config import settings
from app.languages import get_language
from app.main import _user_data_root, app
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore
from app.storage.user_dbs import UserDatabases, owner_user_id
from tests.test_auth_route_coverage import _concrete_path, _iter_api_routes

OWNER = "owner@example.com"
LEARNER = "learner@example.com"
PASSWORD = "correct horse battery staple"


def _unit(text: str) -> SyntacticUnit:
    return SyntacticUnit(text=text, translation="x", word_count=1, difficulty=1, source="corpus")


def _seed_user_deck(user_dbs: UserDatabases, user_id: int, code: str, *texts: str) -> Path:
    path = user_dbs.path_for(user_id, code)
    db = SRSDatabase(str(path))
    for text in texts:
        db.add_collocation(_unit(text))
    return path


@pytest.fixture
def world(tmp_path, monkeypatch):
    """An owner with Norwegian and Slovene decks, and a learner with Norwegian only."""
    owner_no, owner_sl = SRSDatabase(":memory:"), SRSDatabase(":memory:")
    owner_no.add_collocation(_unit("vann"))
    owner_sl.add_collocation(_unit("voda"))
    auth_db = AuthDatabase(":memory:")
    owner = auth_db.create_user(OWNER, PASSWORD)
    learner = auth_db.create_user(LEARNER, PASSWORD)
    user_dbs = UserDatabases(tmp_path / "users", ["no", "sl"])
    learner_path = _seed_user_deck(user_dbs, learner.id, "no", "hus")

    state = {
        "srs_dbs": {"no": owner_no, "sl": owner_sl},
        "content_stores": {"no": ContentStore(":memory:"), "sl": ContentStore(":memory:")},
        "languages": {"no": get_language("no"), "sl": get_language("sl")},
        "auth_db": auth_db,
        "user_dbs": user_dbs,
    }
    for name, value in state.items():
        monkeypatch.setattr(app.state, name, value, raising=False)
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "owner_email", "")
    monkeypatch.setattr(settings, "target_language", "no")
    yield {
        "owner": owner,
        "learner": learner,
        "owner_no": owner_no,
        "user_dbs": user_dbs,
        "learner_path": learner_path,
        "auth_db": auth_db,
        "tmp": tmp_path,
    }
    auth_db.close()


def _client(auth_db: AuthDatabase, user_id: int) -> AsyncClient:
    """Logged in as ``user_id``. https because the session cookie is Secure."""
    token, _ = auth_db.create_session(user_id)
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test", cookies={COOKIE_NAME: token})


async def _texts(client: AsyncClient, headers: dict | None = None) -> set[str]:
    resp = await client.get("/api/srs/items", headers=headers or {})
    assert resp.status_code == 200, resp.text
    return {item["text"] for item in resp.json()["items"]}


class TestDisjointState:
    async def test_each_account_reads_only_its_own_deck(self, world):
        async with _client(world["auth_db"], world["owner"].id) as owner:
            assert await _texts(owner) == {"vann"}
        async with _client(world["auth_db"], world["learner"].id) as learner:
            assert await _texts(learner) == {"hus"}

    async def test_a_write_at_the_same_row_id_lands_only_in_the_writers_file(self, world):
        """Both decks have an item 1. The learner edits theirs; the owner's is untouched."""
        async with _client(world["auth_db"], world["learner"].id) as learner:
            resp = await learner.patch("/api/srs/items/1", json={"text": "hytte", "translation": "cabin"})
            assert resp.status_code == 200, resp.text
            assert await _texts(learner) == {"hytte"}
        async with _client(world["auth_db"], world["owner"].id) as owner:
            assert await _texts(owner) == {"vann"}
        assert SRSDatabase(str(world["learner_path"])).get_collocation_by_id(1)[1].syntactic_unit.text == "hytte"

    async def test_auth_off_serves_the_flat_dbs_to_everyone(self, world, monkeypatch):
        monkeypatch.setattr(settings, "auth_enabled", False)
        async with _client(world["auth_db"], world["learner"].id) as anyone:
            assert await _texts(anyone) == {"vann"}
            assert await _texts(anyone, {"X-TT-Language": "sl"}) == {"voda"}


class TestLanguages:
    async def test_learner_lists_only_their_languages_and_no_sync(self, world):
        async with _client(world["auth_db"], world["learner"].id) as learner:
            resp = await learner.get("/api/languages")
        assert resp.status_code == 200
        assert resp.json() == {
            "languages": [{"code": "no", "name": get_language("no").name}],
            "active": "no",
            "sync_available": False,
        }

    async def test_learner_cannot_select_a_language_they_have_no_deck_for(self, world):
        async with _client(world["auth_db"], world["learner"].id) as learner:
            resp = await learner.get("/api/srs/items", headers={"X-TT-Language": "sl"})
        assert resp.status_code == 400
        assert not world["user_dbs"].path_for(world["learner"].id, "sl").exists(), "asking must never create a deck"

    async def test_a_stale_language_heals_through_the_languages_endpoint(self, world):
        async with _client(world["auth_db"], world["learner"].id) as learner:
            resp = await learner.get("/api/languages", headers={"X-TT-Language": "sl"})
        assert resp.status_code == 200
        assert resp.json()["active"] == "no"

    async def test_no_header_defaults_to_the_learners_first_language_not_the_servers(self, world, monkeypatch):
        monkeypatch.setattr(settings, "target_language", "sl")
        async with _client(world["auth_db"], world["learner"].id) as learner:
            assert await _texts(learner) == {"hus"}

    async def test_an_account_with_no_deck_gets_a_400_and_an_empty_list(self, world):
        stranger = world["auth_db"].create_user("stranger@example.com", PASSWORD)
        async with _client(world["auth_db"], stranger.id) as client:
            items = await client.get("/api/srs/items")
            langs = await client.get("/api/languages")
        assert items.status_code == 400
        assert "No language" in items.json()["detail"]
        assert langs.json() == {"languages": [], "active": "", "sync_available": False}

    async def test_owner_still_sees_every_configured_language_and_sync(self, world):
        async with _client(world["auth_db"], world["owner"].id) as owner:
            body = (await owner.get("/api/languages")).json()
        assert [lang["code"] for lang in body["languages"]] == ["no", "sl"]


def _routes(router) -> list[tuple[str, str]]:
    return sorted(
        (method, route.path)
        for route in _iter_api_routes(router.routes)
        for method in route.methods - {"HEAD", "OPTIONS"}
    )


OWNER_ONLY = _routes(anki.router) + _routes(admin.router)
LESSON_ROUTERS = [curriculum.router, generation.router, review_sessions.router, audio.router, llm.router]
LESSON_WRITES = [(m, p) for r in LESSON_ROUTERS for m, p in _routes(r) if m != "GET"]
LESSON_READS = [(m, p) for r in LESSON_ROUTERS for m, p in _routes(r) if m == "GET"]


class TestOwnerOnlySurfaces:
    def test_the_owner_only_surface_is_mounted_and_not_empty(self):
        """Anti-vacuity: an unmounted router would answer 404, not 403, and a
        sweep over an empty list passes by asserting nothing."""
        mounted = {route.path for route in _iter_api_routes(app.routes)}
        assert any(p == "/api/anki/peer-sync" for _, p in OWNER_ONLY)
        assert {p for _, p in OWNER_ONLY} <= mounted
        assert len(LESSON_WRITES) >= 20 and len(LESSON_READS) >= 15

    @pytest.mark.parametrize(("method", "path"), OWNER_ONLY + LESSON_WRITES)
    async def test_a_non_owner_is_refused(self, world, method, path):
        async with _client(world["auth_db"], world["learner"].id) as learner:
            resp = await learner.request(method, _concrete_path(path))
        assert resp.status_code == 403, f"{method} {path} answered {resp.status_code} to a non-owner"

    @pytest.mark.parametrize(("method", "path"), LESSON_READS)
    async def test_lesson_reads_are_not_refused(self, world, method, path):
        async with _client(world["auth_db"], world["learner"].id) as learner:
            resp = await learner.request(method, _concrete_path(path))
        assert resp.status_code != 403

    async def test_the_owner_is_not_refused(self, world):
        async with _client(world["auth_db"], world["owner"].id) as owner:
            resp = await owner.get("/api/admin/background-work")
        assert resp.status_code == 200

    async def test_anonymous_is_still_401_not_403(self, world):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as anon:
            assert (await anon.get("/api/admin/background-work")).status_code == 401

    async def test_lesson_reads_serve_the_learners_empty_store(self, world):
        async with _client(world["auth_db"], world["learner"].id) as learner:
            resp = await learner.get("/api/curriculum")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_lesson_audio_never_falls_back_to_the_owners_stores(self, world, monkeypatch):
        """The owner-side fallback searches every language's store for an id; a
        non-owner must search only their own."""
        seen = []

        def spy(self, audio_id):
            seen.append(self)

        monkeypatch.setattr(ContentStore, "get_audio_file_row", spy)
        async with _client(world["auth_db"], world["learner"].id) as learner:
            assert (await learner.get("/api/audio/abc")).status_code == 404
        assert len(seen) == 1, "the learner's own store is searched, and only it"
        assert not set(map(id, app.state.content_stores.values())) & set(map(id, seen))


class TestOwnerDesignation:
    def test_first_account_is_the_owner_by_default(self):
        db = AuthDatabase(":memory:")
        first = db.create_user(OWNER, PASSWORD)
        db.create_user(LEARNER, PASSWORD)
        assert owner_user_id(db, "") == first.id

    def test_named_owner_wins(self):
        db = AuthDatabase(":memory:")
        db.create_user(OWNER, PASSWORD)
        second = db.create_user(LEARNER, PASSWORD)
        assert owner_user_id(db, LEARNER) == second.id

    def test_a_named_owner_with_no_account_means_nobody(self):
        db = AuthDatabase(":memory:")
        db.create_user(OWNER, PASSWORD)
        assert owner_user_id(db, "typo@example.com") is None

    def test_no_accounts_or_no_store_means_nobody(self):
        assert owner_user_id(AuthDatabase(":memory:"), "") is None
        assert owner_user_id(None, "") is None

    async def test_naming_the_learner_hands_them_the_flat_dbs(self, world, monkeypatch):
        monkeypatch.setattr(settings, "owner_email", LEARNER)
        async with _client(world["auth_db"], world["learner"].id) as learner:
            assert await _texts(learner) == {"vann"}
        async with _client(world["auth_db"], world["owner"].id) as former_owner:
            assert (await former_owner.get("/api/srs/items")).status_code == 400


class TestUserDatabases:
    def test_path_refuses_anything_that_is_not_a_user_id_or_configured_code(self, tmp_path):
        dbs = UserDatabases(tmp_path, ["no"])
        assert dbs.path_for(7, "no") == tmp_path / "7" / "tunatale_no.db"
        for bad_id in (0, -1, True, "1", "../1"):
            with pytest.raises(ValueError):
                dbs.path_for(bad_id, "no")
        for bad_code in ("sl", "../no", "no/../x", ""):
            with pytest.raises(ValueError):
                dbs.path_for(7, bad_code)

    def test_get_never_creates_a_deck(self, tmp_path):
        dbs = UserDatabases(tmp_path, ["no"])
        assert dbs.get(7, "no") is None
        assert not (tmp_path / "7").exists()
        assert dbs.languages_for(7) == []

    def test_get_reuses_the_same_instances(self, tmp_path):
        dbs = UserDatabases(tmp_path, ["no"])
        _seed_user_deck(dbs, 7, "no", "hus")
        assert dbs.get(7, "no") is dbs.get(7, "no")

    def test_languages_follow_config_order(self, tmp_path):
        dbs = UserDatabases(tmp_path, ["sl", "no"])
        _seed_user_deck(dbs, 7, "no")
        _seed_user_deck(dbs, 7, "sl")
        assert dbs.languages_for(7) == ["sl", "no"]

    def test_first_open_backs_up_into_the_users_own_directory(self, tmp_path):
        """Backups are named by file stem, and every user's deck is tunatale_no.db."""
        dbs = UserDatabases(
            tmp_path / "users",
            ["no"],
            backup_dir=tmp_path / "bk",
            migration_backup_dir=tmp_path / "mig",
        )
        _seed_user_deck(dbs, 7, "no", "hus")
        dbs.get(7, "no")
        assert [p.name for p in (tmp_path / "bk" / "users" / "7").iterdir()][0].startswith("tunatale_no.")

    async def test_no_descriptor_is_held_on_a_learners_file_between_requests(self, world):
        """Why there is no LRU cap: a cached deck holds no file handle."""
        async with _client(world["auth_db"], world["learner"].id) as learner:
            await _texts(learner)
        target = str(world["learner_path"].resolve())
        with open(target, "rb"):
            # Control: the probe must see a descriptor that IS open, or an
            # empty answer below would mean nothing.
            assert any(p.startswith(target) for p in _open_paths())
        assert not [p for p in _open_paths() if p.startswith(target)]


def _open_paths() -> list[str]:
    """Paths of this process's open file descriptors (Linux /proc, macOS F_GETPATH)."""
    import fcntl

    paths = []
    fd_dir = "/proc/self/fd" if os.path.isdir("/proc/self/fd") else "/dev/fd"
    for name in os.listdir(fd_dir):
        fd = int(name)
        try:
            if fd_dir == "/proc/self/fd":
                paths.append(os.readlink(f"{fd_dir}/{name}"))
            else:
                paths.append(fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).rstrip(b"\0").decode())
        except OSError:
            continue
    return paths


class TestDataRoot:
    def test_defaults_beside_the_owners_dbs(self, monkeypatch):
        monkeypatch.setattr(settings, "user_data_dir", None)
        assert _user_data_root({"no": "sqlite:////data/tunatale_no.db"}) == Path("/data/users")

    def test_setting_wins(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "user_data_dir", tmp_path)
        assert _user_data_root({"no": "sqlite:////data/tunatale_no.db"}) == tmp_path


class TestIdempotencyIsPerUser:
    async def test_two_accounts_with_the_same_key_each_run_their_own_work(self):
        """The key is client-chosen; one account's replay must never return another's result."""
        from types import SimpleNamespace

        from app.api.idempotency import once

        fake_app = SimpleNamespace(state=SimpleNamespace())

        def request_for(user_id):
            return SimpleNamespace(app=fake_app, state=SimpleNamespace(user_id=user_id, language_code="no"))

        async def work_for(result):
            return result

        first = await once(request_for(1), "import", "same-key", lambda: work_for("owner's"))
        second = await once(request_for(2), "import", "same-key", lambda: work_for("learner's"))
        replay = await once(request_for(1), "import", "same-key", lambda: work_for("never runs"))
        assert (first, second, replay) == ("owner's", "learner's", "owner's")
