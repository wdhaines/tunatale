"""Tests for the manual image-update API endpoints (Step 8).

Covers: 404, 409 (cloze / no pixabay key), each search_pixabay status,
422s (bad scheme, non-image content, oversize, bad magic bytes), happy paths
for all four verbs, dirty-flag set after mutations, old media deleted on replace,
ext-from-sniff (not filename/URL).
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase

# -- helpers ------------------------------------------------------------------

_JPG = b"\xff\xd8\xff" + b"\x00" * 100  # minimal JPEG
_JPG2 = b"\xff\xd8\xff" + b"\x01" * 100  # different JPEG (different hash)
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # minimal PNG
_GIF = b"GIF89a" + b"\x00" * 100
_WEBP = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 100
_TEXT = b"this is not an image"
_OVERSIZE = b"\xff\xd8\xff" + b"\x00" * (10 * 1024 * 1024 + 1)


def _unit(text: str = "voda", translation: str = "water") -> SyntacticUnit:
    return SyntacticUnit(text=text, translation=translation, word_count=1, difficulty=1, source="corpus")


def _cloze_unit(text: str = "dan", translation: str = "day") -> SyntacticUnit:
    return SyntacticUnit(
        text=text,
        translation=translation,
        word_count=1,
        difficulty=1,
        source="corpus",
        card_type="cloze",
    )


def _id_for_text(db: SRSDatabase, text: str) -> int:
    with db._get_conn() as conn:
        return conn.execute("SELECT id FROM collocations WHERE text = ?", (text,)).fetchone()[0]


def _dirty_fields(db: SRSDatabase, coll_id: int) -> str:
    with db._get_conn() as conn:
        row = conn.execute("SELECT dirty_fields FROM collocations WHERE id = ?", (coll_id,)).fetchone()
    return row["dirty_fields"] if row else ""


def _make_fake_search(hits=None, status="ok"):
    """Return a mock search_pixabay function."""
    from app.cards.media.pixabay import PixabaySearch

    if hits is None:
        hits = [
            {
                "previewURL": "https://cdn.pixabay.com/preview/prev.jpg",
                "webformatURL": "https://cdn.pixabay.com/photo/prev_full.jpg",
                "tags": "water, clear",
                "imageWidth": 800,
                "imageHeight": 600,
                "likes": 42,
            }
        ]

    def _search(query, *, api_key, http_client=None, per_page=50):
        return PixabaySearch(hits=hits, status=status)

    return _search


# -- fixtures -----------------------------------------------------------------


@pytest.fixture
def db():
    with SRSDatabase(":memory:") as d:
        yield d


@pytest.fixture
def api(db, monkeypatch):
    """Set up app state + yield an async client."""
    from app.languages import get_language
    from app.main import app as _app

    _app.state.srs_db = db
    _app.state.content_store = MagicMock()
    _app.state.language = get_language("sl")
    try:
        yield db
    finally:
        for attr in ("srs_db", "content_store", "language"):
            if hasattr(_app.state, attr):
                delattr(_app.state, attr)


# -- 404 / 409 ----------------------------------------------------------------


class TestResolveGuards:
    async def test_404_missing_item(self, api):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/srs/items/99999/image/candidates")
        assert resp.status_code == 404

    async def test_409_cloze_candidates(self, api):
        api.add_collocation(_cloze_unit(), language_code="sl")
        cid = _id_for_text(api, "dan")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(f"/api/srs/items/{cid}/image/candidates")
        assert resp.status_code == 409

    async def test_409_cloze_put_url(self, api):
        api.add_collocation(_cloze_unit(), language_code="sl")
        cid = _id_for_text(api, "dan")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put(f"/api/srs/items/{cid}/image", json={"url": "http://x.com/a.jpg"})
        assert resp.status_code == 409

    async def test_409_cloze_upload(self, api):
        api.add_collocation(_cloze_unit(), language_code="sl")
        cid = _id_for_text(api, "dan")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put(
                f"/api/srs/items/{cid}/image/upload",
                files={"file": ("test.jpg", io.BytesIO(_JPG), "image/jpeg")},
            )
        assert resp.status_code == 409

    async def test_409_cloze_delete(self, api):
        api.add_collocation(_cloze_unit(), language_code="sl")
        cid = _id_for_text(api, "dan")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.delete(f"/api/srs/items/{cid}/image")
        assert resp.status_code == 409


# -- candidates endpoint ------------------------------------------------------


class TestCandidates:
    async def test_409_no_pixabay_key(self, api, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "pixabay_api_key", "")
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(f"/api/srs/items/{cid}/image/candidates")
        assert resp.status_code == 409

    async def test_ok_status(self, api, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "pixabay_api_key", "test-key")
        monkeypatch.setattr("app.cards.media.pixabay.search_pixabay", _make_fake_search(status="ok"))
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(f"/api/srs/items/{cid}/image/candidates")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["query"] == "water"
        assert len(body["candidates"]) == 1
        assert body["candidates"][0]["preview_url"] == "https://cdn.pixabay.com/preview/prev.jpg"
        assert body["candidates"][0]["tags"] == "water, clear"

    async def test_no_results_status(self, api, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "pixabay_api_key", "test-key")
        monkeypatch.setattr("app.cards.media.pixabay.search_pixabay", _make_fake_search(hits=[], status="no_results"))
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(f"/api/srs/items/{cid}/image/candidates")
        assert resp.status_code == 200
        assert resp.json()["status"] == "no_results"
        assert resp.json()["candidates"] == []

    async def test_rate_limited_status(self, api, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "pixabay_api_key", "test-key")
        monkeypatch.setattr("app.cards.media.pixabay.search_pixabay", _make_fake_search(hits=[], status="rate_limited"))
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(f"/api/srs/items/{cid}/image/candidates")
        assert resp.json()["status"] == "rate_limited"

    async def test_api_error_status(self, api, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "pixabay_api_key", "test-key")
        monkeypatch.setattr("app.cards.media.pixabay.search_pixabay", _make_fake_search(hits=[], status="api_error"))
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(f"/api/srs/items/{cid}/image/candidates")
        assert resp.json()["status"] == "api_error"

    async def test_custom_query_param(self, api, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "pixabay_api_key", "test-key")
        captured = []
        from app.cards.media.pixabay import PixabaySearch

        def _capture(query, *, api_key, http_client=None, per_page=50):
            captured.append(query)
            return PixabaySearch(hits=[], status="ok")

        monkeypatch.setattr("app.cards.media.pixabay.search_pixabay", _capture)
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(f"/api/srs/items/{cid}/image/candidates", params={"q": "ocean wave"})
        assert resp.status_code == 200
        assert captured == ["ocean wave"]
        assert resp.json()["query"] == "ocean wave"

    async def test_default_query_from_cache(self, api, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "pixabay_api_key", "test-key")
        captured = []
        from app.cards.media.pixabay import PixabaySearch

        def _capture(query, *, api_key, http_client=None, per_page=50):
            captured.append(query)
            return PixabaySearch(hits=[], status="ok")

        monkeypatch.setattr("app.cards.media.pixabay.search_pixabay", _capture)
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        api.set_image_query("voda", "water", "v1", "crystal clear water")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(f"/api/srs/items/{cid}/image/candidates")
        assert captured == ["crystal clear water"]
        assert resp.json()["query"] == "crystal clear water"

    async def test_candidates_capped_at_24(self, api, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "pixabay_api_key", "test-key")
        big_hits = [
            {
                "previewURL": f"https://cdn.pixabay.com/{i}.jpg",
                "webformatURL": f"https://cdn.pixabay.com/{i}_full.jpg",
                "tags": f"tag{i}",
                "imageWidth": 800,
                "imageHeight": 600,
                "likes": i,
            }
            for i in range(30)
        ]
        monkeypatch.setattr("app.cards.media.pixabay.search_pixabay", _make_fake_search(hits=big_hits))
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get(f"/api/srs/items/{cid}/image/candidates")
        assert len(resp.json()["candidates"]) == 24


# -- PUT image from URL -------------------------------------------------------


class TestPutImageUrl:
    @respx.mock
    async def test_happy_jpg(self, api, monkeypatch, tmp_path):
        monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path)
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        respx.get("http://img.test/photo.jpg").mock(
            return_value=httpx.Response(200, content=_JPG, headers={"content-type": "image/jpeg"}),
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put(f"/api/srs/items/{cid}/image", json={"url": "http://img.test/photo.jpg"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["image_url"] is not None
        assert body["image_url"].endswith(".jpg")
        assert api.get_image_filename(cid) is not None
        assert "image" in _dirty_fields(api, cid)

    @respx.mock
    async def test_ext_from_magic_not_url(self, api, monkeypatch, tmp_path):
        """URL says .gif but content is PNG → ext must be png."""
        monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path)
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        respx.get("http://img.test/fake.gif").mock(
            return_value=httpx.Response(200, content=_PNG, headers={"content-type": "image/gif"}),
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put(f"/api/srs/items/{cid}/image", json={"url": "http://img.test/fake.gif"})
        assert resp.status_code == 200
        filename = api.get_image_filename(cid)
        assert filename.endswith(".png"), f"ext should come from magic bytes, got {filename}"

    @respx.mock
    async def test_old_media_deleted_on_replace(self, api, monkeypatch, tmp_path):
        monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path)
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        # Set an initial image
        respx.get("http://img.test/first.jpg").mock(
            return_value=httpx.Response(200, content=_JPG, headers={"content-type": "image/jpeg"}),
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.put(f"/api/srs/items/{cid}/image", json={"url": "http://img.test/first.jpg"})
        first_filename = api.get_image_filename(cid)
        assert first_filename is not None
        first_path = tmp_path / first_filename
        assert first_path.exists()

        # Replace
        respx.get("http://img.test/second.jpg").mock(
            return_value=httpx.Response(200, content=_JPG2, headers={"content-type": "image/jpeg"}),
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.put(f"/api/srs/items/{cid}/image", json={"url": "http://img.test/second.jpg"})
        second_filename = api.get_image_filename(cid)
        assert second_filename is not None
        assert second_filename != first_filename
        # Old file unlinked, new file present
        assert not first_path.exists(), "Old image file should be unlinked after replace"
        assert (tmp_path / second_filename).exists()
        # Only one media row should exist
        with api._get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM media WHERE collocation_id = ? AND kind = 'image'", (cid,)
            ).fetchone()[0]
        assert count == 1

    @respx.mock
    async def test_replace_shared_file_not_unlinked(self, api, monkeypatch, tmp_path):
        monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path)
        api.add_collocation(_unit("ja", "yes"), language_code="sl")
        api.add_collocation(_unit("da", "yes"), language_code="sl")
        cid_a = _id_for_text(api, "ja")
        cid_b = _id_for_text(api, "da")

        # Set image on A
        respx.get("http://img.test/shared.jpg").mock(
            return_value=httpx.Response(200, content=_JPG, headers={"content-type": "image/jpeg"}),
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.put(f"/api/srs/items/{cid_a}/image", json={"url": "http://img.test/shared.jpg"})
        shared_filename = api.get_image_filename(cid_a)
        shared_path = tmp_path / shared_filename
        assert shared_path.exists()

        # Manually add a media row on B referencing the same file
        import hashlib

        api.add_media(
            cid_b,
            "image",
            shared_filename,
            f"media/{shared_filename}",
            shared_filename,
            hashlib.sha256(_JPG).hexdigest(),
            len(_JPG),
        )

        # Replace image on A — shared file must NOT be unlinked
        respx.get("http://img.test/new.jpg").mock(
            return_value=httpx.Response(200, content=_JPG2, headers={"content-type": "image/jpeg"}),
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put(f"/api/srs/items/{cid_a}/image", json={"url": "http://img.test/new.jpg"})
        assert resp.status_code == 200
        assert shared_path.exists(), "Shared file must survive replace on another collocation"
        assert api.get_image_filename(cid_a) != shared_filename

    async def test_422_bad_scheme(self, api):
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put(f"/api/srs/items/{cid}/image", json={"url": "ftp://img.test/a.jpg"})
        assert resp.status_code == 422

    @respx.mock
    async def test_422_non_image_content_type_and_magic(self, api, monkeypatch, tmp_path):
        monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path)
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        respx.get("http://img.test/file.txt").mock(
            return_value=httpx.Response(200, content=_TEXT, headers={"content-type": "text/plain"}),
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put(f"/api/srs/items/{cid}/image", json={"url": "http://img.test/file.txt"})
        assert resp.status_code == 422

    @respx.mock
    async def test_422_oversize(self, api, monkeypatch, tmp_path):
        monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path)
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        respx.get("http://img.test/big.jpg").mock(
            return_value=httpx.Response(200, content=_OVERSIZE, headers={"content-type": "image/jpeg"}),
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put(f"/api/srs/items/{cid}/image", json={"url": "http://img.test/big.jpg"})
        assert resp.status_code == 422

    @respx.mock
    async def test_dirty_flag_set(self, api, monkeypatch, tmp_path):
        monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path)
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        assert _dirty_fields(api, cid) == ""
        respx.get("http://img.test/d.jpg").mock(
            return_value=httpx.Response(200, content=_JPG, headers={"content-type": "image/jpeg"}),
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.put(f"/api/srs/items/{cid}/image", json={"url": "http://img.test/d.jpg"})
        assert "image" in _dirty_fields(api, cid)

    @respx.mock
    async def test_422_on_http_error_status(self, api, monkeypatch, tmp_path):
        monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path)
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        respx.get("http://img.test/missing.jpg").mock(
            return_value=httpx.Response(404, content=b"<html>Not Found</html>"),
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put(f"/api/srs/items/{cid}/image", json={"url": "http://img.test/missing.jpg"})
        assert resp.status_code == 422
        assert "HTTP 404" in resp.json()["detail"]

    @respx.mock
    async def test_422_on_redirect(self, api, monkeypatch, tmp_path):
        monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path)
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        respx.get("http://img.test/redir.jpg").mock(
            return_value=httpx.Response(
                302,
                headers={"Location": "http://img.test/moved.jpg"},
            ),
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put(f"/api/srs/items/{cid}/image", json={"url": "http://img.test/redir.jpg"})
        assert resp.status_code == 422
        assert "redirect" in resp.json()["detail"].lower()


# -- PUT image upload ---------------------------------------------------------


class TestPutImageUpload:
    async def test_happy_png(self, api, monkeypatch, tmp_path):
        monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path)
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put(
                f"/api/srs/items/{cid}/image/upload",
                files={"file": ("photo.png", io.BytesIO(_PNG), "image/png")},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["image_url"] is not None
        assert body["image_url"].endswith(".png")
        assert api.get_image_filename(cid) is not None

    async def test_ext_from_magic_not_filename(self, api, monkeypatch, tmp_path):
        """File named .txt but contains JPEG → ext must be jpg."""
        monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path)
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put(
                f"/api/srs/items/{cid}/image/upload",
                files={"file": ("photo.txt", io.BytesIO(_JPG), "application/octet-stream")},
            )
        assert resp.status_code == 200
        filename = api.get_image_filename(cid)
        assert filename.endswith(".jpg"), f"ext should come from magic bytes, got {filename}"

    async def test_422_oversize(self, api, monkeypatch, tmp_path):
        monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path)
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put(
                f"/api/srs/items/{cid}/image/upload",
                files={"file": ("big.jpg", io.BytesIO(_OVERSIZE), "image/jpeg")},
            )
        assert resp.status_code == 422

    async def test_422_not_image(self, api, monkeypatch, tmp_path):
        monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path)
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put(
                f"/api/srs/items/{cid}/image/upload",
                files={"file": ("readme.txt", io.BytesIO(_TEXT), "text/plain")},
            )
        assert resp.status_code == 422

    async def test_dirty_flag_set(self, api, monkeypatch, tmp_path):
        monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path)
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        assert _dirty_fields(api, cid) == ""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.put(
                f"/api/srs/items/{cid}/image/upload",
                files={"file": ("a.jpg", io.BytesIO(_JPG), "image/jpeg")},
            )
        assert "image" in _dirty_fields(api, cid)

    async def test_gif_upload(self, api, monkeypatch, tmp_path):
        monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path)
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put(
                f"/api/srs/items/{cid}/image/upload",
                files={"file": ("anim.gif", io.BytesIO(_GIF), "image/gif")},
            )
        assert resp.status_code == 200
        assert resp.json()["image_url"].endswith(".gif")


# -- DELETE image -------------------------------------------------------------


class TestDeleteImage:
    @respx.mock
    async def test_happy(self, api, monkeypatch, tmp_path):
        monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path)
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        # First, set an image
        respx.get("http://img.test/first.jpg").mock(
            return_value=httpx.Response(200, content=_JPG, headers={"content-type": "image/jpeg"}),
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.put(f"/api/srs/items/{cid}/image", json={"url": "http://img.test/first.jpg"})
        assert api.get_image_filename(cid) is not None

        # Delete it
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.delete(f"/api/srs/items/{cid}/image")
        assert resp.status_code == 200
        assert resp.json()["image_url"] is None
        assert api.get_image_filename(cid) is None
        assert "image" in _dirty_fields(api, cid)

    async def test_delete_no_image_is_noop(self, api):
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.delete(f"/api/srs/items/{cid}/image")
        assert resp.status_code == 200
        assert resp.json()["image_url"] is None
        assert "image" in _dirty_fields(api, cid)

    async def test_delete_sets_dirty_flag(self, api):
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.delete(f"/api/srs/items/{cid}/image")
        assert "image" in _dirty_fields(api, cid)

    @respx.mock
    async def test_delete_unlinks_unshared_file(self, api, monkeypatch, tmp_path):
        monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path)
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        respx.get("http://img.test/sole.jpg").mock(
            return_value=httpx.Response(200, content=_JPG, headers={"content-type": "image/jpeg"}),
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.put(f"/api/srs/items/{cid}/image", json={"url": "http://img.test/sole.jpg"})
        filename = api.get_image_filename(cid)
        filepath = tmp_path / filename
        assert filepath.exists()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.delete(f"/api/srs/items/{cid}/image")
        assert not filepath.exists()

    @respx.mock
    async def test_shared_file_not_unlinked_when_other_collocation_still_references_it(
        self, api, monkeypatch, tmp_path
    ):
        monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path)
        # Add two collocations
        api.add_collocation(_unit("ja", "yes"), language_code="sl")
        api.add_collocation(_unit("da", "yes"), language_code="sl")
        cid_a = _id_for_text(api, "ja")
        cid_b = _id_for_text(api, "da")

        # Set image on collocation A
        respx.get("http://img.test/shared.jpg").mock(
            return_value=httpx.Response(200, content=_JPG, headers={"content-type": "image/jpeg"}),
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.put(f"/api/srs/items/{cid_a}/image", json={"url": "http://img.test/shared.jpg"})
        shared_filename = api.get_image_filename(cid_a)
        shared_path = tmp_path / shared_filename
        assert shared_path.exists()

        # Manually add a media row on B referencing the same filename
        import hashlib

        api.add_media(
            cid_b,
            "image",
            shared_filename,
            f"media/{shared_filename}",
            shared_filename,
            hashlib.sha256(_JPG).hexdigest(),
            len(_JPG),
        )

        # Delete image from A — file should NOT be unlinked (B still references it)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.delete(f"/api/srs/items/{cid_a}/image")
        assert resp.status_code == 200
        assert shared_path.exists(), "Shared file must not be deleted while another collocation references it"

    @respx.mock
    async def test_delete_missing_file_is_swallowed(self, api, monkeypatch, tmp_path):
        monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path)
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        # Manually insert a media row with a filename that doesn't exist on disk
        import hashlib

        api.add_media(cid, "image", "ghost.jpg", "media/ghost.jpg", "ghost.jpg", hashlib.sha256(b"x").hexdigest(), 1)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.delete(f"/api/srs/items/{cid}/image")
        assert resp.status_code == 200

    @respx.mock
    async def test_replace_skips_new_file_from_unlink(self, api, monkeypatch, tmp_path):
        """When replacing, the new filename must not be unlinked even if it
        happens to match an old row (defensive: normal hashes differ)."""
        monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path)
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        # Manually insert an image row whose filename equals the one replace_item_image will produce
        import hashlib

        fake_new_fname = f"img_water_{hashlib.sha256(_JPG).hexdigest()[:8]}.jpg"
        api.add_media(
            cid,
            "image",
            fake_new_fname,
            f"media/{fake_new_fname}",
            fake_new_fname,
            hashlib.sha256(_JPG).hexdigest(),
            len(_JPG),
        )
        fake_path = tmp_path / fake_new_fname
        fake_path.write_bytes(_JPG)
        assert fake_path.exists()

        # Replace with the exact same bytes → skip_filename matches → must NOT unlink
        respx.get("http://img.test/replace.jpg").mock(
            return_value=httpx.Response(200, content=_JPG, headers={"content-type": "image/jpeg"}),
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put(f"/api/srs/items/{cid}/image", json={"url": "http://img.test/replace.jpg"})
        assert resp.status_code == 200
        assert fake_path.exists(), "skip_filename must prevent unlinking the new file"

    @respx.mock
    async def test_unlink_os_error_is_swallowed(self, api, monkeypatch, tmp_path):
        """An OSError during unlink must not 500 the request."""
        monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path)
        api.add_collocation(_unit(), language_code="sl")
        cid = _id_for_text(api, "voda")
        # Set an initial image
        respx.get("http://img.test/first.jpg").mock(
            return_value=httpx.Response(200, content=_JPG, headers={"content-type": "image/jpeg"}),
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.put(f"/api/srs/items/{cid}/image", json={"url": "http://img.test/first.jpg"})
        old_filename = api.get_image_filename(cid)
        old_path = tmp_path / old_filename
        assert old_path.exists()

        # Make unlink raise OSError
        original_unlink = Path.unlink

        def _raising_unlink(self, *a, **kw):
            if self == old_path:
                raise OSError("permission denied")
            return original_unlink(self, *a, **kw)

        monkeypatch.setattr(Path, "unlink", _raising_unlink)

        # Replace with a different image
        respx.get("http://img.test/second.jpg").mock(
            return_value=httpx.Response(200, content=_JPG2, headers={"content-type": "image/jpeg"}),
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.put(f"/api/srs/items/{cid}/image", json={"url": "http://img.test/second.jpg"})
        assert resp.status_code == 200, "OSError during unlink must not propagate"


# -- coverage gap helpers ------------------------------------------------------


class TestSniffExtCoverage:
    def test_riff_not_webp_returns_none(self):
        """Data starting with RIFF but missing the inner WEBP marker is not a match."""
        from app.api.srs_images import _sniff_ext

        riff_not_webp = b"RIFF" + b"\x00" * 4 + b"AVI " + b"\x00" * 100
        assert _sniff_ext(riff_not_webp) is None
