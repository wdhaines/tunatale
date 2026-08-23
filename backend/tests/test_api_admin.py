"""Tests for admin endpoints."""

import sqlite3
import sys

from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app
from tests._helpers.anki_sync_create_new import _make_dual_collection_conn


def _write_collection(tmp_path, notes):
    """Persist a synthetic collection to disk and return its path.

    ``import_seed`` goes through ``safe_open``, which needs a real file (lock
    probe + SHA256 backup + integrity check), so the in-memory builder is dumped
    via sqlite3's backup API.

    *notes* is a list of ``(note_id, sfld, fields)``. Card rows carry the FULL
    column set, as real Anki always writes: a partial INSERT leaves reps/lapses
    NULL and the import fails on ``collocation_directions.reps NOT NULL``.
    """
    mem = _make_dual_collection_conn()
    for note_id, sfld, fields in notes:
        mem.execute(
            "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data) "
            "VALUES (?, ?, 1000001, 0, 0, '', ?, ?, 0, 0, '')",
            (note_id, f"g-{note_id}", "\x1f".join(fields), sfld),
        )
        mem.execute(
            "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl, "
            "factor, reps, lapses, left, odue, odid, flags, data) "
            "VALUES (?, ?, 12345, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, '')",
            (note_id * 10, note_id),
        )
    mem.commit()
    path = tmp_path / "collection.anki2"
    disk = sqlite3.connect(str(path))
    mem.backup(disk)
    disk.commit()
    disk.close()
    mem.close()
    return path


def _vocab_fields(l2, english, image=None):
    return [l2, english, "", f'<img src="{image}">' if image else "", "", "", ""]


def _pin_paths(monkeypatch, tmp_path, collection_path, anki_media):
    monkeypatch.setattr(settings, "anki_collection_path", collection_path)
    monkeypatch.setattr(settings, "anki_media_path", anki_media)
    # settings.media_dir defaults to ./media — the real backend/media, which holds
    # hundreds of MB of generated audio. Pin it, or a real import writes there.
    monkeypatch.setattr(settings, "media_dir", tmp_path / "tt_media")


async def _refresh():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/admin/refresh-media")


class TestRefreshMediaEndpoint:
    async def test_returns_503_when_anki_sync_plugin_not_importable(self, monkeypatch):
        """The anki_sync plugin is optional; refresh-media degrades to 503, not a crash.

        Simulates a missing/broken plugin package by making its import fail —
        the lazy `from app.plugins.anki_sync.import_seed import import_seed`
        inside the endpoint must catch this and return 503, never propagate.
        """
        monkeypatch.setitem(sys.modules, "app.plugins.anki_sync.import_seed", None)
        resp = await _refresh()
        assert resp.status_code == 503

    async def test_maps_counts_correctly(self, tmp_path, monkeypatch, api_app_state):
        """Each response key is fed by the right ``import_seed`` result field.

        Driven by three REAL imports of the same collection rather than a stubbed
        return dict: the first sees the image for the first time (``new_media``),
        the second sees identical bytes (``unchanged_media``), the third sees
        changed bytes (``updated_media``). A stub could assert the same mapping
        while the endpoint and import_seed disagreed about the field names —
        which is exactly what a rename would break.
        """
        collection = _write_collection(tmp_path, [(9001, "voda", _vocab_fields("voda", "water", "voda.jpg"))])
        anki_media = tmp_path / "anki_media"
        anki_media.mkdir()
        (anki_media / "voda.jpg").write_bytes(b"ORIGINAL-IMAGE")
        _pin_paths(monkeypatch, tmp_path, collection, anki_media)

        first = await _refresh()
        assert first.status_code == 200
        assert first.json() == {"updated": 0, "unchanged": 0, "new": 1, "errors": 0}

        second = await _refresh()
        assert second.json() == {"updated": 0, "unchanged": 1, "new": 0, "errors": 0}

        (anki_media / "voda.jpg").write_bytes(b"SWAPPED-IMAGE")
        third = await _refresh()
        assert third.json() == {"updated": 1, "unchanged": 0, "new": 0, "errors": 0}

    async def test_errors_only_counts_guid_collisions(self, tmp_path, monkeypatch, api_app_state):
        """``errors`` is fed by ``skipped_guid_collisions`` alone.

        The collection holds a note with empty L2 text, which the real import
        counts under ``skipped_non_vocab``. That bucket must NOT surface as
        ``errors`` — a skipped non-vocab note is normal, not a failure.
        """
        collection = _write_collection(
            tmp_path,
            [
                (9001, "voda", _vocab_fields("voda", "water", "voda.jpg")),
                (9002, "", _vocab_fields("", "not a vocab note")),
            ],
        )
        anki_media = tmp_path / "anki_media"
        anki_media.mkdir()
        (anki_media / "voda.jpg").write_bytes(b"ORIGINAL-IMAGE")
        _pin_paths(monkeypatch, tmp_path, collection, anki_media)

        resp = await _refresh()
        assert resp.status_code == 200
        # The non-vocab note was skipped (only the vocab note produced media)…
        assert resp.json()["new"] == 1
        # …and it did not inflate the error count.
        assert resp.json()["errors"] == 0

    async def test_raises_500_on_runtime_error(self, tmp_path, monkeypatch, api_app_state):
        """A real import failure surfaces as 500 carrying the underlying reason.

        Driven by a misconfigured ``anki_deck_name`` — the realistic trigger, and
        the one place ``import_seed`` itself raises RuntimeError. Reaching it for
        real (rather than a stubbed ``side_effect=RuntimeError``) is what pins
        that the endpoint's ``except RuntimeError`` actually matches what the
        import raises.

        ⚠️ Known gap this exposed, deliberately NOT changed here: a *missing*
        collection file raises ``sqlite3.OperationalError`` from the safety
        envelope, which the endpoint does not map — it escapes as an unhandled
        500 with no detail. The stub could never have shown that, because it
        chose the exception class the handler already caught.
        """
        collection = _write_collection(tmp_path, [(9001, "voda", _vocab_fields("voda", "water"))])
        anki_media = tmp_path / "anki_media"
        anki_media.mkdir()
        _pin_paths(monkeypatch, tmp_path, collection, anki_media)
        monkeypatch.setattr(settings, "anki_deck_name", "No Such Deck")

        resp = await _refresh()
        assert resp.status_code == 500
        assert "No Such Deck" in resp.json()["detail"]


async def _tts_cache():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get("/api/admin/tts-cache")


class TestTtsCacheStatsEndpoint:
    async def test_counts_mp3_sizes_only(self, tmp_path, monkeypatch):
        """``*.mp3`` under ``settings.tts_cache_dir`` is summed and counted.

        A non-mp3 file sits in the same directory on purpose: the cache is
        mp3-keyed by construction, and counting strays would misreport it.
        """
        cache = tmp_path / "tts-cache"
        cache.mkdir()
        (cache / "a.mp3").write_bytes(b"x" * 100)
        (cache / "b.mp3").write_bytes(b"y" * 250)
        (cache / "stray.txt").write_text("not audio")
        monkeypatch.setattr(settings, "tts_cache_dir", cache)

        resp = await _tts_cache()
        assert resp.status_code == 200
        assert resp.json() == {"present": True, "file_count": 2, "total_bytes": 350}

    async def test_missing_dir_is_absent_not_error(self, tmp_path, monkeypatch):
        """Fresh install: no cache dir yet is 200 with zeros, never a 500."""
        monkeypatch.setattr(settings, "tts_cache_dir", tmp_path / "does-not-exist")

        resp = await _tts_cache()
        assert resp.status_code == 200
        assert resp.json() == {"present": False, "file_count": 0, "total_bytes": 0}

    async def test_file_where_dir_expected_is_absent(self, tmp_path, monkeypatch):
        """A non-directory at the configured path is reported as absent."""
        blocker = tmp_path / "blocker"
        blocker.write_text("not a dir")
        monkeypatch.setattr(settings, "tts_cache_dir", blocker)

        resp = await _tts_cache()
        assert resp.status_code == 200
        assert resp.json() == {"present": False, "file_count": 0, "total_bytes": 0}
