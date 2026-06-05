"""Tests for POST /api/srs/items/base (Phase 5, Part C — unknown→create base).

A click on an unknown transcript word mints its base card as NEW, branching by
word type (decision 8, C-a): function word → production-only cloze (surface
blanked in the sentence); content word → vocab (recognition + production). Both
honor the add_collocation card-adding contract (no Anki ids; sync mints + links).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.common.guid import compute_guid
from app.main import app
from app.models.srs_item import Direction, SRSState


class TestCreateBaseCard:
    @pytest.fixture(autouse=True)
    def _mock_audio(self, monkeypatch: pytest.MonkeyPatch):
        import app.api.srs as srs_mod

        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", AsyncMock())

    async def test_content_word_creates_vocab_base(self, api_app_state):
        """A non-function word → vocab base, NEW, both directions, no Anki ids."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": "kava",
                    "lemma": "kava",
                    "sentence": "Pijem kavo.",
                    "language_code": "sl",
                    "translation": "coffee",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["was_created"] is True
        item = data["item"]
        assert item["card_type"] == "vocab"
        assert item["state"] == "new"
        assert item["directions"]["recognition"] is not None
        assert item["directions"]["production"] is not None

        coll = api_app_state.get_collocation_by_guid(compute_guid("kava", "sl", ""))
        assert coll is not None
        assert coll.syntactic_unit.card_type == "vocab"
        assert coll.syntactic_unit.translation == "coffee"
        assert coll.anki_note_id is None
        assert coll.directions[Direction.RECOGNITION].state == SRSState.NEW
        assert coll.directions[Direction.PRODUCTION].state == SRSState.NEW

    async def test_clozes_only_verb_rejected(self, api_app_state):
        """A clozes-only verb (biti) has no base card — 409, no row created."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": "boste",
                    "lemma": "biti",
                    "sentence": "Kje boste ostali?",
                    "language_code": "sl",
                    "translation": "you will be",
                },
            )

        assert resp.status_code == 409
        detail = resp.json()["detail"].lower()
        assert "clozes-only" in detail or "no base card" in detail
        assert api_app_state.get_collocation_by_guid(compute_guid("biti", "sl", "")) is None

    async def test_function_word_creates_cloze_base(self, api_app_state):
        """A function word (surface==lemma) → production-only cloze base."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": "na",
                    "lemma": "na",
                    "sentence": "Kava na mizi.",
                    "language_code": "sl",
                    "translation": "on",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["was_created"] is True
        item = data["item"]
        assert item["card_type"] == "cloze"
        assert item["directions"]["recognition"] is None
        assert item["directions"]["production"] is not None

        coll = api_app_state.get_collocation_by_guid(compute_guid("na", "sl", ""))
        assert coll.syntactic_unit.card_type == "cloze"
        assert coll.syntactic_unit.source_sentence == "Kava {{c1::na}} mizi."
        assert coll.anki_note_id is None
        assert coll.directions[Direction.PRODUCTION].state == SRSState.NEW

    async def test_function_word_via_pos_blanks_surface_not_lemma(self, api_app_state, monkeypatch):
        """A function word classified via its classla UPOS (PRON) → cloze base,
        keyed by the lemma and blanking the surface as it appeared.

        Uses a pronoun (surface 'ga' / lemma 'on') so the surface≠lemma blanking
        branch is covered with a *non*-clozes-only word — biti is rejected outright
        (see test_clozes_only_verb_rejected). Also exercises the upos-present path.
        """
        import app.api.srs as srs_mod
        from tests._helpers.lemmatizer import StubLemmatizer

        stub = StubLemmatizer()
        stub.set_analysis("ga", "on", upos="PRON")
        monkeypatch.setattr(srs_mod, "_lemmatizer", stub)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": "ga",
                    "lemma": "on",
                    "sentence": "Ona ga vidi",
                    "language_code": "sl",
                    "translation": "him",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["item"]["card_type"] == "cloze"
        coll = api_app_state.get_collocation_by_guid(compute_guid("on", "sl", ""))
        assert coll.syntactic_unit.card_type == "cloze"
        assert coll.syntactic_unit.text == "on"
        assert coll.syntactic_unit.source_sentence == "Ona {{c1::ga}} vidi"
        assert coll.directions[Direction.PRODUCTION].state == SRSState.NEW

    async def test_idempotent_returns_existing(self, api_app_state):
        """POST twice → one row; second call was_created False, same id."""
        body = {
            "surface": "kava",
            "lemma": "kava",
            "sentence": "Pijem kavo.",
            "language_code": "sl",
            "translation": "coffee",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.post("/api/srs/items/base", json=body)
            r2 = await client.post("/api/srs/items/base", json=body)

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["was_created"] is True
        assert r2.json()["was_created"] is False
        assert r1.json()["id"] == r2.json()["id"]

        with api_app_state._get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM collocations WHERE guid = ?", (compute_guid("kava", "sl", ""),)
            ).fetchone()[0]
        assert count == 1

    async def test_translation_defaults_empty(self, api_app_state):
        """translation is optional; omitting it stores empty (no LLM call)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={"surface": "hotel", "lemma": "hotel", "sentence": "To je hotel.", "language_code": "sl"},
            )
        assert resp.status_code == 200
        coll = api_app_state.get_collocation_by_guid(compute_guid("hotel", "sl", ""))
        assert coll.syntactic_unit.translation == ""
        assert coll.syntactic_unit.card_type == "vocab"

    async def test_audio_synth_failure_does_not_crash(self, api_app_state, monkeypatch):
        """A cloze base whose audio synthesis raises still returns 200."""
        import app.api.srs as srs_mod

        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", AsyncMock(side_effect=RuntimeError("TTS failed")))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={"surface": "na", "lemma": "na", "sentence": "Kava na mizi.", "language_code": "sl"},
            )

        assert resp.status_code == 200
        assert resp.json()["was_created"] is True

    async def test_sync_create_new_round_trip_vocab(self, api_app_state):
        """A vocab base links a 2-card Anki note via sync_create_new."""
        from app.anki.sync import AnkiSync, OfflineWriter
        from tests.test_anki_sync_create_new import FakeReader, _make_dual_collection_conn

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": "kava",
                    "lemma": "kava",
                    "sentence": "Pijem kavo.",
                    "language_code": "sl",
                    "translation": "coffee",
                },
            )
        assert resp.status_code == 200

        anki_conn = _make_dual_collection_conn()
        writer = OfflineWriter(anki_conn)
        await AnkiSync(db=api_app_state, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )

        notes = anki_conn.execute("SELECT n.id, n.mid FROM notes n").fetchall()
        assert len(notes) == 1
        cards = anki_conn.execute("SELECT * FROM cards WHERE nid = ?", (notes[0]["id"],)).fetchall()
        assert len(cards) == 2  # recognition + production
        coll = api_app_state.get_collocation_by_guid(compute_guid("kava", "sl", ""))
        assert coll.anki_note_id == notes[0]["id"]

    async def test_sync_create_new_round_trip_cloze(self, api_app_state):
        """A function-word cloze base links a single-card Cloze note."""
        from app.anki.sync import AnkiSync, OfflineWriter
        from tests.test_anki_sync_create_new import FakeReader, _make_dual_collection_conn

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={"surface": "na", "lemma": "na", "sentence": "Kava na mizi.", "language_code": "sl"},
            )
        assert resp.status_code == 200

        anki_conn = _make_dual_collection_conn()
        writer = OfflineWriter(anki_conn)
        await AnkiSync(db=api_app_state, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )

        notes = anki_conn.execute("SELECT n.id, n.mid, n.flds FROM notes n").fetchall()
        assert len(notes) == 1
        assert notes[0]["mid"] == 1000002  # built-in Cloze notetype
        assert notes[0]["flds"].split("\x1f")[0] == "Kava {{c1::na}} mizi."
        cards = anki_conn.execute("SELECT * FROM cards WHERE nid = ?", (notes[0]["id"],)).fetchall()
        assert len(cards) == 1

    async def test_surfaces_same_day_in_review_queue(self, api_app_state):
        """A freshly minted NEW base appears in /review-queue without a sync."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": "kava",
                    "lemma": "kava",
                    "sentence": "Pijem kavo.",
                    "language_code": "sl",
                    "translation": "coffee",
                },
            )
        assert resp.status_code == 200

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            queue_resp = await client.get("/api/srs/review-queue")
        assert queue_resp.status_code == 200

        matching = [q for q in queue_resp.json()["queue"] if q["text"] == "kava"]
        assert len(matching) >= 1
        assert matching[0]["state"] == "new"
