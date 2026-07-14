"""Add-time vocab media at the endpoint level.

A card created in TunaTale must be complete (image + audio) in /review without a
sync — the nasvidenje gap. These tests pin that POST /items and the base-card
path fetch and attach media inline. The Pixabay/Forvo fetch and the image-query
LLM call are patched so nothing hits the network; conftest pins
``pixabay_api_key=""`` by default, so each test that wants generation sets it.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.cards.media import vocab_media
from app.cards.media.pipeline import MediaResult
from app.config import settings
from app.main import app


@pytest.fixture
def fake_media(monkeypatch):
    """Patch vocab_media's fetch + image-query so generation runs offline."""
    monkeypatch.setattr(settings, "pixabay_api_key", "test-key")

    async def _query(_word, _english, **_kw):
        return "a clear depiction"

    async def _fetch(_word, _english, **_kw):
        return MediaResult(
            audio_bytes=b"AUDIO",
            audio_source="forvo",
            image_bytes=b"IMAGE",
            image_ext="jpg",
        )

    monkeypatch.setattr(vocab_media, "generate_image_query", _query)
    monkeypatch.setattr(vocab_media, "fetch_card_media", _fetch)


async def test_create_item_attaches_media_inline(api_app_state, fake_media):
    """POST /items returns image_url + audio_url and records media rows — no sync."""
    db = api_app_state
    payload = {"text": "nasvidenje", "translation": "goodbye", "language_code": "sl", "word_count": 1}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/srs/items", json=payload)

    assert resp.status_code == 201
    data = resp.json()
    assert data["image_url"] == "/api/srs/media/img_goodbye.jpg"
    assert data["audio_url"] == "/api/srs/media/sl_nasvidenje.mp3"

    # Media rows persisted against the new collocation.
    assert db.get_image_filename(data["id"]) == "img_goodbye.jpg"
    assert db.get_audio_filename(data["id"]) == "sl_nasvidenje.mp3"


async def test_create_item_without_key_skips_media(api_app_state):
    """No Pixabay key (conftest default) → no media, card still created."""
    db = api_app_state
    payload = {"text": "nasvidenje", "translation": "goodbye", "language_code": "sl", "word_count": 1}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/srs/items", json=payload)

    assert resp.status_code == 201
    data = resp.json()
    assert data["image_url"] is None
    assert data["audio_url"] is None
    assert db.get_image_filename(data["id"]) is None


async def test_base_card_vocab_attaches_media_inline(api_app_state, fake_media):
    """A content-word base card (/items/base) also completes inline."""
    payload = {
        "lemma": "morje",
        "surface": "morje",
        "translation": "sea",
        "sentence": "Vidim morje.",
        "language_code": "sl",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/srs/items/base", json=payload)

    assert resp.status_code == 200
    data = resp.json()
    assert data["was_created"] is True
    assert data["item"]["image_url"] == "/api/srs/media/img_sea.jpg"
    assert data["item"]["audio_url"] == "/api/srs/media/sl_morje.mp3"
