"""Unit tests for app.cards.media.vocab_media — add-time vocab media generation.

Covers the helper that the card-adding endpoints call so a new vocab card is
complete (image + word audio) in /review without waiting for a sync. The image
query and Pixabay/Forvo fetch are injected (``_query_fn`` / ``_fetch_fn``) so
these tests make no outbound HTTP.
"""

from __future__ import annotations

from hashlib import sha256

import pytest

from app.cards.media import vocab_media
from app.cards.media.pipeline import MediaResult


class _FakeDB:
    """Records add_media calls, and answers the cross-card duplicate lookup.

    ``owned_digests`` maps an image's full sha256 to the collocation that already
    holds those bytes — the state ``image_digest_owner`` reads. Empty by default,
    so the common case is "nothing else has this picture".
    """

    def __init__(self, owned_digests: dict[str, int] | None = None) -> None:
        self.media: list[tuple] = []
        self.owned_digests = owned_digests or {}

    def add_media(self, coll_id, kind, filename, path, anki_filename, sha256, size_bytes) -> int:
        self.media.append((coll_id, kind, filename, path, anki_filename, sha256, size_bytes))
        return len(self.media)

    def image_digest_owner(self, sha256: str, *, exclude_collocation_id: int) -> int | None:
        owner = self.owned_digests.get(sha256)
        return None if owner is None or owner == exclude_collocation_id else owner

    # generate_image_query is injected, so these are only here for the real-fn path
    def get_image_query(self, *_a, **_k):  # pragma: no cover - not hit (query injected)
        return None

    def set_image_query(self, *_a, **_k):  # pragma: no cover - not hit (query injected)
        return None


@pytest.fixture
def media_dir(tmp_path, monkeypatch):
    """Point _MEDIA_DIR at a tmp dir so stored bytes don't touch backend/media."""
    d = tmp_path / "media"
    monkeypatch.setattr(vocab_media, "_MEDIA_DIR", d)
    return d


def test_safe_stem_sanitizes() -> None:
    assert vocab_media.safe_stem("voda", "sl") == "sl_voda"
    assert vocab_media.safe_stem("letni čas", "sl") == "sl_letni_čas"
    assert vocab_media.safe_stem("hello!", "tts") == "tts_hello"
    assert vocab_media.safe_stem("table", "img").startswith("img_")


async def test_noop_without_pixabay_key() -> None:
    """No key configured → no fetch, no media (and no outbound HTTP in tests)."""
    db = _FakeDB()
    called = False

    async def _fetch(*_a, **_k):  # pragma: no cover - must NOT be called
        nonlocal called
        called = True
        return MediaResult()

    out = await vocab_media.generate_vocab_media(
        db, 1, "nasvidenje", "goodbye", llm=None, pixabay_key="", _fetch_fn=_fetch
    )
    assert out == {}
    assert called is False
    assert db.media == []


async def test_stores_image_and_audio(media_dir) -> None:
    db = _FakeDB()

    async def _query(*_a, **_k):
        return "waving goodbye"

    async def _fetch(*_a, **_k):
        return MediaResult(
            audio_bytes=b"AUD",
            audio_source="forvo",
            image_bytes=b"IMG",
            image_ext="jpg",
        )

    out = await vocab_media.generate_vocab_media(
        db, 7, "nasvidenje", "goodbye", llm=object(), pixabay_key="k", _query_fn=_query, _fetch_fn=_fetch
    )

    assert out == {"audio": "sl_nasvidenje.mp3", "image": "img_goodbye_d083ab05.jpg"}
    # Files written to the (tmp) media dir.
    assert (media_dir / "sl_nasvidenje.mp3").read_bytes() == b"AUD"
    assert (media_dir / "img_goodbye_d083ab05.jpg").read_bytes() == b"IMG"
    # Media rows recorded: audio_forvo + image.
    kinds = {row[1] for row in db.media}
    assert kinds == {"audio_forvo", "image"}


async def test_threads_language_code_to_fetch(media_dir) -> None:
    """Backlog #28: the card's language_code reaches fetch_card_media so a
    Norwegian card resolves the Norwegian voice / Forvo section."""
    db = _FakeDB()
    captured: dict[str, str] = {}

    async def _query(*_a, **_k):
        return "q"

    async def _fetch(*_a, language_code, **_k):
        captured["language_code"] = language_code
        return MediaResult()

    await vocab_media.generate_vocab_media(
        db,
        7,
        "snakke",
        "to speak",
        llm=object(),
        pixabay_key="k",
        language_code="no",
        _query_fn=_query,
        _fetch_fn=_fetch,
    )
    assert captured["language_code"] == "no"


async def test_forvo_audio_prefix_follows_target_language(media_dir, monkeypatch) -> None:
    """Forvo audio filename prefix is the active language code (so Norwegian
    Forvo audio is no_*.mp3, matching the sync fetch path)."""
    monkeypatch.setattr(vocab_media.settings, "target_language", "no")
    db = _FakeDB()

    async def _query(*_a, **_k):
        return "speaking"

    async def _fetch(*_a, **_k):
        return MediaResult(audio_bytes=b"AUD", audio_source="forvo")

    out = await vocab_media.generate_vocab_media(
        db, 7, "snakke", "to speak", llm=object(), pixabay_key="k", _query_fn=_query, _fetch_fn=_fetch
    )
    assert out["audio"] == "no_snakke.mp3"
    assert (media_dir / "no_snakke.mp3").read_bytes() == b"AUD"


async def test_tts_audio_prefix(media_dir) -> None:
    """Non-Forvo audio is stored under the tts_ prefix / audio_tts kind."""
    db = _FakeDB()

    async def _query(*_a, **_k):
        return "q"

    async def _fetch(*_a, **_k):
        return MediaResult(audio_bytes=b"AUD", audio_source="tts")

    out = await vocab_media.generate_vocab_media(
        db, 1, "voda", "water", llm=object(), pixabay_key="k", _query_fn=_query, _fetch_fn=_fetch
    )
    assert out == {"audio": "tts_voda.mp3"}
    assert db.media[0][1] == "audio_tts"


async def test_image_ext_defaults_to_jpg(media_dir) -> None:
    db = _FakeDB()

    async def _query(*_a, **_k):
        return "q"

    async def _fetch(*_a, **_k):
        return MediaResult(image_bytes=b"IMG", image_ext=None)

    out = await vocab_media.generate_vocab_media(
        db, 1, "voda", "water", llm=object(), pixabay_key="k", _query_fn=_query, _fetch_fn=_fetch
    )
    assert out == {"image": "img_water_d083ab05.jpg"}


async def test_none_media_result_stores_nothing(media_dir) -> None:
    db = _FakeDB()

    async def _query(*_a, **_k):
        return "q"

    async def _fetch(*_a, **_k):
        return None

    out = await vocab_media.generate_vocab_media(
        db, 1, "voda", "water", llm=object(), pixabay_key="k", _query_fn=_query, _fetch_fn=_fetch
    )
    assert out == {}
    assert db.media == []


async def test_fetch_exception_is_swallowed(media_dir, caplog) -> None:
    """A network/LLM error must not propagate — card creation can't fail on media."""
    db = _FakeDB()

    async def _query(*_a, **_k):
        raise RuntimeError("groq down")

    out = await vocab_media.generate_vocab_media(
        db, 1, "voda", "water", llm=object(), pixabay_key="k", _query_fn=_query
    )
    assert out == {}
    assert db.media == []
    assert any("vocab media generation failed" in r.message for r in caplog.records)


async def test_image_rate_limited_logs_warning(media_dir, caplog) -> None:
    """A rate_limited image_status → warning, no image key, image_status stored."""
    db = _FakeDB()

    async def _query(*_a, **_k):
        return "water"

    async def _fetch(*_a, **_k):
        return MediaResult(image_status="rate_limited")

    with caplog.at_level("WARNING"):
        out = await vocab_media.generate_vocab_media(
            db, 1, "voda", "water", llm=object(), pixabay_key="k", _query_fn=_query, _fetch_fn=_fetch
        )
    assert "image" not in out
    assert out["image_status"] == "rate_limited"
    assert any("image fetch failed" in r.message for r in caplog.records)


async def test_forvo_blocked_logs_warning_and_stores_status(media_dir, caplog) -> None:
    """A Forvo outage must reach the log, not just quietly become a TTS card.

    This is the visibility the scraper never had: the card still gets audio, so
    nothing downstream looks wrong — the warning is the only thing that says
    Forvo stopped working.
    """
    db = _FakeDB()

    async def _query(*_a, **_k):
        return "water"

    async def _fetch(*_a, **_k):
        return MediaResult(audio_bytes=b"TTS", audio_source="tts", audio_status="blocked")

    with caplog.at_level("WARNING"):
        out = await vocab_media.generate_vocab_media(
            db, 1, "voda", "water", llm=object(), pixabay_key="k", _query_fn=_query, _fetch_fn=_fetch
        )
    assert out["audio_status"] == "blocked"
    assert out["audio"]  # the card is still fully usable
    assert any("Forvo unavailable" in r.message for r in caplog.records)


async def test_no_pronunciation_stores_status_without_warning(media_dir, caplog) -> None:
    """The common case must stay quiet or the warning above becomes unreadable noise."""
    db = _FakeDB()

    async def _query(*_a, **_k):
        return "water"

    async def _fetch(*_a, **_k):
        return MediaResult(audio_bytes=b"TTS", audio_source="tts", audio_status="no_pronunciation")

    with caplog.at_level("WARNING"):
        out = await vocab_media.generate_vocab_media(
            db, 1, "voda", "water", llm=object(), pixabay_key="k", _query_fn=_query, _fetch_fn=_fetch
        )
    assert out["audio_status"] == "no_pronunciation"
    assert not any("Forvo unavailable" in r.message for r in caplog.records)


async def test_image_ok_sets_status(media_dir) -> None:
    """Happy path: image_status='ok' propagated to stored dict."""
    db = _FakeDB()

    async def _query(*_a, **_k):
        return "water"

    async def _fetch(*_a, **_k):
        return MediaResult(image_bytes=b"IMG", image_ext="jpg", image_status="ok")

    out = await vocab_media.generate_vocab_media(
        db, 1, "voda", "water", llm=object(), pixabay_key="k", _query_fn=_query, _fetch_fn=_fetch
    )
    assert out["image"] == "img_water_d083ab05.jpg"
    assert out["image_status"] == "ok"


class TestAddTimeImagesAreUniquePerCard:
    """Two defects in the add-time image write, both measured 2026-09-06.

    1. It was the ONE write path that named its file bare — `img_<gloss>.<ext>` —
       while the pre-stage, `promote_production_cards` and `replace_item_image`
       all hash-suffix. `store_tt_media` writes with `write_bytes`, so a second
       card sharing an English gloss overwrote the first card's picture in place,
       silently changing a card the learner already knew. Found as 3 Slovene
       files whose on-disk bytes no longer matched their recorded sha256, all
       three bare-named.
    2. Nothing asked whether another card already held those exact bytes. On the
       Norwegian deck 16 image files were each shown on 2+ different words, all
       with live production cards — `vite` and `kjenne` are both "know", and one
       photo cannot ask for a particular one of them.
    """

    @staticmethod
    async def _generate(db, *, image_bytes=b"IMG", coll_id=7, english="goodbye"):
        async def _query(*_a, **_k):
            return "waving goodbye"

        async def _fetch(*_a, **_k):
            return MediaResult(image_bytes=image_bytes, image_ext="jpg", image_status="ok")

        return await vocab_media.generate_vocab_media(
            db, coll_id, "nasvidenje", english, llm=object(), pixabay_key="k", _query_fn=_query, _fetch_fn=_fetch
        )

    async def test_the_filename_carries_the_digest(self, media_dir) -> None:
        """A shared gloss must not resolve to one filename."""
        db = _FakeDB()
        first = await self._generate(db, image_bytes=b"FIRST", english="decision")
        second = await self._generate(db, image_bytes=b"SECOND", english="decision", coll_id=8)

        assert first["image"] != second["image"], "two pictures, two filenames"
        assert sha256(b"FIRST").hexdigest()[:8] in first["image"]
        assert sha256(b"SECOND").hexdigest()[:8] in second["image"]
        # And neither overwrote the other on disk.
        assert (media_dir / first["image"]).read_bytes() == b"FIRST"
        assert (media_dir / second["image"]).read_bytes() == b"SECOND"

    async def test_bytes_another_card_already_holds_are_not_stored(self, media_dir) -> None:
        """`vite` and `kjenne` must not end up fronted by the same photograph."""
        db = _FakeDB(owned_digests={sha256(b"IMG").hexdigest(): 42})

        out = await self._generate(db)

        assert "image" not in out
        # The FETCH was fine; what we declined was the store. Two facts, two keys.
        assert out["image_status"] == "ok"
        assert out["image_duplicate_of"] == 42
        assert db.media == [], "nothing written"
        assert list(media_dir.glob("img_*")) == []

    async def test_a_cards_own_picture_is_not_a_collision(self, media_dir) -> None:
        """Re-storing the same card's image must still work — the lookup excludes
        the card being written, or a refresh would refuse itself."""
        db = _FakeDB(owned_digests={sha256(b"IMG").hexdigest(): 7})

        out = await self._generate(db, coll_id=7)

        assert out["image"].startswith("img_goodbye_")
        assert len(db.media) == 1

    async def test_a_refused_duplicate_leaves_the_word_for_the_pre_stage(self, media_dir, caplog) -> None:
        """Not silent: the word is now imageless, and something must say so.

        It stays a pre-stage candidate — the mint queue, or the repair queue if
        its production card already exists — and that pass retries with the URL
        set populated.
        """
        db = _FakeDB(owned_digests={sha256(b"IMG").hexdigest(): 42})

        with caplog.at_level("WARNING"):
            await self._generate(db)

        assert "duplicates collocation 42" in caplog.text
