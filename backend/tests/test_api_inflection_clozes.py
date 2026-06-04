"""Tests for POST /api/srs/inflection-clozes (Phase 4a)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.common.guid import compute_guid
from app.main import app
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit


class TestInflectionClozes:
    """Phase 4a: on-demand morphology cloze creation."""

    @pytest.fixture(autouse=True)
    def _mock_audio(self, monkeypatch: pytest.MonkeyPatch):
        import app.api.srs as srs_mod

        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", AsyncMock())

    @staticmethod
    def _seed_base_learned(db):
        """Seed a base vocab collocation with production in REVIEW."""
        unit = SyntacticUnit(text="ljubljana", translation="ljubljana", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("ljubljana")
        today = date.today()
        db.update_direction(
            item.guid,
            Direction.PRODUCTION,
            DirectionState(
                direction=Direction.PRODUCTION,
                due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
                stability=5.0,
                difficulty=4.0,
                reps=5,
                state=SRSState.REVIEW,
            ),
        )

    async def test_eligible_base_creates_cloze(self, api_app_state):
        """Base production in REVIEW → cloze created with correct shape."""
        self._seed_base_learned(api_app_state)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["id"] > 0
        assert "item" in data

        guid = compute_guid("Ljubljano", "sl", "morph:noun-acc-sg")
        cloze = api_app_state.get_collocation_by_guid(guid)
        assert cloze is not None
        assert cloze.syntactic_unit.card_type == "cloze"
        assert cloze.syntactic_unit.disambig_key == "morph:noun-acc-sg"
        expected_sentence = "Grem v Ljubljan{{c1::o}}."
        assert cloze.syntactic_unit.source_sentence == expected_sentence
        assert cloze.syntactic_unit.grammar == "ljubljana, accusative singular"
        assert cloze.anki_note_id is None
        assert cloze.directions[Direction.PRODUCTION].state == SRSState.NEW

    async def test_base_absent_returns_409(self, api_app_state):
        """No base collocation → 409, no row created."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                },
            )

        assert resp.status_code == 409
        detail = resp.json()["detail"].lower()
        assert "base word not yet learned" in detail or "not yet learned" in detail

        guid = compute_guid("Ljubljano", "sl", "morph:noun-acc-sg")
        assert api_app_state.get_collocation_by_guid(guid) is None

    async def test_base_not_learned_returns_409(self, api_app_state):
        """Base production still NEW → 409, no row created."""
        # Seed base with production in NEW (default)
        unit = SyntacticUnit(text="ljubljana", translation="ljubljana", word_count=1, difficulty=1, source="test")
        api_app_state.add_collocation(unit, language_code="sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                },
            )

        assert resp.status_code == 409
        detail = resp.json()["detail"].lower()
        assert "base word not yet learned" in detail or "not yet learned" in detail

        guid = compute_guid("Ljubljano", "sl", "morph:noun-acc-sg")
        assert api_app_state.get_collocation_by_guid(guid) is None

    async def test_degenerate_surface_equals_lemma_returns_422(self, api_app_state):
        """surface==lemma → 422."""
        self._seed_base_learned(api_app_state)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "ljubljana",
                    "lemma": "ljubljana",
                    "feature": "noun:nom:sg",
                    "sentence": "Ljubljana je lepa.",
                    "language_code": "sl",
                },
            )

        assert resp.status_code == 422
        detail = resp.json()["detail"].lower()
        assert "surface equals lemma" in detail or "nothing to cloze" in detail

    async def test_idempotent(self, api_app_state):
        """POST twice → exactly one row."""
        self._seed_base_learned(api_app_state)

        body = {
            "surface": "Ljubljano",
            "lemma": "ljubljana",
            "feature": "noun:acc:sg",
            "sentence": "Grem v Ljubljano.",
            "language_code": "sl",
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp1 = await client.post("/api/srs/inflection-clozes", json=body)
            resp2 = await client.post("/api/srs/inflection-clozes", json=body)

        assert resp1.status_code == 200
        assert resp2.status_code == 200

        guid = compute_guid("Ljubljano", "sl", "morph:noun-acc-sg")
        with api_app_state._get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM collocations WHERE guid = ?", (guid,)).fetchone()[0]
        assert count == 1

    async def test_sync_create_new_round_trip(self, api_app_state):
        """After creation, sync_create_new links an Anki Cloze note."""
        from app.anki.sync import AnkiSync, OfflineWriter
        from tests.test_anki_sync_create_new import (
            FakeReader,
            _make_dual_collection_conn,
        )

        self._seed_base_learned(api_app_state)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                },
            )
        assert resp.status_code == 200

        # Pre-link the base vocab so sync_create_new only processes the cloze
        base_item = api_app_state.get_collocation("ljubljana")
        api_app_state.set_anki_ids(base_item.guid, 99999, {Direction.RECOGNITION: 999991, Direction.PRODUCTION: 999992})

        anki_conn = _make_dual_collection_conn()
        writer = OfflineWriter(anki_conn)
        await AnkiSync(db=api_app_state, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )

        notes = anki_conn.execute("SELECT n.id, n.mid, n.flds, n.tags, n.guid FROM notes n").fetchall()
        assert len(notes) == 1
        note = notes[0]
        assert note["mid"] == 1000002  # Cloze notetype
        flds = note["flds"].split("\x1f")
        expected_cloze = "Grem v Ljubljan{{c1::o}}."
        assert flds[0] == expected_cloze
        assert "ljubljana, accusative singular" in flds[1]
        assert 'class="grammar"' in flds[1]

        cards = anki_conn.execute("SELECT * FROM cards WHERE nid = ?", (note["id"],)).fetchall()
        assert len(cards) == 1

        guid = compute_guid("Ljubljano", "sl", "morph:noun-acc-sg")
        cloze = api_app_state.get_collocation_by_guid(guid)
        assert cloze.anki_note_id == note["id"]

    async def test_audio_synthesis_failure_does_not_crash_endpoint(self, api_app_state, monkeypatch):
        """synthesize_cloze_audios raising does not prevent 200 response."""
        import app.api.srs as srs_mod

        audio_mock = AsyncMock(side_effect=RuntimeError("TTS failed"))
        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", audio_mock)

        self._seed_base_learned(api_app_state)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["was_created"] is True

    async def test_surfaces_same_day_in_review_queue(self, api_app_state):
        """NEW cloze appears in /review-queue without sync."""
        self._seed_base_learned(api_app_state)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                },
            )
        assert resp.status_code == 200

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            queue_resp = await client.get("/api/srs/review-queue")
        assert queue_resp.status_code == 200
        queue = queue_resp.json()["queue"]

        matching = [q for q in queue if q["text"] == "Ljubljano"]
        assert len(matching) >= 1
        item = matching[0]
        assert item["card_type"] == "cloze"
        assert item["state"] == "new"

    async def test_biti_with_no_base_succeeds(self, api_app_state):
        """biti is a clozes-only verb — no base required for inflection cloze."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "ste",
                    "lemma": "biti",
                    "feature": "verb:2pl",
                    "sentence": "Zdravo kje ste",
                    "language_code": "sl",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["was_created"] is True

        guid = compute_guid("ste", "sl", "morph:verb-2pl")
        cloze = api_app_state.get_collocation_by_guid(guid)
        assert cloze is not None
        assert cloze.syntactic_unit.card_type == "cloze"
        assert cloze.syntactic_unit.disambig_key == "morph:verb-2pl"
        assert cloze.syntactic_unit.grammar == "biti, 2nd person plural"
        assert cloze.directions[Direction.PRODUCTION].state == SRSState.NEW

    async def test_biti_with_no_base_idempotent(self, api_app_state):
        """POST biti inflection cloze twice → exactly one row."""
        body = {
            "surface": "ste",
            "lemma": "biti",
            "feature": "verb:2pl",
            "sentence": "Zdravo kje ste",
            "language_code": "sl",
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp1 = await client.post("/api/srs/inflection-clozes", json=body)
            resp2 = await client.post("/api/srs/inflection-clozes", json=body)

        assert resp1.status_code == 200
        assert resp2.status_code == 200

        guid = compute_guid("ste", "sl", "morph:verb-2pl")
        with api_app_state._get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM collocations WHERE guid = ?", (guid,)).fetchone()[0]
        assert count == 1
