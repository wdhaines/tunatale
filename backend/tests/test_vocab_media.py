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


async def test_forvo_audio_prefix_follows_passed_language_code(media_dir, monkeypatch) -> None:
    """tunatale-w4m7.12: an explicit language_code names the Forvo file, not the
    process-global default — a Slovene card minted on a Norwegian-default
    instance must be sl_*.mp3, not no_*.mp3."""
    monkeypatch.setattr(vocab_media.settings, "target_language", "no")
    db = _FakeDB()

    async def _query(*_a, **_k):
        return "q"

    async def _fetch(*_a, **_k):
        return MediaResult(audio_bytes=b"AUD", audio_source="forvo")

    out = await vocab_media.generate_vocab_media(
        db,
        7,
        "govoriti",
        "to speak",
        llm=object(),
        pixabay_key="k",
        language_code="sl",
        _query_fn=_query,
        _fetch_fn=_fetch,
    )
    assert out["audio"] == "sl_govoriti.mp3"


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


async def test_disabled_stores_status_without_warning(media_dir, caplog) -> None:
    """Forvo turned OFF is a configuration, not a failure — it must stay quiet.

    Production accepts TTS-only (Forvo blocks datacenter IPs). Warning on every
    card-add for a state the operator chose would bury the `blocked` warning
    above, which is the one that means something went wrong unexpectedly.
    """
    db = _FakeDB()

    async def _query(*_a, **_k):
        return "water"

    async def _fetch(*_a, **_k):
        return MediaResult(audio_bytes=b"TTS", audio_source="tts", audio_status="disabled")

    with caplog.at_level("WARNING"):
        out = await vocab_media.generate_vocab_media(
            db, 1, "voda", "water", llm=object(), pixabay_key="k", _query_fn=_query, _fetch_fn=_fetch
        )
    assert out["audio_status"] == "disabled"
    assert out["audio"]
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


class TestADuplicatePictureIsRetriedOnce:
    """A refused duplicate must not leave the card imageless when a second
    picture was there for the asking (tunatale-t61w).

    Measured 2026-10-06 on the owner's Cebuano deck: `anhi` "come" and `kanus-a`
    "when" were added from the reader and came up with no picture. Both fetches
    had SUCCEEDED — the query cache held "person entering house" and "calendar
    page date" — and both were refused because another card already showed those
    exact bytes (`bisita` "visitor", `petsa` "date"). The refusal was right; what
    was missing is the pre-stage's second half, where the URL that produced the
    duplicate is barred and the same query is asked once more for the NEXT hit.
    """

    _URL_1 = "https://cdn.pixabay.com/photo/first.jpg"
    _URL_2 = "https://cdn.pixabay.com/photo/second.jpg"

    def _fetcher(self, results):
        """A fetch fake that hands out *results* in order and records each call."""
        calls: list[dict] = []
        queue = list(results)

        async def _fetch(word, english, **kwargs):
            # A snapshot: the set is mutated in place between calls.
            calls.append({**kwargs, "used_image_urls": set(kwargs["used_image_urls"] or ())})
            result = queue.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result

        return _fetch, calls

    async def _generate(self, db, fetch, *, used_image_urls=None):
        async def _query(*_a, **_k):
            return "boat on water"

        return await vocab_media.generate_vocab_media(
            db,
            7,
            "båt",
            "boat",
            llm=object(),
            pixabay_key="k",
            language_code="no",
            used_image_urls=used_image_urls,
            _query_fn=_query,
            _fetch_fn=fetch,
        )

    def _first(self):
        return MediaResult(
            audio_bytes=b"AUD",
            audio_source="tts",
            image_bytes=b"TAKEN",
            image_ext="jpg",
            image_url=self._URL_1,
            image_status="ok",
        )

    async def test_the_next_picture_is_fetched_and_stored(self, media_dir) -> None:
        db = _FakeDB(owned_digests={sha256(b"TAKEN").hexdigest(): 42})
        fetch, calls = self._fetcher(
            [
                self._first(),
                MediaResult(image_bytes=b"FREE", image_ext="png", image_url=self._URL_2, image_status="ok"),
            ]
        )

        out = await self._generate(db, fetch)

        assert out["image"] == f"img_boat_{sha256(b'FREE').hexdigest()[:8]}.png"
        assert (media_dir / out["image"]).read_bytes() == b"FREE"
        assert "image_duplicate_of" not in out, "the card HAS a picture; nothing was left refused"
        assert len(calls) == 2
        # The retry asks the SAME query with the offending URL barred — that is
        # what makes the pipeline hand back the next candidate rather than the
        # same top hit — and asks for no audio, which the first call already got.
        assert calls[0]["used_image_urls"] == set()
        assert calls[1]["used_image_urls"] == {self._URL_1}
        assert calls[1]["image_query"] == calls[0]["image_query"] == "boat on water"
        assert calls[1]["audio"] == "none"
        assert calls[1]["language_code"] == "no"
        assert "audio" not in calls[0], "the first call is unchanged"
        # One audio row and one image row: the retry must not store audio twice.
        assert [m[1] for m in db.media] == ["audio_tts", "image"]

    async def test_a_second_duplicate_ends_it(self, media_dir) -> None:
        """Bounded to ONE retry: a loop would spend the free-tier budget on a
        query whose results are simply exhausted."""
        db = _FakeDB(owned_digests={sha256(b"TAKEN").hexdigest(): 42, sha256(b"ALSO").hexdigest(): 43})
        fetch, calls = self._fetcher(
            [
                self._first(),
                MediaResult(image_bytes=b"ALSO", image_ext="jpg", image_url=self._URL_2, image_status="ok"),
            ]
        )

        out = await self._generate(db, fetch)

        assert "image" not in out
        assert out["image_duplicate_of"] == 43, "names the picture refused LAST"
        assert len(calls) == 2
        assert list(media_dir.glob("img_*")) == []

    async def test_a_retry_that_finds_nothing_leaves_the_card_imageless(self, media_dir, caplog) -> None:
        db = _FakeDB(owned_digests={sha256(b"TAKEN").hexdigest(): 42})
        fetch, calls = self._fetcher([self._first(), MediaResult(image_status="no_results")])

        with caplog.at_level("WARNING"):
            out = await self._generate(db, fetch)

        assert "image" not in out
        assert out["image_duplicate_of"] == 42
        assert len(calls) == 2
        assert "duplicates collocation 42" in caplog.text

    async def test_a_retry_that_raises_does_not_fail_the_add(self, media_dir) -> None:
        """Media is best-effort; the card and the audio already stored survive."""
        db = _FakeDB(owned_digests={sha256(b"TAKEN").hexdigest(): 42})
        fetch, _calls = self._fetcher([self._first(), RuntimeError("pixabay down")])

        out = await self._generate(db, fetch)

        assert "image" not in out
        assert out["image_duplicate_of"] == 42
        assert out["audio"] == "tts_båt.mp3"

    async def test_a_batch_callers_url_set_learns_the_barred_url(self, media_dir) -> None:
        """`POST /items/batch` shares one set across its cards. The barred URL
        goes into THAT set, so a later card in the batch is not offered it."""
        db = _FakeDB(owned_digests={sha256(b"TAKEN").hexdigest(): 42})
        fetch, _calls = self._fetcher(
            [
                self._first(),
                MediaResult(image_bytes=b"FREE", image_ext="jpg", image_url=self._URL_2, image_status="ok"),
            ]
        )
        shared: set[str] = set()

        await self._generate(db, fetch, used_image_urls=shared)

        assert self._URL_1 in shared

    async def test_a_duplicate_with_no_url_cannot_be_barred_so_is_not_retried(self, media_dir) -> None:
        """Without the URL there is nothing to exclude, and the same query would
        return the same top hit: one wasted search and the same refusal."""
        db = _FakeDB(owned_digests={sha256(b"TAKEN").hexdigest(): 42})
        fetch, calls = self._fetcher([MediaResult(image_bytes=b"TAKEN", image_ext="jpg", image_status="ok")])

        out = await self._generate(db, fetch)

        assert out["image_duplicate_of"] == 42
        assert len(calls) == 1

    async def test_through_the_real_pipeline_the_retry_gets_the_second_hit(self, media_dir) -> None:
        """The seam the fakes above assume: `fetch_card_media` reports the URL it
        downloaded even when the caller passed no set, and filters a barred URL
        out of the next search. Nothing here is faked but the network."""
        from functools import partial

        from app.cards.media.forvo import ForvoOutcome, ForvoResult
        from app.cards.media.pipeline import fetch_card_media
        from app.cards.media.pixabay import PixabaySearch

        hits = [
            {"webformatURL": self._URL_1, "tags": "boat, water", "imageWidth": 800, "imageHeight": 600, "likes": 90},
            {"webformatURL": self._URL_2, "tags": "boat, water", "imageWidth": 800, "imageHeight": 600, "likes": 10},
        ]
        by_url = {self._URL_1: b"TAKEN", self._URL_2: b"FREE"}
        tts_renders: list[str] = []

        async def _tts(text, *, voice=None):
            tts_renders.append(text)
            return b"AUD"

        fetch = partial(
            fetch_card_media,
            forvo_enabled=True,
            normalize=False,
            _forvo_fn=lambda word, **_k: ForvoResult(ForvoOutcome.NO_PRONUNCIATION),
            _tts_fn=_tts,
            _search_fn=lambda query, **_k: PixabaySearch(hits=hits, status="ok"),
            _download_fn=lambda hit, **_k: (by_url[hit["webformatURL"]], "jpg", hit["webformatURL"]),
        )
        db = _FakeDB(owned_digests={sha256(b"TAKEN").hexdigest(): 42})

        async def _query(*_a, **_k):
            return "boat water"

        out = await vocab_media.generate_vocab_media(
            db, 7, "båt", "boat", llm=None, pixabay_key="k", language_code="no", _query_fn=_query, _fetch_fn=fetch
        )

        assert out["image"] == f"img_boat_{sha256(b'FREE').hexdigest()[:8]}.jpg"
        assert tts_renders == ["båt"], "the retry rendered no second voice"


@pytest.mark.parametrize(
    ("word", "language_code", "stem"),
    [("fem", "no", "count_005"), ("singko", "ceb", "clock_05"), ("kinse", "ceb", "money_0015")],
)
async def test_a_number_is_drawn_not_searched_for(media_dir, word, language_code, stem) -> None:
    """A number added as a NEW card gets its drawn picture (tunatale-w4m7.10).

    This path used to send "five" to the LLM image query and Pixabay like any
    other word, so only numbers promoted through the pre-stage were drawn. The
    fetch still runs — the card needs audio — but is told to skip the image.
    """
    from app.cards.number_picture import number_picture

    db = _FakeDB()
    queried: list[str] = []
    fetched_with: list[str | None] = []

    async def _query(word, *_a, **_k):  # must NOT be called
        queried.append(word)
        return "a photo of a numeral"

    async def _fetch(*_a, image_query=None, **_k):
        fetched_with.append(image_query)
        return MediaResult(audio_bytes=b"AUD", audio_source="tts", image_status="skipped")

    out = await vocab_media.generate_vocab_media(
        db, 7, word, "n", llm=object(), pixabay_key="k", language_code=language_code, _query_fn=_query, _fetch_fn=_fetch
    )

    picture = number_picture(word, language_code)
    assert picture is not None and picture.filename.startswith(f"{stem}_")
    assert out["image"] == picture.filename
    assert (media_dir / picture.filename).read_bytes() == picture.svg
    assert queried == []
    assert fetched_with == [""]
    assert out["audio"].startswith("tts_")


async def test_an_ordinary_word_still_gets_an_image_query(media_dir) -> None:
    """The control for the test above: the number check does not swallow words."""
    db = _FakeDB()
    queried: list[str] = []

    async def _query(word, *_a, **_k):
        queried.append(word)
        return "a boat"

    async def _fetch(*_a, **_k):
        return MediaResult()

    await vocab_media.generate_vocab_media(
        db, 7, "båt", "boat", llm=object(), pixabay_key="k", language_code="no", _query_fn=_query, _fetch_fn=_fetch
    )
    assert queried == ["båt"]


@pytest.mark.parametrize(("word", "language_code", "english"), [("under", "no", "under"), ("sulod", "ceb", "inside")])
async def test_a_spatial_word_is_drawn_not_searched_for(media_dir, word, language_code, english) -> None:
    """A spatial word added as a NEW card gets its box-and-ball picture (tunatale-hvj0)."""
    from app.cards.spatial_picture import spatial_picture

    db = _FakeDB()
    queried: list[str] = []
    fetched_with: list[str | None] = []

    async def _query(word, *_a, **_k):  # must NOT be called
        queried.append(word)
        return "a photo of a table"

    async def _fetch(*_a, image_query=None, **_k):
        fetched_with.append(image_query)
        return MediaResult(audio_bytes=b"AUD", audio_source="tts", image_status="skipped")

    out = await vocab_media.generate_vocab_media(
        db,
        7,
        word,
        english,
        llm=object(),
        pixabay_key="k",
        language_code=language_code,
        _query_fn=_query,
        _fetch_fn=_fetch,
    )

    picture = spatial_picture(word, language_code, english)
    assert picture is not None and out["image"] == picture.filename
    assert (media_dir / picture.filename).read_bytes() == picture.svg
    assert (queried, fetched_with) == ([], [""])


async def test_a_spatial_homograph_glossed_with_its_other_sense_is_searched_for(media_dir) -> None:
    """Cebuano `wala` is "left" and "none": a card glossed "none" gets no arrow."""
    db = _FakeDB()
    queried: list[str] = []

    async def _query(word, *_a, **_k):
        queried.append(word)
        return "nothing"

    async def _fetch(*_a, **_k):
        return MediaResult()

    await vocab_media.generate_vocab_media(
        db, 7, "wala", "none", llm=object(), pixabay_key="k", language_code="ceb", _query_fn=_query, _fetch_fn=_fetch
    )
    assert queried == ["wala"]
