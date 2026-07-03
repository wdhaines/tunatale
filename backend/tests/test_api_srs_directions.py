"""Tests for direction-aware SRS endpoints and drill feedback."""

from __future__ import annotations

import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.language import Language
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore


def _unit(text: str, translation: str = "test", word_count: int = 1) -> SyntacticUnit:
    return SyntacticUnit(text=text, translation=translation, word_count=word_count, difficulty=1, source="corpus")


@pytest.fixture(autouse=True)
def _clean_app_state():
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    app.state.srs_db = db
    app.state.content_store = store
    app.state.language = Language.slovene()
    yield
    db.close()
    store.close()
    for attr in ("srs_db", "content_store", "language", "llm"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def _db() -> SRSDatabase:
    return app.state.srs_db


def _advance_recognition_due(db: SRSDatabase, text: str) -> None:
    """Set recognition due_date to yesterday so item is due."""
    item = db.get_collocation(text)
    assert item is not None
    rec = item.directions[Direction.RECOGNITION]
    rec.due_at = datetime.datetime.combine(
        datetime.date.today() - datetime.timedelta(days=1),
        datetime.time(4, 0),
        tzinfo=datetime.UTC,
    )
    rec.state = SRSState.REVIEW
    db.update_direction(item.guid, Direction.RECOGNITION, rec)


def _advance_production_due(db: SRSDatabase, text: str) -> None:
    """Set production due_date to yesterday so item is due."""
    item = db.get_collocation(text)
    assert item is not None
    prod = item.directions[Direction.PRODUCTION]
    prod.due_at = datetime.datetime.combine(
        datetime.date.today() - datetime.timedelta(days=1),
        datetime.time(4, 0),
        tzinfo=datetime.UTC,
    )
    prod.state = SRSState.REVIEW
    db.update_direction(item.guid, Direction.PRODUCTION, prod)


class TestDirectionAwareDueEndpoint:
    """GET /api/srs/due?direction=... partitions correctly."""

    async def test_due_recognition_only(self):
        db = _db()
        db.add_collocation(_unit("beseda"), language_code="sl")
        _advance_recognition_due(db, "beseda")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due", params={"direction": "recognition"})
        assert r.status_code == 200
        texts = [i["text"] for i in r.json()["due"]]
        assert "beseda" in texts

    async def test_due_production_only(self):
        db = _db()
        db.add_collocation(_unit("beseda"), language_code="sl")
        _advance_production_due(db, "beseda")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due", params={"direction": "production"})
        assert r.status_code == 200
        texts = [i["text"] for i in r.json()["due"]]
        assert "beseda" in texts

    async def test_due_recognition_does_not_show_production_only(self):
        db = _db()
        db.add_collocation(_unit("beseda"), language_code="sl")
        _advance_production_due(db, "beseda")
        # recognition not advanced — state is 'new', which is excluded

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due", params={"direction": "recognition"})
        assert r.status_code == 200
        texts = [i["text"] for i in r.json()["due"]]
        assert "beseda" not in texts

    async def test_due_any_returns_union(self):
        db = _db()
        db.add_collocation(_unit("rec_only"), language_code="sl")
        db.add_collocation(_unit("prod_only"), language_code="sl")
        _advance_recognition_due(db, "rec_only")
        _advance_production_due(db, "prod_only")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due", params={"direction": "any"})
        assert r.status_code == 200
        texts = [i["text"] for i in r.json()["due"]]
        assert "rec_only" in texts
        assert "prod_only" in texts

    async def test_due_any_deduplicates(self):
        """Item due in both directions appears only once."""
        db = _db()
        db.add_collocation(_unit("both"), language_code="sl")
        _advance_recognition_due(db, "both")
        _advance_production_due(db, "both")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due", params={"direction": "any"})
        assert r.status_code == 200
        texts = [i["text"] for i in r.json()["due"]]
        assert texts.count("both") == 1

    async def test_due_default_is_recognition_compat(self):
        """No direction param → recognition (back-compat)."""
        db = _db()
        db.add_collocation(_unit("rec"), language_code="sl")
        db.add_collocation(_unit("prod"), language_code="sl")
        _advance_recognition_due(db, "rec")
        _advance_production_due(db, "prod")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due")
        assert r.status_code == 200
        texts = [i["text"] for i in r.json()["due"]]
        assert "rec" in texts
        assert "prod" not in texts


class TestDirectionAwareNewEndpoint:
    """GET /api/srs/new?direction=... respects direction."""

    async def test_new_recognition_returns_new_items(self):
        db = _db()
        db.add_collocation(_unit("nieuw"), language_code="sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/new", params={"direction": "recognition"})
        assert r.status_code == 200
        assert any(i["text"] == "nieuw" for i in r.json()["new"])

    async def test_new_default_is_recognition(self):
        db = _db()
        db.add_collocation(_unit("word"), language_code="sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/new")
        assert r.status_code == 200
        assert any(i["text"] == "word" for i in r.json()["new"])


class TestItemDictShape:
    """_item_to_dict emits directions block + flat shims."""

    async def test_due_item_has_directions_block(self):
        db = _db()
        db.add_collocation(_unit("okno"), language_code="sl")
        _advance_recognition_due(db, "okno")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due", params={"direction": "recognition"})
        assert r.status_code == 200
        items = r.json()["due"]
        assert len(items) == 1
        item = items[0]

        # Flat shims still present
        assert "text" in item
        assert "state" in item
        assert "due_at" in item

        # directions block present
        assert "directions" in item
        dirs = item["directions"]
        assert "recognition" in dirs
        assert "production" in dirs

        rec = dirs["recognition"]
        for key in ("state", "due_at", "stability", "difficulty", "reps", "lapses", "last_review", "anki_card_id"):
            assert key in rec, f"Missing key {key!r} in recognition direction"

    async def test_due_item_has_id(self):
        db = _db()
        db.add_collocation(_unit("miza"), language_code="sl")
        _advance_recognition_due(db, "miza")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due", params={"direction": "recognition"})
        items = r.json()["due"]
        assert items[0]["id"] > 0

    async def test_due_item_has_image_url_field(self):
        db = _db()
        db.add_collocation(_unit("slika"), language_code="sl")
        _advance_recognition_due(db, "slika")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due", params={"direction": "recognition"})
        item = r.json()["due"][0]
        assert "image_url" in item  # null when no media row exists
        assert item["image_url"] is None

    async def test_due_item_has_word_count(self):
        db = _db()
        db.add_collocation(_unit("dober dan", word_count=2), language_code="sl")
        _advance_recognition_due(db, "dober dan")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due", params={"direction": "recognition"})
        item = r.json()["due"][0]
        assert item["word_count"] == 2

    async def test_due_item_has_guid_and_anki_note_id(self):
        db = _db()
        db.add_collocation(_unit("hiša"), language_code="sl")
        _advance_recognition_due(db, "hiša")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due", params={"direction": "recognition"})
        item = r.json()["due"][0]
        assert "guid" in item
        assert item["guid"] is not None
        assert len(item["guid"]) == 16
        assert "anki_note_id" in item
        assert item["anki_note_id"] is None  # not set by add_collocation


class TestDrillEndpoint:
    """POST /api/srs/items/{id}/direction/{direction}/feedback"""

    async def test_recognition_drill_advances_recognition_only(self):
        db = _db()
        db.add_collocation(_unit("voda"), language_code="sl")
        rows, _ = db.list_collocations(search="voda", limit=1)
        item_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                f"/api/srs/items/{item_id}/direction/recognition/feedback",
                json={"rating": "good"},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["direction"] == "recognition"
        assert "new_due_at" in body
        assert "new_state" in body

        # production direction should still be NEW
        updated = db.get_collocation_by_id(item_id)
        assert updated is not None
        _, item, _ = updated
        assert item.directions[Direction.PRODUCTION].state == SRSState.NEW

    async def test_production_drill_advances_production_only(self):
        db = _db()
        db.add_collocation(_unit("voda"), language_code="sl")
        rows, _ = db.list_collocations(search="voda", limit=1)
        item_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                f"/api/srs/items/{item_id}/direction/production/feedback",
                json={"rating": "good"},
            )
        assert r.status_code == 200
        assert r.json()["direction"] == "production"

        # recognition direction should still be NEW
        updated = db.get_collocation_by_id(item_id)
        assert updated is not None
        _, item, _ = updated
        assert item.directions[Direction.RECOGNITION].state == SRSState.NEW

    async def test_unknown_direction_returns_422(self):
        db = _db()
        db.add_collocation(_unit("voda"), language_code="sl")
        rows, _ = db.list_collocations(search="voda", limit=1)
        item_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                f"/api/srs/items/{item_id}/direction/baddir/feedback",
                json={"rating": "good"},
            )
        assert r.status_code == 422

    async def test_missing_rating_and_signal_returns_400(self):
        db = _db()
        db.add_collocation(_unit("voda"), language_code="sl")
        rows, _ = db.list_collocations(search="voda", limit=1)
        item_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                f"/api/srs/items/{item_id}/direction/recognition/feedback",
                json={},
            )
        assert r.status_code == 400

    async def test_signal_also_accepted(self):
        db = _db()
        db.add_collocation(_unit("voda"), language_code="sl")
        rows, _ = db.list_collocations(search="voda", limit=1)
        item_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                f"/api/srs/items/{item_id}/direction/recognition/feedback",
                json={"signal": "no_help"},
            )
        assert r.status_code == 200

    async def test_nonexistent_item_returns_404(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/srs/items/99999/direction/recognition/feedback",
                json={"rating": "good"},
            )
        assert r.status_code == 404


class TestReadAheadRecognitionGrade:
    """Read-ahead review of a not-due recognition card (the review-ahead feature).

    The frontend submits the literal recognition direction; the endpoint has no
    due-ness gate, so these lock the backend semantics that back that flow.
    """

    def _graduate(self, db: SRSDatabase, text: str) -> int:
        """Add a word with both directions in REVIEW, not due, and return its id."""
        db.add_collocation(_unit(text), language_code="sl")
        item = db.get_collocation(text)
        for d in (Direction.RECOGNITION, Direction.PRODUCTION):
            ds = item.directions[d]
            ds.state = SRSState.REVIEW
            ds.due_at = datetime.datetime(2099, 1, 1, 4, 0, tzinfo=datetime.UTC)
            ds.last_review = datetime.datetime(2026, 1, 1, 4, 0, tzinfo=datetime.UTC)
            ds.reps = 3
            ds.stability = 10.0
            ds.difficulty = 5.0
            db.update_direction(item.guid, d, ds)
        rows, _ = db.list_collocations(search=text, limit=1)
        return rows[0][0]

    async def test_grade_recognition_leaves_production_untouched(self):
        """Guardrail: read-ahead grades RECOGNITION even when active_direction is
        PRODUCTION (graduated word). Production must be untouched."""
        db = _db()
        item_id = self._graduate(db, "voda")
        _, before, _ = db.get_collocation_by_id(item_id)
        prod_before = before.directions[Direction.PRODUCTION]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                f"/api/srs/items/{item_id}/direction/recognition/feedback",
                json={"rating": "good", "time_ms": 1000},
            )
        assert r.status_code == 200
        assert r.json()["direction"] == "recognition"

        _, after, _ = db.get_collocation_by_id(item_id)
        rec_after = after.directions[Direction.RECOGNITION]
        prod_after = after.directions[Direction.PRODUCTION]
        # Recognition advanced (last_review re-stamped to ~now).
        assert rec_after.last_review is not None
        assert rec_after.last_review.year >= 2026
        assert rec_after.last_review != datetime.datetime(2026, 1, 1, 4, 0, tzinfo=datetime.UTC)
        # A revlog row was written for the recognition direction.
        assert db.latest_revlog_id_for_direction(item_id, Direction.RECOGNITION) is not None
        # Production is completely untouched.
        assert prod_after.last_review == prod_before.last_review
        assert prod_after.due_at == prod_before.due_at
        assert prod_after.stability == prod_before.stability
        assert prod_after.state == prod_before.state

    async def test_early_review_dampens_interval_vs_on_schedule(self):
        """Early (well-spaced) recognition review yields a smaller resulting
        interval than an on-schedule review of an identical card — FSRS
        elapsed-based dampening, no queue/FSRS math change needed."""
        db = _db()
        now = datetime.datetime.now(datetime.UTC)
        # Two identical REVIEW cards, stability 10; one reviewed 1 day in (early,
        # high retrievability), one reviewed ~10 days in (on schedule, R≈0.9).
        specs = {"early": 1, "ontime": 10}
        ids: dict[str, int] = {}
        for text, elapsed_days in specs.items():
            db.add_collocation(_unit(text), language_code="sl")
            item = db.get_collocation(text)
            rec = item.directions[Direction.RECOGNITION]
            rec.state = SRSState.REVIEW
            rec.reps = 3
            rec.stability = 10.0
            rec.difficulty = 5.0
            rec.last_review = now - datetime.timedelta(days=elapsed_days)
            rec.due_at = rec.last_review + datetime.timedelta(days=10)
            db.update_direction(item.guid, Direction.RECOGNITION, rec)
            rows, _ = db.list_collocations(search=text, limit=1)
            ids[text] = rows[0][0]

        intervals: dict[str, float] = {}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            for text, item_id in ids.items():
                r = await c.post(
                    f"/api/srs/items/{item_id}/direction/recognition/feedback",
                    json={"rating": "good", "time_ms": 1000},
                )
                assert r.status_code == 200
                body = r.json()
                assert body["new_state"] == "review"
                new_due = datetime.datetime.fromisoformat(body["new_due_at"])
                intervals[text] = (new_due - now).total_seconds()

        # The early review's resulting interval is smaller — reviewing while
        # retrievability is still high grows stability less.
        assert intervals["early"] < intervals["ontime"]


class _SpyBalancer:
    """Stand-in load balancer: records add_card, never relocates (find_interval=None)."""

    def __init__(self):
        self.added: list[tuple[int, int, int]] = []

    def find_interval(self, *args, **kwargs):
        return None

    def add_card(self, card_id, note_id, interval):
        self.added.append((card_id, note_id, interval))


class TestDrillLoadBalancerWiring:
    """Layer 55: drill_feedback builds the session balancer, threads it into
    schedule(), and add_card's the graded card afterward."""

    async def test_enabled_passes_balancer_and_adds_card(self, monkeypatch):
        import app.api.srs as srs_mod

        spy = _SpyBalancer()
        captured: dict[str, object] = {}
        monkeypatch.setattr(srs_mod, "build_live_load_balancer", lambda db, **k: spy)
        orig = srs_mod.schedule

        def spy_schedule(*a, **k):
            captured["lb"] = k.get("load_balancer")
            return orig(*a, **k)

        monkeypatch.setattr(srs_mod, "schedule", spy_schedule)

        db = _db()
        db.add_collocation(_unit("voda"), language_code="sl")
        rows, _ = db.list_collocations(search="voda", limit=1)
        item_id = rows[0][0]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(f"/api/srs/items/{item_id}/direction/recognition/feedback", json={"rating": "good"})
        assert r.status_code == 200
        assert captured["lb"] is spy
        assert len(spy.added) == 1  # the graded card was fed back into the histogram

    async def test_disabled_passes_none(self, monkeypatch):
        import app.api.srs as srs_mod

        captured: dict[str, object] = {"lb": "sentinel"}
        monkeypatch.setattr(srs_mod, "build_live_load_balancer", lambda db, **k: None)
        orig = srs_mod.schedule

        def spy_schedule(*a, **k):
            captured["lb"] = k.get("load_balancer")
            return orig(*a, **k)

        monkeypatch.setattr(srs_mod, "schedule", spy_schedule)

        db = _db()
        db.add_collocation(_unit("voda"), language_code="sl")
        rows, _ = db.list_collocations(search="voda", limit=1)
        item_id = rows[0][0]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(f"/api/srs/items/{item_id}/direction/recognition/feedback", json={"rating": "good"})
        assert r.status_code == 200
        assert captured["lb"] is None


class TestInvalidDirectionParams:
    async def test_due_invalid_direction_422(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due", params={"direction": "sideways"})
        assert r.status_code == 422

    async def test_new_invalid_direction_422(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/new", params={"direction": "sideways"})
        assert r.status_code == 422


class TestMediaEndpoint:
    async def test_missing_file_returns_404(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/media/no_such_file_xyz.jpg")
        assert r.status_code == 404

    async def test_path_traversal_guard(self):
        from fastapi import HTTPException

        from app.api.srs import serve_media

        with pytest.raises(HTTPException) as exc_info:
            await serve_media("..")
        assert exc_info.value.status_code == 400

    async def test_sibling_prefix_dir_traversal_400(self, tmp_path, monkeypatch):
        """A bare startswith() prefix check passes for a SIBLING directory whose
        name extends the media dir's ("media" vs "media-evil") — the guard must
        use is_relative_to, not string prefixing."""
        from fastapi import HTTPException

        import app.api.srs as srs_mod
        from app.api.srs import serve_media

        media_dir = tmp_path / "media"
        media_dir.mkdir()
        evil_dir = tmp_path / "media-evil"
        evil_dir.mkdir()
        (evil_dir / "secret.txt").write_text("leaked")
        monkeypatch.setattr(srs_mod, "_MEDIA_DIR", media_dir)

        with pytest.raises(HTTPException) as exc_info:
            await serve_media("../media-evil/secret.txt")
        assert exc_info.value.status_code == 400

    async def test_existing_file_returns_200(self, tmp_path, monkeypatch):
        import app.api.srs as srs_mod

        test_file = tmp_path / "test_audio.mp3"
        test_file.write_bytes(b"fake audio")
        monkeypatch.setattr(srs_mod, "_MEDIA_DIR", tmp_path)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/media/test_audio.mp3")
        assert r.status_code == 200


class TestUpdateDirectionByIdMissing:
    """update_direction_by_id is a no-op for unknown row_id."""

    def test_missing_id_is_no_op(self):
        db = _db()
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.datetime.combine(
                datetime.date.today(),
                datetime.time(4, 0),
                tzinfo=datetime.UTC,
            ),
            state=SRSState.NEW,
        )
        db.update_direction_by_id(99999, Direction.RECOGNITION, ds)  # should not raise


class TestUndoGradeEndpoint:
    """POST /api/srs/items/{id}/direction/{direction}/undo — single-level grade undo.

    The popover's "Got it ✓" needs a cycle back ("Undo ↩"): restore the exact
    pre-grade DirectionState and delete the grade's tt_revlog row, but ONLY
    while the grade is still TT-local (dirty_fsrs=1, revlog row still the
    latest). After a sync Anki owns the review — undo must refuse (409).
    """

    def _item_id(self) -> int:
        db = _db()
        db.add_collocation(_unit("voda"), language_code="sl")
        rows, _ = db.list_collocations(search="voda", limit=1)
        return rows[0][0]

    @staticmethod
    def _rec_state(item_id: int) -> DirectionState:
        result = _db().get_collocation_by_id(item_id)
        assert result is not None
        _, item, _ = result
        return item.directions[Direction.RECOGNITION]

    async def test_undo_restores_exact_prior_state_and_deletes_revlog(self):
        item_id = self._item_id()
        prior = self._rec_state(item_id)
        assert prior.introduced_at is None  # first grade stamps it; undo must unstamp

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                f"/api/srs/items/{item_id}/direction/recognition/feedback",
                json={"rating": "good"},
            )
            assert r.status_code == 200
            assert _db().latest_revlog_id_for_direction(item_id, Direction.RECOGNITION) is not None
            assert self._rec_state(item_id) != prior

            r2 = await c.post(f"/api/srs/items/{item_id}/direction/recognition/undo")
        assert r2.status_code == 200
        assert r2.json()["status"] == "ok"
        assert self._rec_state(item_id) == prior
        assert _db().latest_revlog_id_for_direction(item_id, Direction.RECOGNITION) is None

    async def test_undo_without_any_grade_returns_409(self):
        item_id = self._item_id()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(f"/api/srs/items/{item_id}/direction/recognition/undo")
        assert r.status_code == 409

    async def test_undo_wrong_direction_returns_409(self):
        item_id = self._item_id()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post(
                f"/api/srs/items/{item_id}/direction/recognition/feedback",
                json={"rating": "good"},
            )
            r = await c.post(f"/api/srs/items/{item_id}/direction/production/undo")
        assert r.status_code == 409

    async def test_undo_twice_returns_409_second_time(self):
        item_id = self._item_id()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post(
                f"/api/srs/items/{item_id}/direction/recognition/feedback",
                json={"rating": "good"},
            )
            r1 = await c.post(f"/api/srs/items/{item_id}/direction/recognition/undo")
            r2 = await c.post(f"/api/srs/items/{item_id}/direction/recognition/undo")
        assert r1.status_code == 200
        assert r2.status_code == 409

    async def test_undo_after_sync_cleared_dirty_returns_409(self):
        """Once sync clears dirty_fsrs the grade lives in Anki — undo must refuse."""
        item_id = self._item_id()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post(
                f"/api/srs/items/{item_id}/direction/recognition/feedback",
                json={"rating": "good"},
            )
            db = _db()
            result = db.get_collocation_by_id(item_id)
            assert result is not None
            _, item, _ = result
            db.mark_direction_clean(item.guid, Direction.RECOGNITION)

            r = await c.post(f"/api/srs/items/{item_id}/direction/recognition/undo")
        assert r.status_code == 409

    async def test_undo_after_second_grade_restores_post_first_grade_state(self):
        """Single-level: undo unwinds only the most recent grade."""
        import asyncio

        item_id = self._item_id()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post(
                f"/api/srs/items/{item_id}/direction/recognition/feedback",
                json={"rating": "good"},
            )
            after_first = self._rec_state(item_id)
            await asyncio.sleep(0.002)  # distinct revlog id (ms-resolution)
            await c.post(
                f"/api/srs/items/{item_id}/direction/recognition/feedback",
                json={"rating": "good"},
            )
            assert self._rec_state(item_id) != after_first

            r1 = await c.post(f"/api/srs/items/{item_id}/direction/recognition/undo")
            r2 = await c.post(f"/api/srs/items/{item_id}/direction/recognition/undo")
        assert r1.status_code == 200
        assert self._rec_state(item_id) == after_first
        assert r2.status_code == 409

    async def test_undo_superseded_snapshot_returns_409(self):
        """A newer revlog row on the direction (e.g. a /listen grade) invalidates the snapshot."""
        from app.models.srs_item import RevlogRow

        item_id = self._item_id()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post(
                f"/api/srs/items/{item_id}/direction/recognition/feedback",
                json={"rating": "good"},
            )
            latest = _db().latest_revlog_id_for_direction(item_id, Direction.RECOGNITION)
            assert latest is not None
            _db().append_revlog(
                RevlogRow(
                    id=latest + 10_000,
                    collocation_id=item_id,
                    direction=Direction.RECOGNITION,
                    button_chosen=3,
                    interval=1,
                    last_interval=0,
                    factor=0,
                    taken_millis=1000,
                    review_kind=0,
                    anki_card_id=None,
                )
            )
            r = await c.post(f"/api/srs/items/{item_id}/direction/recognition/undo")
        assert r.status_code == 409

    async def test_undo_nonexistent_item_returns_404(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/srs/items/99999/direction/recognition/undo")
        assert r.status_code == 404

    async def test_undo_invalid_direction_returns_422(self):
        item_id = self._item_id()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(f"/api/srs/items/{item_id}/direction/baddir/undo")
        assert r.status_code == 422
